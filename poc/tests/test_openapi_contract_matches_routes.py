"""제출 API 규약서(doc/03_openapi_koipa_kl.yaml)와 실제 라우트가 어긋나지 않게 잠근다.

왜(2026-08-19). 규약서와 코드를 실제로 대조해 보니 양쪽으로 어긋나 있었다 —
규약서에만 있는 경로 1개(`/golden/candidates/actual-intake.html`, 코드에 없음)와
코드에만 있는 경로 5개. 문서↔문서 점검으로는 이런 것이 안 잡힌다.
교훈은 하나다: **문서에 적힌 식별자를 소스에 직접 대조할 것.**

⚠ 학습 라우터는 **조건부 마운트**다. app.py 는 enable_training 또는
  enable_incremental_retrain 일 때만 /train 계열을 등록한다(순수 추론 노드에는 없다).
  그래서 실행 중인 앱만 보면 /train 3개가 "규약서에만 있는 유령 계약" 으로 잘못 잡힌다 —
  실제로 처음 셀 때 그렇게 나왔다. 계약서에는 있어야 하는 경로이므로 여기서 더해 준다.

이 시험이 하는 일은 하나다: **새 엔드포인트를 만들고 규약서에 안 적으면 실패한다.**
지금 알려진 차이는 아래 두 유예 목록에 사유와 함께 적어 두었다.
해소되면 목록에서 지우면 된다(남아 있어도 시험을 방해하지는 않는다 —
병행 작업 중인 다른 커밋을 빨갛게 만들지 않으려고 일부러 그렇게 뒀다).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SPEC = Path(__file__).resolve().parents[2] / "doc" / "03_openapi_koipa_kl.yaml"
PREFIX = "/api/v1"

# 규약서에는 있는데 코드에 없는 것.
KNOWN_PHANTOMS = {
    # 2026-08-19 현재 HEAD 규약서에 남아 있다. 워킹트리에서 이미 제거되는 중이라
    # 그 커밋이 들어오면 이 항목을 지우면 된다.
    "/golden/candidates/actual-intake.html",
}

# 코드에는 있으나 규약서에 없는 것 — 없애거나 문서에 넣기로 정하기 전까지의 유예 목록.
# ⚠ 새 경로를 여기 추가하는 것으로 시험을 통과시키지 말 것. 사유가 없으면 규약서에 적는 게 맞다.
KNOWN_GAPS = {
    # RAG 질의응답 모듈(api/answer.py:130). 같은 파일의 POST /answer(163행)는 규약서에
    # 있는데 이것만 없다 — 기준이 엇갈린다. 넣을지 뺄지 정해야 한다.
    "/rag/search",
    # 데모 콘솔 전용 **파괴적 물리삭제**(api/admin.py:481). OpenAPI 에는 노출되는데
    # 계약서에는 없다. 문서에 넣든지 include_in_schema=False 로 감추든지 정해야 한다
    # (/metrics-prom 은 후자를 쓴다).
    "/admin/demo/purge",
    # 관제 대시보드 요약(api/metrics.py:35). 단순 누락으로 보인다.
    "/dashboard/summary",
    # 2026-08-19 현재 워킹트리 규약서에는 추가되는 중이다. 그 커밋이 들어오면 지운다.
    "/golden/candidates/{doc_id}/provenance",
    "/golden/jobs/{job_id}/signoff/preflight",
}


def _live_paths() -> set[str]:
    """실제로 서빙되는 경로 + 조건부 마운트되는 학습 경로."""
    from koipa.api import training as training_api
    from koipa.api.app import app

    paths = set(app.openapi()["paths"])
    # 배포 프로파일에 따라 안 붙는 라우터 — 계약서 기준으로는 존재하는 경로다.
    paths.update(PREFIX + r.path for r in training_api.router.routes if hasattr(r, "path"))
    return {p[len(PREFIX):] for p in paths if p.startswith(PREFIX)}


def _spec_paths() -> set[str]:
    """규약서의 paths: 블록. 서버 접두사(/api/v1)는 servers: 에 있어 경로에는 없다."""
    text = SPEC.read_text(encoding="utf-8")
    body = text.split("\npaths:", 1)[1].split("\ncomponents:", 1)[0]
    return set(re.findall(r"^  (/[^:]*):", body, re.M))


@pytest.fixture(scope="module")
def sides() -> tuple[set[str], set[str]]:
    if not SPEC.exists():
        pytest.skip(f"규약서를 찾을 수 없다: {SPEC}")
    return _live_paths(), _spec_paths()


def test_spec_has_no_new_phantom_paths(sides):
    """규약서에 있는데 코드에 없으면 발주처가 없는 API 를 부른다."""
    live, spec = sides
    phantom = sorted(spec - live - KNOWN_PHANTOMS)
    assert not phantom, f"규약서에만 있는 경로(코드에 없음): {phantom}"


def test_every_route_is_in_the_contract(sides):
    """새 엔드포인트를 만들고 규약서에 안 적으면 여기서 걸린다."""
    live, spec = sides
    missing = sorted(live - spec - KNOWN_GAPS)
    assert not missing, (
        f"코드에는 있는데 규약서에 없는 경로: {missing}\n"
        "규약서에 적거나, 사유를 달아 KNOWN_GAPS 에 넣어라(사유 없이 넣지 말 것)."
    )


def test_training_routes_are_in_the_contract(sides):
    """학습 라우터는 배포 프로파일에 따라 안 붙지만 계약에는 있어야 한다(FUN-004)."""
    _, spec = sides
    for p in ("/train", "/train/jobs", "/train/jobs/{train_job_id}"):
        assert p in spec, f"규약서에 {p} 가 없다"


def test_the_check_would_catch_a_new_undocumented_route(sides):
    """검사가 실제로 잡는지 — 없는 경로를 코드 쪽에 넣어 확인한다(파일은 안 건드린다)."""
    live, spec = sides
    fake = live | {"/completely/new/route"}
    assert sorted(fake - spec - KNOWN_GAPS) == ["/completely/new/route"]
