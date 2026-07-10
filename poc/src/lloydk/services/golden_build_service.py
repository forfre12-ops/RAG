"""Golden build service — 통합 골든셋 빌더 비동기 잡 (G3b).

submit: 브로커 가용 시 Celery golden_build_task, 아니면 in-proc 동기 실행(폴백).
정본(classification_gold.jsonl)은 직접 변경하지 않고 run-스코프 후보 파일(build_<id>.jsonl,
uncertain_<id>.jsonl)에 쓴다. human_review 승격은 별개 경로(import_review_corrections,
지재원 관리자) — 두 검수 루프 분리 유지.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Callable, Optional

from lloydk.golden_builder import GoldenBuildResult, build_golden_set, make_label_fn
from lloydk.golden_review_html import (
    render_review_html_from_jsonl,
    render_signoff_html_from_jsonl,
)
from lloydk.golden_signoff import Signoff, merge_locked_records, promote_to_locked
from lloydk.golden_tiers import eval_readiness
from lloydk.schemas.golden import (
    GoldenBuildRequest,
    GoldenBuildResponse,
    GoldenBuildStatus,
    GoldenSignoffDecision,
)
from lloydk.services.async_classify_service import _celery_dispatch_available
from lloydk.services.job_store import get_default_store

logger = logging.getLogger(__name__)

LabelFn = Callable[[str], "object"]

# ── 경로 샌드박스 (path traversal 차단) ──────────────────────────────────────
# corpus_dir/holdout_path/out_dir 은 요청 바디로 들어오는 파일시스템 경로다. 무검증이면
# "/etc/passwd" 읽기·"../../" 임의 위치 쓰기가 가능하다(인증된 admin/kl_backend 경로라도
# 최소권한 위반). 허용 루트(리포 datasets/ · 시스템 temp — pytest tmp_path 포함) 하위로만
# 제한한다. golden_build_service.py = poc/src/lloydk/services/... → parents[3] = poc 루트.
_POC_ROOT = Path(__file__).resolve().parents[3]
_ALLOWED_PATH_ROOTS = (
    (_POC_ROOT / "datasets").resolve(),
    Path(tempfile.gettempdir()).resolve(),
)


def _safe_path(raw: str | None) -> Path:
    """요청 경로를 허용 루트 하위로 제한. 벗어나면 ValueError(작업 자체를 거부)."""
    if not raw:
        raise ValueError("경로 미지정")
    p = Path(raw)
    resolved = (p if p.is_absolute() else _POC_ROOT / p).resolve()
    if not any(resolved.is_relative_to(base) for base in _ALLOWED_PATH_ROOTS):
        raise ValueError(
            f"허용 루트(datasets/ · temp) 밖 경로 거부: {raw}"
        )
    return resolved


class GoldenBuildService:
    def __init__(self):
        self.jobs = get_default_store()

    def submit(
        self,
        req: GoldenBuildRequest,
        *,
        label_fn: Optional[LabelFn] = None,
    ) -> GoldenBuildResponse:
        """빌드 잡 등록. label_fn 주입(테스트) 또는 브로커 미가용 시 in-proc 동기 실행."""
        job_id = uuid.uuid4()
        self.jobs.create(
            job_id,
            payload={
                # tenant 제거: 격리는 KL 포털 전담 — 골든 빌드 잡은 전역 네임스페이스.
                "kind": "golden_build",
                "source_type": req.source_type,
                "actor": req.actor.user_id,
                "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        )
        # label_fn 주입 시엔 직렬화 불가하므로 항상 in-proc. 아니면 브로커 가용 시 Celery.
        if _celery_dispatch_available() and label_fn is None:
            try:
                from lloydk.workers.tasks import golden_build_task  # noqa: PLC0415

                golden_build_task.delay(req.model_dump(mode="json"), job_id=str(job_id))
                logger.info("golden_build enqueued to celery: job_id=%s", job_id)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "golden_build celery enqueue 실패 — in-proc 실행: job_id=%s",
                    job_id, exc_info=True,
                )
                self.run_build(req, job_id, label_fn=label_fn)
        else:
            self.run_build(req, job_id, label_fn=label_fn)

        return GoldenBuildResponse(golden_job_id=job_id, status_url=f"/golden/jobs/{job_id}")

    def run_build(
        self,
        req: GoldenBuildRequest,
        job_id: uuid.UUID,
        *,
        label_fn: Optional[LabelFn] = None,
    ) -> dict:
        """실제 빌드: 문서 로드 → build_golden_set → run-스코프 파일 출력 → JobStore 갱신."""
        self.jobs.update(job_id, status="running")
        try:
            docs = self._load_docs(req)
            holdout = self._load_holdout(req)
            lf = label_fn or make_label_fn(req.llm_provider, sensitive=req.sensitive)
            result = build_golden_set(
                docs,
                label_fn=lf,
                holdout_texts=holdout,
                min_self_consistency=req.min_self_consistency,
                require_evidence=req.require_evidence,
            )
            gold_path, unc_path = self._write_outputs(req, job_id, result)
            self.jobs.update(
                job_id,
                status="done",
                stats=result.stats,
                gold_count=len(result.gold),
                uncertain_count=len(result.uncertain),
                gold_path=str(gold_path),
                uncertain_path=str(unc_path),
            )
            logger.info(
                "golden_build done: job_id=%s gold=%d uncertain=%d dup=%d leaked=%d",
                job_id, len(result.gold), len(result.uncertain),
                result.dropped_duplicate, result.dropped_leaked,
            )
            return result.stats
        except Exception as exc:  # noqa: BLE001
            self.jobs.update(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            logger.warning("golden_build 실패: job_id=%s err=%s", job_id, exc, exc_info=True)
            raise

    def get_status(self, job_id: uuid.UUID) -> Optional[GoldenBuildStatus]:
        job = self.jobs.get(job_id)
        if job is None:
            return None
        return GoldenBuildStatus(
            status=job.get("status", "queued"),
            stats=job.get("stats"),
            gold_count=job.get("gold_count"),
            uncertain_count=job.get("uncertain_count"),
            gold_path=job.get("gold_path"),
            uncertain_path=job.get("uncertain_path"),
            error=job.get("error"),
        )

    def render_review(self, job_id: uuid.UUID, *, title: str = "골든셋 후보 검토본") -> Optional[str]:
        """완료된 빌드 잡의 후보(build·uncertain)를 지재원 관리자 검수용 HTML로 렌더.

        JobStore에서 잡의 출력 경로를 찾아 render_review_html_from_jsonl로 렌더.
        잡이 없거나 출력 경로가 없으면 None. (지재원 관리자가 URL로 바로 검수)
        """
        job = self.jobs.get(job_id)
        if job is None:
            return None
        paths = [p for p in (job.get("gold_path"), job.get("uncertain_path")) if p]
        if not paths:
            return None
        return render_review_html_from_jsonl(paths, title=title, subtitle=f"job {job_id}")

    def render_signoff(
        self, job_id: uuid.UUID, *, title: str = "골든셋 검수 · 서명"
    ) -> Optional[str]:
        """빌드 잡의 gold 후보(gold_candidate)를 화면 서명용 인터랙티브 HTML로 렌더.

        render_review(보기 전용)와 달리 각 후보에 승인/등급변경/거부 폼을 붙이고, 제출 시
        POST /golden/jobs/{id}/signoff 로 결정을 보낸다. gold 후보만 대상(uncertain 은
        아직 승격 후보가 아님). 잡/경로 없으면 None.
        """
        job = self.jobs.get(job_id)
        if job is None:
            return None
        gold_path = job.get("gold_path")
        if not gold_path:
            return None
        return render_signoff_html_from_jsonl(
            [gold_path],
            job_id=str(job_id),
            post_url=f"/api/v1/golden/jobs/{job_id}/signoff",
            title=title,
        )

    def apply_signoff(
        self,
        job_id: uuid.UUID,
        decisions: "list[GoldenSignoffDecision]",
        *,
        reviewer_id: str,
        publish: bool = False,
        dual_for_upper: bool = False,
    ) -> Optional[dict]:
        """검수 결정(승인/변경/거부)을 골든 후보에 적용해 locked_gold_eval 로 승격.

        골든셋 검수의 서명 캡처 단계 — golden_signoff.promote_to_locked 계약 그대로.
        후보는 잡의 gold_path(build_<id>.jsonl)에서 로드한다. 정본(classification_gold.jsonl)은
        건드리지 않는다. run-스코프 locked_<id>.jsonl 은 항상 기록(감사), 라이브 readiness
        읽기경로(settings.locked_eval_jsonl)는 publish=True 일 때만 dedup 병합(배포 게이트 소비).

        reviewer_id 는 호출부(엔드포인트)가 인증 신원으로 확정해 넘긴다(위조 차단). 머신
        reviewer 는 promote_to_locked 내부 is_human_reviewer 가 거부(전건 machine_reviewer).

        반환: {locked, rejected, locked_by_grade, rejected_reasons, readiness, published,
        run_locked_path}. 잡/경로 없으면 None.
        """
        job = self.jobs.get(job_id)
        if job is None:
            return None
        gold_path = job.get("gold_path")
        if not gold_path or not Path(gold_path).exists():
            return None

        candidates = _read_jsonl(Path(gold_path))
        by_doc = {c.get("doc_id"): c for c in candidates}

        signed_at = dt.datetime.now(dt.timezone.utc).isoformat()
        signoffs: list[Signoff] = []
        decided_ids: set = set()
        for d in decisions:
            cand = by_doc.get(d.doc_id)
            if cand is None:
                continue  # 후보에 없는 doc_id 는 서명 불가(팬텀 방지) — 무시
            decided_ids.add(d.doc_id)
            if d.decision == "reject":
                continue  # 거부 — 서명 없음(promote 에서 no_signoff 로 rejected 집계)
            if d.decision == "change":
                if not d.grade:
                    continue  # 변경인데 등급 미지정 — 방어적 skip(스키마가 1차 방지)
                grade = d.grade
            else:  # approve — 제안등급(후보 label) 유지
                grade = cand.get("label")
            signoffs.append(
                Signoff(
                    doc_id=d.doc_id, reviewer_id=reviewer_id, grade=grade,
                    signed_at=signed_at, note=d.note,
                )
            )

        # 결정된 후보만 승격 대상으로(미결정 후보가 rejected 로 잡히는 노이즈 차단).
        subset = [c for c in candidates if c.get("doc_id") in decided_ids]
        res = promote_to_locked(subset, signoffs, dual_for_upper=dual_for_upper)

        # run-스코프 감사 기록(정본·라이브 무변경) — 항상. 같은 잡을 여러 세션에 나눠 서명하는 게
        # 정상 흐름(대량 후보)이므로 덮어쓰기 대신 기존 승격분과 doc_id dedup 누적(라이브 경로와 동일
        # 규율) — 세션1 서명이 세션2 제출로 유실되지 않게.
        run_locked_path = Path(gold_path).parent / f"locked_{job_id}.jsonl"
        _atomic_write_jsonl(
            run_locked_path, merge_locked_records(_read_jsonl(run_locked_path), res.locked)
        )

        # 라이브 readiness 읽기경로 — publish 병합 대상 + readiness union 계산.
        from lloydk.config import settings  # noqa: PLC0415

        live_path = getattr(settings, "locked_eval_jsonl", "") or ""
        existing_live = (
            _read_jsonl(Path(live_path))
            if live_path and Path(live_path).exists()
            else []
        )
        merged = merge_locked_records(existing_live, res.locked)

        published = False
        if publish and res.locked and live_path:
            _atomic_write_jsonl(Path(live_path), merged)
            published = True

        return {
            "locked": len(res.locked),
            "rejected": len(res.rejected),
            "locked_by_grade": res.stats.get("locked_by_grade", {}),
            "rejected_reasons": res.stats.get("rejected_reasons", {}),
            # published=False 면 '라이브+이번 승격' 미리보기(라이브는 실제 무변경).
            "readiness": eval_readiness(merged),
            "published": published,
            "run_locked_path": str(run_locked_path),
        }

    # ------------------------------------------------------------------ helpers
    def _load_docs(self, req: GoldenBuildRequest) -> list[dict]:
        if req.source_type == "inline":
            return list(req.docs)
        # corpus: jsonl 파일 또는 *.json 디렉토리 (허용 루트 하위로 제한)
        p = _safe_path(req.corpus_dir)
        if not p.exists():
            raise FileNotFoundError(f"corpus_dir 없음: {p}")
        rows: list[dict] = []
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        else:
            for f in sorted(p.glob("*.json")):
                rows.append(json.loads(f.read_text(encoding="utf-8")))
        out: list[dict] = []
        for d in rows[: req.n]:
            text = d.get("text") or f"{d.get('title', '')}\n\n{d.get('body', '')}".strip()
            out.append({
                "doc_id": d.get("doc_id"),
                "text": text,
                "source": d.get("source", ""),
                "domain": d.get("domain", ""),
            })
        return out

    def _load_holdout(self, req: GoldenBuildRequest) -> Optional[list[str]]:
        if not req.holdout_path:
            return None
        p = _safe_path(req.holdout_path)
        if not p.exists():
            return None
        texts: list[str] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                t = json.loads(line).get("text")
                if t:
                    texts.append(t)
        return texts

    def _write_outputs(
        self, req: GoldenBuildRequest, job_id: uuid.UUID, result: GoldenBuildResult,
    ) -> tuple[Path, Path]:
        out = _safe_path(req.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        gold_path = out / f"build_{job_id}.jsonl"
        unc_path = out / f"uncertain_{job_id}.jsonl"
        _write_jsonl(gold_path, [r.to_dict() for r in result.gold])
        _write_jsonl(unc_path, [r.to_dict() for r in result.uncertain])
        return gold_path, unc_path


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _atomic_write_jsonl(path: Path, rows: list[dict]) -> None:
    """원자적 jsonl 기록(tmp→replace) — 라이브 readiness 읽기경로가 부분기록으로 깨지지 않게.

    배포 게이트/readiness 게이지가 이 파일을 읽으므로 (run_golden_split_signoff._write_jsonl
    과 동일 규율) 원자적 교체로 반쪽 상태 노출을 막는다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)
