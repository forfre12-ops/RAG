"""[W6] 검수 승인 합성 샘플 → 학습셋 빌드 검증 — generate→queue→review→train 루프 마감.

DB 없이 SynthesisService.build_training_rows 배선만 검증(session_scope·SynthRepo·ClassifyRepo
페이크): (1) 승인분만 대상 (2) noop_fallback/llm_nonjson 마커는 학습에서 배제 (3) 빈 본문 드롭
(4) level_id→code 라벨 매핑 (5) limit. 무음 드롭 없이 사유별 카운트가 노출되는지도 본다.

이 하드 위생 게이트가 없으면 P0#1이 검수큐에 보존한 label_source 마커가 무의미해지고,
placeholder·비-JSON 합성 노이즈가 학습셋에 새어든다 — 이 테스트가 그 회귀를 막는다.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import lloydk.services.synthesis_service as ss
from lloydk.services.synthesis_service import SynthesisService, _is_training_admissible

_LEVELS = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


class _FakeClassifyRepo:
    def __init__(self, db):  # noqa: D401
        pass

    def level_id_by_code(self, code):
        return _LEVELS.get(str(code))


class _FakeSynthRepo:
    samples: list = []

    def __init__(self, db):  # noqa: D401
        pass

    def count_by_status(self, status):
        return sum(1 for s in _FakeSynthRepo.samples if s.review_status == status)

    def list_by_status(self, status, *, limit=50, offset=0):
        rows = [s for s in _FakeSynthRepo.samples if s.review_status == status]
        return rows[offset:offset + limit]


@contextmanager
def _fake_scope():
    yield object()


def _sample(level_code, *, label_source=None, content="가상 사내 문서 본문", status="approved", domain="finance"):
    return SimpleNamespace(
        sample_id=uuid.uuid4(),
        target_level_id=_LEVELS[level_code],
        generated_content=content,
        doc_type=domain,
        label_source=label_source,
        review_status=status,
    )


def _patch(monkeypatch, samples):
    _FakeSynthRepo.samples = samples
    monkeypatch.setattr(ss, "session_scope", _fake_scope)
    monkeypatch.setattr(ss, "SynthRepo", _FakeSynthRepo)
    monkeypatch.setattr(ss, "ClassifyRepo", _FakeClassifyRepo)


def test_admissible_predicate() -> None:
    assert _is_training_admissible(None) is True        # 정상 생성
    assert _is_training_admissible("some_source") is True
    assert _is_training_admissible("noop_fallback") is False   # placeholder
    assert _is_training_admissible("llm_nonjson") is False     # 비-JSON 원문


def test_excludes_noise_markers(monkeypatch) -> None:
    _patch(monkeypatch, [
        _sample("TS"),                               # 정상 → 포함
        _sample("S1", label_source="noop_fallback"),  # 배제
        _sample("S2", label_source="llm_nonjson"),    # 배제
        _sample("S3"),                               # 정상 → 포함
    ])
    res = SynthesisService().build_training_rows()
    assert res["approved_total"] == 4
    assert res["included"] == 2
    assert res["excluded_noise"] == 2
    labels = sorted(r["label"] for r in res["rows"])
    assert labels == ["S3", "TS"]
    # 학습 행 스키마: 본문=generated_content, source=synthetic.
    assert all(r["source"] == "synthetic" and r["text"] for r in res["rows"])


def test_excludes_empty_text(monkeypatch) -> None:
    _patch(monkeypatch, [_sample("TS", content="   "), _sample("S3", content="실본문")])
    res = SynthesisService().build_training_rows()
    assert res["included"] == 1
    assert res["excluded_empty"] == 1
    assert res["rows"][0]["label"] == "S3"


def test_ignores_non_approved(monkeypatch) -> None:
    _patch(monkeypatch, [
        _sample("TS", status="approved"),
        _sample("S1", status="pending_review"),
        _sample("S2", status="rejected"),
    ])
    res = SynthesisService().build_training_rows()
    assert res["approved_total"] == 1
    assert res["included"] == 1
    assert res["rows"][0]["label"] == "TS"


def test_limit_caps_rows(monkeypatch) -> None:
    _patch(monkeypatch, [_sample("TS"), _sample("S1"), _sample("S3")])
    res = SynthesisService().build_training_rows(limit=1)
    assert res["included"] == 1
