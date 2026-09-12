"""비밀관리성(M) 입력 공백을 잰다 — 얼마나 없고, 없으면 무엇이 불가능해지는가.

왜 이 도구가 리포에 있어야 하는가. 이 숫자들은 KL 에 `access_scope` 공급을 요청하는
근거이고, 감리가 "다시 재 보라"고 하면 재현할 수 있어야 한다. 손으로 센 숫자는 다시
물으면 답이 없다(2026-09-08 교훈: 같은 질문에 101/102/52 가 나왔다).

세 가지를 한 번에 낸다.

    ① 공급량   전 데이터셋에서 security_marking·access_scope 를 가진 행이 몇 건인가
    ② 무게     M 값에 따라 등급이 갈리는 행이 몇 %인가 (요소값을 가진 셋으로)
    ③ 대가     M 을 못 받아 보수적으로 채우면 몇 건이 틀리고, 어느 방향으로 틀리는가

②③ 은 **요소 우선 구조를 전제로 한 수치**다. 오늘 배포본은 등급 우선·요소 후행이라
M 은 요소값만 채우고 등급을 바꾸지 않는다. 두 숫자를 갈라서 보고해야 한다 —
"M 을 채우면 등급이 좋아진다"를 오늘 배포본에 대고 말하면 틀린다.

실행:
    python scripts/measure_management_input_gap.py
    --factor-set datasets/labeled_v6_factor_grounded   요소값을 가진 셋(②③ 의 분모)
"""
from __future__ import annotations

try:  # 콘솔 출구 고정 — cp949 에서 em dash 하나에 죽지 않게
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import 될 때(릴리스 번들의 import 폐쇄 검사)
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import glob
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from koipa.modules.m3_labeling.rule_engine import (  # noqa: E402
    grade_from_svm,
    management_from_metadata_dict,
)

# ① 의 분모 — 데이터셋 전역. 손으로 고르면 분모가 틀린다(2026-09-08 교훈).
_SUPPLY_GLOBS = ("datasets/**/*.jsonl", "evidence/*.jsonl")


def _iter_rows(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _fingerprint(paths: list[Path]) -> str:
    """입력 지문 — datasets/ 는 git 밖이라 산출물만 보면 무엇을 쟀는지 알 수 없다."""
    h = hashlib.sha256()
    for p in sorted(paths):
        try:
            h.update(p.name.encode())
            h.update(str(p.stat().st_size).encode())
        except OSError:
            continue
    return h.hexdigest()[:16]


def measure_supply() -> dict:
    """① M 메타데이터 공급량 — 전 데이터셋 전수."""
    paths = sorted({
        Path(p) for g in _SUPPLY_GLOBS for p in glob.glob(str(_ROOT / g), recursive=True)
    })
    rows = marking = scope = known = 0
    for path in paths:
        for row in _iter_rows(path):
            rows += 1
            md = row.get("metadata") if isinstance(row.get("metadata"), dict) else row
            if not isinstance(md, dict):
                continue
            if str(md.get("security_marking") or "").strip():
                marking += 1
            if str(md.get("access_scope") or "").strip():
                scope += 1
            if management_from_metadata_dict(md)[0] != "unknown":
                known += 1
    return {
        "files": len(paths), "rows": rows,
        "security_marking": marking, "access_scope": scope, "m_known": known,
        "coverage": (known / rows) if rows else 0.0,
        "input_fingerprint": _fingerprint(paths),
    }


def measure_weight(factor_dir: Path) -> dict:
    """②③ M 의 무게와 못 받았을 때의 대가 — 요소값을 가진 셋에서."""
    rows = [
        r for f in sorted(factor_dir.glob("*.jsonl")) for r in _iter_rows(f)
        if isinstance(r.get("expected_factor_scores"), dict)
    ]
    if not rows:
        return {"rows": 0}

    label_agrees = decides = 0
    conservative_wrong: Counter = Counter()
    m_dist: Counter = Counter()
    for r in rows:
        fs = r["expected_factor_scores"]
        s, v, m = int(fs["secrecy"]), int(fs["value"]), int(fs["management"])
        m_dist[m] += 1
        true_grade = grade_from_svm(s, v, m)
        if r.get("label") == true_grade:
            label_agrees += 1
        # ② M 을 0/1/2 로 돌렸을 때 등급이 갈리는가 = M 이 결정권을 쥔 행
        if len({grade_from_svm(s, v, mm) for mm in (0, 1, 2)}) > 1:
            decides += 1
        # ③ M 미확인 → 보수적으로 최고값(2)으로 채우면 무엇이 틀리는가
        filled = grade_from_svm(s, v, 2)
        if filled != true_grade:
            conservative_wrong[f"{true_grade}->{filled}"] += 1
    n = len(rows)
    wrong = sum(conservative_wrong.values())
    return {
        "rows": n,
        "formula_matches_label": label_agrees,
        "m_decides_grade": decides,
        "m_decides_rate": decides / n,
        "conservative_fill_wrong": wrong,
        "conservative_fill_wrong_rate": wrong / n,
        "conservative_fill_shifts": dict(conservative_wrong),
        "m_distribution": dict(sorted(m_dist.items())),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--factor-set", default="datasets/labeled_v6_factor_grounded")
    ap.add_argument("--out", default="reports/management_input_gap.json")
    args = ap.parse_args()

    supply = measure_supply()
    weight = measure_weight(_ROOT / args.factor_set)

    print("① M 메타데이터 공급량 — 전 데이터셋 전수")
    print(f"   파일 {supply['files']}개 · 행 {supply['rows']:,}")
    print(f"   보안표시 {supply['security_marking']:,} · 접근범위 {supply['access_scope']:,}"
          f" · M 확인 {supply['m_known']:,}  ({supply['coverage']:.1%})")
    print(f"   입력 지문 {supply['input_fingerprint']}")

    if weight.get("rows"):
        n = weight["rows"]
        print(f"\n② M 의 무게 — {args.factor_set} {n:,}행 (요소→공식 등급이 라벨과 "
              f"{weight['formula_matches_label']:,}/{n:,} 일치)")
        print(f"   M 값에 따라 등급이 갈리는 행  {weight['m_decides_grade']:,} / {n:,}"
              f"  ({weight['m_decides_rate']:.1%})")
        print(f"\n③ M 을 못 받아 보수적으로 2 로 채웠을 때")
        print(f"   등급이 틀리는 행  {weight['conservative_fill_wrong']:,} / {n:,}"
              f"  ({weight['conservative_fill_wrong_rate']:.1%})")
        for shift, cnt in sorted(weight["conservative_fill_shifts"].items(), key=lambda x: -x[1]):
            print(f"      {shift:<12}{cnt:>6}건")
        print(f"   M 값 분포  {weight['m_distribution']}")

    print("\n⚠ ②③ 은 요소 우선 구조를 전제한 수치다. 오늘 배포본은 등급 우선·요소 후행이라")
    print("   M 은 요소값만 채우고 등급을 바꾸지 않는다. 두 숫자를 갈라서 보고할 것.")

    out = _ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"supply": supply, "weight": weight}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n기록: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
