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

from lloydk.golden_builder import promote_candidates  # noqa: E402

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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="골든 빌더 후보를 정본 gold 로 승격(게이트 통과분만).")
    p.add_argument("candidates", help="빌더 후보 jsonl (build_<id>.jsonl)")
    p.add_argument("--gold", default=str(DEFAULT_GOLD), help="정본 gold jsonl 경로")
    p.add_argument("--holdout", default=None, help="누출 차단용 holdout jsonl (선택)")
    p.add_argument("--out", default=None, help="병합 결과 출력(기본: --gold 덮어쓰기)")
    p.add_argument("--no-backup", action="store_true", help="정본 덮어쓰기 전 .bak 백업 생략")
    p.add_argument("--dry-run", action="store_true", help="파일 미작성 — 통계만 출력")
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
