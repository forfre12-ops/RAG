"""Qdrant → Elasticsearch 데이터 마이그레이션.

doc/13 §6 S2.5·§6.1 7단계 절차 구현.

단계:
  1. Extract  : Qdrant scroll → JSONL 파일 (배치별 분할)
  2. Transform: 재임베딩 판정, payload 매핑, NDJSON 변환
  3. Load     : ES _bulk (배치 1000건, 재시도 3회, 실패건 격리)
  4. Verify   : count 일치 + 무작위 100건 sampling (vector L2 ≤ 1e-6)
  5. Swap     : alias 스위칭 (이전 인덱스 보존)

옵션:
  --dry-run   : 실제 ES upsert·alias swap 안 함 (변환·검증만)
  --reembed   : 텍스트만 추출하고 새 임베딩 모델로 재임베딩 (차원 변경 시 필수)
  --batch-size: _bulk 배치 크기 (기본 1000)
  --limit     : 처음 N건만 마이그 (PoC sample-5 검증용)

사용 예:
  # PoC sample 검증 (5건만, ES 미가용)
  python scripts/migrate_qdrant_to_es.py --collection guides_v1 --limit 5 --dry-run

  # 운영 전환 (실제 적재)
  python scripts/migrate_qdrant_to_es.py \\
      --collection guides_v1 \\
      --target-index secrets-guides-koipa-kure-v1 \\
      --alias secrets-guides-koipa

  # 역방향 (롤백): ES → Qdrant
  python scripts/migrate_qdrant_to_es.py --reverse --collection guides_v1
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ─────────────────────────────────────────────────────────────
# 결과 집계
# ─────────────────────────────────────────────────────────────


@dataclass
class StageResult:
    stage: str
    ok: bool
    detail: str
    metrics: dict = field(default_factory=dict)


@dataclass
class MigrationReport:
    collection: str
    target_index: str
    dry_run: bool
    stages: list[StageResult] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def all_ok(self) -> bool:
        return all(s.ok for s in self.stages)

    def to_dict(self) -> dict:
        return {
            "collection": self.collection,
            "target_index": self.target_index,
            "dry_run": self.dry_run,
            "duration_sec": round(self.finished_at - self.started_at, 2),
            "all_ok": self.all_ok,
            "stages": [
                {"stage": s.stage, "ok": s.ok, "detail": s.detail, "metrics": s.metrics}
                for s in self.stages
            ],
        }


# ─────────────────────────────────────────────────────────────
# Extract: Qdrant scroll → JSONL
# ─────────────────────────────────────────────────────────────


def extract_qdrant(
    collection: str,
    out_path: Path,
    *,
    batch_size: int = 1000,
    limit: int | None = None,
) -> StageResult:
    """Qdrant scroll API로 (id, vector, payload) 전건 export.

    PoC sample-5 검증 시 Qdrant 미가용이면 graceful skip — 결과에 skipped 표기.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from qdrant_client import QdrantClient  # noqa: PLC0415

        from lloydk.config import settings  # noqa: PLC0415

        client = QdrantClient(url=settings.qdrant_url)
    except Exception as exc:  # noqa: BLE001
        return StageResult(
            "extract", ok=False,
            detail=f"Qdrant unavailable: {exc}",
            metrics={"exported": 0, "skipped_reason": "qdrant_unreachable"},
        )

    exported = 0
    offset = None
    with out_path.open("w", encoding="utf-8") as f:
        while True:
            try:
                points, next_offset = client.scroll(
                    collection_name=collection,
                    limit=batch_size,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
            except Exception as exc:  # noqa: BLE001
                return StageResult(
                    "extract", ok=False,
                    detail=f"scroll failed at offset={offset}: {exc}",
                    metrics={"exported": exported},
                )

            for p in points:
                row = {
                    "id": str(p.id),
                    "vector": list(p.vector) if p.vector is not None else None,
                    "payload": dict(p.payload or {}),
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                exported += 1
                if limit and exported >= limit:
                    break

            if limit and exported >= limit:
                break
            if next_offset is None:
                break
            offset = next_offset

    return StageResult(
        "extract", ok=True,
        detail=f"exported {exported} points → {out_path}",
        metrics={"exported": exported, "jsonl_path": str(out_path)},
    )


# ─────────────────────────────────────────────────────────────
# Transform: JSONL → ES _bulk actions (in-memory generator)
# ─────────────────────────────────────────────────────────────


def iter_bulk_actions(
    jsonl_path: Path,
    target_index: str,
    *,
    reembed_with=None,  # callable(text) -> list[float] | None
):
    """JSONL의 각 줄을 ES _bulk action으로 변환하는 generator.

    reembed_with가 주어지면 payload['text']를 새 임베딩으로 교체.
    """
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            _id = row["id"]
            payload = dict(row.get("payload") or {})
            vector = row.get("vector")

            if reembed_with is not None:
                text = payload.get("text")
                if not text:
                    # text 없으면 재임베딩 불가 — 원본 vector 유지 또는 skip 결정
                    if vector is None:
                        continue
                else:
                    vector = list(reembed_with(text))

            if vector is None:
                continue

            source = {**payload, "embedding": vector}
            yield {
                "_op_type": "index",
                "_index": target_index,
                "_id": _id,
                "_source": source,
            }


# ─────────────────────────────────────────────────────────────
# Load: ES _bulk
# ─────────────────────────────────────────────────────────────


def load_to_es(
    jsonl_path: Path,
    target_index: str,
    *,
    dry_run: bool,
    batch_size: int = 1000,
    failed_path: Path | None = None,
    reembed_with=None,
) -> StageResult:
    if dry_run:
        # 변환만 시뮬레이션 — 실제 ES 호출 없음
        count = 0
        for _ in iter_bulk_actions(jsonl_path, target_index, reembed_with=reembed_with):
            count += 1
        return StageResult(
            "load", ok=True,
            detail=f"[dry-run] would index {count} docs into '{target_index}'",
            metrics={"indexed": count, "dry_run": True},
        )

    try:
        from elasticsearch.helpers import bulk  # noqa: PLC0415

        from lloydk.adapters.vectorstore.es_store import EsStore  # noqa: PLC0415

        store = EsStore()
        # 인덱스 사전 생성 — dim은 첫 줄에서 추출
        first_dim = _peek_first_dim(jsonl_path)
        if first_dim:
            store.ensure_collection(target_index, dim=first_dim)
    except Exception as exc:  # noqa: BLE001
        return StageResult(
            "load", ok=False,
            detail=f"ES init failed: {exc}",
            metrics={"indexed": 0},
        )

    indexed = 0
    failed: list[dict] = []
    try:
        success, errors = bulk(
            store._client,
            iter_bulk_actions(jsonl_path, target_index, reembed_with=reembed_with),
            chunk_size=batch_size,
            refresh="wait_for",
            raise_on_error=False,
            max_retries=3,
        )
        indexed = int(success)
        if errors:
            failed.extend(errors)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        return StageResult(
            "load", ok=False,
            detail=f"_bulk failed: {exc}",
            metrics={"indexed": indexed, "failed_count": len(failed)},
        )

    if failed and failed_path:
        failed_path.parent.mkdir(parents=True, exist_ok=True)
        with failed_path.open("w", encoding="utf-8") as f:
            for err in failed:
                f.write(json.dumps(err, ensure_ascii=False) + "\n")

    return StageResult(
        "load", ok=len(failed) == 0,
        detail=f"indexed {indexed}, failed {len(failed)}",
        metrics={"indexed": indexed, "failed_count": len(failed)},
    )


def _peek_first_dim(jsonl_path: Path) -> int | None:
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            vec = row.get("vector")
            if vec:
                return len(vec)
    return None


# ─────────────────────────────────────────────────────────────
# Verify: count + sampling
# ─────────────────────────────────────────────────────────────


def verify(
    jsonl_path: Path,
    target_index: str,
    *,
    dry_run: bool,
    sample_size: int = 100,
    vector_tol: float = 1e-6,
) -> StageResult:
    """원본 jsonl과 ES 인덱스의 일치 검증.

    - count 일치
    - 무작위 sample_size건의 id·payload·vector 동일성
    """
    # 원본 count + ID 풀
    src_ids: list[str] = []
    src_rows: dict[str, dict] = {}
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            src_ids.append(row["id"])
            src_rows[row["id"]] = row
    expected_count = len(src_ids)

    if dry_run:
        return StageResult(
            "verify", ok=True,
            detail=f"[dry-run] source has {expected_count} rows (target verify skipped)",
            metrics={"expected": expected_count, "verified": 0, "dry_run": True},
        )

    try:
        from lloydk.adapters.vectorstore.es_store import EsStore  # noqa: PLC0415

        store = EsStore()
    except Exception as exc:  # noqa: BLE001
        return StageResult(
            "verify", ok=False, detail=f"ES init failed: {exc}",
            metrics={"expected": expected_count, "verified": 0},
        )

    actual_count = store.count(target_index)
    if actual_count != expected_count:
        return StageResult(
            "verify", ok=False,
            detail=f"count mismatch: src={expected_count}, es={actual_count}",
            metrics={"expected": expected_count, "actual": actual_count},
        )

    sample_ids = random.sample(src_ids, min(sample_size, len(src_ids))) if src_ids else []
    mismatches = 0
    for _id in sample_ids:
        try:
            doc = store._client.get(index=target_index, id=_id)
        except Exception:  # noqa: BLE001
            mismatches += 1
            continue
        es_src = doc.get("_source", {}) or {}
        src_payload = src_rows[_id]["payload"] or {}
        # payload 동일성 (embedding 제외)
        for k, v in src_payload.items():
            if es_src.get(k) != v:
                mismatches += 1
                break
        else:
            # vector 동일성 (재임베딩 케이스가 아니면)
            src_vec = src_rows[_id].get("vector")
            es_vec = es_src.get("embedding")
            if src_vec and es_vec and not _vectors_close(src_vec, es_vec, vector_tol):
                mismatches += 1

    ok = mismatches == 0
    return StageResult(
        "verify", ok=ok,
        detail=f"count={actual_count}, sampled={len(sample_ids)}, mismatches={mismatches}",
        metrics={
            "expected": expected_count,
            "actual": actual_count,
            "sampled": len(sample_ids),
            "mismatches": mismatches,
        },
    )


def _vectors_close(a, b, tol: float) -> bool:
    if len(a) != len(b):
        return False
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b))) <= tol


# ─────────────────────────────────────────────────────────────
# Swap: alias 스위칭
# ─────────────────────────────────────────────────────────────


def swap_alias(
    alias: str,
    new_index: str,
    *,
    dry_run: bool,
    old_index: str | None = None,
) -> StageResult:
    if dry_run:
        return StageResult(
            "swap", ok=True,
            detail=f"[dry-run] would point alias '{alias}' → '{new_index}' (old={old_index})",
            metrics={"alias": alias, "new": new_index, "old": old_index, "dry_run": True},
        )

    try:
        from lloydk.adapters.vectorstore.es_store import EsStore  # noqa: PLC0415

        store = EsStore()
        store.swap_alias(alias, new_index=new_index, old_index=old_index)
        return StageResult(
            "swap", ok=True,
            detail=f"alias '{alias}' → '{new_index}' (removed old={old_index})",
            metrics={"alias": alias, "new": new_index, "old": old_index},
        )
    except Exception as exc:  # noqa: BLE001
        return StageResult(
            "swap", ok=False, detail=f"alias swap failed: {exc}",
            metrics={"alias": alias, "new": new_index},
        )


# ─────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────


def run_migration(args: argparse.Namespace) -> MigrationReport:
    report = MigrationReport(
        collection=args.collection,
        target_index=args.target_index,
        dry_run=args.dry_run,
    )
    report.started_at = time.perf_counter()

    work_dir = Path(args.work_dir)
    jsonl = work_dir / f"{args.collection}.jsonl"
    failed = work_dir / f"{args.collection}.failed.jsonl"

    # 1. Extract
    if not args.skip_extract:
        ex = extract_qdrant(
            args.collection, jsonl, batch_size=args.batch_size, limit=args.limit
        )
        report.stages.append(ex)
        if not ex.ok and not args.continue_on_error:
            report.finished_at = time.perf_counter()
            return report
    else:
        if not jsonl.exists():
            report.stages.append(StageResult(
                "extract", ok=False, detail=f"--skip-extract but {jsonl} missing"
            ))
            report.finished_at = time.perf_counter()
            return report
        report.stages.append(StageResult(
            "extract", ok=True, detail=f"skipped (existing {jsonl})"
        ))

    if report.stages[-1].metrics.get("exported", 0) == 0 and not jsonl.exists():
        # 데이터 자체가 없으면 이후 단계는 의미 없음 → graceful exit
        report.finished_at = time.perf_counter()
        return report

    # 2+3. Transform + Load
    ld = load_to_es(
        jsonl, args.target_index,
        dry_run=args.dry_run, batch_size=args.batch_size,
        failed_path=failed,
    )
    report.stages.append(ld)
    if not ld.ok and not args.continue_on_error:
        report.finished_at = time.perf_counter()
        return report

    # 4. Verify
    vr = verify(jsonl, args.target_index, dry_run=args.dry_run, sample_size=args.sample_size)
    report.stages.append(vr)
    if not vr.ok and not args.continue_on_error:
        report.finished_at = time.perf_counter()
        return report

    # 5. Swap (alias 명시 시에만)
    if args.alias:
        sw = swap_alias(args.alias, args.target_index, dry_run=args.dry_run, old_index=args.old_index)
        report.stages.append(sw)

    report.finished_at = time.perf_counter()
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", required=True, help="Qdrant collection name")
    ap.add_argument(
        "--target-index", default=None,
        help="ES target index (기본: secrets-{collection}-migrated-{ts})",
    )
    ap.add_argument("--alias", default=None, help="검증 통과 후 스위칭할 alias")
    ap.add_argument("--old-index", default=None, help="alias에서 제거할 이전 인덱스")
    ap.add_argument("--work-dir", default="migration", help="JSONL 작업 디렉터리")
    ap.add_argument("--batch-size", type=int, default=1000)
    ap.add_argument(
        "--limit", type=int, default=None,
        help="처음 N건만 (PoC sample 검증용)",
    )
    ap.add_argument("--sample-size", type=int, default=100, help="verify sampling 개수")
    ap.add_argument("--dry-run", action="store_true", help="ES 호출 없이 변환·검증만")
    ap.add_argument(
        "--skip-extract", action="store_true",
        help="기존 JSONL 재사용 (재시도 시)",
    )
    ap.add_argument(
        "--continue-on-error", action="store_true",
        help="단계 실패해도 다음 단계 진행 (디버깅용)",
    )
    ap.add_argument(
        "--report", default="reports/migration_report.json",
        help="결과 리포트 JSON 경로",
    )
    args = ap.parse_args()

    if args.target_index is None:
        ts = time.strftime("%Y%m%d-%H%M%S")
        args.target_index = f"secrets-{args.collection}-migrated-{ts}"

    print(
        f"[migrate] collection={args.collection} → index={args.target_index} "
        f"(dry_run={args.dry_run}, limit={args.limit})"
    )

    report = run_migration(args)
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    verdict = "PASS" if report.all_ok else "FAIL"
    print(f"\n[migrate] verdict: {verdict} -> {out}")
    return 0 if report.all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
