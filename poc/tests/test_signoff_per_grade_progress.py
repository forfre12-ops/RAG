"""검수자가 등급별로 몇 건을 채웠는지 화면에서 본다.

왜(2026-08-17). 종전 표시는 "결정 N / 후보 120건" 하나였다. 그런데 배포 게이트가 요구하는
것은 총량이 아니라 **등급별 최소치**다(settings.deploy_gate_min_locked_per_grade = 5).
20건을 전부 S3 에 하면 readiness 가 안 열리는데, 화면만 봐서는 알 수 없었다.

세는 규칙이 서버의 locked_by_grade 와 같아야 한다 — 어긋나면 화면 숫자를 믿고 끝냈는데
게이트가 안 열린다.

    거부      정답지에서 빠진다 → 세지 않는다
    등급변경   **바꾼 등급**으로 들어간다 → 원래 등급이 아니라 바꾼 쪽에 센다
    승인      후보 등급 그대로

최소치도 화면에 박지 않고 서버 값을 받는다. 둘이 어긋나면 "다 했는데 안 열린다" 가 된다.
"""

from __future__ import annotations

import inspect

from koipa.golden_review_html import render_signoff_html
from koipa.services.golden_build_service import GoldenBuildService

_RECORDS = [{"doc_id": "d1", "label": "S2", "text": "본 문서는 내부 관리 기준을 정한다." * 3}]


def _html(**kw) -> str:
    return render_signoff_html(_RECORDS, job_id="J", post_url="/p", **kw)


def test_minimum_comes_from_the_server_not_the_page():
    assert "const MIN_PG=5;" in _html(min_per_grade=5)
    assert "const MIN_PG=7;" in _html(min_per_grade=7)
    assert "__MINPG__" not in _html(min_per_grade=5), "치환되지 않은 자리표시가 남았다"


def test_service_feeds_the_gate_value():
    """게이트와 다른 상수를 화면에 쓰면 '다 했는데 안 열린다' 가 된다."""
    src = inspect.getsource(GoldenBuildService.render_signoff)
    assert "deploy_gate_min_locked_per_grade" in src


def test_counter_shows_per_grade_promotion_forecast():
    html = _html(min_per_grade=5)
    assert "승격 예정" in html
    assert "pg-lack" in html, "부족한 등급을 구분하지 않으면 눈에 안 띈다"
    assert "건 필요" in html


def test_rejected_items_are_not_counted_as_promotions():
    seg = _html(min_per_grade=5).split("function decCount")[1][:800]
    assert "d.decision==='reject'" in seg and "return" in seg


def test_changed_grade_counts_toward_the_new_grade():
    """등급변경을 원래 등급에 세면 화면과 서버 결과가 어긋난다."""
    assert "(d.decision==='change')?(d.grade||r.grade):r.grade" in _html(min_per_grade=5)


def test_zero_minimum_hides_the_requirement_note():
    """설정이 없으면 있지도 않은 기준을 말하지 않는다."""
    html = _html()
    assert "const MIN_PG=0;" in html
    assert "(MIN_PG>0)" in html, "0 일 때 안내를 숨기는 분기가 없다"
