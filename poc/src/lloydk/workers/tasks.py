"""Celery tasks — 비동기 분류·합성·학습 트리거.

API → Redis 큐 → Worker가 무거운 작업을 수행. Redis 없으면 task는 동기 호출용 함수로도 사용 가능.
"""

from __future__ import annotations

from lloydk.workers.celery_app import celery_app


@celery_app.task(name="lloydk.classify_async")
def classify_async(payload: dict) -> dict:
    from lloydk.schemas.classify import ClassifyRequest
    from lloydk.services.classify_service import ClassifyService

    req = ClassifyRequest(**payload)
    svc = ClassifyService.get_instance()
    result = svc.classify(req)
    return result.model_dump(mode="json")


@celery_app.task(name="lloydk.synthesize_batch")
def synthesize_batch(grade: str, count: int, domain: str = "mixed") -> list[dict]:
    from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator

    gen = SyntheticDocGenerator()
    docs = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=count))
    return [
        {
            "title": d.title,
            "body": d.body,
            "target_grade": d.target_grade,
            "domain": d.domain,
            "llm_provider": d.llm_provider,
            "cost_usd": d.usage.cost_usd if d.usage else 0.0,
        }
        for d in docs
    ]


@celery_app.task(name="lloydk.train_classifier")
def train_classifier_task(spec_kwargs: dict | None = None) -> dict:
    from lloydk.modules.m4_training.trainer import TrainSpec, train_classifier

    spec = TrainSpec(**(spec_kwargs or {}))
    report = train_classifier(spec)
    return report.__dict__
