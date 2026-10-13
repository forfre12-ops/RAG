"""모든 koipa.* Celery task 가 task_routes 에 명시적으로 있는가 (2026-09-30).

classify_batch 가 신설 당시 task_routes·task_annotations 둘 다에서 빠져 있었다 —
라우팅이 없으면 기본 'celery' 큐로 가고(동작은 하지만 의도치 않은 분리), 시간제한
annotation 이 없으면 전역 기본값(소프트15분·하드20분)을 물려받는다. 고객사가 "1000건
업로드되냐"고 물어 재다가 발견했다: 1000건 배치에서 문서가 크면(과대문서 가드 바로
아래 크기) 최악 약 6.8시간까지 걸릴 수 있는데, 20분에 걸리면 이미 처리된 결과까지
전부 버려진다(배치는 끝에 한 번만 JobStore 에 쓴다).

이 시험은 새 task 를 추가하고 라우팅을 깜빡하는 같은 종류의 버그를 구조적으로 막는다 —
"어느 task 가 몇 번째 줄에 있나"가 아니라 "등록된 koipa.* task 전부가 명시적으로
라우팅돼 있나"를 묻는다.
"""
from __future__ import annotations

from koipa.workers.celery_app import celery_app


def _registered_koipa_task_names() -> set[str]:
    return {name for name in celery_app.tasks if name.startswith("koipa.")}


def test_every_registered_task_has_an_explicit_route():
    routes = celery_app.conf.task_routes
    tasks = _registered_koipa_task_names()
    assert tasks, "koipa.* task 가 하나도 등록돼 있지 않다 — import 가 깨졌을 수 있다"
    missing = sorted(tasks - set(routes))
    assert not missing, (
        f"task_routes 에 라우팅이 없는 task: {missing} — 기본 'celery' 큐로 조용히 떨어진다. "
        "celery_app.py 의 task_routes 에 큐를 명시할 것."
    )


def test_classify_batch_has_a_batch_scale_time_limit():
    """1000건 배치의 실측 최악 추정(≈6.8시간)을 안전하게 덮는 시간제한이어야 한다 —
    전역 기본값(900/1200초)을 그대로 쓰면 큰 배치가 도중에 죽고 결과가 전부 버려진다."""
    ann = celery_app.conf.task_annotations.get("koipa.classify_batch")
    assert ann is not None, "classify_batch 전용 task_annotations 가 없다 — 전역 기본값(20분)을 물려받는다"
    assert ann["soft_time_limit"] >= 21600, "최소 6시간은 돼야 1000건 최악 추정(≈6.8h)을 안전하게 덮는다"
    assert ann["time_limit"] > ann["soft_time_limit"]
