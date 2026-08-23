"""Track A (Wave2) 회귀 — 추론·평가 정합 / 미탐(fail-SECURE) 보강.

대상 수정:
  [M-agg]   m5_inference/pipeline.py _aggregate_chunk_probs — 고등급(TS/S1) 청크
            어그리게이션을 단순 평균이 아니라 most-severe-wins(max)로. 한 청크의
            강한 비밀 신호가 다수 평범 청크에 희석되어 미탐되는 것을 차단.
  [M-renorm] m5_inference/pipeline.py _enforce_label_consistency + run() — FNR-safe
            override / source-prior cap 후 해당 등급을 strict argmax로, scores 합=1
            재정규화, confidence=scores[label] 재계산 (label/scores/confidence 정합).
  [M-metrics-confirm] m6_evaluation/metrics.py — 정답 라벨을 direction 무관 '가장 최신
            correction'의 corrected_level_id로. confirm 무시 버그 제거.
  [M-metrics-len] m6_evaluation/metrics.py — 길이 불일치는 ValueError(조용한 all-zeros
            은폐 금지). n==0만 N/A(빈 결과).
  [M-confusion-gate] m6_evaluation/confusion_matrix.py — critical·high severity 미탐은
            count>=1 단독 경보(rate 게이팅 제거). 고위험 소량 미탐 누락 차단.

실제 ML 모델/DB는 안 돌린다 — torch 텐서·순수함수·fake로 로직만 검증.
"""

from __future__ import annotations

import numpy as np
import pytest

from koipa.schemas.common import Grade


# ===========================================================================
# 공용 fake pipeline (model_dir=None → 룰폴백 경로, _id2label은 기본 [TS,S1,S2,S3])
# ===========================================================================


def _fresh_pipeline():
    from koipa.modules.m5_inference.pipeline import InferencePipeline

    return InferencePipeline(model_dir=None)


# ===========================================================================
# [M-agg] 청크 어그리게이션: most-severe-wins (고등급 희석 미탐 차단)
# ===========================================================================


def test_magg_single_secret_chunk_not_diluted_into_missed_detection():
    """평범한 청크 다수 + TS 강신호 1청크 → 단순 평균이면 TS가 묻히지만,
    most-severe-wins로 TS 확률이 max 청크값으로 보강되어 미탐을 막는다."""
    import torch

    pipe = _fresh_pipeline()
    # [TS, S1, S2, S3] 순. 9개 청크는 S3=0.9 (평범/공개), 1개 청크만 TS=0.95 (핵심 비밀).
    rows = [[0.02, 0.02, 0.06, 0.90]] * 9 + [[0.95, 0.02, 0.02, 0.01]]
    chunk_probs = torch.tensor(rows, dtype=torch.float32)
    weights = [100] * 9 + [40]  # 비밀 청크가 더 짧아도(길이가중) 묻히면 안 됨

    # 단순 비가중 평균: TS ≈ (0.02*9 + 0.95)/10 ≈ 0.113 → S3가 argmax (미탐)
    mean_ts = float(chunk_probs.mean(dim=0)[0])
    assert mean_ts < 0.5

    doc = pipe._aggregate_chunk_probs(chunk_probs, weights)
    # 보강 후 TS는 가장 강한 청크값(0.95) 이상 → argmax=TS (미탐 차단)
    assert float(doc[0]) >= 0.95 - 1e-4
    assert int(doc.argmax().item()) == 0  # TS


def test_magg_low_grade_behavior_preserved_length_weighted():
    """저등급(S2/S3)만 있는 평범 문서는 일반(길이가중 평균) 동작 유지 — over-escalation 없음."""
    import torch

    pipe = _fresh_pipeline()
    rows = [[0.05, 0.05, 0.10, 0.80]] * 5
    chunk_probs = torch.tensor(rows, dtype=torch.float32)
    doc = pipe._aggregate_chunk_probs(chunk_probs, [1] * 5)
    # 균질 입력이면 평균과 동일, argmax=S3 유지 (고등급 max 보강이 동률을 넘지 못함)
    assert int(doc.argmax().item()) == 3  # S3
    # TS/S1 max 보강값(0.05)이 S3(0.80)을 넘지 못함 → 일반 동작
    assert float(doc[3]) == pytest.approx(0.80, abs=1e-4)


def test_magg_is_monotonic_non_decreasing_for_high_grades():
    """고등급 보강은 단조 비감소 — 어떤 경우에도 평균보다 낮아져 미탐 방향으로 후퇴하지 않는다."""
    import torch

    pipe = _fresh_pipeline()
    rows = [[0.30, 0.10, 0.30, 0.30], [0.10, 0.40, 0.20, 0.30]]
    chunk_probs = torch.tensor(rows, dtype=torch.float32)
    mean = chunk_probs.mean(dim=0)
    doc = pipe._aggregate_chunk_probs(chunk_probs, [1, 1])
    assert float(doc[0]) >= float(mean[0]) - 1e-6  # TS
    assert float(doc[1]) >= float(mean[1]) - 1e-6  # S1


def test_magg_single_chunk_is_identity():
    import torch

    pipe = _fresh_pipeline()
    chunk_probs = torch.tensor([[0.1, 0.2, 0.3, 0.4]], dtype=torch.float32)
    doc = pipe._aggregate_chunk_probs(chunk_probs, [1])
    assert int(doc.argmax().item()) == 3


# ===========================================================================
# [M-renorm] override/cap 후 label·scores(argmax)·confidence 정합
# ===========================================================================


def test_mrenorm_helper_makes_label_strict_argmax_and_renormalizes():
    pipe = _fresh_pipeline()
    # 모델이 S3=0.95로 강하게, TS는 0 — override가 TS를 0.7로만 올리면 여전히 S3가 argmax였다.
    scores = {"TS": 0.0, "S1": 0.0, "S2": 0.05, "S3": 0.95}
    out, conf = pipe._enforce_label_consistency(scores, "TS", floor=0.7)
    # 합=1 재정규화
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)
    # TS가 strict argmax
    assert max(out, key=out.get) == "TS"
    assert out["TS"] > out["S3"]
    # confidence == 재정규화된 scores[label]
    assert conf == pytest.approx(out["TS"], abs=1e-9)


def test_mrenorm_helper_breaks_ties_toward_label_fail_secure():
    """동률이어도 label(고등급)이 argmax가 되어야 미탐을 막는다."""
    pipe = _fresh_pipeline()
    scores = {"TS": 0.5, "S1": 0.0, "S2": 0.0, "S3": 0.5}
    out, _ = pipe._enforce_label_consistency(scores, "TS", floor=0.0)
    assert max(out, key=out.get) == "TS"
    assert out["TS"] > out["S3"]


def test_mrenorm_helper_all_zero_scores_assigns_label_full():
    pipe = _fresh_pipeline()
    out, conf = pipe._enforce_label_consistency(
        {"TS": 0.0, "S1": 0.0, "S2": 0.0, "S3": 0.0}, "S1", floor=0.0
    )
    assert out["S1"] == pytest.approx(1.0)
    assert conf == pytest.approx(1.0)
    assert sum(out.values()) == pytest.approx(1.0)


def test_mrenorm_override_path_keeps_label_scores_confidence_consistent(monkeypatch):
    """run() FNR-safe override: 모델이 S3 강신호여도 룰 TS override 후 label·argmax·conf 정합."""
    from koipa.modules.m5_inference.pipeline import InferenceResult

    pipe = _fresh_pipeline()
    # 모델 경로 진입을 위해 _model을 truthy로 두고 _run_model을 fake로 — S3로 강하게 예측.
    pipe._model = object()
    monkeypatch.setattr(
        pipe, "_run_model",
        lambda *a, **k: InferenceResult(
            label=Grade.S3, confidence=0.95,
            scores={"TS": 0.0, "S1": 0.0, "S2": 0.05, "S3": 0.95},
        ),
    )

    # 룰 엔진이 TS를 임계 이상으로 강하게 잡도록 fake.
    class _RuleRes:
        confidence = 0.8
        grade_scores = {"TS": 9.0, "S1": 0.0, "S2": 0.0, "S3": 0.0}

    monkeypatch.setattr(pipe.labeling.engine, "label", lambda _t: _RuleRes())
    # source-prior가 cap으로 끼어들지 않도록 metadata 미지정.
    res = pipe.run(text="원천기술 수식 한 문단", use_rag=False)

    assert res.label == Grade.TS, "룰 TS override가 적용되어야 함 (fail-SECURE)"
    # label == scores argmax
    assert max(res.scores, key=res.scores.get) == "TS"
    # confidence == scores[label]
    assert res.confidence == pytest.approx(res.scores["TS"], abs=1e-6)
    # scores 합 ≈ 1
    assert sum(res.scores.values()) == pytest.approx(1.0, abs=1e-6)


def test_mrenorm_source_prior_cap_keeps_consistency(monkeypatch):
    """source-prior cap(공개출처→S3): cap 후 label·argmax·conf 정합 + conf<=0.7."""
    from koipa.modules.m5_inference.pipeline import InferenceResult

    pipe = _fresh_pipeline()
    pipe._model = object()
    # 모델은 TS 강신호. 공개 판례 메타데이터 → S3로 cap 되어야 함.
    monkeypatch.setattr(
        pipe, "_run_model",
        lambda *a, **k: InferenceResult(
            label=Grade.TS, confidence=0.99,
            scores={"TS": 0.97, "S1": 0.01, "S2": 0.01, "S3": 0.01},
        ),
    )
    # 룰 override가 안 끼어들게 룰 점수는 낮게.
    class _RuleRes:
        confidence = 0.1
        grade_scores = {"TS": 0.0, "S1": 0.0, "S2": 0.0, "S3": 0.0}

    monkeypatch.setattr(pipe.labeling.engine, "label", lambda _t: _RuleRes())

    res = pipe.run(text="공개 판례 본문", use_rag=False,
                   metadata={"source": "court_decision"})

    assert res.label == Grade.S3, "공개 출처는 S3로 cap (비공지성 게이트)"
    assert max(res.scores, key=res.scores.get) == "S3"
    assert res.confidence == pytest.approx(min(res.scores["S3"], 0.7), abs=1e-6) or res.confidence <= 0.7
    assert sum(res.scores.values()) == pytest.approx(1.0, abs=1e-6)
    assert res.confidence <= 0.7 + 1e-9


# ===========================================================================
# [M-metrics-len] 길이 불일치 → ValueError (조용한 0.0 통과 은폐 금지)
# ===========================================================================


def test_mlen_length_mismatch_raises_valueerror():
    from koipa.modules.m6_evaluation.metrics import compute_metrics_from_arrays

    # 이전 계약(조용히 all-zeros)을 새 계약(ValueError)으로 갱신.
    # 사유: 길이 불일치는 데이터/파이프라인 버그인데 FNR=0.0으로 보여 미탐 회귀를
    # 게이트가 못 잡던 fail-SECURE 위반. 안전을 위해 강제 실패시킨다.
    with pytest.raises(ValueError):
        compute_metrics_from_arrays(["TS", "S1"], ["TS"])


def test_mlen_empty_still_na_empty_result():
    from koipa.modules.m6_evaluation.metrics import compute_metrics_from_arrays

    m = compute_metrics_from_arrays([], [])
    assert m.sample_count == 0
    assert m.confusion_matrix == []
    # N/A 신호 — accuracy 0.0 (빈 결과)
    assert m.accuracy == 0.0


def test_mlen_valid_pairs_still_compute():
    from koipa.modules.m6_evaluation.metrics import compute_metrics_from_arrays

    m = compute_metrics_from_arrays(["TS", "S1", "S2", "S3"], ["TS", "S1", "S2", "S3"])
    assert m.sample_count == 4
    assert m.accuracy == 1.0
    assert m.fnr_overall == 0.0


# ===========================================================================
# [M-metrics-confirm] 최신 correction(=confirm 포함)을 정답으로
# ===========================================================================


def test_mconfirm_latest_confirm_overrides_older_correction():
    """예측 S3, 처음 교정 S3→TS(underclass), 나중에 TS로 confirm.
    confirm을 무시하던 옛 로직이면 오래된 교정값을 잡지만, 둘 다 TS이므로 정답=TS여야.
    핵심: confirm correction이 truth 추출에서 누락되면 안 된다."""
    import datetime as _dt
    from koipa.modules.m6_evaluation import metrics as metrics_mod

    # 가벼운 fake correction/classification — DB 없이 _fetch_truth_pred_pairs 로직 검증.
    class _C:
        def __init__(self, cid, direction, level_id, at):
            self.classification_id = cid
            self.direction = direction
            self.corrected_level_id = level_id
            self.corrected_at = at

    class _Cls:
        def __init__(self, cid, pred_level):
            self.classification_id = cid
            self.predicted_level_id = pred_level

    # level_id: 1=TS, 4=S3
    class _Level:
        def __init__(self, lid, code):
            self.level_id = lid
            self.level_code = code

    base = _dt.datetime(2026, 6, 1)
    levels = [_Level(1, "TS"), _Level(2, "S1"), _Level(3, "S2"), _Level(4, "S3")]
    classifications = [_Cls("c1", 4)]  # 예측 S3
    corrections = [
        _C("c1", "underclass", 1, base),                       # S3→TS (오래됨)
        _C("c1", "confirm", 1, base + _dt.timedelta(days=1)),  # TS confirm (최신)
    ]

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return list(self._rows)

    class _DB:
        def execute(self, stmt):
            s = str(stmt).lower()
            if "classification_levels" in s and "corrections" not in s and "classifications" not in s:
                return _Scalars(levels)
            if "corrections" in s:
                # 최신순 정렬을 흉내 — 실제 쿼리는 corrected_at.desc()
                ordered = sorted(corrections, key=lambda c: c.corrected_at, reverse=True)
                return _Scalars(ordered)
            return _Scalars(classifications)

    pairs = metrics_mod._fetch_truth_pred_pairs(_DB(), "v-test")
    assert pairs == [("TS", "S3")], f"최신 correction(TS) 정답이어야 함, got {pairs}"


def test_mconfirm_confirm_changes_truth_when_it_differs_from_older():
    """더 결정적: 처음 S3→S2(underclass) 후 최신 confirm이 S1을 가리키면 정답=S1.
    옛 로직(confirm 무시)이면 S2를 잡아 정답이 왜곡됐다."""
    import datetime as _dt
    from koipa.modules.m6_evaluation import metrics as metrics_mod

    class _C:
        def __init__(self, cid, direction, level_id, at):
            self.classification_id = cid
            self.direction = direction
            self.corrected_level_id = level_id
            self.corrected_at = at

    class _Cls:
        def __init__(self, cid, pred_level):
            self.classification_id = cid
            self.predicted_level_id = pred_level

    class _Level:
        def __init__(self, lid, code):
            self.level_id = lid
            self.level_code = code

    base = _dt.datetime(2026, 6, 1)
    levels = [_Level(1, "TS"), _Level(2, "S1"), _Level(3, "S2"), _Level(4, "S3")]
    classifications = [_Cls("c1", 4)]  # 예측 S3
    corrections = [
        _C("c1", "underclass", 3, base),                       # S3→S2 (오래됨)
        _C("c1", "confirm", 2, base + _dt.timedelta(days=2)),  # S1 confirm (최신)
    ]

    class _Scalars:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return list(self._rows)

    class _DB:
        def execute(self, stmt):
            s = str(stmt).lower()
            if "classification_levels" in s and "corrections" not in s and "classifications" not in s:
                return _Scalars(levels)
            if "corrections" in s:
                ordered = sorted(corrections, key=lambda c: c.corrected_at, reverse=True)
                return _Scalars(ordered)
            return _Scalars(classifications)

    pairs = metrics_mod._fetch_truth_pred_pairs(_DB(), "v-test")
    assert pairs == [("S1", "S3")], f"최신 confirm(S1)이 정답이어야 함, got {pairs}"


# ===========================================================================
# [M-confusion-gate] high severity는 count>=1 단독 경보 (rate 게이팅 제거)
# ===========================================================================


def test_mgate_high_severity_alarms_on_single_count_below_rate():
    """TS 100건 중 단 1건이 S2로 미탐(TS→S2 = high). rate=1% < 5%여도 경보돼야 한다.
    옛 로직(high도 rate 게이팅)이면 누락 — 고위험 소량 미탐 은폐 = fail-SECURE 위반."""
    from koipa.modules.m6_evaluation import build_confusion_matrix

    y_true = ["TS"] * 100
    y_pred = ["TS"] * 99 + ["S2"]  # TS→S2 (order diff 2 = high)
    cm = build_confusion_matrix(y_true, y_pred, alarm_rate_threshold=0.05)
    high = [a for a in cm.alarms if a.truth == "TS" and a.pred == "S2"]
    assert len(high) == 1
    assert high[0].severity == "high"
    assert high[0].count == 1


def test_mgate_critical_still_alarms_on_single_count():
    from koipa.modules.m6_evaluation import build_confusion_matrix

    cm = build_confusion_matrix(["TS"] * 100, ["TS"] * 99 + ["S3"], alarm_rate_threshold=0.05)
    crit = [a for a in cm.alarms if a.severity == "critical"]
    assert len(crit) == 1


def test_mgate_medium_still_rate_gated_to_avoid_noise():
    """medium(order diff 1, 예: TS→S1)은 여전히 rate 게이팅 — 저위험 노이즈 억제 유지."""
    from koipa.modules.m6_evaluation import build_confusion_matrix

    cm = build_confusion_matrix(["TS"] * 100, ["TS"] * 99 + ["S1"], alarm_rate_threshold=0.05)
    med = [a for a in cm.alarms if a.severity == "medium"]
    assert med == []  # 1% < 5% → 필터됨


def test_mgate_high_direct_detect_helper():
    from koipa.modules.m6_evaluation import detect_underclass_alarms

    # S1(행) 100건 중 1건만 S3(열)로 미탐 = high (order diff 2). count>=1 경보.
    cm = np.zeros((4, 4), dtype=int)
    cm[1, 1] = 99
    cm[1, 3] = 1
    alarms = detect_underclass_alarms(cm, labels=["TS", "S1", "S2", "S3"],
                                      alarm_rate_threshold=0.5)  # 높은 rate 임계
    high = [a for a in alarms if a.truth == "S1" and a.pred == "S3"]
    assert len(high) == 1 and high[0].severity == "high"
