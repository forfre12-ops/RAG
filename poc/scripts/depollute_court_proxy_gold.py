"""[P1] 공개 판례 프록시 과라벨 디오염 — 결정론적·재현가능·감사가능 정본 정정.

배경(court-proxy pollution): gold_real 안에는 공개 판례(source=판례)를 본문에 '영업비밀'·'특허'
같은 키워드가 있다는 이유로 LLM 프록시(label_source=llm_judge_primary)가 S1/S2로 과라벨한
레코드가 있다. 룰 엔진은 같은 문서에서 실제 보호표식(S/V/M)을 못 찾아 rule_grade=S3(공개)로
판정한다. 공개된 판결문은 그 자체로 영업비밀이 아니므로 정답은 S3다(부정경쟁방지법상 '비공지성'
결여). 이 과라벨은 학습셋(silver)에 흘러들어 판례=고등급 shortcut을 가르치는 오염원이다.

이 스크립트는 그 디오염을 **결정론적 규칙**으로 정본에 적용한다(이전의 미커밋 수기/기계 in-place
변경을 대체 — 재현·감사 가능하게). 선정 술어는 저장된 필드만으로 판정하므로 네트워크·모델 불요.

선정 술어(AND):
  - source 가 '판례'로 시작(공개 판례)
  - label_source == 'llm_judge_primary'       (LLM 프록시 경로만 — 법적근거/큐레이션/사람검수 불가침)
  - rule_grade == 'S3'                          (룰 엔진 실측: 보호표식 없음 = 공개)
  - label ∈ {'S1','S2'}                         (프록시가 과상향한 것만)
정정: label→S3, label_original 보존, corrected 프로비넌스 스탬프.

tier 영향: 대상은 전부 llm_judge_primary = golden_tiers TIER_SILVER(학습연료). eval 정답
(is_real_locked_eval=서명+실문서)에는 애초에 포함되지 않으므로 이 정정은 **학습 분포에만** 작용
하고 평가 정답지는 건드리지 않는다. FNR 지표 이동 없음(평가 순환성 우려 무효).

멱등: 정정 후 label=S3라 술어에 재매칭되지 않는다 → 재실행 무변경.

모드:
  --dry-run : 통계만(파일 무변경).
  --check   : 미정정(pending) 술어 매칭이 하나라도 있으면 exit 1 — CI 가드/재현성 트립와이어.
              (정본은 이 스크립트로 '완전' 디오염된 상태여야 하며, 새 판례 프록시가 유입되면 RED.)
  (기본)    : 정본에 적용(.bak 백업 후 in-place). 승격/평가 정답 아님 — silver 학습분포 정정.

사용:
  python scripts/depollute_court_proxy_gold.py --dry-run
  python scripts/depollute_court_proxy_gold.py --check     # CI 가드
  python scripts/depollute_court_proxy_gold.py             # 적용
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DEFAULT_GOLD = Path("datasets/gold_real/classification_gold.jsonl")

# 결정론적 정정 프로비넌스(고정 상수 — 재실행해도 동일 출력).
CORRECTION_DATE = "2026-07-22"
CORRECTED_BY = "scripts/depollute_court_proxy_gold.py"
CORRECTION_BASIS = (
    "public_ruling_depollution: source=판례 + rule_grade=S3(보호표식 없음) "
    "+ label_source=llm_judge_primary → 공개 판례로 판정, LLM 프록시 고등급 오라벨을 S3로 정정"
)
CORRECTED_NOTE = (
    "룰기반 결정론적 자동정정(사람서명 아님) — silver tier 유지·eval 정답 아님(golden_tiers). "
    "재현/검증: scripts/depollute_court_proxy_gold.py --check"
)
_HIGH_LABELS = frozenset({"S1", "S2"})


def is_court_proxy_overlabel(r: dict) -> bool:
    """공개 판례를 LLM 프록시가 과상향(S1/S2)했고 룰은 공개(S3)로 본 레코드인가 — 결정론적."""
    return (
        str(r.get("source") or "").startswith("판례")
        and r.get("label_source") == "llm_judge_primary"
        and r.get("rule_grade") == "S3"
        and r.get("label") in _HIGH_LABELS
    )


def apply_depollution(row: dict) -> dict:
    """대상 레코드를 S3로 정정하고 프로비넌스를 스탬프(원 필드 보존)."""
    row["label_original"] = row["label"]
    row["label"] = "S3"
    row["corrected"] = True
    row["corrected_by"] = CORRECTED_BY
    row["corrected_date"] = CORRECTION_DATE
    row["correction_basis"] = CORRECTION_BASIS
    row["corrected_note"] = CORRECTED_NOTE
    return row


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _save_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="공개 판례 프록시 과라벨 디오염(결정론적·재현가능).")
    ap.add_argument("--gold", default=str(DEFAULT_GOLD))
    ap.add_argument("--dry-run", action="store_true", help="통계만 — 파일 무변경")
    ap.add_argument("--check", action="store_true", help="미정정 술어 매칭이 있으면 exit 1(CI 가드)")
    args = ap.parse_args()

    gold_path = Path(args.gold)
    rows = _load_jsonl(gold_path)
    if not rows:
        print(f"[ERROR] gold 없음/빈 파일: {gold_path}", file=sys.stderr)
        return 2

    pending = [r for r in rows if is_court_proxy_overlabel(r)]
    by_label = Counter(r["label"] for r in pending)

    if args.check:
        if pending:
            print(f"[check] FAIL — 미정정 court-proxy 과라벨 {len(pending)}건 {dict(by_label)}", file=sys.stderr)
            print("        scripts/depollute_court_proxy_gold.py 로 재생성 필요.", file=sys.stderr)
            return 1
        print("[check] OK — 판례 프록시 과라벨 0건(완전 디오염).")
        return 0

    before = Counter(r["label"] for r in rows)
    after = Counter(before)
    for lbl, n in by_label.items():
        after[lbl] -= n
        after["S3"] += n

    print("=== 공개 판례 프록시 디오염 ===")
    print(f"입력: {len(rows)}건  | 정정 대상(court+llm_primary+rule S3+label S1/S2): {len(pending)}건 {dict(by_label)}")
    print(f"분포 before: {dict(before)}")
    print(f"분포 after : {dict(after)}")

    if args.dry_run:
        print("\n[DRY-RUN] 파일 무변경")
        return 0
    if not pending:
        print("\n[OK] 정정 대상 0건 — 이미 디오염됨(무변경).")
        return 0

    bak = gold_path.with_suffix(gold_path.suffix + ".pre_depollution.bak")
    shutil.copy2(gold_path, bak)
    for r in rows:
        if is_court_proxy_overlabel(r):
            apply_depollution(r)
    _save_jsonl(gold_path, rows)
    print(f"\n[OK] 백업 → {bak}")
    print(f"[OK] 정본 정정 → {gold_path} ({len(pending)}건 S3, corrected 스탬프)")
    print("[INFO] silver 학습분포 정정 — 평가 정답(locked)·승격 아님.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
