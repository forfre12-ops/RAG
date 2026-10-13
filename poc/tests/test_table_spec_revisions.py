"""정의서 개정 이력이 재생성 때마다 다시 쓰이지 않는가.

왜 이 시험이 있는가(2026-09-05). 개정 이력의 "근거 커밋" 칸은 "이 문서 머리말의 커밋"
이라는 표식을 쓰면 렌더할 때 **생성 시점 HEAD** 로 채워진다. 아직 커밋되지 않은 최신
행 하나를 위한 장치인데, 행을 더하면서 앞 행이 표식을 달고 남았다.

그 결과 재생성할 때마다 **과거 행의 커밋까지 현재 HEAD 로 덮어써졌다.** 실측:

    판 3  2026-08-29  ...  근거 커밋 a3705759   ← 2026-09-05 커밋이다
    판 4  2026-09-03  ...  근거 커밋 a3705759
    판 5  2026-09-05  ...  근거 커밋 a3705759
    판 6  2026-09-05  ...  근거 커밋 a3705759
    판 7  2026-09-05  ...  근거 커밋 a3705759

다섯 행이 같은 커밋을 가리켰다. 개정 이력이 "언제 무엇을 왜 고쳤나"를 남기는 문서인데
그 근거가 매번 새로 쓰이면 남는 것이 없다.

git log 로 확인한 실제 해시로 못박았고, 이 시험이 다시 늘어나는 것을 막는다.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

# 이 생성기는 **리포 루트**의 scripts/ 에 있다(poc/scripts/ 가 아니다). pytest 의 rootdir 가
# poc/ 라 `import scripts.build_table_spec` 은 poc/scripts 를 가리켜 못 찾는다. 경로로 연다.
_SPEC_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_table_spec.py"


def _load():
    if not _SPEC_PATH.exists():
        pytest.skip("정의서 생성기 없음: %s" % _SPEC_PATH)
    sys.path.insert(0, str(_SPEC_PATH.parent))          # table_spec_meta 를 옆에서 찾는다
    sys.path.insert(0, str(_SPEC_PATH.parents[1] / "poc" / "src"))
    spec = importlib.util.spec_from_file_location("_build_table_spec", _SPEC_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_M = _load()
REVISIONS = _M.REVISIONS
_HEAD_MARKER = _M._HEAD_MARKER
_check_revision_marker = _M._check_revision_marker

_SHA = re.compile(r"^[0-9a-f]{7,40}$")


def test_at_most_one_row_tracks_head():
    """표식은 최신 행 하나까지만. 둘 이상이면 과거 이력이 다시 쓰인다."""
    marked = _check_revision_marker()
    assert len(marked) <= 1, (
        "HEAD 표식을 단 행이 %d개다(판 %s). 새 행을 더할 때 앞 행의 표식을 "
        "그 행을 실제로 만든 커밋 해시로 바꿔야 한다." % (len(marked), ", ".join(marked))
    )


def test_head_marker_if_present_is_the_last_row():
    """표식이 있다면 마지막 행이어야 한다 — 중간 행이 HEAD 를 따라가면 순서가 깨진다."""
    marked = _check_revision_marker()
    if not marked:
        return
    assert marked[0] == REVISIONS[-1][0], "표식이 최신 행이 아니다: 판 %s" % marked[0]


def test_pinned_rows_look_like_commit_hashes():
    """못박은 행은 커밋 해시여야 한다. 여러 커밋이면 ' · ' 로 잇는다."""
    for rev, _day, ref, _what in REVISIONS:
        if ref.startswith(_HEAD_MARKER):
            continue
        for part in [p.strip() for p in ref.split("·")]:
            assert _SHA.match(part), "판 %s 의 근거 커밋이 해시가 아니다: %r" % (rev, part)


def test_revision_numbers_and_dates_are_ordered():
    """판 번호는 1부터 연속이고 일자는 거꾸로 가지 않는다."""
    nums = [int(r[0]) for r in REVISIONS]
    assert nums == list(range(1, len(nums) + 1)), nums
    days = [r[1] for r in REVISIONS]
    assert days == sorted(days), "개정 일자가 거꾸로 간다: %s" % days
