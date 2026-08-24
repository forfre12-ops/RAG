"""화면 기본 상태에 **구현 정보**가 새어 나오지 않는가 — 금지 문자열 잠금.

왜(2026-08-24). 문구를 고쳤다고 보고했는데 실제로는 다섯 건이 반영되지 않았고 두 건은
문장이 깨진 채 배포됐다. 원인은 문구 수정 실패가 아니라 **검증 절차**다 — 수정 스크립트가
중간에 죽었는데 이미 찍힌 성공 표시를 보고 완료라고 말했다. 사람이 눈으로 몇 개 보는 방식은
같은 사고를 다시 낸다.

그래서 **금지 목록을 시험으로 잠근다.** 사용자 지시(2026-08-24):
    "publish, noop, version_label, insufficient_per_grade, FUN-, POST /, demo-secret-key
     같은 금지 목록을 테스트로 잠가야 합니다."

■ 판정 기준이 핵심이다 — "파일에 없을 것"이 아니다

기술 담당자는 원래 식별자를 볼 수 있어야 한다(운영·개발 대조). 그래서 금지 대상은
**기본 화면에 보이는 것**이고, 접힌 곳(`<details>`)과 기술 상세(`[data-tech]`) 안은 허용한다.
이 시험은 그 둘을 **제거한 뒤** 남은 텍스트만 본다.

■ 지금은 일부러 빨간 시험이다

0차 시점의 잔여 건수를 기준선으로 박아 둔다(BASELINE). 차수를 진행하며 이 숫자를 줄이고,
0 이 되면 `xfail` 표시를 떼고 회귀 방지 시험으로 전환한다. 숫자가 늘면 그 자리에서 실패한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from koipa.api.golden import _render_console_login_html, _render_specledger_gold_console_html
from koipa.golden_review_html import _nav_html

_POC = Path(__file__).resolve().parents[1]
STATIC = _POC / "src" / "koipa" / "api" / "static"

# 금지 대상 — 일반 사용자 화면에 나오면 안 되는 구현 정보.
# 값은 (문자열, 왜 금지인가) 다. 사유를 적어 두는 이유: 다음 사람이 목록만 보고
# "이건 왜 안 되지" 를 되묻지 않게 하려는 것이다.
FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("demo-secret-key", "실제 키와 달라 사용자를 401 로 유도한다"),
    ("version_label", "코드 변수명이 입력칸 이름으로 나온다"),
    # ⚠ 소스는 소문자다 — 화면이 대문자로 보이는 것은 CSS(.fld text-transform)다.
    #   대문자로만 찾으면 하나도 안 걸린다(2026-08-24 실측).
    ("training_type", "코드 변수명이 입력칸 이름으로 나온다"),
    ("base_model", "코드 변수명이 입력칸 이름으로 나온다"),
    ("use_rag", "코드 변수명이 입력칸 이름으로 나온다"),
    ("insufficient_per_grade", "서버 내부 사유 코드가 그대로 노출"),
    ("locked_gold_eval", "DB/코드 식별자"),
    ("label_source", "DB 컬럼명"),
    ("build_training_rows", "함수명"),
    ("noop_fallback", "내부 상태값"),
    ("artifacts/", "서버 파일 경로"),
    (".jsonl", "서버 파일 경로"),
    ("scripts/", "서버 파일 경로"),
    ("모델 공장", "우리가 만든 비유 — 제출본·매뉴얼에 없다"),
    ("학습행", "우리가 만든 말 — 제출본·매뉴얼에 없다"),
    ("소프트 삭제", "우리가 만든 말"),
    ("자리표시", "우리가 만든 말"),
    ("헬스체크", "우리가 만든 말 — 「서버 상태 확인」"),
    ("메트릭 스크랩", "우리가 만든 말"),
)

# 0차 시점 잔여 건수. 차수를 진행하며 줄인다. 늘면 실패한다.
# 0차 **시작** 시점 10건 → **완료** 시점 6건. 아래 BASELINE 은 현재값이고, 늘면 실패한다.
# 0차에서 지운 4건: 학습행 · 소프트 삭제 · 헬스체크 · 메트릭 스크랩(전부 우리가 만든 말).
# 남은 6건과 처리 차수:
#   version_label · training_type · base_model · use_rag  → 0.5차([기술 상세] 토글 뒤로)
#   demo-secret-key                                        → 0.5차(입력칸 제거)
#   모델 공장                                              → 2차(배지 단순화)
# 0차 시작 시점 실측(2026-08-24): 10건.
#   admin.html  training_type · base_model · version_label · use_rag ·
#               학습행 · 소프트 삭제 · 헬스체크 · 메트릭 스크랩
#   index.html  demo-secret-key
#   signoff     모델 공장
# ⚠ 이 시험은 **정적 마크업만** 본다. 실행 중 만들어지는 문구(insufficient_per_grade ·
#   locked_gold_eval · artifacts/… 같은 JS 문자열)는 여기서 안 걸린다 — 콘솔 e2e 하니스가
#   실제로 렌더해서 잡아야 한다(1차에서 추가). 그때 이 숫자는 다시 올라간다.
BASELINE = 6

_DETAILS = re.compile(r"<details.*?</details>", re.S)
_TECH = re.compile(r"<[^>]*data-tech[^>]*>.*?</[a-zA-Z]+>", re.S)
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<script[^>]*>.*?</script>", re.S)
_STYLE = re.compile(r"<style[^>]*>.*?</style>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _visible(html: str) -> str:
    """기본 상태에서 사람이 읽는 글자만 남긴다.

    빼는 것: 주석 · <style> · <script> · 접힌 <details> · 기술 상세 [data-tech].
    ⚠ <script> 를 빼면 실행 중 생기는 문구(빈 화면·오류 메시지)를 놓친다. 그것은
      콘솔 e2e 하니스가 실제로 렌더해서 잡는다 — 여기서는 정적 마크업만 본다.
    """
    s = _COMMENT.sub(" ", html)
    s = _STYLE.sub(" ", s)
    s = _SCRIPT.sub(" ", s)
    s = _DETAILS.sub(" ", s)
    s = _TECH.sub(" ", s)
    s = _TAG.sub(" ", s)
    return re.sub(r"\s+", " ", s)


def _screens() -> dict[str, str]:
    return {
        "admin.html": (STATIC / "admin.html").read_text(encoding="utf-8"),
        "index.html": (STATIC / "index.html").read_text(encoding="utf-8"),
        "manage": _render_specledger_gold_console_html(),
        "login": _render_console_login_html(),
        "signoff": _nav_html("검증문서 검수 · 서명", "full-train", "서명"),
    }


def collect_hits() -> list[tuple[str, str, str]]:
    """(화면, 금지문자열, 사유) 목록."""
    out: list[tuple[str, str, str]] = []
    for name, html in _screens().items():
        vis = _visible(html)
        for needle, why in FORBIDDEN:
            if needle in vis:
                out.append((name, needle, why))
    return out


@pytest.mark.xfail(
    reason="0차 완료 시점 잔여 6건 — 0.5차([기술 상세] 토글·API 키 제거)와 2차(배지)에서 0 이 된다. "
           "0 이 되면 이 표시를 떼고 회귀 방지 시험으로 전환할 것.",
    strict=False,
)
def test_no_forbidden_strings_on_default_screens():
    """기본 화면에 구현 정보가 없어야 한다 — 0 이 될 때까지 잔여 건수를 줄여 간다.

    ⚠ 지금은 xfail 이다. **줄어드는 것을 지키는 것은 아래 test_hit_count_does_not_grow** 이고,
    그쪽이 진짜 잠금장치다(BASELINE 을 넘으면 실패). 이 시험은 목표 상태를 문서로 남긴다.
    """
    hits = collect_hits()
    report = "\n".join(f"  {n:12} {s:24} {w}" for n, s, w in hits)
    assert not hits, f"기본 화면에 구현 정보가 남아 있다 ({len(hits)}건)\n{report}"


def test_hit_count_does_not_grow():
    """차수 진행 중 회귀 방지 — 잔여 건수가 기준선보다 늘면 실패한다."""
    n = len(collect_hits())
    assert n <= BASELINE, f"금지 문자열이 {BASELINE}건 → {n}건으로 늘었다"
