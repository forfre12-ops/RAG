"""Track B 회귀 테스트 — 추론·학습 코어 / 미탐(fail-SECURE).

대상 수정:
  [H2] services/classify_service.py  — 빈 텍스트/본문 읽기 실패 시 S3(공개) 폴백 제거.
       보수적으로 최고등급 격리 + status=needs_review (절대 공개로 안 떨어뜨림).
  [H6] modules/m5_inference/pipeline.py — softmax id→등급 매핑을 모델 config.id2label에서
       구성. 길이·코드집합 불일치면 load 거부(fail-closed). GradeRegistry는 rule-fallback 전용.
  [H8] modules/m5_inference/pipeline.py _build_rag_context — 무스코핑 검색(단일 고객사 엔진).
  [H4] modules/m4_training/trainer.py — 전부-TS degenerate 예측기가 fnr_high=0으로
       best 모델 선정을 게이밍하지 못하도록 합성 지표 + degenerate 가드.

실제 ML 모델/학습은 안 돌린다 — 가벼운 fake/단위 검증으로 로직만 확인.
"""

from __future__ import annotations


import numpy as np
import pytest

from lloydk.schemas.common import Grade


# ===========================================================================
# [H2] classify_service: 빈 텍스트 → 공개(S3) 미탐 차단
# ===========================================================================


def _empty_req(monkeypatch):
    """content 없음 + doc_id가 storage에 없음 → 본문 미확보 케이스 구성."""
    from lloydk.schemas.classify import ClassifyRequest
    from lloydk.services.classify_service import ClassifyService

    svc = ClassifyService()
    # 본문 조회는 storage/DB 없이도 (빈 문자열, 상태 None) 반환하도록 강제 (조회 실패 시뮬레이션)
    monkeypatch.setattr(svc, "_fetch_content_by_doc_id", lambda *a, **k: ("", None))
    req = ClassifyRequest(doc_id="not-a-real-doc", content=None)
    return svc, req


def test_h2_empty_text_never_falls_back_to_public_s3(monkeypatch):
    svc, req = _empty_req(monkeypatch)
    resp = svc.classify(req)
    # 핵심: 절대 공개(S3)로 떨어지면 안 됨 (미탐 방지)
    assert resp.label != Grade.S3, "빈 텍스트가 공개(S3)로 분류되면 미탐 — fail-SECURE 위반"


def test_h2_empty_text_isolates_at_highest_grade_and_needs_review(monkeypatch):
    svc, req = _empty_req(monkeypatch)
    resp = svc.classify(req)
    # 보수적 최고등급 격리
    assert resp.label == Grade.TS, "fail-SECURE는 최고등급(TS)으로 격리해야 함"
    # 사람 검수 큐 강제
    assert resp.status == "needs_review"
    # 운영 신호가 warning에 남아야 함
    assert any("fail-SECURE" in w or "human_review" in w for w in resp.warnings)


def test_h2_highest_grade_helper_returns_top_of_registry_order():
    from lloydk.services.classify_service import ClassifyService

    # DB 미가용 → GradeRegistry fallback = [TS,S1,S2,S3], 선두=TS
    assert ClassifyService._highest_grade() == "TS"


def test_h2_highest_grade_respects_custom_registry_order(monkeypatch):
    from lloydk.schemas import common as common_mod
    from lloydk.services.classify_service import ClassifyService

    # level_order가 다른 프로젝트: S1이 선두(가장 비밀)라고 가정
    monkeypatch.setattr(common_mod.GradeRegistry, "get_codes",
                        classmethod(lambda cls: ["S1", "TS", "S2", "S3"]))
    assert ClassifyService._highest_grade() == "S1"


# ===========================================================================
# [H6] pipeline: id2label는 모델 config에서 — DB 순서로 오매핑 금지
# ===========================================================================


class _FakeConfig:
    def __init__(self, id2label):
        self.id2label = id2label


class _FakeModel:
    """transformers 없이 config.id2label만 노출하는 최소 fake."""

    def __init__(self, id2label):
        self.config = _FakeConfig(id2label)


def _fresh_pipeline():
    from lloydk.modules.m5_inference.pipeline import InferencePipeline

    # model_dir 미지정 → _load_model 미호출, _id2label은 GradeRegistry 기반(rule-fallback용)
    return InferencePipeline(model_dir=None)


def test_h6_config_id2label_used_when_matching():
    """config.id2label 코드집합이 활성 등급과 일치하면 그 인덱스 매핑을 채택."""
    pipe = _fresh_pipeline()
    # 학습 체크포인트 표준 순서 [TS,S1,S2,S3]
    pipe._model = _FakeModel({0: "TS", 1: "S1", 2: "S2", 3: "S3"})
    mapping = pipe._id2label_from_config()
    assert mapping is not None
    assert mapping[0] == Grade.TS
    assert mapping[3] == Grade.S3


def test_h6_config_order_honored_not_registry_order(monkeypatch):
    """DB(GradeRegistry) 순서가 뒤집혀 있어도 모델 config 인덱스 순서를 따라야 한다.

    이게 핵심 버그: GradeRegistry로 매핑을 만들면 인덱스가 엉뚱한 등급에 붙어
    '비밀→공개' 미탐을 일으킨다. config 인덱스 0이 TS면 0은 반드시 TS여야 함.
    """
    from lloydk.schemas import common as common_mod

    # DB 순서를 반대로 (S3 선두) 시뮬레이션 — 코드집합은 동일
    monkeypatch.setattr(common_mod.GradeRegistry, "get_codes",
                        classmethod(lambda cls: ["S3", "S2", "S1", "TS"]))
    pipe = _fresh_pipeline()
    pipe._model = _FakeModel({0: "TS", 1: "S1", 2: "S2", 3: "S3"})
    mapping = pipe._id2label_from_config()
    assert mapping is not None
    # config 인덱스 0 == TS (DB 순서였다면 0==S3가 되어 오매핑됐을 것)
    assert mapping[0] == Grade.TS
    assert mapping[0] != Grade.S3


def test_h6_string_keys_in_config_are_normalized():
    """HF config.id2label 키가 문자열('0'..)이어도 int 인덱스로 정규화."""
    pipe = _fresh_pipeline()
    pipe._model = _FakeModel({"0": "TS", "1": "S1", "2": "S2", "3": "S3"})
    mapping = pipe._id2label_from_config()
    assert mapping is not None
    assert mapping[0] == Grade.TS


def test_h6_codeset_mismatch_refuses_mapping():
    """config 코드집합이 활성 등급집합과 다르면 None (load 거부 유도)."""
    pipe = _fresh_pipeline()
    # 활성 등급(TS,S1,S2,S3)과 다른 코드집합
    pipe._model = _FakeModel({0: "A", 1: "B", 2: "C", 3: "D"})
    assert pipe._id2label_from_config() is None


def test_h6_length_mismatch_refuses_mapping():
    """라벨 수가 다르면(인덱스가 0..N-1 연속 아님 포함) None."""
    pipe = _fresh_pipeline()
    pipe._model = _FakeModel({0: "TS", 1: "S1", 2: "S2"})  # S3 누락
    assert pipe._id2label_from_config() is None


def test_h6_missing_config_refuses_mapping():
    pipe = _fresh_pipeline()
    pipe._model = _FakeModel(None)  # config.id2label 없음
    assert pipe._id2label_from_config() is None


def test_h6_load_model_refuses_on_mismatch(monkeypatch, tmp_path):
    """_load_model: config 매핑 검증 실패 시 ValueError + 모델 폐기(fail-closed)."""

    pipe = _fresh_pipeline()
    pipe.model_dir = tmp_path

    # transformers 로더를 가짜로 — mismatched 코드집합 모델 반환
    class _FakeTok:
        @staticmethod
        def from_pretrained(_p):
            return object()

    class _FakeAutoModel:
        @staticmethod
        def from_pretrained(_p):
            return _FakeModel({0: "X", 1: "Y", 2: "Z", 3: "W"})

    import sys
    import types

    fake_transformers = types.SimpleNamespace(
        AutoTokenizer=_FakeTok,
        AutoModelForSequenceClassification=_FakeAutoModel,
    )
    monkeypatch.setitem(sys.modules, "transformers", fake_transformers)

    with pytest.raises(ValueError):
        pipe._load_model()
    # fail-closed: 모델/토크나이저는 폐기되어 rule-fallback 경로로 가야 함
    assert pipe._model is None
    assert pipe._tokenizer is None


# ===========================================================================
# [H8] _build_rag_context: 무스코핑 검색 (tenant 제거: 격리는 KL 포털 전담)
# ===========================================================================


def test_h8_none_metadata_preserves_search(monkeypatch):
    """metadata is None → 검색은 진행(필터 None). 단일 고객사 엔진, tenant 격리 없음."""
    pipe = _fresh_pipeline()

    captured = {}

    def _spy(*, filter=None, **kwargs):
        captured["filter"] = filter
        return []  # hit 없음이어도 검색은 호출됐다는 사실이 핵심

    monkeypatch.setattr("lloydk.services.retrieval.expand_then_search", _spy)
    # build_store/build_embedder도 안전 fake
    monkeypatch.setattr("lloydk.adapters.vectorstore.build_store", lambda *a, **k: object())
    monkeypatch.setattr("lloydk.adapters.embedding.build_embedder",
                        lambda *a, **k: type("E", (), {"embed": lambda self, t: [[0.0]]})())

    pipe._build_rag_context(query="q", namespace="docs", metadata=None)
    assert "filter" in captured, "metadata None인데 검색이 호출되지 않음 — 검색 경로 손상"
    assert captured["filter"] is None


# ===========================================================================
# [H4] trainer: 전부-TS degenerate 예측기 게이밍 방지
# ===========================================================================


def _cm_all_ts(n_per_grade=10):
    """모든 진짜 등급 표본을 전부 TS(id=0)로 예측한 confusion matrix."""
    cm = np.zeros((4, 4), dtype=int)
    for i in range(4):
        cm[i, 0] = n_per_grade  # 진짜 i등급을 전부 TS(0)로 예측
    return cm


def _cm_perfect(n_per_grade=10):
    cm = np.zeros((4, 4), dtype=int)
    for i in range(4):
        cm[i, i] = n_per_grade
    return cm


def test_h4_all_ts_predictor_has_zero_fnr_high_but_high_penalty():
    """전부-TS는 fnr_high=0(게임됨)이지만 합성 지표는 크게 패널티되어야 한다."""
    from lloydk.modules.m4_training.trainer import (
        _compute_fnr_high, _compute_over_class_rate, _compute_degenerate_penalty,
    )

    cm = _cm_all_ts()
    high_ids = [0, 1]  # TS, S1

    fnr_high = _compute_fnr_high(cm, high_ids)
    assert fnr_high == 0.0, "전부-TS는 정의상 fnr_high=0 (이게 게이밍의 근원)"

    over = _compute_over_class_rate(cm, high_ids)
    degen = _compute_degenerate_penalty(cm)
    balanced = fnr_high + over + degen

    # 저등급(S2,S3) 전부를 TS로 끌어올렸으니 over_class_rate=1.0
    assert over == pytest.approx(1.0)
    # 한 열(TS)에 100% 몰림 → degenerate penalty 발동
    assert degen == 1.0
    # 합성 지표는 0이 아니라 크게 벌점 (best 모델 선정에서 거부됨)
    assert balanced >= 1.0


def test_h4_balanced_predictor_beats_degenerate_on_composite():
    """완벽한(또는 균형) 예측기가 합성 지표에서 전부-TS보다 낮아야(=더 좋아야) 한다.

    metric_for_best_model은 greater_is_better=False이므로 낮을수록 선택됨.
    """
    from lloydk.modules.m4_training.trainer import (
        _compute_fnr_high, _compute_over_class_rate, _compute_degenerate_penalty,
    )
    high_ids = [0, 1]

    def composite(cm):
        return (_compute_fnr_high(cm, high_ids)
                + _compute_over_class_rate(cm, high_ids)
                + _compute_degenerate_penalty(cm))

    degen_score = composite(_cm_all_ts())
    perfect_score = composite(_cm_perfect())

    assert perfect_score < degen_score, (
        "합성 지표가 균형 예측기를 degenerate보다 우수(작은 값)로 평가해야 함"
        f" (perfect={perfect_score}, all_ts={degen_score})"
    )
    assert perfect_score == 0.0  # 완벽 예측: 미탐0 + 과분류0 + degenerate0


def test_h4_default_early_stop_metric_is_balanced():
    """기본 best-metric이 게이밍 가능한 fnr_high 단독이 아니라 합성 지표여야 한다."""
    from lloydk.modules.m4_training.trainer import TrainSpec

    assert TrainSpec().early_stop_metric == "fnr_high_balanced"


def test_h4_over_class_rate_zero_when_no_overclassification():
    """저등급을 고등급으로 올리지 않으면 over_class_rate=0."""
    from lloydk.modules.m4_training.trainer import _compute_over_class_rate

    cm = _cm_perfect()
    assert _compute_over_class_rate(cm, [0, 1]) == 0.0
