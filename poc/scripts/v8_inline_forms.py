"""요소 진술의 **삽입형** — 실문서는 요소를 문장 안에 짧게 끼워 넣는다.

왜(실측 2026-08-14). 경화42 고등급 26건을 100% 과소분류했다. 요소 진술이 없어서가 아니다.
있는데 못 읽는다. 문서를 열어 보면 형태가 다르다:

    우리 학습셋   "관련 특허 공보에도 이 범위는 기재돼 있지 않다."
                  -> 한 문장을 통째로 요소 진술에 쓴다

    경화42        "독자 개발 스코어링 모델 (외부 미공개)"
                  "고객 데이터 분석 결과는 경쟁사 대비 핵심 자산"
                  "유출 시 주가 영향 및 협상 무산 위험"
                  -> 내용 문장 안에 괄호·수식어·짧은 구로 끼워 넣는다

같은 사실인데 진술 방식이 다르다. 모델은 "요소 문장 한 줄" 형태만 봤으므로 짧게 끼워 넣은
형태를 못 읽는다.

이 모듈은 (요소, 상태) 마다 **짧은 삽입형**을 제공한다. 생성기가 절반쯤을 이 형태로 만들면
모델이 두 형태를 모두 배운다.

⚠ 삽입형도 프레임처럼 분할을 지킨다. 학습·보정·판정면이 같은 삽입형을 쓰면 안 된다.
"""
from __future__ import annotations

FACTORS = ("secrecy", "value", "management")

# (요소, 상태) -> 짧은 삽입형. 앞의 절반은 학습, 뒤는 판정면 몫으로 나눈다.
#   paren  괄호 삽입    "… 모델 (외부 미공개)"
#   tail   서술 꼬리    "…는 경쟁사 대비 핵심 자산"
#   clause 짧은 절      "유출 시 협상 무산 위험"
INLINE: dict[str, dict] = {
    "secrecy": {
        "proven_absent": [
            ("paren", "(공개 자료)"), ("paren", "(전문 공개)"), ("paren", "(공표 완료)"),
            ("tail", "는 이미 공표된 내용이다"), ("tail", "는 누구나 열람 가능하다"),
            ("clause", "공개 배포 이력 있음"), ("clause", "대외 공개분"),
            ("paren", "(홈페이지 게재분)"), ("tail", "는 공개 자료와 동일하다"),
            ("clause", "외부 배포 완료"),
            ("tail", "는 공개 배포본과 같다"),
            ("clause", "공개 게시 완료"),
        ],
        1: [
            ("paren", "(일부 공개)"), ("paren", "(요약만 공개)"),
            ("tail", "는 개요만 알려져 있다"), ("clause", "부분 공개 상태"),
            ("paren", "(공개분 혼재)"), ("tail", "는 일부만 대외 제공됐다"),
            ("clause", "일부 항목 미공개"), ("paren", "(선별 공개)"),
            ("tail", "는 개요 외에는 알려지지 않았다"), ("clause", "공개 범위 제한적"),
            ("tail", "는 요약만 대외에 나갔다"),
            ("clause", "상세분 미공개"),
        ],
        2: [
            ("paren", "(외부 미공개)"), ("paren", "(대외 비공개)"),
            ("tail", "는 외부에 알린 적이 없다"), ("clause", "공개 이력 없음"),
            ("paren", "(비공개 유지)"), ("tail", "는 사내에만 존재한다"),
            ("clause", "대외 제공 이력 없음"), ("paren", "(미공표)"),
            ("tail", "는 어디에도 배포되지 않았다"), ("clause", "외부 노출 없음"),
            ("tail", "는 외부 문의에 회신한 적이 없다"),
            ("clause", "대외 언급 이력 없음"),
            # ── 이하 12종은 **간접 신호**다(실측 2026-08-14).
            # business 판정면 고등급 95건의 비공개 신호를 세어 보니 우리가 쓰던 명시적
            # 부정("공개한 적 없다")은 20.0% 뿐이고, 독자성 53.7% · 유출위험 42.1% ·
            # 기밀표시 30.5% 가 더 흔했다. 실문서는 "공개 안 했다" 고 쓰지 않고
            # "독자 개발" · "운영 기밀" · "유출 시 위험" 으로 쓴다.
            # 우리 학습셋이 20% 만 커버하는 형태였고, 그래서 secrecy 가 45건을
            # absent 로 단언해 과소분류 52건 중 86.5% 를 만들었다.
            ("paren", "(독자 개발분)"), ("paren", "(자사 고유 기술)"),
            ("tail", "는 자체 개발한 고유 방식이다"),
            ("clause", "타사 공개 자료에 없는 조건"),
            ("paren", "(운영 기밀)"), ("paren", "(사내 한정)"),
            ("tail", "는 사내 취급 한정 자료다"),
            ("clause", "내부 취급 문서"),
            ("tail", "가 유출되면 경쟁사가 즉시 추격한다"),
            ("clause", "유출 시 기술 우위 상실"),
            ("paren", "(경쟁사 미보유)"),
            ("tail", "는 경쟁사가 확보하지 못한 영역이다"),
        ],
    },
    "value": {
        "proven_absent": [
            ("paren", "(일반 정보)"), ("paren", "(통용 자료)"),
            ("tail", "는 업계에서 흔히 쓰인다"), ("clause", "선점 효과 없음"),
            ("paren", "(대체 다수)"), ("tail", "는 어디서나 구할 수 있다"),
            ("clause", "취득 비용 미미"), ("paren", "(표준 양식)"),
            ("tail", "는 경쟁 우위와 무관하다"), ("clause", "독자성 없음"),
            ("tail", "는 대체 자료가 흔하다"),
            ("clause", "보유 이점 없음"),
        ],
        1: [
            ("paren", "(제한적 가치)"), ("paren", "(보조 자료)"),
            ("tail", "는 도움은 되나 결정적이지 않다"), ("clause", "부분적 유용"),
            ("paren", "(대체 가능)"), ("tail", "는 시간을 들이면 대체된다"),
            ("clause", "효과 한정적"), ("paren", "(일부 유효)"),
            ("tail", "는 참고 수준이다"), ("clause", "영향 제한적"),
            ("tail", "는 일부 구간에서만 유효하다"),
            ("clause", "기여 제한적"),
        ],
        2: [
            ("paren", "(핵심 자산)"), ("paren", "(대체 불가)"),
            ("tail", "는 경쟁사 대비 핵심 자산이다"), ("clause", "유출 시 협상 무산 위험"),
            ("paren", "(독자 확보분)"), ("tail", "는 재현에 수년이 걸린다"),
            ("clause", "유출 시 손실 큼"), ("paren", "(대외 유출 금지 대상)"),
            ("tail", "는 수주 성패를 가른다"), ("clause", "경쟁 우위 원천"),
            ("tail", "는 확보에 수년이 들었다"),
            ("clause", "재현 비용 큼"),
        ],
    },
    "management": {
        "proven_absent": [
            ("paren", "(통제 없음)"), ("paren", "(권한 미설정)"),
            ("tail", "는 누구나 접근할 수 있다"), ("clause", "열람 이력 미기록"),
            ("paren", "(공용 보관)"), ("tail", "는 별도 관리 없이 보관된다"),
            ("clause", "반출 승인 없음"), ("paren", "(표시 없음)"),
            ("tail", "는 회수 절차가 없다"), ("clause", "접근 제한 없음"),
            ("tail", "는 사본이 흩어져 있다"),
            ("clause", "보관 규정 없음"),
        ],
        1: [
            ("paren", "(부분 통제)"), ("paren", "(권한만 분리)"),
            ("tail", "는 통제하나 이력은 남지 않는다"), ("clause", "점검 미실시"),
            ("paren", "(규정만 존재)"), ("tail", "는 안내 수준의 관리다"),
            ("clause", "일부 통제"), ("paren", "(기록 일부 누락)"),
            ("tail", "는 승인은 받되 대장이 없다"), ("clause", "통제 불완전"),
            ("tail", "는 규정만 있고 점검이 없다"),
            ("clause", "이행 확인 안 됨"),
        ],
        2: [
            ("paren", "(접근 통제)"), ("paren", "(반출 승인 대상)"),
            ("tail", "는 열람 이력이 자동 기록된다"), ("clause", "직무별 권한 제한"),
            ("paren", "(대장 관리)"), ("tail", "는 지정 단말에서만 열린다"),
            ("clause", "정기 점검 대상"), ("paren", "(암호화 보관)"),
            ("tail", "는 사본 생성이 차단돼 있다"), ("clause", "출입 통제 구역 보관"),
            ("tail", "는 반출 대장으로 관리된다"),
            ("clause", "권한 정기 재검토"),
        ],
    },
}

# 앞 SPLIT_TRAIN 개가 학습. 나머지를 보정·1차판정·2차판정이 나눠 갖는다.
# secrecy lv2 만 24종(간접 신호 12종 추가)이라 학습 몫을 비율로 잡는다.
SPLIT_TRAIN = 6


def inline_for(factor: str, state: str, level: int | None,
               split: str = "train") -> list[tuple[str, str]]:
    key = "proven_absent" if state == "proven_absent" else level
    if state == "unknown" or key is None:
        return []
    pool = INLINE[factor][key]
    # 풀이 큰 경우(간접 신호를 더한 secrecy lv2)에도 학습이 절반을 갖도록 비율로 자른다.
    cut = max(SPLIT_TRAIN, len(pool) // 2)
    if split == "train":
        return list(pool[:cut])
    if split in ("calib", "holdout", "holdout2"):
        rest = pool[cut:]
        # 보정·1차판정·2차판정이 같은 삽입형을 쓰면 판정면이 오염된다. 셋으로 나눈다.
        i = {"calib": 0, "holdout": 1, "holdout2": 2}[split]
        return [x for j, x in enumerate(rest) if j % 3 == i]
    return list(pool)


def embed(content: str, factor: str, state: str, level: int | None,
          rng, split: str = "train") -> str:
    """내용 문장에 요소 진술을 **끼워 넣는다.**

    실문서가 하는 방식이다 — 별도 문장을 만들지 않고 기술 서술 안에 짧게 붙인다.
    """
    pool = inline_for(factor, state, level, split)
    if not pool:
        return content
    kind, frag = rng.choice(pool)
    body = content.rstrip().rstrip(".")
    if kind == "paren":
        return f"{body} {frag}."
    if kind == "tail":
        return f"{body}{frag}."
    return f"{body}. {frag}."


def audit() -> list[str]:
    problems: list[str] = []
    seen: dict[str, tuple] = {}
    for f, states in INLINE.items():
        for key, pool in states.items():
            if len(pool) < 12:
                problems.append(f"[{f}/{key}] 삽입형 {len(pool)}개 — 최소 12개 필요")
            for kind, frag in pool:
                if kind not in ("paren", "tail", "clause"):
                    problems.append(f"[{f}/{key}] 알 수 없는 형태 {kind}")
                if frag in seen:
                    problems.append(f"[{f}/{key}] 중복 삽입형: {frag}")
                seen[frag] = (f, key)
    # 분할 간 겹침
    for f in FACTORS:
        for key, lv in (("proven_absent", None), ("present", 1), ("present", 2)):
            sets = {sp: {x[1] for x in inline_for(f, key, lv, sp)}
                    for sp in ("train", "calib", "holdout", "holdout2")}
            ks = list(sets)
            for i, a in enumerate(ks):
                for b in ks[i + 1:]:
                    if sets[a] & sets[b]:
                        problems.append(f"[{f}/{key}] {a}∩{b} 겹침 {len(sets[a] & sets[b])}종")
    return problems


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    probs = audit()
    print(f"{'요소':11s}{'상태':14s}{'학습':>5s}{'보정':>5s}{'1차':>5s}{'2차':>5s}")
    for f in FACTORS:
        for key, lv, nm in (("proven_absent", None, "absent"), ("present", 1, "lv1"),
                            ("present", 2, "lv2")):
            n = [len(inline_for(f, key, lv, sp)) for sp in ("train", "calib", "holdout", "holdout2")]
            print(f"{f:11s}{nm:14s}{n[0]:>5d}{n[1]:>5d}{n[2]:>5d}{n[3]:>5d}")
    print(f"\n감사 {'문제 없음' if not probs else str(len(probs)) + '건'}")
    for p in probs:
        print(f"  - {p}")
    print("\n예시 (경화42 형태 재현)")
    import random

    rng = random.Random(0)
    c = "신용 평가 모델은 20개 변수와 내부 가중치로 부도를 예측한다"
    for f, key, lv in (("secrecy", "present", 2), ("value", "present", 2)):
        print(f"  {embed(c, f, key, lv, rng)}")
