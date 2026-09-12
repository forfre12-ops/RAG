"""[G4] 골든 빌더 후보(build_<id>.jsonl)를 정본 gold 로 승격(promote) — 운영자 결정점.

골든 빌더(/golden/build)는 정본(classification_gold.jsonl)을 직접 바꾸지 않고 run-스코프
후보 파일(build_<id>.jsonl)만 만든다(정본 보호). 본 CLI 가 그 후보를 게이트(중복·누출·
비-gold 제외) 통과분만 정본에 병합하는 **명시적 호출부**다 — golden_builder.promote_candidates
가 라이브러리 함수로만 존재해 운영자가 쓸 진입점이 없던 갭(do_now rank12) 해소.

주의: 빌더 후보는 label_source=consensus(룰·LLM 합의)다. ★★★★★ human_review 정본 루프
(import_review_corrections, 지재원 관리자 사인오프)와는 별개 경로다 — 본 CLI 는 consensus
후보를 평가 보조 gold 로 병합할 뿐, human_review 권위를 만들지 않는다.

사용:
  python scripts/promote_golden_candidates.py datasets/gold_real/builds/build_<id>.jsonl --dry-run
  python scripts/promote_golden_candidates.py build_<id>.jsonl --holdout datasets/gold_real/holdout_eval.clean.jsonl
  python scripts/promote_golden_candidates.py build_<id>.jsonl --gold datasets/gold_real/classification_gold.jsonl
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from koipa.golden_builder import promote_candidates  # noqa: E402

DEFAULT_GOLD = Path("datasets/gold_real/classification_gold.jsonl")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _save_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _holdout_texts(path: Path | None) -> list[str]:
    if not path:
        return []
    return [r.get("text", "") for r in _load_jsonl(path) if r.get("text")]


def _leakage_verdict(rows: list[dict], *, max_baseline=None, max_theil_u=None) -> dict | None:
    """도메인->등급 shortcut 누출 판정(순수·실패-안전) — 승격 후보 가시화용.

    build_synthetic_golden 인라인 게이트와 **동일 출처**(analyze_golden_run.domain_leakage +
    check_domain_leakage_gate.domain_leakage_verdict)를 재사용. text 중복 dedup(promote_candidates
    skipped_leaked)과는 별개 축이다. 계산 실패는 승격을 막지 않고 None 반환(경고만)."""
    try:
        from analyze_golden_run import domain_leakage  # noqa: PLC0415
        from check_domain_leakage_gate import (  # noqa: PLC0415
            DEFAULT_MAX_BASELINE,
            DEFAULT_MAX_THEIL_U,
            domain_leakage_verdict,
        )
        mb = DEFAULT_MAX_BASELINE if max_baseline is None else max_baseline
        mt = DEFAULT_MAX_THEIL_U if max_theil_u is None else max_theil_u
        return domain_leakage_verdict(domain_leakage(list(rows)), max_baseline=mb, max_theil_u=mt)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 누출 게이트 계산 실패 — 스킵(승격 계속): {e}", file=sys.stderr)
        return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="골든 빌더 후보를 정본 gold 로 승격(게이트 통과분만).")
    p.add_argument("candidates", help="빌더 후보 jsonl (build_<id>.jsonl)")
    p.add_argument("--gold", default=str(DEFAULT_GOLD), help="정본 gold jsonl 경로")
    p.add_argument("--holdout", default=None, help="누출 차단용 holdout jsonl (선택)")
    p.add_argument("--out", default=None, help="병합 결과 출력(기본: --gold 덮어쓰기)")
    p.add_argument("--no-backup", action="store_true", help="정본 덮어쓰기 전 .bak 백업 생략")
    p.add_argument("--dry-run", action="store_true", help="파일 미작성 — 통계만 출력")
    # 도메인->등급 shortcut 누출: 기본=경고만(플래그+로그). 위반 시 승격 거부는 플래그 뒤에.
    p.add_argument("--enforce-leakage-gate", action="store_true",
                   help="도메인->등급 누출 게이트 위반 시 승격 거부(exit 2). 기본=경고만")
    p.add_argument("--max-baseline", type=float, default=None,
                   help="도메인-다수결 정확도 상한(기본=게이트 기본값 0.55)")
    p.add_argument("--max-theil-u", type=float, default=None,
                   help="도메인->등급 Theil's U 상한(기본=게이트 기본값 0.35)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    cand_path = Path(args.candidates)
    if not cand_path.exists():
        print(f"[ERROR] 후보 파일 없음: {cand_path}", file=sys.stderr)
        return 1

    gold_path = Path(args.gold)
    out_path = Path(args.out) if args.out else gold_path

    candidates = _load_jsonl(cand_path)
    existing = _load_jsonl(gold_path)
    holdout = _holdout_texts(Path(args.holdout) if args.holdout else None)

    merged, result = promote_candidates(candidates, existing, holdout_texts=holdout)

    print(
        f"[INFO] 후보 {result.total_candidates}건 → 추가 {result.added}, "
        f"중복 {result.skipped_duplicate}, 누출 {result.skipped_leaked}, "
        f"비-gold {result.skipped_not_gold} | 병합 후 gold {result.gold_total}건"
    )

    # 도메인->등급 shortcut 누출 가시화(플래그+로그). 기본은 경고만 — 승격 흐름 무변경.
    leak = _leakage_verdict(candidates, max_baseline=args.max_baseline, max_theil_u=args.max_theil_u)
    if leak is not None:
        lk = leak.get("leakage", {})
        print(
            f"[누출] 도메인->등급 baseline={lk.get('trivial_domain_baseline')} "
            f"theil_u={lk.get('theil_u')} n={lk.get('n')} blocked={leak.get('blocked')} "
            f"({leak.get('reason')})"
        )
        if leak.get("blocked"):
            print(
                "[누출경고] 도메인->등급 shortcut 의심 — 도메인이 등급을 과도하게 결정. "
                "학습 편입 전 검수 권장.",
                file=sys.stderr,
            )
            if args.enforce_leakage_gate:
                print(
                    "[BLOCK] --enforce-leakage-gate: 누출 게이트 위반으로 승격 거부(정본 미변경).",
                    file=sys.stderr,
                )
                return 2

    if args.dry_run:
        print("[DRY-RUN] 파일 미작성")
        return 0

    if result.added == 0:
        print("[INFO] 추가 0건 — 정본 미변경")
        return 0

    # 정본 덮어쓰기 전 백업(in-place 일 때만 의미).
    if not args.no_backup and out_path == gold_path and gold_path.exists():
        bak = gold_path.with_suffix(gold_path.suffix + f".bak_{datetime.now():%Y%m%d_%H%M%S}")
        shutil.copy2(gold_path, bak)
        print(f"[OK] 백업 → {bak}")

    _save_jsonl(out_path, merged)
    print(f"[OK] 승격 완료 → {out_path} (총 {len(merged)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
