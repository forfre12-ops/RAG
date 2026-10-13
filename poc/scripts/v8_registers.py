"""저술 계보 — 같은 사실을 세 가지 문체로 쓴다.

왜 필요한가. 지금 v8 은 생성기가 하나라 문서 전체가 한 사람이 쓴 것처럼 보인다. 그러면
모델은 "이 문체에서 이런 문장은 이 수준" 을 배우고, 회원사 문서(문체가 제각각)에서 무너진다.
형태(축 1)와 표현 어휘(축 3)를 갈라 놨어도 **문장 구조와 어투가 한 종류면 그것이 다음
tell 이 된다.** 장르 -> 길이 -> 형태 순으로 이미 세 번 당한 패턴이다.

세 계보는 **사실을 바꾸지 않는다.** 같은 근거를 다르게 적을 뿐이라 정답 요소값이 보존된다.
그래서 계보를 가로질러 성적이 유지되는지가 곧 "사실을 읽는가"의 검정이 된다.

    prose   서술형 평서문      "공개 자료만으로는 같은 조건 조합을 다시 만들 수 없다."
    terse   개조식 명사형      "○ 공개 자료만으로는 같은 조건 조합을 다시 만들 수 없음"
    field   항목:값 기재형     "재현 가능성 : 공개 자료만으로는 같은 조건 조합을 다시 만들 수 없음"

⚠ 변환은 어미만 건드린다. 규칙에 걸리지 않으면 원문을 그대로 둔다 — 억지로 바꾸면 비문이
   되고, 비문 자체가 계보 tell 이 된다.
"""
from __future__ import annotations

import re

LINEAGES = ("prose", "terse", "field")

# 불규칙만 표로 둔다. 나머지는 종성 규칙으로 일반화한다(표를 늘리면 계속 새는 것을 확인).
_IRREGULAR: list[tuple[str, str]] = [
    ("어렵다", "어려움"), ("쉽다", "쉬움"), ("무겁다", "무거움"), ("가볍다", "가벼움"),
    ("만들다", "만듦"), ("들다", "듦"), ("팔다", "팖"), ("알다", "앎"), ("살다", "삶"),
    ("멀다", "멂"), ("길다", "긺"), ("낫다", "나음"), ("짓다", "지음"),
    ("아니다", "아님"), ("다르다", "다름"), ("크다", "큼"), ("쓰다", "씀"),
]

# 현재형 '-ㄴ다/는다' 는 어간을 복원해야 한다. '한다'->'하'+ㅁ, '먹는다'->'먹'+음
_PRESENT = [("는다", ""), ("ㄴ다", "")]

_HANGUL_BASE = 0xAC00
_JONG_COUNT = 28
_JONG_M = 16  # 종성 ㅁ


def _has_batchim(ch: str) -> bool:
    code = ord(ch) - _HANGUL_BASE
    return 0 <= code < 11172 and code % _JONG_COUNT != 0


def _add_m(ch: str) -> str:
    """받침 없는 음절에 ㅁ 을 합성한다. '하' -> '함'"""
    return chr(ord(ch) + _JONG_M)


def _strip_present(stem: str) -> str:
    """'-ㄴ다/는다' 의 어간 복원. '한' -> '하', '먹는' -> '먹'"""
    if stem.endswith("는"):
        return stem[:-1]
    last = stem[-1]
    code = ord(last) - _HANGUL_BASE
    if 0 <= code < 11172 and code % _JONG_COUNT == 4:  # 종성 ㄴ
        return stem[:-1] + chr(ord(last) - 4)
    return stem

# field 계보의 항목명 — 요소별로 다르게 붙인다. 등급을 말하지 않는 중립 명칭만 쓴다.
_FIELD_KEY = {
    "secrecy": ["공개 여부", "자료 성격", "배포 이력", "외부 노출"],
    "value": ["업무상 의미", "활용 효과", "대체 가능성", "경쟁 관계"],
    "management": ["보관 상태", "접근 통제", "취급 이력", "관리 방식"],
    None: ["기재 사항", "확인 내용", "비고"],
}


def nominalize(sentence: str) -> str:
    """평서문 -> 개조식 명사형.

    국어의 실제 규칙을 따른다 — 어간 끝에 받침이 있으면 '음', 없으면 'ㅁ' 을 합성한다.
    표로 종결어미를 나열하는 방식은 계속 샜다(149종 중 66종 실패). 불규칙만 표로 둔다.
    """
    s = sentence.strip().rstrip(".")
    for tail, repl in _IRREGULAR:
        if s.endswith(tail):
            return s[: -len(tail)] + repl
    if not s.endswith("다"):
        return s
    stem = _strip_present(s[:-1])
    if not stem:
        return s
    last = stem[-1]
    if not ("가" <= last <= "힣"):
        return s
    return stem + "음" if _has_batchim(last) else stem[:-1] + _add_m(last)


def render(sentence: str, lineage: str, *, factor: str | None = None, seq: int = 0) -> str:
    """한 문장을 계보 문체로 적는다. 사실은 그대로다."""
    if lineage == "prose":
        return sentence
    if lineage == "terse":
        return f"○ {nominalize(sentence)}"
    if lineage == "field":
        keys = _FIELD_KEY.get(factor, _FIELD_KEY[None])
        return f"{keys[seq % len(keys)]} : {nominalize(sentence)}"
    raise ValueError(f"알 수 없는 계보: {lineage}")


def audit(samples: list[str]) -> list[str]:
    """변환 결과가 비문이 되지 않았는지 훑는다 — 비문 자체가 계보 tell 이 된다."""
    problems: list[str] = []
    for s in samples:
        n = nominalize(s)
        if n.endswith("다"):
            problems.append(f"명사형 변환 실패(‘다’로 끝남): {s}")
        if re.search(r"[가-힣]다\s*$", n):
            problems.append(f"종결어미 잔존: {n}")
        if len(n) < 4:
            problems.append(f"과도 절단: {s} -> {n}")
    return problems


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.stdout.reconfigure(encoding="utf-8")
    from v8_factor_sentences import POOLS

    every: list[str] = []
    for pool in POOLS.values():
        for key in ("proven_absent", 1, 2):
            every.extend(pool[key])
        every.extend(s for s, _ in pool["near_miss"])

    probs = audit(every)
    print(f"문장 {len(every)}종 변환 검사 — {'문제 없음' if not probs else str(len(probs)) + '건'}")
    for p in probs[:12]:
        print(f"  - {p}")

    print("\n예시:")
    for s in (POOLS["secrecy"]["proven_absent"][0], POOLS["value"][2][0], POOLS["management"][1][0]):
        for lg in LINEAGES:
            print(f"  [{lg:5s}] {render(s, lg, factor='secrecy')}")
        print()
