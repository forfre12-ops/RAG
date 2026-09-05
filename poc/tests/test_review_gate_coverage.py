"""검수 라우팅 게이트가 **하나도 빠짐없이** 시험을 갖는가.

왜 이 파일이 있는가(2026-09-06). `classify_service` 는 문서를 검수 큐로 보내는 게이트를
14개 갖고 있다. 그 중 경고 토큰으로 걸리는 9개를 세어 보니 **둘은 시험이 0건**이었다:

    body_below_classifiable_threshold   본문이 분류 가능 하한 미만
    metadata-management-conflict        전 임직원 열람(M=0)인데 고등급 예측

게이트가 안 열리면 그 문서는 **자동 확정으로 나간다** = 무음 미탐. 이 사업이 1차로 막아야
하는 실패다. 그래서 두 게이트에 시험을 붙이고, 다음에 또 빠지지 않도록 **소스에서 게이트를
세어 등록부와 대조하는 검사기**를 같이 둔다.
"""
from __future__ import annotations

import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "koipa"
_CLASSIFY = _SRC / "services" / "classify_service.py"


# ── 등록부 — 게이트마다 어느 시험이 지키는가 ────────────────────────────────
#   소스에 게이트를 추가하면 아래 검사기가 실패한다. 그때 여기 한 줄을 더한다.
GATE_TESTS: dict[str, str] = {
    "cap-conflict": "test_classify_gates_scenario.py",
    "sparse-evidence": "test_classify_gates_scenario.py",
    "abbrev-only-escalation": "test_m5_inference.py",
    "metadata-access-conflict": "test_safety_gates_flag_on.py",
    "s2-underclass-risk": "test_classify_gates_scenario.py",
    "gate-fail-open": "test_pipeline_gate_fail_open.py",
    "미탐 위험": "test_classifier_fallback_gate.py",
    # 2026-09-06 신규 — 이 파일이 지킨다
    "body_below_classifiable_threshold": "test_review_gate_coverage.py",
    "metadata-management-conflict": "test_review_gate_coverage.py",
}


def _source_gate_tokens() -> set[str]:
    """`status = "needs_review"` 로 가는 조건에서 경고 토큰을 뽑는다.

    문자열 리터럴이 없는 게이트(저신뢰 임계·review_flagged·합의/2차의견/브레이크)는 여기
    안 잡힌다 — 그쪽은 각자 전용 시험이 있다. 이 검사기가 세는 분모는 **토큰 게이트**다.
    """
    lines = _CLASSIFY.read_text("utf-8").split("\n")
    tokens: set[str] = set()
    for i, ln in enumerate(lines):
        if not re.search(r'status\s*=\s*"needs_review"', ln):
            continue
        for j in range(i, max(0, i - 12), -1):
            if re.match(r"\s*(if|elif)\b", lines[j]):
                cond = " ".join(x.strip() for x in lines[j:i])
                tokens.update(re.findall(r'"([^"]{4,60})"', cond))
                break
    return {t for t in tokens if t != "needs_review"}


def test_every_warning_gate_has_a_named_test():
    """소스의 게이트 토큰 == 등록부의 키.

    새 게이트를 넣고 시험을 안 붙이면 여기서 걸린다. 게이트가 늘어나는 것이 문제가 아니라
    **시험 없이** 늘어나는 것이 문제다.
    """
    found = _source_gate_tokens()
    missing = found - set(GATE_TESTS)
    stale = set(GATE_TESTS) - found
    assert not missing, "시험 등록 안 된 검수 게이트: %s" % sorted(missing)
    assert not stale, "소스에서 사라진 게이트가 등록부에 남았다: %s" % sorted(stale)


def test_registry_points_at_files_that_exist():
    """등록부가 없는 파일을 가리키면 '덮여 있다'는 말이 거짓이 된다."""
    here = pathlib.Path(__file__).parent
    for token, fname in GATE_TESTS.items():
        assert (here / fname).exists(), "%s → %s 가 없다" % (token, fname)


# ── ① 본문 하한 게이트 — 형식을 가리지 않는가 ──────────────────────────────
def _substantive(text: str) -> int:
    from koipa.modules.m2_preprocess.extractor import _substantive_len

    return _substantive_len(text)


def test_thin_body_guard_covers_every_extractor_not_just_hwp():
    """가드가 `extract()` 에 있어야 한다 — 형식별 함수에 흩어 두면 빠뜨린다.

    [2026-09-06] 실측: 추출기 10개 중 이 가드를 가진 것은 `_extract_hwp` **하나뿐**이었다.
    docx·pptx·pdf·xls 은 본문이 얇게 나와도 경고 없이 통과했고, 그런 문서는 전부 S3 로
    떨어진다 = 무음 미탐. 게다가 `legacy_office.py` 머리말은 이 가드가 .doc/.ppt 를
    지켜준다고 **적어 두고 있었다.** 그 경로엔 가드가 없었다.
    """
    src = (_SRC / "modules" / "m2_preprocess" / "extractor.py").read_text("utf-8")
    assert "_guard_thin_body(_dispatch_extract(" in src, (
        "공통 가드가 extract() 에서 빠졌다 — 형식별로 흩어지면 새 추출기에서 또 빠진다"
    )


@pytest.mark.parametrize("suffix", [".txt", ".md", ".csv"])
def test_thin_body_routes_to_review_for_plain_formats(tmp_path, suffix):
    """HWP 가 아닌 형식도 얇은 본문이면 경고가 붙는다."""
    from koipa.modules.m2_preprocess.extractor import extract

    p = tmp_path / ("thin" + suffix)
    p.write_text("￼ ￼\n\n   ", encoding="utf-8")
    res = extract(p)
    assert any("body_below_classifiable_threshold" in w for w in (res.warnings or [])), (
        "%s 얇은 본문이 경고 없이 통과했다 — 무음으로 S3 가 된다" % suffix
    )


def test_normal_document_is_not_flagged(tmp_path):
    """짧지만 정상인 문서까지 막으면 안 된다 — 검수 큐가 쓰레기로 찬다."""
    from koipa.modules.m2_preprocess.extractor import extract

    p = tmp_path / "ok.txt"
    p.write_text(
        "영업비밀에 해당하는 공정 조건과 수율 데이터를 담은 내부 기술 검토 문서입니다.",
        encoding="utf-8",
    )
    res = extract(p)
    assert not any("body_below_classifiable_threshold" in w for w in (res.warnings or []))


def test_placeholder_characters_do_not_count_as_content():
    """U+FFFC(객체 자리표시자)·BOM·zero-width 는 글자 수만 늘리고 신호를 안 준다.

    종전 가드가 `not text.strip()` 만 보다가 placeholder 한 글자에 속았던 자리다.
    """
    assert _substantive("￼" * 100 + "﻿" + "​" * 50) == 0
    assert _substantive("  \n\t  ") == 0
    assert _substantive("영업비밀") == 4


def test_guard_does_not_erase_existing_warnings():
    """가드가 앞선 경고를 덮어쓰면 추출 실패 사유가 사라진다."""
    from koipa.modules.m2_preprocess.extractor import ExtractResult, _guard_thin_body

    res = ExtractResult(text="", method="pdf", quality=0.0, warnings=["pdf_pages_empty"])
    out = _guard_thin_body(res)
    assert "pdf_pages_empty" in out.warnings
    assert "body_below_classifiable_threshold" in out.warnings


def test_guard_is_idempotent_for_hwp():
    """HWP 는 자기 경로에서도 한 번 붙인다 — 두 번 붙으면 안 된다(_warn_once)."""
    from koipa.modules.m2_preprocess.extractor import ExtractResult, _guard_thin_body

    res = ExtractResult(
        text="", method="rhwp", quality=0.5,
        warnings=["body_below_classifiable_threshold"],
    )
    out = _guard_thin_body(res)
    assert out.warnings.count("body_below_classifiable_threshold") == 1


def test_thin_body_gate_actually_routes_to_review():
    """경고를 내는 것으로 끝나면 소용없다 — classify_service 가 그 토큰을 읽어야 한다."""
    assert "body_below_classifiable_threshold" in _source_gate_tokens()


# ── ② M=0 충돌 게이트 — 전 임직원 열람인데 고등급 ──────────────────────────
@pytest.fixture
def _stub_pipeline_db(monkeypatch):
    """InferencePipeline 생성이 PG 에 매달리지 않게 스텁(lite 실행)."""
    from koipa.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )
    from koipa.modules.m3_labeling import pipeline as _m3

    monkeypatch.setattr(_m3, "build_rule_engine_from_db", lambda *a, **k: object())


def _force_rule(pipe, monkeypatch, grade):
    """룰 폴백을 고정한다 — 모델 없이 게이트만 본다.

    factors 를 반드시 넣는다. M 충돌 게이트는 `result.factors is not None` 안에 있어서
    요소가 없으면 **블록 전체를 건너뛴다** — 게이트가 안 도는데 시험은 통과한 것처럼 보인다.
    (2026-09-06 실측: factors 없이 쓴 첫 판이 그래서 빈 warnings 를 받았다.)
    """
    from koipa.modules.m5_inference.pipeline import InferenceResult
    from koipa.schemas.classify import EvaluationFactors

    res = InferenceResult(
        label=grade, confidence=0.9,
        scores={"TS": 0.0, "S1": 0.0, "S2": 0.9, "S3": 0.1},
        factors=EvaluationFactors.from_factor_scores(
            {"SECRECY": 2.0, "VALUE": 2.0, "MANAGEMENT": 2.0}
        ),
    )
    monkeypatch.setattr(pipe, "_run_rule_fallback", lambda *a, **k: res)


def test_all_employee_access_with_high_grade_routes_to_review(monkeypatch, _stub_pipeline_db):
    """전 임직원이 볼 수 있으면 비밀관리성(M) 요건이 깨진다 — 고등급 예측과 충돌한다.

    등급을 **내리지 않는다.** 하향은 미탐 방향이라 사람이 본다(ICD §3.3).
    """
    from koipa import config as cfg
    from koipa.modules.m5_inference.pipeline import InferencePipeline
    from koipa.schemas.common import Grade

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    pipe = InferencePipeline()
    _force_rule(pipe, monkeypatch, Grade.S1)
    res = pipe.run("내부 기술 문서", metadata={"access_scope": "all_employees"})

    assert any("metadata-management-conflict" in w for w in res.warnings), res.warnings
    assert res.label == Grade.S1, "등급을 무음으로 내리면 안 된다 — 검수 신호로만 낸다"


def test_all_employee_access_with_low_grade_is_not_a_conflict(monkeypatch, _stub_pipeline_db):
    """S2 이하는 충돌이 아니다 — 전 직원 열람과 모순되지 않는다."""
    from koipa import config as cfg
    from koipa.modules.m5_inference.pipeline import InferencePipeline
    from koipa.schemas.common import Grade

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    pipe = InferencePipeline()
    _force_rule(pipe, monkeypatch, Grade.S3)
    res = pipe.run("사내 공지", metadata={"access_scope": "all_employees"})
    assert not any("metadata-management-conflict" in w for w in res.warnings)


def test_management_conflict_gate_is_read_by_classify_service():
    """파이프라인이 경고를 내도 서비스가 안 읽으면 검수로 안 간다."""
    assert "metadata-management-conflict" in _source_gate_tokens()
