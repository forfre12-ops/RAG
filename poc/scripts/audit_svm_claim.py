# -*- coding: utf-8 -*-
"""문서가 S×V×M 곱셈식을 '서빙 판정자'로 말하는 곳을 전수로 걷는다.

왜(2026-08-29). 실측 결과 곱셈 블록은 서빙에서 등급을 바꾸지 않는다(평가셋 5종 1,028건
발동 0건 · scripts/audit_rule_formula.py). 실제 역할은 화면·API 에 나가는 S·V·M 표시값을
최종 등급에 정합화하는 것이다(rule_engine.py:639-646, additive 대조에서 42건 중 29건의
factor_scores 가 달라짐). 그런데 문서 여러 곳이 이것을 '등급을 산정하는 판정식'이라 쓴다.

한 곳만 고치면 나머지가 남는다. 그래서 세는 도구를 먼저 만든다.

분류:
    [정본]  발주처 가이드·정책 결정의 매핑 자체를 서술 — 맞는 서술, 손대지 않는다
    [판정]  서빙이 곱으로 등급을 정한다는 취지 — 고칠 대상
    [표시]  곱셈이 판정자가 아님을 문장 안에서 이미 밝힌 서술 — 목표 형태

판정 우선순위는 표시 > 정본 > 판정 이다. 바른 틀을 갖춘 문장은 가이드 어휘를 함께
써도 고칠 대상이 아니고, 가이드·정책을 서술하는 문장은 곱셈 어휘를 써도 그대로 둔다.

⚠ 이 도구는 후보를 드러낼 뿐 판정하지 않는다. 최종 분류는 사람이 문장을 열어 정한다.

사용:
    python scripts/audit_svm_claim.py [--dir doc/result/KL_회신_2026-08-28]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DIRS = [
    "doc/result/KL_회신_2026-08-28",
    "doc/감리문서",
    "doc/result/KL_AI자료_2026-08",
]

HIT = re.compile(r"S\s*[×x]\s*V\s*[×x]\s*M|곱셈식|곱셈 매핑|grade_from_svm|곱으로 등급|곱\s*≥")
# 서빙이 곱으로 등급을 정한다는 취지 — 고칠 후보
DECIDE = re.compile(
    r"(곱[^。.]{0,40}(산정|결정|판정|정한다|매긴다))"
    r"|((산정|결정|판정)[^。.]{0,30}S\s*[×x]\s*V\s*[×x]\s*M)"
    r"|(등급\s*=\s*S\s*[×x]\s*V\s*[×x]\s*M)"
    r"|(독립[^。.]{0,20}판독)"
)
# 이미 바른 틀을 갖춘 서술 — 곱셈이 판정자가 아님을 문장 안에서 밝히고 있다
OK = re.compile(
    r"표시값|참고값|표시 정합|정합화|화면에 표시|교차 확인|상향[^。.]{0,10}(만|확인)"
    r"|키워드 룰이 (정|판정)|키워드 등급이 (정|판정)|에서 파생|가중합 argmax"
)
# 발주처 가이드·정책 결정을 서술하는 문장 — 정본 그대로가 맞다, 고칠 대상 아님
CANON = re.compile(
    r"가이드|정본|B\s*안|A\s*안|p1[12]|부정경쟁방지법|산정법|설계 결정|제안요청서|발주처"
    r"|변경 요약|결정 상태|정책 승인"
)

TAG = re.compile(r"<[^>]+>")


def sentences(html: str):
    text = TAG.sub(" ", html)
    text = re.sub(r"&[a-z]+;|&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text)
    for s in re.split(r"(?<=[.。!?])\s+|(?<=다)\s+(?=[가-힣A-Z])", text):
        s = s.strip()
        if s:
            yield s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", dest="dirs")
    args = ap.parse_args(argv)
    targets = args.dirs or DIRS

    totals = {"정본": 0, "판정": 0, "표시": 0}
    for d in targets:
        base = _ROOT / d
        if not base.exists():
            print(f"[없음] {d}")
            continue
        print(f"\n=== {d} ===")
        for path in sorted(base.rglob("*.html")):
            html = path.read_text("utf-8", errors="replace")
            rows = []
            for s in sentences(html):
                if not HIT.search(s):
                    continue
                if OK.search(s):
                    kind = "표시"
                elif CANON.search(s):
                    kind = "정본"
                elif DECIDE.search(s):
                    kind = "판정"
                else:
                    kind = "정본"
                totals[kind] += 1
                rows.append((kind, s[:150]))
            if rows:
                print(f"  {path.relative_to(base)}  ({len(rows)}문장)")
                for kind, s in rows:
                    mark = "  ⚠" if kind == "판정" else "   "
                    print(f"{mark} [{kind}] {s}")
    print(f"\n합계 — 정본 {totals['정본']} · 판정(고칠 대상) {totals['판정']} · 표시 {totals['표시']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
