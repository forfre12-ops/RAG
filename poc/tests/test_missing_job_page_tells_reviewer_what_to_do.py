"""잡이 없을 때 검수자가 원시 JSON 대신 무엇을 해야 하는지 본다.

왜(2026-08-17). review/signoff 는 **검수자가 링크를 눌러 도착하는 자리**다. 그런데 잡이
없으면 `{"detail":"golden build job not found"}` 한 줄이 나왔다. 링크를 받은 사람은 그게
자기 잘못인지 서버 문제인지 알 수 없다.

실제로 드문 상황이 아니다.

    JobStore = RedisJobStore (223 실측)
    golden_register 종류 TTL = 30일 (job_store.py:145-146) · 갱신 때마다 연장
    실측 잔여 2,429,084초 ≈ 28.1일 → 2026-09-14 경 만료

in-memory 폴백이면 API 재시작 한 번에 사라진다(로컬에서 그렇게 뜬다). 재등록은 멱등이라
관리자가 스크립트를 한 번 더 돌리면 끝나므로, 그 사실을 화면에서 알려 준다.
"""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.responses import HTMLResponse

from koipa.api.golden import _job_gate_html


def _page():
    r = _job_gate_html(uuid4())
    assert isinstance(r, HTMLResponse), "예외를 던지면 검수자는 원시 JSON 을 본다"
    return r, r.body.decode("utf-8")


def test_missing_job_returns_a_page_not_raw_json():
    r, html = _page()
    assert r.status_code == 404, "상태 코드는 정직해야 한다 — 화면만 바꾼다"
    assert html.startswith("<!doctype html")


def test_page_says_the_link_is_not_the_problem():
    _, html = _page()
    text = re.sub(r"<[^>]+>", " ", html)
    assert "링크가 잘못된 것이 아닙니다" in text


def test_page_tells_the_reviewer_who_to_ask_and_what_they_will_run():
    _, html = _page()
    text = re.sub(r"<[^>]+>", " ", html)
    assert "재등록" in text
    assert "register_review_signoff_job.py" in html
    assert "멱등" in text, "재등록으로 검수 내용이 사라지지 않는다는 점이 빠지면 못 부탁한다"


def test_page_warns_that_the_new_link_carries_a_token():
    """새 주소의 ?t= 를 잘라내면 다시 403 이다 — 같은 실수를 되풀이하지 않게."""
    _, html = _page()
    assert "?t=" in html


def test_page_embeds_the_job_id_for_the_admin_to_act_on():
    r = _job_gate_html(uuid4())
    html = r.body.decode("utf-8")
    assert re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", html), "어느 잡인지 없으면 관리자가 못 찾는다"
