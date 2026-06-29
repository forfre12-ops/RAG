"""[번들 A] 메타데이터/출처 게이트 가시성 메트릭 단위 테스트.

게이트(metadata_floor / source_prior)를 켜도 입력 메타데이터가 없으면 silent no-op이라,
'게이트 발동 횟수'와 '분류 입력의 ICD 메타 존재율'을 함께 노출한다. 서빙 choke point
(ClassifyService._emit_gate_visibility_metrics)가 pred.warnings(pipeline 산출)와 입력
메타데이터로 best-effort 증가시키는지 고정한다. (게이트 동작 자체는 test_safety_gates_flag_on.)

카운터 값은 자식(child)의 내부 _value 로 직접 읽어 sample 이름 규칙에 의존하지 않는다.
공유 레지스트리이므로 before/after 델타로 검증(테스트 순서 무관).
"""

from __future__ import annotations

from lloydk.api import prom_metrics as pm
from lloydk.services.classify_service import ClassifyService

_emit = ClassifyService._emit_gate_visibility_metrics


def _val(counter, **labels) -> float:
    return counter.labels(**labels)._value.get()


# ── 게이트 발동(METADATA_FLOOR / SOURCE_PRIOR) ────────────────────────────────

def test_floor_raised_warning_increments_metric():
    before = _val(pm.METADATA_FLOOR_APPLIED_TOTAL, action="raised")
    _emit(
        {"security_marking": "secret"},
        ["metadata-floor: security_marking=secret → grade raised S2→S1 (ICD §4.2 명시표기 우선)"],
    )
    assert _val(pm.METADATA_FLOOR_APPLIED_TOTAL, action="raised") == before + 1


def test_access_conflict_warning_increments_metric():
    before = _val(pm.METADATA_FLOOR_APPLIED_TOTAL, action="access_conflict")
    _emit(
        {"access_scope": "approved_only"},
        ["metadata-access-conflict: access_scope=approved_only(제한 접근)인데 예측 S3 → 검수 라우팅"],
    )
    assert _val(pm.METADATA_FLOOR_APPLIED_TOTAL, action="access_conflict") == before + 1


def test_source_prior_cap_and_conflict_increment():
    b_cap = _val(pm.SOURCE_PRIOR_APPLIED_TOTAL, action="capped")
    b_conf = _val(pm.SOURCE_PRIOR_APPLIED_TOTAL, action="cap_conflict")
    _emit(
        {"source_type": "판례"},
        [
            "source-prior: '판례' is public → grade capped at S3",
            "cap-conflict: public-source cap overrode high content grade TS → routing to human review",
        ],
    )
    assert _val(pm.SOURCE_PRIOR_APPLIED_TOTAL, action="capped") == b_cap + 1
    assert _val(pm.SOURCE_PRIOR_APPLIED_TOTAL, action="cap_conflict") == b_conf + 1


def test_no_gate_warning_no_firing_increment():
    b_raise = _val(pm.METADATA_FLOOR_APPLIED_TOTAL, action="raised")
    b_cap = _val(pm.SOURCE_PRIOR_APPLIED_TOTAL, action="capped")
    _emit({"security_marking": "secret"}, ["low-confidence: ... — review recommended"])
    assert _val(pm.METADATA_FLOOR_APPLIED_TOTAL, action="raised") == b_raise
    assert _val(pm.SOURCE_PRIOR_APPLIED_TOTAL, action="capped") == b_cap


# ── 메타데이터 존재율(CLASSIFY_METADATA_PRESENT) ──────────────────────────────

def test_metadata_present_counts_each_field():
    b_mark = _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="security_marking")
    b_scope = _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="access_scope")
    b_src = _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="source")
    _emit({"security_marking": "secret", "access_scope": "department", "source": "보도자료"}, [])
    assert _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="security_marking") == b_mark + 1
    assert _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="access_scope") == b_scope + 1
    assert _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="source") == b_src + 1


def test_metadata_absent_counts_none():
    before = _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="none")
    _emit({}, [])
    assert _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="none") == before + 1


def test_blank_strings_count_as_none():
    before = _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="none")
    _emit({"security_marking": "  ", "access_scope": "", "source": None}, [])
    assert _val(pm.CLASSIFY_METADATA_PRESENT_TOTAL, field="none") == before + 1


# ── best-effort: 비정상 입력에도 예외 전파 안 함(분류 무영향) ───────────────────

def test_emit_is_best_effort_on_none_meta():
    _emit(None, [])  # 예외 없으면 통과
