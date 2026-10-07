#!/usr/bin/env python
"""서버가 렌더하는 콘솔 화면을 파일로 떠 둔다 — e2e 하니스가 그것을 띄운다.

왜 필요한가.
  `/api/v1/golden/candidates/manage.html`(검증문서 후보 관리)과
  `/api/v1/golden/jobs/{id}/signoff.html`(골든셋 검수·서명)은 정적 파일이 아니라
  파이썬이 문자열로 만들어 내려 주는 화면이다. node 하니스는 파이썬을 부를 수 없어
  그 화면을 열 방법이 없었고, 그래서 그 두 면만 **실행 시험이 0건**이었다
  (test_console_nav 가 링크 목록만 본다).

  여기서 렌더 결과를 떠 두면 하니스가 그것을 서빙해 실제로 띄우고 눌러 볼 수 있다.
  뜬 판이 낡으면 시험이 실제와 다른 것을 보게 되므로, 파이썬 쪽
  `tests/test_e2e_console_rendered_snapshot.py` 가 지금 렌더와 대조해 어긋나면 깨진다.

사용:
    python scripts/dump_console_html.py                # 커밋된 자리에 다시 뜬다
    python scripts/dump_console_html.py --out DIR      # 다른 자리에 (pytest 가 임시 폴더로 쓴다)
    make console-e2e-snapshot                          # 첫 번째와 같은 것

pytest 로 돌릴 때는 시험이 임시 폴더에 **그 자리에서 다시 떠서** 하니스에 넘긴다
(`KOIPA_E2E_RENDERED_DIR`). 그래서 렌더러를 고친 직후에도 시험은 실제 화면을 본다.
커밋된 판은 `node run.mjs` 단독 실행용 편의본이다.

⚠ 렌더러가 설정을 읽는 화면(login.html 의 prefill 토큰 등)은 여기서 뜨지 않는다 —
  환경마다 달라져 스냅샷이 성립하지 않는다. 그 화면들은 전용 파이썬 시험이 이미 본다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

OUT_DIR = _ROOT / "tests" / "e2e_console" / "lib" / "rendered"

# ── 서명 화면의 고정 후보 ────────────────────────────────────────────────────
# 서명 화면은 잡의 후보를 인자로 받아 그린다 — 스냅샷이 성립하려면 그 후보가 고정이어야
# 한다. 실제 검수 회차(kl-ff5a822c)와 같은 모양으로 짠다: 등급이 섞여 있고, 룰과 LLM 이
# 합의하지 못한 후보가 하나 있고, 본문에 마크다운 서식이 들어 있다. 마지막 한 건은
# doc_id·본문에 HTML 을 넣어, 임베드 이스케이프가 풀리면 시나리오가 그 자리에서 걸린다.
SIGNOFF_JOB_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
SIGNOFF_POST_URL = f"/api/v1/golden/jobs/{SIGNOFF_JOB_ID}/signoff"
SIGNOFF_REVIEW_URL = f"/api/v1/golden/jobs/{SIGNOFF_JOB_ID}/review.html?t=e2e-token"
SIGNOFF_MIN_PER_GRADE = 2

_TEXT_TS = """# 차세대 공정 배합비 관리기준

본 문서는 대외비로 관리하며 사외 반출을 금한다.

- 접근 권한: 공정개발팀장 이상
- 보관: 문서관리시스템 내 암호화 저장

배합비 원단위와 촉매 투입 시점은 경쟁사가 알 수 없는 정보로서, 본사가 상당한 노력으로
비밀로 관리하고 있다."""

_TEXT_S1 = """## 시제품 내구 시험 결과 요약

시제품 3종의 내구 시험 결과를 정리한 내부 자료다. 공개된 규격시험 방법을 따랐고 수치는
사내 검토용으로만 회람한다. 별도의 보안 표시나 접근 통제는 지정돼 있지 않다."""

_TEXT_S2 = """거래처별 납품 단가표(2026년 상반기)

| 거래처 | 품목 | 단가 |
| --- | --- | --- |
| A사 | 모듈 | 12,000 |
| B사 | 모듈 | 11,500 |

단가는 계약 종료 후 공개할 수 있으나, 계약 기간에는 사내 열람으로 제한한다."""

_TEXT_S3 = """보도자료 — 신제품 출시 안내

당사는 오는 9월 신제품을 출시한다. 본 자료는 배포용으로 작성됐으며 공개 이후 제한 없이
인용할 수 있다."""

_TEXT_PENDING = """설비 점검 절차서

룰은 S2, LLM 은 TS 로 봤다. 합의하지 못한 후보라 서명 대상이 아니다."""

_SIGNOFF_GOLD = [
    {
        "doc_id": "E2E-SIGN-TS-1", "label": "TS", "rule_grade": "TS", "llm_grade": "TS",
        "llm_confidence": 0.91, "domain": "제조", "text": _TEXT_TS,
    },
    {
        "doc_id": "E2E-SIGN-S1-1", "label": "S1", "rule_grade": "S2", "llm_grade": "S1",
        "llm_confidence": 0.64, "domain": "연구", "text": _TEXT_S1,
    },
    {
        "doc_id": "E2E-SIGN-S2-1", "label": "S2", "rule_grade": "S2", "llm_grade": "S2",
        "llm_confidence": 0.77, "domain": "영업", "text": _TEXT_S2,
    },
    {
        "doc_id": "E2E-SIGN-S3-1", "label": "S3", "rule_grade": "S3", "llm_grade": "S3",
        "llm_confidence": 0.88, "domain": "홍보", "text": _TEXT_S3,
    },
    {
        # 임베드 이스케이프 확인용 — 화면에 글자로 보여야 하고 실행되면 안 된다.
        "doc_id": '<img src=x onerror="window.__pwned=1">', "label": "S3",
        "rule_grade": "S3", "llm_grade": "S3", "llm_confidence": 0.51, "domain": "기타",
        "text": '본문에도 </script><script>window.__pwned2=1</script> 가 섞여 있다.',
    },
]

_SIGNOFF_PENDING = [
    {
        "doc_id": "E2E-SIGN-PEND-1", "label": None, "rule_grade": "S2", "llm_grade": "TS",
        "llm_confidence": 0.42, "domain": "제조", "text": _TEXT_PENDING,
    },
]


def render_manage_html() -> str:
    """검증문서 후보 관리 — /api/v1/golden/candidates/manage.html"""
    from koipa.api import golden as golden_api

    return golden_api._render_specledger_gold_console_html()


def render_signoff_html_sample() -> str:
    """골든셋 검수 · 서명 — /api/v1/golden/jobs/{id}/signoff.html

    review.html 은 **같은 화면**이다(2026-08-18 통합, test_review_signoff_cross_link 가
    같음을 잠근다). 그래서 이 판 하나로 두 주소를 다 띄운다.

    실제 라우트는 잡의 gold/uncertain jsonl 을 읽어 같은 렌더러를 부른다. 여기서는 그
    파일 자리에 위 고정 후보를 넣는다 — 파일 내용이 바뀌어도 스냅샷이 흔들리지 않게.
    """
    from koipa.golden_review_html import render_signoff_html

    return render_signoff_html(
        _SIGNOFF_GOLD,
        job_id=SIGNOFF_JOB_ID,
        post_url=SIGNOFF_POST_URL,
        review_url=SIGNOFF_REVIEW_URL,
        min_per_grade=SIGNOFF_MIN_PER_GRADE,
        pending=_SIGNOFF_PENDING,
    )


# (파일명, 이 모듈 안의 렌더 함수 이름) — 인자 없이 결정론적으로 같은 문자열을 내는 것만.
TARGETS = [
    ("manage.html", "render_manage_html"),
    ("signoff.html", "render_signoff_html_sample"),
]


def render(name: str) -> str:
    return globals()[name]()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out_dir = OUT_DIR
    if "--out" in argv:
        out_dir = Path(argv[argv.index("--out") + 1])
    out_dir.mkdir(parents=True, exist_ok=True)
    changed = []
    for filename, fn_name in TARGETS:
        html = render(fn_name)
        path = out_dir / filename
        old = path.read_text(encoding="utf-8") if path.exists() else None
        if old != html:
            path.write_text(html, encoding="utf-8", newline="")
            changed.append(filename)
        print(f"{filename}: {len(html):,}자 {'(갱신)' if old != html else '(변동 없음)'}")
    if changed:
        # cp949 콘솔에서 깨지므로 em dash 를 쓰지 않는다.
        print("\n갱신된 파일이 있다. 함께 커밋할 것: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
