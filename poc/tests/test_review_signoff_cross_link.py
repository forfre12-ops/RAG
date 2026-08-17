"""검토 화면과 서명 화면은 같은 job 안에서 서로 오갈 수 있어야 한다.

왜(2026-08-17). 두 화면은 같은 job 의 앞뒤인데 링크가 **0개**였다. 검수자가 검토본에서
서명으로 가려면 주소를 다시 받아야 했고, 그 주소는 ?t= HMAC 토큰이 붙어 있어 손으로
칠 수도 없다.

전역 네비에는 넣을 수 없는 것이 맞다 — review/signoff 는 job_id 와 ?t= 가 있어야 열리고
(golden.py:741·762) 고정 링크로 걸면 403 이다. 그런데 **같은 job 안에서는** 서버가 형제
주소를 서명해 줄 수 있다(_signed_html_urls). 그것을 렌더러에 넘겨 링크로 만든다.

⚠ 토큰을 아는 곳은 API 층이다. 서비스·렌더러는 받은 문자열을 그대로 쓴다 — 비밀키가
  아래 층으로 새지 않게.
"""

from __future__ import annotations

import inspect

from koipa.api import golden as golden_api
from koipa.golden_review_html import render_review_html, render_signoff_html
from koipa.services.golden_build_service import GoldenBuildService

_RECORDS = [{"doc_id": "d1", "label": "S2", "text": "본 문서는 내부 관리 기준을 정한다." * 4,
             "llm_grade": "S2", "confidence": 0.9}]
_TOKENED = "/api/v1/golden/jobs/J/{}.html?t=55f85adf91ebee29acc2bacd"


def test_review_links_to_signoff_with_the_token_intact():
    html = render_review_html(_RECORDS, signoff_url=_TOKENED.format("signoff"))
    assert _TOKENED.format("signoff") in html.replace("&amp;", "&")
    assert "서명 화면" in html


def test_signoff_links_back_to_review_with_the_token_intact():
    html = render_signoff_html(_RECORDS, job_id="J", post_url="/p",
                               review_url=_TOKENED.format("review"))
    assert _TOKENED.format("review") in html.replace("&amp;", "&")
    assert "검토본" in html


def test_no_link_renders_nothing_rather_than_a_dead_anchor():
    """주소가 없으면(비밀키 미설정 등) 빈 링크를 그리지 않는다 — 눌러도 안 되는 것이 더 나쁘다."""
    for html in (render_review_html(_RECORDS),
                 render_signoff_html(_RECORDS, job_id="J", post_url="/p")):
        assert 'cnav-link" href=""' not in html


def test_service_accepts_the_url_but_does_not_compute_it():
    """서명 토큰을 아는 곳은 API 층 하나여야 한다 — 서비스가 비밀키를 만지면 안 된다."""
    assert "signoff_url" in inspect.signature(GoldenBuildService.render_review).parameters
    assert "review_url" in inspect.signature(GoldenBuildService.render_signoff).parameters
    src = inspect.getsource(GoldenBuildService)
    assert "golden_html_url_secret" not in src
    assert "_mint_html_token" not in src


def test_api_layer_passes_the_signed_sibling_url():
    src = inspect.getsource(golden_api)
    assert "render_review(job_id, signoff_url=" in src
    assert "render_signoff(job_id, review_url=" in src
