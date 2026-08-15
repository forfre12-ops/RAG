"""ICD 3필드가 **업로드 경로에서 분류까지** 살아서 도착하는지 고정한다.

왜(실측 2026-08-15). 사용자 지적이 정확했다 — "문서만 업로드되지 메타값은 주게 되어
있지 않다". 확인하니 두 군데가 끊겨 있었다.

    POST /documents   source_type 만 받고 security_marking·access_scope 자리가 없었다
    _effective_metadata  source_type 이 있으면 **거기서 바로 반환**해 나머지 두 필드를
                      DB 에서 영영 안 읽었다. 마스트헤드 분기도 같은 자리에서 끊겼다.

그래서 파일로 올린 문서는 관리성 근거를 줄 방법도, 저장돼 있어도 쓸 방법도 없었다.

이게 왜 치명적인가. 관리성은 본문에서 관측되지 않는 축이다(실측: 실문서 업무문서
17~21% 만 관리 표시 보유). 그리고 정본에서 S1 은 (2,2,0) 하나뿐이라 M 이 0 으로
확정되지 않으면 **S1 이 구조적으로 도달 불가**다 — 봉인 판정면의 28%가 그래서 전부
TS 로 갔다. 이 경로가 그 자물쇠의 열쇠다.
"""
from __future__ import annotations

import pytest

from koipa.schemas.classify import ClassifyRequest
from koipa.services.classify_service import ClassifyService

ICD_FIELDS = ("source_type", "security_marking", "access_scope")


@pytest.fixture()
def svc():
    return ClassifyService()


def test_all_three_fields_survive_to_pipeline(svc):
    """요청에 실린 3필드가 잘리지 않고 그대로 간다."""
    md = {"source_type": "internal", "security_marking": "none",
          "access_scope": "all_employees"}
    got = svc._effective_metadata(
        ClassifyRequest(doc_id="x", content="본문", metadata=md), content="본문"
    )
    for k in ICD_FIELDS:
        assert got.get(k) == md[k], f"{k} 가 유실됐다: {got}"


def test_source_type_alone_does_not_short_circuit(svc):
    """source_type 만 있다고 조기 반환하면 관리성 두 필드를 DB 에서 못 읽는다.

    종전 버그가 정확히 이것이었다 — `if meta.get("source_type"): return meta`.
    """
    got = svc._effective_metadata(
        ClassifyRequest(doc_id="x", content="본문", metadata={"source_type": "internal"}),
        content="본문",
    )
    # DB 가 없으면 채워지지 않는 것이 정상이지만, source_type 은 보존돼야 한다.
    assert got.get("source_type") == "internal"


def test_upload_endpoint_accepts_all_three():
    """POST /documents 폼에 3필드 자리가 있어야 한다.

    /classify 는 metadata dict 로 받지만 파일 업로드는 별도 경로다. 여기 자리가
    없으면 업로드 문서는 관리성을 영영 못 받는다.
    """
    import inspect

    from koipa.api.documents import upload_document

    params = set(inspect.signature(upload_document).parameters)
    missing = [f for f in ICD_FIELDS if f not in params]
    assert not missing, f"업로드 폼에 없는 ICD 필드: {missing}"


def test_ingest_service_accepts_all_three():
    """서비스가 받아서 metadata_ 에 저장해야 분류 때 읽힌다."""
    import inspect

    from koipa.services.document_ingestion_service import DocumentIngestionService

    params = set(inspect.signature(DocumentIngestionService.ingest).parameters)
    missing = [f for f in ICD_FIELDS if f not in params]
    assert not missing, f"ingest() 가 안 받는 ICD 필드: {missing}"


def test_management_mapping_is_reachable_from_metadata():
    """3필드가 도착하면 관리성이 실제로 확정되는가 — S1 자물쇠가 열리는지."""
    from koipa.modules.m3_labeling.rule_engine import (
        grade_from_svm,
        management_from_metadata_dict,
    )

    state, level, _ = management_from_metadata_dict({"access_scope": "all_employees"})
    assert (state, level) == ("proven_absent", 0)
    # 비공지성·가치가 완전할 때 S1 이 나오는 유일한 경로다.
    assert grade_from_svm(2, 2, level) == "S1"
    # 메타데이터가 없으면 보수적 완성이 2 로 채워 TS 가 된다 — S1 은 안 나온다.
    assert grade_from_svm(2, 2, 2) == "TS"


def test_analyze_endpoint_accepts_all_three():
    """/documents/analyze 는 **시연·연동이 실제로 타는 경로**다.

    여기 자리가 없으면 콘솔로 올린 문서는 출처도 관리성도 줄 수 없다 — /classify 는
    dict 로 받는데 이쪽만 빠져 있었다.
    """
    import inspect

    from koipa.api.documents import analyze_document

    params = set(inspect.signature(analyze_document).parameters)
    missing = [f for f in ICD_FIELDS if f not in params]
    assert not missing, f"analyze 에 없는 ICD 필드: {missing}"


def test_analyze_does_not_pass_empty_strings_as_metadata():
    """빈 값을 실어 보내면 'unknown' 과 '명시적으로 없음' 이 구분되지 않는다.

    access_scope="" 가 그대로 흘러가면 관리성 매핑이 미지정 값을 만나 판정이 뒤집힐 수
    있다. 폼 미입력은 **키 자체가 없어야** 한다.
    """
    import inspect

    src = inspect.getsource(
        __import__("koipa.api.documents", fromlist=["x"]).analyze_document
    )
    assert "if v}" in src, "빈 값 필터가 없다 — 미입력 폼이 빈 문자열로 전달된다"
