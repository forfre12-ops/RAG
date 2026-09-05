"""보존기간 삭제 — 기본 OFF · 앞에서부터 연속 삭제 · 월 슬라이스 상한.

DB 없이 순수 함수(_month_slices)와 게이트(retention_enabled)만 검증한다. 실제 DELETE 는
두 dialect 라이브에서 확인했다(2026-09-05: PostgreSQL·MariaDB 각각 오래된 3건만 삭제,
최신 행 보존).
"""

from __future__ import annotations

import datetime as dt

from koipa.services.retention import RETAINED_TABLES, _month_slices, purge_expired

UTC = dt.timezone.utc


def test_disabled_by_default_does_nothing():
    """감사 증빙 보호 — 설정이 꺼져 있으면 세지도 지우지도 않는다."""
    import koipa.config as cfg

    saved = getattr(cfg.settings, "retention_enabled", False)
    cfg.settings.retention_enabled = False
    try:
        out = purge_expired(dry_run=True)
    finally:
        cfg.settings.retention_enabled = saved
    assert out["status"] == "disabled"
    assert out["deleted"] == {}


def test_month_slices_are_contiguous_and_bounded():
    """슬라이스는 빈틈 없이 이어지고 cutoff 를 넘지 않는다 — 중간을 뚫으면 체인이 끊긴다."""
    start = dt.datetime(2025, 11, 17, 9, 30, tzinfo=UTC)
    cutoff = dt.datetime(2026, 2, 10, tzinfo=UTC)
    sl = list(_month_slices(start, cutoff))

    assert sl, "슬라이스가 비면 아무것도 안 지운다"
    # 첫 슬라이스는 월초로 내림 — start 이전을 포함해도 그 구간엔 행이 없다.
    assert sl[0][0] == dt.datetime(2025, 11, 1, tzinfo=UTC)
    # 연속성: 앞 슬라이스의 끝 == 뒤 슬라이스의 시작
    for (_, prev_end), (next_start, _) in zip(sl, sl[1:]):
        assert prev_end == next_start
    # 마지막은 cutoff 를 정확히 만나고 넘지 않는다
    assert sl[-1][1] == cutoff
    assert all(lo < hi for lo, hi in sl)


def test_month_slices_empty_when_nothing_expired():
    start = dt.datetime(2026, 5, 1, tzinfo=UTC)
    assert list(_month_slices(start, dt.datetime(2026, 4, 1, tzinfo=UTC))) == []


def test_retained_tables_have_time_axis_index():
    """삭제가 타야 할 인덱스가 models.py 에 실제로 있는지 — 없으면 전건 스캔이 된다."""
    from koipa.db.models import Base

    expected = {
        "tb_audit_log": "idx_audit_occurred",
        "tb_llm_usage": "idx_lu_called",
    }
    for table, col, _key in RETAINED_TABLES:
        t = Base.metadata.tables[table]
        names = {ix.name for ix in t.indexes}
        assert expected[table] in names, f"{table}: {expected[table]} 없음 — {names}"
        ix = next(ix for ix in t.indexes if ix.name == expected[table])
        first = list(ix.columns)[0].name
        assert first == col, f"{ix.name} 의 선두가 {first} — 시간 칼럼({col})이어야 한다"
