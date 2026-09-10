"""문서에 **찍힌** 보안표시를 비밀관리성(M)으로 읽는가 — 그리고 언급까지 읽지는 않는가.

왜 이 경로가 있는가(2026-09-10). M 의 정본 입력은 ICD §3.2·§3.3 인데 실제 공급이 0 건이다
(전 데이터셋 432,820행 · `scripts/measure_management_input_gap.py`). 그래서 KL 이 아무것도
안 보내도 채울 수 있는 경로를 하나 열었다 — 문서에 찍힌 보안표시다. ICD §3.2 가 이미
규정한 값이고 매핑도 이미 있었다. 없던 것은 **문서에서 읽는 경로**뿐이었다.

이 시험이 지키는 것은 하나로 요약된다 — **표시와 언급을 가르는 것은 위치다.**

    머리말·꼬리말에 "대외비"가 있다     = 조직이 그렇게 관리한다는 표시
    본문에 "대외비"가 나온다            = 그 말을 하고 있을 뿐이다(보안규정 안내문)

이 구분이 없으면 2026-07-01 실측 과분류가 그대로 돌아온다 — `_MANAGEMENT_MARKING_TERMS`
를 등급 시드로 승격했더니 S3 문서가 본문에서 "사외비"를 언급했다는 이유로 TS 가 됐다.

⚠ 라벨이 없는 형식(HWP 는 rhwp 가 평문으로 덤프한다)은 구간을 모르므로 **추측하지 않는다.**
  '표시가 없다'와 '읽을 수 없다'는 다른 사실이고, 후자를 전자로 처리하면 위 과분류로 간다.
"""

from __future__ import annotations

import uuid

import pytest

from koipa.modules.m3_labeling.rule_engine import (
    _ICD_MARKING_TO_M,
    marking_from_document,
    marking_regions,
)

_BODY = "전극 코팅 공정 시험 결과. 경쟁사가 확보하지 못한 공정 노하우를 담고 있다."


def _docx(header: str = "", body: str = _BODY, footer: str = "") -> str:
    parts = []
    if header:
        parts += ["[docx section 1 header]", header]
    parts += ["[docx section 1 body]", body]
    if footer:
        parts += ["[docx section 1 footer]", footer]
    return "\n".join(parts)


# ── 표시와 언급을 가르는가 ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "stamp,expected",
    [("대외비", "confidential"), ("극비", "top_secret"), ("특급기밀", "top_secret"),
     ("기밀", "secret"), ("Confidential", "secret"), ("Restricted", "confidential")],
)
def test_stamp_in_header_is_read(stamp, expected):
    code, why = marking_from_document(_docx(header=stamp))
    assert code == expected, f"{stamp!r} 를 못 읽었다: {why}"
    assert _ICD_MARKING_TO_M[code] >= 1, "표시가 있는데 M 이 0 이면 매핑이 깨진 것이다"


def test_stamp_in_footer_is_read():
    """도장은 아래에도 찍힌다 — 머리말만 보면 절반을 놓친다."""
    code, _ = marking_from_document(_docx(footer="대외비"))
    assert code == "confidential"


def test_mention_in_body_is_not_a_marking():
    """**이 시험이 이 기능의 존재 이유다.** 본문 언급을 표시로 읽으면 과분류가 돌아온다."""
    text = _docx(body="본 규정은 사외비 문서의 취급 절차를 정한다. 대외비 표시를 해야 한다.")
    code, why = marking_from_document(text)
    assert code is None, f"본문 언급을 표시로 읽었다: {code} ({why})"


def test_unlabelled_text_is_unreadable_not_unmarked():
    """HWP 평문에는 구간이 없다 — '표시 없음'이 아니라 '읽을 수 없음'으로 답해야 한다."""
    code, why = marking_from_document("대외비\n" + _BODY)
    assert code is None
    assert why == "marking_regions_unavailable", (
        "읽을 수 없는 것을 '표시 없음'으로 처리하면 호출부가 두 사실을 못 가른다"
    )


def test_labelled_but_clean_header_says_no_marking():
    code, why = marking_from_document(_docx(header="2026년 1분기"))
    assert code is None and why == "no_marking_in_regions"


def test_longer_marking_wins_over_substring():
    """'특급기밀' 이 '기밀' 로 잡히면 등급이 내려간다 — 미탐 방향이다."""
    code, _ = marking_from_document(_docx(header="특급기밀"))
    assert code == "top_secret"


def test_regions_exclude_body():
    """구간 분해가 본문을 물고 오면 위 구분이 통째로 무너진다."""
    regions = marking_regions(_docx(header="대외비", body="본문 문장입니다."))
    joined = "\n".join(regions)
    assert "대외비" in joined and "본문 문장입니다." not in joined


# ── 서빙까지 실제로 이어지는가 ────────────────────────────────────────────────

@pytest.fixture
def _stub_pipeline_db(monkeypatch):
    from koipa.schemas import common as _common

    monkeypatch.setattr(
        _common.GradeRegistry, "get_codes", lambda *a, **k: ["TS", "S1", "S2", "S3"]
    )
    from koipa.modules.m3_labeling import pipeline as _m3

    monkeypatch.setattr(_m3, "build_rule_engine_from_db", lambda *a, **k: object())


def _classify(monkeypatch, text, predicted="S1"):
    import koipa.config as cfg
    from koipa.modules.m5_inference.pipeline import InferenceResult
    from koipa.schemas.classify import ClassifyRequest, EvaluationFactors
    from koipa.schemas.common import Grade
    from koipa.modules.m3_labeling.rule_engine import svm_levels_for_grade
    from koipa.services.classify_service import ClassifyService

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    s, v, m = svm_levels_for_grade(predicted)
    forced = InferenceResult(
        label=Grade[predicted], confidence=0.99,
        scores={g: (0.99 if g == predicted else 0.0) for g in ("TS", "S1", "S2", "S3")},
        factors=EvaluationFactors.from_factor_scores(
            {"SECRECY": float(s), "VALUE": float(v), "MANAGEMENT": float(m)}
        ),
    )
    svc = ClassifyService()
    monkeypatch.setattr(svc.inference, "_run_rule_fallback", lambda *a, **k: forced)
    return svc.classify(ClassifyRequest(doc_id=str(uuid.uuid4()), content=text))


def test_serving_fills_management_from_a_stamp(monkeypatch, _stub_pipeline_db):
    """메타데이터가 **하나도 없어도** 표시가 있으면 M 이 채워진다 — 이것이 이 경로의 목적이다."""
    res = _classify(monkeypatch, _docx(header="대외비"))
    assert res.evaluation_factors.management == 1.0, res.warnings
    assert any("document_marking" in w for w in res.warnings), res.warnings


def test_serving_does_not_change_the_grade(monkeypatch, _stub_pipeline_db):
    """우리가 읽어낸 값은 KL 이 단언한 값과 근거의 무게가 다르다 — 등급 floor 를 주지 않는다."""
    res = _classify(monkeypatch, _docx(header="극비"))
    assert res.label.value == "S1", "문서에서 읽은 표시가 등급을 올렸다"
    assert res.status == "needs_review", "그렇다고 무음 자동확정으로 두면 안 된다"


def test_serving_ignores_a_body_mention(monkeypatch, _stub_pipeline_db):
    res = _classify(monkeypatch, _docx(body=_BODY + " 본 자료는 대외비 규정에 따라 관리한다."))
    assert res.evaluation_factors.management == 0.0
    assert res.status != "needs_review", f"본문 언급으로 검수를 만들었다: {res.warnings}"


def test_real_docx_header_stamp_survives_extraction(tmp_path):
    """**파일 → 추출 → 표시 판독**이 실제로 이어지는가.

    왜 이 시험이 꼭 있어야 하는가(2026-09-10). 이 경로는 우리 데이터로는 한 건도 검증할 수
    없다 — 전 데이터셋 430,387 문서가 `marking_regions_unavailable` 이고(라벨 없는 텍스트
    덤프), 저장소의 DOCX 30개는 열어 보니 **header/footer 파트 자체가 없다**(python-docx 로
    만든 합성본이라 머리말을 안 넣었다). 즉 "안 걸린다"는 관측만으로는 기능이 동작하는지
    알 수 없다. 그래서 도장이 찍힌 문서를 여기서 만들어 태운다.

    이 시험이 깨지면 추출기가 머리말 구간 라벨을 바꾼 것이고, 그 순간 이 경로는 조용히
    죽는다(경고도 없이 `marking_regions_unavailable` 만 늘어난다).
    """
    docx = pytest.importorskip("docx", reason="python-docx 없으면 파일을 만들 수 없다")

    from koipa.modules.m2_preprocess.extractor import extract

    doc = docx.Document()
    doc.sections[0].header.paragraphs[0].text = "대외비"
    doc.add_paragraph(_BODY)
    path = tmp_path / "stamped.docx"
    doc.save(str(path))

    result = extract(path)
    text = getattr(result, "text", "") or ""
    assert "대외비" in text, f"추출이 머리말을 통째로 흘렸다: {getattr(result, 'error', None)}"

    regions = marking_regions(text)
    assert regions, "추출은 됐는데 구간 라벨이 없다 — 라벨 형식이 바뀌면 이 경로가 죽는다"
    code, why = marking_from_document(text)
    assert code == "confidential", f"도장을 못 읽었다: {code} ({why})"


def test_body_only_docx_is_not_marked(tmp_path):
    """머리말 없는 문서를 '표시 있음'으로 읽지 않는다 — 저장소의 DOCX 30개가 전부 이 모양이다."""
    docx = pytest.importorskip("docx")

    from koipa.modules.m2_preprocess.extractor import extract

    doc = docx.Document()
    doc.add_paragraph(_BODY + " 본 자료는 대외비 규정에 따라 관리한다.")
    path = tmp_path / "plain.docx"
    doc.save(str(path))

    code, _why = marking_from_document(getattr(extract(path), "text", "") or "")
    assert code is None, "본문 언급만 있는 문서를 표시로 읽었다"


def test_metadata_wins_over_the_document(monkeypatch, _stub_pipeline_db):
    """KL 이 값을 주면 그것이 권위다 — 문서에서 읽은 값이 덮으면 안 된다."""
    import koipa.config as cfg
    from koipa.modules.m3_labeling.rule_engine import management_from_metadata_dict

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    # 메타데이터가 M 을 주면 문서 경로는 아예 진입하지 않는다(호출부 분기 계약).
    state, level, _ = management_from_metadata_dict({"access_scope": "all_employees"})
    assert state == "proven_absent" and level == 0, (
        "메타데이터가 M 을 확정하면 문서에서 읽은 표시로 덮지 않는다"
    )
