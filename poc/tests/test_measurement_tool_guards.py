"""측정 도구가 **분모를 조용히 잃지 않는가** — 2026-09-13 에 하루 세 번 난 사고의 잠금.

왜. 그날 같은 모양의 사고가 셋이었다. 분모가 줄거나 0이 됐는데 결과는 초록불로 나왔다.

    ① pytest 전수를 tail 로 잘라 받아 실패 2건의 이름을 잃었다
    ② measure_serving_fnr 이 429(분당 60건 한도)를 URLError 와 함께 None 하나로 뭉개
       holdout109 109건 중 84건이 빠진 채 **"무음 미탐 0건"** 을 냈다
    ③ 같은 도구가 평가셋의 label/text 키를 못 찾아 **빈 본문 100건을 분류**하고
       "실패 0 · 미탐률 0.0%" 를 냈다(전부 TS 예측 · needs_review 100)

②③ 은 확인하지 않았으면 그대로 보고될 뻔했다. 분모가 0 인 0% 는 "미탐 없음" 이 아니다.
이 시험은 그 가드가 살아 있는지만 본다 — 수치를 재지 않는다(느리고 API 가 필요하다).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
_SCRIPTS = _POC / "scripts"


def _load(name: str):
    """scripts/ 의 모듈을 직접 읽어 온다 — 패키지가 아니라 스크립트다."""
    path = _SCRIPTS / f"{name}.py"
    if not path.exists():
        pytest.skip(f"{path} 없음")
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── ③ 평가셋 필드 별칭 ────────────────────────────────────────────────────────

def test_serving_fnr_normalizes_known_eval_schemas() -> None:
    """리포의 평가셋은 스키마가 최소 세 가지다. 별칭이 빠지면 빈 본문을 분류한다."""
    mod = _load("measure_serving_fnr")
    cases = [
        ({"label": "TS", "text": "본문"}, "holdout_eval*"),
        ({"target": "S1", "body": "본문"}, "golden100_labeled_v2"),
        ({"intended_label": "S2", "content": "본문"}, "proxy_gold 후보"),
    ]
    for row, where in cases:
        out = mod._normalize_row(row)
        assert out.get("label"), f"{where}: 정답을 못 읽는다 — {row}"
        assert str(out.get("text") or "").strip(), f"{where}: 본문을 못 읽는다 — {row}"


def test_serving_fnr_keeps_rule_grade_in_records() -> None:
    """미탐 분석에 rule_grade 가 필요하다. 없으면 문서를 다시 태워야 한다(9/13 실제로 그랬다)."""
    src = (_SCRIPTS / "measure_serving_fnr.py").read_text(encoding="utf-8")
    for field in ('"rule_grade"', '"rule_factors"', '"model_factors"'):
        assert field in src, f"레코드에 {field} 가 없다"


def test_serving_fnr_counts_failure_reasons() -> None:
    """실패를 숫자 하나로 두면 429 인지 타임아웃인지 알 수 없다."""
    src = (_SCRIPTS / "measure_serving_fnr.py").read_text(encoding="utf-8")
    assert "failure_reasons" in src, "실패 사유 집계가 없다"
    assert "429" in src, "429(분당 한도) 처리가 없다 — 조용히 분모를 잃는다"
    assert "Retry-After" in src, "429 를 기다렸다 재시도하지 않는다"


# ── 미탐이 능력인지 과탐인지 ───────────────────────────────────────────────────

def test_gate_metrics_reports_prediction_distribution() -> None:
    """미탐 0건이 '전부 최고등급으로 찍어서' 나온 값인지 보려면 예측 분포가 필요하다.

    2026-09-13: v-0d2e9ad0 이 미탐 0건이었는데 42건 중 40건을 TS 로 찍은 결과였고,
    도구가 분포를 안 내 손으로 로짓 캐시를 세어야 알았다.
    """
    path = _SCRIPTS / "measure_gate_metrics.py"
    if not path.exists():
        pytest.skip("measure_gate_metrics.py 없음")
    src = path.read_text(encoding="utf-8")
    assert "prediction_distribution" in src, "리포트에 예측 분포가 없다"
    assert "쏠림" in src, "한 등급 쏠림 경고가 없다"


# ── 감리 DB 지적: R4 사유 ─────────────────────────────────────────────────────

def test_schema_audit_r4_rationales_cover_current_violations() -> None:
    """R4(NOT NULL 불일치)에 사유 없는 항목이 생기면 알려준다 — 그것이 진짜 검토 대상이다."""
    mod = _load("audit_schema_consistency")
    result = mod.audit()
    violations = result.get("R4_id_to_notnull") or {}
    missing = [k for k in violations if k not in mod.R4_JUSTIFIED]
    assert not missing, (
        "R4 에 사유가 적히지 않은 컬럼ID 가 있다 — models.py 에서 확인하고 "
        f"audit_schema_consistency.R4_JUSTIFIED 에 근거를 적을 것: {missing}"
    )


# ── 등급식: 경계 쌍 유형 ──────────────────────────────────────────────────────

def test_grade_boundary_pair_types_are_stable() -> None:
    """등급식이 바뀌면 경계 쌍 유형 수와 S1 희소성이 달라진다 — 조용히 바뀌면 안 된다."""
    mod = _load("enumerate_grade_boundary_pairs")
    cells = mod.grade_cells()
    pairs = mod.boundary_pairs(cells)
    assert len(cells) == 27, "S·V·M 각 0..2 = 27 조합이어야 한다"
    assert len(pairs) == 18, f"경계 쌍 유형은 18개였다 — 지금 {len(pairs)}개"
    s1 = [k for k, v in cells.items() if v == "S1"]
    assert s1 == [(2, 2, 0)], f"S1 은 (2,2,0) 하나뿐이었다 — 지금 {s1}"

# ── 리포트가 "어느 조건의 수치인지" 를 말하는가 ──────────────────────────────

def test_serving_fnr_report_carries_provenance() -> None:
    """수치만 있고 조건이 없으면 나중에 무엇과 비교할지 알 수 없다.

    2026-09-13 에 하루 동안 v22/guide/2차의견ON/S2제외를 번갈아 재면서 같은 파일명에
    덮어썼다. 외부 코드 리뷰가 그 점을 지적했다.
    """
    mod = _load("measure_serving_fnr")
    pv = mod._provenance(_POC / "datasets/gold_real/holdout_eval.hardened.jsonl", "http://x")
    for key in ("measured_at", "git_sha", "git_dirty", "eval_sha256_16", "client_env"):
        assert key in pv, f"지문에 {key} 가 없다"
    assert pv["eval_sha256_16"] != "?", "평가셋 해시를 못 읽었다"
    # 못 읽은 것을 false(깨끗함)로 단정하면 안 된다 — unknown 이어야 한다.
    assert pv["git_dirty"] in (True, False, "unknown")


def test_rate_naming_is_not_misleading() -> None:
    """auto_confirm_rate 는 사람 확정 완료율이 아니다 — 이름이 오해를 부른다.

    실제 계산은 `status != needs_review` 의 비율이고, 측정은 비-UUID doc_id 로
    DB 저장을 건너뛰므로 확정 단계를 타지도 않는다.
    """
    src = (_SCRIPTS / "measure_serving_fnr.py").read_text(encoding="utf-8")
    assert "not_routed_to_review_rate" in src, "오해 없는 이름이 없다"
    assert "rate_naming_note" in src, "이름의 뜻을 리포트에 적지 않았다"
    assert "auto_confirm_rate" in src, "옛 키를 지우면 과거 리포트와 대조가 끊긴다"
