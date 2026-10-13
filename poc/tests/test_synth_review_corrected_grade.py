"""POST /synth/{id}/review 의 corrected_grade 가 실제로 반영되는가.

왜 이 시험이 있나(2026-08-24). 이 엔드포인트는 진작부터 corrected_grade 를 **받고** 있었고
제출본(부록B API명세)도 "검수 결정(decision, corrected_grade?, comment?, actor)을 적용하고"
로 서술하는데, 서버는 그 값을 어디에도 쓰지 않았다. SynthesisService.review() 가 repo 에
넘기는 인자가 approved/reviewed_by/rejection_reason 뿐이었다. 검수자가 등급을 고쳐 승인해도
학습행은 원래 목표 등급으로 만들어졌다 — 화면은 고쳤다고 말하는데 데이터는 안 고쳐지는,
사람이 알아챌 수 없는 종류의 결함이다.

DB 없이 배선만 본다(session_scope·SynthRepo·ClassifyRepo 페이크). 학습 라벨까지 이어지는
구간은 test_synth_training_build.py 가 맡는다 — 여기는 "요청의 등급이 repo 까지 가는가"다.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import koipa.services.synthesis_service as ss
from koipa.schemas.common import Actor
from koipa.schemas.synthesis import SynthReviewRequest
from koipa.services.synthesis_service import SynthesisService

_LEVELS = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}
_TARGET_LEVEL = _LEVELS["S3"]


class _FakeClassifyRepo:
    def __init__(self, db):  # noqa: D401
        pass

    def level_id_by_code(self, code):
        # 등급체계에서 비활성화된 코드를 흉내 내려고 S1 은 일부러 뺀다.
        table = {k: v for k, v in _LEVELS.items() if k != "S1"}
        return table.get(getattr(code, "value", str(code)))


class _FakeSynthRepo:
    """review() 가 받은 인자를 기록하고, 갱신된 샘플을 돌려준다."""

    last_kwargs: dict = {}
    found: bool = True

    def __init__(self, db):  # noqa: D401
        pass

    def review(self, sample_id, **kwargs):
        _FakeSynthRepo.last_kwargs = dict(kwargs)
        if not _FakeSynthRepo.found:
            return None
        corrected = kwargs.get("corrected_level_id")
        approved = kwargs.get("approved")
        # 실제 repo 와 같은 규칙: 승인분에만, 목표와 다를 때만 교정으로 남는다.
        stored = corrected if (approved and corrected and corrected != _TARGET_LEVEL) else None
        return SimpleNamespace(
            sample_id=sample_id,
            target_level_id=_TARGET_LEVEL,
            corrected_level_id=stored,
            review_status="approved" if approved else "rejected",
        )


@contextmanager
def _fake_scope():
    yield object()


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    _FakeSynthRepo.last_kwargs = {}
    _FakeSynthRepo.found = True
    monkeypatch.setattr(ss, "session_scope", _fake_scope)
    monkeypatch.setattr(ss, "SynthRepo", _FakeSynthRepo)
    monkeypatch.setattr(ss, "ClassifyRepo", _FakeClassifyRepo)


def _req(decision="approve", corrected=None):
    return SynthReviewRequest(
        decision=decision,
        corrected_grade=corrected,
        comment="콘솔 검수",
        actor=Actor(user_id="지재원관리자", role="reviewer"),
    )


def test_corrected_grade_reaches_the_repository() -> None:
    res = SynthesisService().review(uuid.uuid4(), _req(corrected="TS"))
    assert _FakeSynthRepo.last_kwargs["corrected_level_id"] == _LEVELS["TS"]
    assert res.applied_grade == "TS", "응답이 실제 적용된 등급을 밝혀야 한다"


def test_no_correction_leaves_target_grade() -> None:
    res = SynthesisService().review(uuid.uuid4(), _req())
    assert _FakeSynthRepo.last_kwargs["corrected_level_id"] is None
    assert res.applied_grade == "S3"


def test_correction_equal_to_target_is_not_a_correction() -> None:
    """목표와 같은 등급으로 '고쳐' 승인하면 교정 기록이 남지 않는다."""
    res = SynthesisService().review(uuid.uuid4(), _req(corrected="S3"))
    assert res.applied_grade == "S3"


def test_reject_does_not_carry_a_corrected_grade() -> None:
    """반려는 학습에 들어가지 않으므로 등급을 고칠 자리가 없다."""
    SynthesisService().review(uuid.uuid4(), _req(decision="reject", corrected="TS"))
    assert _FakeSynthRepo.last_kwargs["corrected_level_id"] is None


def test_unknown_grade_raises_instead_of_silently_dropping() -> None:
    """등급체계에 없는 코드로 교정하면 조용히 무시하지 않는다.

    조용히 무시하는 것이 정확히 이 시험이 막으려는 원래 결함이다 — 검수자는 고친 줄 알고
    넘어가는데 학습행은 원래 등급으로 만들어진다.
    """
    with pytest.raises(ValueError, match="S1"):
        SynthesisService().review(uuid.uuid4(), _req(corrected="S1"))


def test_missing_sample_still_returns_none() -> None:
    _FakeSynthRepo.found = False
    assert SynthesisService().review(uuid.uuid4(), _req(corrected="TS")) is None
