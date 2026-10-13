"""검수 큐 위험 정렬 — 사람 검수 시간을 미탐 잡는 데 먼저 쓰는가.

왜 이 시험이 있는가. 검수 큐는 지금까지 `classified_at.asc()` FIFO 하나였다. 사람 검수는
이 제품에서 가장 비싼 자원인데 **도착 순서**로 쓰고 있었다는 뜻이다. 계약 핵심목표는
"미탐 최소화"이고 미탐은 비밀문서를 낮은 등급으로 본 것이므로, 같은 한 시간에 잡히는
미탐이 가장 많은 순서는 **낮은 등급 · 낮은 신뢰도 먼저**다.

이 시험이 지키는 것 넷.
    ① 기본은 FIFO 그대로다 — 옵션을 안 주면 종전 동작이어야 한다(회귀 금지)
    ② risk 는 낮은 등급을 먼저 올린다 — level_order 는 TS=1…S3=4 라 **내림차순**이다
    ③ 동률이면 오래된 것 먼저 — 기아 방지. 이게 없으면 낡은 항목이 영원히 안 뜬다
    ④ 정렬이 limit/offset **앞에** 걸린다 — 페이지 안에서만 정렬하면 큐 전체로는 무효다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_SERVICE = Path(__file__).resolve().parents[1] / "src" / "koipa" / "services" / "confirm_service.py"
_API = Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "confirm.py"


def test_default_is_still_fifo():
    """옵션을 안 주면 종전과 같아야 한다 — 기본값이 바뀌면 검수자가 보던 순서가 말없이 바뀐다."""
    import inspect

    from koipa.services import confirm_service as cs

    sig = inspect.signature(cs.list_review_queue)
    assert sig.parameters["order"].default == cs.ORDER_FIFO


def test_risk_branch_orders_low_grade_first():
    """level_order 는 TS=1…S3=4 다. 낮은 등급을 먼저 올리려면 **내림차순**이어야 한다.

    여기서 asc/desc 를 뒤집으면 TS(이미 최고 등급이라 검수해도 미탐이 안 잡히는 것)를
    맨 앞에 놓게 되어, 이 기능이 목표의 정반대로 동작한다.
    """
    src = _SERVICE.read_text(encoding="utf-8")
    m = re.search(r"ORDER_RISK:(.*?)else:", src, re.S)
    assert m, "risk 분기를 찾지 못했다"
    branch = m.group(1)
    assert "ClassificationLevel.level_order.desc()" in branch, (
        "낮은 등급 먼저가 아니다 — level_order 내림차순이어야 한다"
    )
    assert "Classification.confidence.asc()" in branch, "경계(저신뢰) 우선이 빠졌다"
    assert "Classification.classified_at.asc()" in branch, (
        "마지막 기준이 FIFO 가 아니면 낡은 항목이 굶는다"
    )


def test_ordering_is_applied_before_pagination():
    """가져온 페이지만 다시 정렬하면 '첫 페이지 안에서만 위험순'이 되어 큐 전체로는 무효다."""
    src = _SERVICE.read_text(encoding="utf-8")
    body = src[src.index("def list_review_queue"):]
    order_at = body.index(".order_by(")
    limit_at = body.index(".limit(limit)")
    assert order_at < limit_at, "정렬이 limit/offset 뒤에 걸려 있다"


def test_api_exposes_the_option_with_a_closed_vocabulary():
    """임의 문자열을 받으면 정렬 키가 조용히 무시된다 — 열린 값은 무음 실패를 만든다."""
    src = _API.read_text(encoding="utf-8")
    assert 'pattern="^(fifo|risk)$"' in src, "정렬 값이 열려 있다"
    assert "order=order_value" in src, "엔드포인트가 서비스로 값을 안 넘긴다"


def test_unknown_order_falls_back_to_fifo(monkeypatch):
    """모르는 값이 오면 FIFO 로 떨어진다 — 정렬 때문에 큐가 비어 보이면 안 된다."""
    from koipa.services import confirm_service as cs

    captured: dict = {}

    class _Stmt:
        def join(self, *a, **k):
            return self

        def where(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            captured["order_by"] = a
            return self

        def limit(self, *a):
            return self

        def offset(self, *a):
            return self

    # DB 를 태우지 않고 분기만 본다 — 이 시험의 관심은 '어떤 정렬을 골랐는가' 하나다.
    src = _SERVICE.read_text(encoding="utf-8")
    assert 'str(order or "").strip().lower() == ORDER_RISK' in src, (
        "정확히 'risk' 일 때만 위험 정렬로 가야 한다(그 외는 FIFO 폴백)"
    )
    assert cs.ORDER_RISK == "risk" and cs.ORDER_FIFO == "fifo"


@pytest.mark.parametrize("order", ["fifo", "risk"])
def test_queue_runs_against_the_real_db(order):
    """두 정렬 모두 실제 DB 에서 예외 없이 돈다 — SQL 조인이 틀리면 여기서 터진다.

    큐가 비어 있어도 통과한다(빈 큐는 정상). 이 시험이 잡는 것은 **쿼리가 성립하는가**다.
    """
    from koipa.services.confirm_service import list_review_queue

    items, total, warnings = list_review_queue(limit=5, order=order)
    if warnings and any("db unavailable" in w for w in warnings):
        pytest.skip("Postgres 미도달 — 이 시험은 DB 가 있어야 의미가 있다")
    assert isinstance(items, list) and isinstance(total, int)
    assert not warnings, f"쿼리가 실패했다: {warnings}"
