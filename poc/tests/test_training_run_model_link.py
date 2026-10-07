"""학습 작업 기록에 **어느 모델이 나왔는지** 남는지 고정한다.

왜(실측 2026-08-16, KL 서버 223). 재학습이 8/8 이후 처음으로 완주해 모델 `v-24c7c02c` 가
나왔고 `tb_model_versions` 에 정상 등록됐다. 그런데 `GET /train/jobs` 응답의
`model_version` 은 여전히 null 이었다.

DB 를 열어 보니 연결이 **한 방향뿐**이었다.

    tb_model_versions.training_run_id   기록됨    (모델 -> 학습작업)
    tb_training_runs.model_version      비어 있음  (학습작업 -> 모델)

컬럼은 있고(FK 로 tb_model_versions 참조) 값만 아무도 안 채웠다. `mark_completed()` 의
시그니처에 그 인자가 없었다. 그래서 화면과 API 에서 "이 학습이 무슨 모델을 만들었는가" 를
볼 수 없었다 - 데이터가 없는 게 아니라 반대편에서 뒤져야만 알 수 있는 상태였다.

⚠ 이 결함 때문에 나는 한 번 잘못 보고했다. "학습이 모델을 만들어낸 적이 없다" 고 적었는데
  실제로는 산출물이 있었다(8/8 에 3개). job 기록만 비어 있었던 것이다.
"""
from __future__ import annotations

import inspect
import uuid

from koipa.repositories.training_repo import TrainingRepo


def test_mark_completed_accepts_model_version():
    """완료 기록 함수가 모델 버전을 받아야 한다 - 안 받으면 연결할 방법이 없다."""
    params = inspect.signature(TrainingRepo.mark_completed).parameters
    assert "model_version" in params, f"mark_completed 가 model_version 을 안 받는다: {list(params)}"


def test_worker_passes_model_version_on_completion():
    """워커가 등록 결과의 version_id 를 완료 기록으로 넘겨야 한다."""
    from koipa.workers import tasks

    import ast as _ast

    # ⚠ 종전에는 소스를 문자열로 잘라 봤다(앞 600자·뒤 400자). 두 번 깨졌다.
    #     · 사이에 주석이 늘면 창 밖으로 밀려난다
    #     · 주석 안의 `status="completed"` 를 먼저 잡는다 (2026-08-16 실제로 그랬다)
    #   그래서 구문 트리에서 **실제 호출**을 찾는다 - 주석은 트리에 없다.
    tree = _ast.parse(inspect.getsource(tasks))
    completed_calls = [
        node for node in _ast.walk(tree)
        if isinstance(node, _ast.Call)
        and getattr(node.func, "id", "") == "_mark_training_run"
        and any(kw.arg == "status" and isinstance(kw.value, _ast.Constant)
                and kw.value.value == "completed" for kw in node.keywords)
    ]
    assert completed_calls, "status=\"completed\" 로 기록하는 호출이 없다"
    for call in completed_calls:
        names = {kw.arg for kw in call.keywords}
        assert "model_version" in names, (
            f"완료 처리에 model_version 을 안 넘긴다: {sorted(n for n in names if n)}"
        )
    # 등록 결과(version_id)에서 온 값이어야 한다.
    assert "version_id" in inspect.getsource(tasks), "등록 결과(version_id)를 참조하지 않는다"


def test_mark_completed_does_not_clear_existing_link():
    """model_version 없이 다시 부르면 기존 연결을 지우면 안 된다.

    완료 처리는 재시도될 수 있다. 두 번째 호출이 첫 번째가 남긴 연결을 지우면
    화면이 다시 비어버린다.
    """
    src = inspect.getsource(TrainingRepo.mark_completed)
    assert "if model_version is not None" in src, (
        "None 일 때 덮어쓰지 않는 가드가 없다 - 재호출로 연결이 끊긴다"
    )


def test_uuid_string_is_accepted():
    """등록 결과의 version_id 는 문자열이다. 워커가 UUID 로 바꿔 넘겨야 한다."""
    from koipa.workers import tasks

    src = inspect.getsource(tasks._mark_training_run)
    assert "UUID(str(" in src or "_uuid.UUID" in src, (
        "문자열 version_id 를 UUID 로 변환하지 않는다"
    )
    # 변환 실패가 학습 완료 기록 자체를 막으면 안 된다.
    assert "except" in src, "변환 실패를 삼키지 않으면 완료 기록이 통째로 실패한다"


def test_uuid_type_is_valid():
    """전달되는 값이 실제 UUID 로 파싱 가능한 형태여야 한다(형식 확인)."""
    assert uuid.UUID(str(uuid.uuid4()))
