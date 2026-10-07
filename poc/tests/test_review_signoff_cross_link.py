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
from koipa.golden_review_html import render_signoff_html
from koipa.services.golden_build_service import GoldenBuildService

_RECORDS = [{"doc_id": "d1", "label": "S2", "text": "본 문서는 내부 관리 기준을 정한다." * 4,
             "llm_grade": "S2", "confidence": 0.9}]
_TOKENED = "/api/v1/golden/jobs/J/{}.html?t=55f85adf91ebee29acc2bacd"


def test_service_accepts_the_url_but_does_not_compute_it():
    """서명 토큰을 아는 곳은 API 층 하나여야 한다 — 서비스가 비밀키를 만지면 안 된다."""
    assert "review_url" in inspect.signature(GoldenBuildService.render_signoff).parameters
    src = inspect.getsource(GoldenBuildService)
    assert "golden_html_url_secret" not in src
    assert "_mint_html_token" not in src


def test_both_urls_serve_the_same_screen():
    """[통합 2026-08-18] 형제 링크가 아니라 **같은 화면**이 됐다.

    같은 job 의 같은 후보를 보는데 화면이 둘이라 검수자가 같은 목록을 두 번 봤다.
    ⚠ review.html 주소는 없앨 수 없다 — 감리정본 화면설계서 UI-04 이고 스크립트·문서
      6곳이 참조한다. 주소는 남기고 같은 화면을 준다.
    """
    src = inspect.getsource(golden_api)
    assert "render_signoff(job_id, title=" in src, "review 라우트가 서명 화면을 안 준다"
    assert "render_review" not in src, "옛 검토본 렌더러를 아직 부른다"


def test_uncertain_candidates_are_shown_read_only():
    """합의 미달 후보는 서명 대상이 아니다 — 보여주되 결정 폼을 주지 않는다.

    폼이 있으면 "왜 눌러도 안 되나" 가 되고, 아예 빼면 "왜 안 보이나" 가 된다.
    """
    html = render_signoff_html(
        [{"doc_id": "a", "label": "S2", "text": "가" * 60, "review_status": "gold_candidate"},
         {"doc_id": "b", "label": "TS", "text": "나" * 60, "review_status": "uncertain"}],
        job_id="J", post_url="/p")
    assert '"signable"' in html
    assert "function pendingCard" in html
    assert "서명 대상이 아닙니다" in html
    assert "합의 미달" in html


def test_service_reads_uncertain_candidates_too():
    """gold 만 읽으면 검토본이 하던 일이 사라진다."""
    from koipa.services.golden_build_service import GoldenBuildService
    src = inspect.getsource(GoldenBuildService.render_signoff)
    assert 'job.get("uncertain_path")' in src


def test_signable_comes_from_the_source_file_not_the_record_value():
    """⚠ 2026-08-18 라이브에서 잡힌 오류 — 전건이 보기 전용이 되어 아무것도 서명 못 했다.

    처음에는 `review_status` 값으로 서명 가능 여부를 판정했다. 그런데 실제 전달본 120건은
    전부 `review_status='pending'` 이라 허용 목록에 없었고, **120건 전부가 잠겼다.**

    옳은 기준은 **어느 파일에서 왔는가** 다. apply_signoff 는 gold_path 의 doc_id 만
    서명 대상으로 삼고 review_status 는 보지 않는다.
    """
    import json
    import re

    gold = [{"doc_id": "a", "label": "S2", "text": "가" * 60, "review_status": "pending"}]
    unc = [{"doc_id": "b", "label": "TS", "text": "나" * 60, "review_status": "uncertain"}]
    html = render_signoff_html(gold, job_id="J", post_url="/p", pending=unc)
    data = json.loads(
        re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1))
    by = {d["id"]: d["signable"] for d in data}
    assert by == {"a": True, "b": False}, by


def test_every_review_status_value_in_gold_is_signable():
    """gold 파일에 어떤 값이 들어와도 서명 대상이다 — 값으로 거르면 같은 사고가 난다."""
    import json
    import re

    rows = [{"doc_id": f"d{i}", "label": "S2", "text": "가" * 60, "review_status": v}
            for i, v in enumerate(["pending", "gold_candidate", "accepted", "", None, "무엇이든"])]
    html = render_signoff_html(rows, job_id="J", post_url="/p")
    data = json.loads(
        re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.S).group(1))
    assert all(d["signable"] for d in data), [d for d in data if not d["signable"]]
