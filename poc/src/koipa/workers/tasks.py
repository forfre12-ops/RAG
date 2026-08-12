"""Celery tasks — 비동기 분류·합성·학습 트리거.

API → Redis 큐 → Worker가 무거운 작업을 수행. Redis 없으면 task는 동기 호출용 함수로도 사용 가능.

부분 실패 처리 (2026-05 추가):
- classify_async / synthesize_batch: bind=True + max_retries=3 + 지수 백오프
- 모든 retry 실패 시 보상 트랜잭션: 이미 처리된 결과를 status="partial"로 JobStore에 기록
"""

from __future__ import annotations

import logging
from typing import Any

from koipa.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _record_job_done(
    job_id: str | None,
    *,
    results: list[dict],
    completed: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Best-effort JobStore success transition for Celery worker paths."""
    if not job_id:
        return
    try:
        import uuid as _uuid

        from koipa.services.job_store import get_default_store

        fields: dict[str, Any] = {
            "status": "done",
            "completed": len(results) if completed is None else completed,
            "results": results,
        }
        if extra:
            fields.update(extra)
        get_default_store().update(_uuid.UUID(job_id), **fields)
    except Exception:  # noqa: BLE001
        logger.exception("job success update failed: job_id=%s", job_id)


def _record_compensation(job_id: str | None, partial_results: list[dict], reason: str) -> None:
    """모든 retry 실패 시 보상 트랜잭션 — JobStore에 partial 기록.

    job_id가 없으면 (단발 호출) JobStore 갱신 생략 — 호출자 책임.
    JobStore 접근 실패는 워커를 죽이지 않고 로깅만.
    """
    if not job_id:
        return
    try:
        import uuid as _uuid

        from koipa.services.job_store import get_default_store
        store = get_default_store()
        store.update(
            _uuid.UUID(job_id),
            status="partial",
            results=partial_results,
            error=reason,
            failed=1,
        )
        logger.warning(
            "compensation recorded: job_id=%s partial_count=%d reason=%s",
            job_id, len(partial_results), reason,
        )
    except Exception:  # noqa: BLE001
        logger.exception("compensation record failed: job_id=%s", job_id)


@celery_app.task(
    name="koipa.classify_async",
    bind=True,
    max_retries=2,
    default_retry_delay=1,
)
def classify_async(
    self: Any, payload: dict, job_id: str | None = None, callback_url: str | None = None
) -> dict:
    """단일 문서 분류 비동기 task.

    Retry: 일시 예외 → self.retry(countdown=2**attempts).
    모든 retry 실패 → 보상 트랜잭션 (JobStore에 partial 상태 기록) 후 예외 재발생.
    callback_url 이 있으면 최종 완료/실패 시 결과 webhook 을 outbox 로 발사(신뢰전달). 운영(celery)
    경로는 그동안 callback_url 을 떼어내고 워커도 발사하지 않아 webhook 이 영원히 안 울렸다 — 이제
    submit_async 가 callback_url 을 워커로 넘기고 여기서 발사한다(in-process 경로와 상호배타 = 중복 없음).
    """
    from koipa.schemas.classify import ClassifyRequest
    from koipa.services.classify_service import ClassifyService

    try:
        req = ClassifyRequest(**payload)
        svc = ClassifyService.get_instance()
        result = svc.classify(req)
        result_json = result.model_dump(mode="json")
        _record_job_done(job_id, results=[result_json], completed=1)
        _publish_callback_webhook(callback_url, {"job_id": job_id, "status": "done", "results": [result_json]})
        return result_json
    except Exception as exc:  # noqa: BLE001
        attempts = self.request.retries
        max_r = self.max_retries or 0
        if attempts < max_r:
            countdown = 2 ** attempts
            logger.warning(
                "classify_async retry: attempts=%d/%d countdown=%ds err=%s",
                attempts + 1, max_r, countdown, type(exc).__name__,
            )
            raise self.retry(exc=exc, countdown=countdown) from exc
        # 모든 retry 실패 — 보상 트랜잭션 + 실패 콜백.
        _record_compensation(
            job_id,
            partial_results=[],
            reason=f"classify_async exhausted: {type(exc).__name__}: {exc}",
        )
        _publish_callback_webhook(
            callback_url, {"job_id": job_id, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        )
        raise


def _publish_callback_webhook(callback_url: str | None, payload: dict) -> None:
    """워커 완료/실패 시 callback_url 로 결과 webhook 발사 — outbox.publish_callback(공용 계약)로 위임.

    (in-process 경로 AsyncClassifyService._publish_callback 와 동일 계약. 발사 로직은 outbox 로 통합.)
    """
    from koipa.services.outbox import publish_callback  # noqa: PLC0415
    publish_callback(callback_url, payload)


def _persist_synth_samples(docs: list, *, job_id: str | None) -> int:
    """[P0#1] 생성 문서를 검수큐(tb_sample_documents)에 적재 — generate→queue→review 루프 마감.

    이전 워커는 list[dict]만 반환하고 SynthRepo.create_sample 을 호출하지 않아, 검수큐가 비어
    운영학습(유일 자동화 레버)의 입력원이 단절돼 있었다. 여기서 각 SynthDoc 을 검수 대기(pending_review)
    상태로 적재하며, 본문 출처 마커(label_source/parse_error)를 보존한다.

    best-effort: DB 미가용/오류는 로깅만(워커를 죽이거나 retry→재생성 유발하지 않음). 생성은 이미
    성공했으므로 적재 실패는 loud ERROR 로 가시화하되 예외를 전파하지 않는다.

    Returns: 적재 성공 건수.
    """
    if not docs:
        return 0
    try:
        from koipa.db import session_scope  # noqa: PLC0415
        from koipa.repositories import ClassifyRepo, SynthRepo  # noqa: PLC0415

        persisted = 0
        with session_scope() as db:
            cls_repo = ClassifyRepo(db)
            synth_repo = SynthRepo(db)
            level_cache: dict[str, int | None] = {}
            for d in docs:
                grade_code = str(getattr(d, "target_grade", "") or "")
                if grade_code not in level_cache:
                    level_cache[grade_code] = cls_repo.level_id_by_code(grade_code)
                level_id = level_cache[grade_code]
                if level_id is None:
                    logger.warning(
                        "synth persist skip: 알 수 없는 등급 code=%r (레벨 미시드) — 적재 생략",
                        grade_code,
                    )
                    continue
                synth_repo.create_sample(
                    target_level_id=level_id,
                    llm_provider=str(getattr(d, "llm_provider", "") or "unknown"),
                    llm_model=str(getattr(d, "llm_model", "") or "unknown"),
                    generated_content=str(getattr(d, "body", "") or ""),
                    doc_type=(getattr(d, "domain", None) or None),
                    label_source=getattr(d, "label_source", None),
                    parse_error=getattr(d, "parse_error", None),
                )
                persisted += 1
                try:
                    from koipa.api.prom_metrics import SYNTH_SAMPLE_PERSISTED_TOTAL  # noqa: PLC0415

                    SYNTH_SAMPLE_PERSISTED_TOTAL.labels(
                        label_source=(getattr(d, "label_source", None) or "clean")
                    ).inc()
                except Exception:  # noqa: BLE001
                    pass
        logger.info(
            "synth persisted to review queue: job_id=%s persisted=%d/%d",
            job_id, persisted, len(docs),
        )
        return persisted
    except Exception:  # noqa: BLE001
        # 검수큐 적재 실패 = 운영학습 입력원 단절 신호 — loud ERROR(무음 금지). 재생성은 유발 안 함.
        logger.error(
            "synth persist FAILED (검수큐 적재 실패, generate→review 루프 단절): job_id=%s count=%d",
            job_id, len(docs), exc_info=True,
        )
        return 0


@celery_app.task(
    name="koipa.synthesize_batch",
    bind=True,
    max_retries=2,
    default_retry_delay=1,
)
def synthesize_batch(
    self: Any,
    grade: str,
    count: int,
    domain: str = "mixed",
    job_id: str | None = None,
) -> list[dict]:
    """합성 문서 N건 생성 + 검수큐 적재.

    SyntheticDocGenerator 내부도 best-effort지만, 전체 호출 실패 시 retry.
    생성 성공 시 tb_sample_documents(검수 대기)에 적재해 generate→queue→review 루프를 잇는다(P0#1).
    부분 결과가 있으면 보상 트랜잭션으로 partial 기록.
    """
    from koipa.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator

    partial: list[dict] = []
    try:
        gen = SyntheticDocGenerator()
        docs = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=count))
        partial = [
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
        # [P0#1] 검수큐 적재 — best-effort(예외 미전파: retry→재생성 방지). 루프 마감.
        persisted = _persist_synth_samples(docs, job_id=job_id)
        _record_job_done(
            job_id,
            results=partial,
            completed=len(partial),
            extra={"persisted": persisted},
        )
        return partial
    except Exception as exc:  # noqa: BLE001
        attempts = self.request.retries
        max_r = self.max_retries or 0
        if attempts < max_r:
            countdown = 2 ** attempts
            logger.warning(
                "synthesize_batch retry: attempts=%d/%d countdown=%ds err=%s",
                attempts + 1, max_r, countdown, type(exc).__name__,
            )
            raise self.retry(exc=exc, countdown=countdown) from exc
        # 모든 retry 실패 — 보상 트랜잭션 (partial은 빈 리스트일 수 있음).
        _record_compensation(
            job_id,
            partial_results=partial,
            reason=f"synthesize_batch exhausted: {type(exc).__name__}: {exc}",
        )
        raise


@celery_app.task(
    name="koipa.golden_build",
    bind=True,
    max_retries=2,
    default_retry_delay=2,
)
def golden_build_task(self: Any, req_dict: dict, job_id: str | None = None) -> dict:
    """통합 골든셋 빌드(위생→라벨→합의→조립)를 워커에서 실행 (G3b).

    GoldenBuildService.run_build이 JobStore 상태(running→done/failed)와 run-스코프 후보
    파일 출력을 소유한다. 일시 예외 → self.retry(2**attempts). 최종 실패 시 run_build이
    이미 status=failed를 기록(부분결과 없음 → partial 보상 불필요)하므로 그대로 재발생.
    """
    import uuid as _uuid

    from koipa.schemas.golden import GoldenBuildRequest
    from koipa.services.golden_build_service import GoldenBuildService

    try:
        req = GoldenBuildRequest(**req_dict)
        return GoldenBuildService().run_build(req, _uuid.UUID(job_id))
    except Exception as exc:  # noqa: BLE001
        attempts = self.request.retries
        max_r = self.max_retries or 0
        if attempts < max_r:
            countdown = 2 ** attempts
            logger.warning(
                "golden_build retry: attempts=%d/%d countdown=%ds err=%s",
                attempts + 1, max_r, countdown, type(exc).__name__,
            )
            raise self.retry(exc=exc, countdown=countdown) from exc
        logger.warning("golden_build exhausted: job_id=%s err=%s", job_id, exc)
        raise


def _train_rows_count(spec_kwargs: dict) -> int | None:
    """이번 학습에 **실제로 쓰이는** train.jsonl 행 수. 못 세면 None(기록 생략).

    [실측 2026-08-08] 종전에는 이 자리에 rebuild.row_count(=교정에서 만든 행 수)가 들어갔다.
    학습셋 크기가 아니라 '이번에 병합된 교정 건수'라, 실서버에서 2,044행으로 학습한 모델이
    training_data_count=2 로 기록됐다. 그 값이 화면까지 흘러가 /metrics/latest 의
    sample_count(=평가 표본 수) 자리에 2 로 표시됐다(metrics_service._from_stored).
    교정이 0건이면 병합이 없어 base 학습셋이 그대로 쓰이므로, spec_kwargs 의 train_path
    (병합됐으면 병합본, 아니면 TrainSpec 기본값)를 세는 것이 두 경우 모두에서 정확하다.
    """
    try:
        from koipa.modules.m4_training.trainer import TrainSpec  # noqa: PLC0415
        path = spec_kwargs.get("train_path") or TrainSpec().train_path
        with open(path, encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except Exception:  # noqa: BLE001 — 카운트 실패가 학습을 막지 않는다(기록만 생략)
        return None


def _create_training_run_guarded(
    spec_kwargs: dict, *, total_samples: int, trigger: str = "active_learning"
):
    """실제 TrainingRun 1건 생성 → run_id 반환(소비 FK·감사용). DB 미가용 시 None.

    [A2-① 정합] 기존엔 report.run_id(부재) → 랜덤 UUID로 소비를 시도했으나, 그 UUID는
    training_runs에 없어 consumed_in_run FK가 깨졌다(소비 실패 → 교정 무한누적). 실제
    TrainingRun을 만들어 그 run_id로만 소비해 FK 무결성과 감사추적을 보장한다.
    """
    try:
        import uuid as _uuid  # noqa: PLC0415
        from koipa.db import session_scope  # noqa: PLC0415
        from koipa.repositories import TrainingRepo  # noqa: PLC0415
        with session_scope() as db:
            run = TrainingRepo(db).create_run(
                total_samples=total_samples,
                hyperparameters=spec_kwargs,
                trigger_type=trigger,
            )
            rid = run.run_id
            return rid if hasattr(rid, "hex") else _uuid.UUID(str(rid))
    except Exception:  # noqa: BLE001
        logger.warning("training run create skipped (DB unavailable) — 소비/등록 best-effort 진행")
        return None


def _mark_training_run(run_id, *, status: str, **fields) -> None:
    """best-effort TrainingRun 상태 갱신 — 실패해도 학습 흐름 불변(관리 상태 표면만).

    mark_started/completed/failed 는 정의만 있고 prod 호출자가 없어 /train 상태가 영구
    'queued' 로 남던 것(고아 행)을 이 헬퍼가 닫는다. DB 미가용/오류는 삼켜 학습을 막지 않는다.
    """
    if run_id is None:
        return
    try:
        import uuid as _uuid  # noqa: PLC0415

        from koipa.db import session_scope  # noqa: PLC0415
        from koipa.repositories import TrainingRepo  # noqa: PLC0415
        rid = run_id if hasattr(run_id, "hex") else _uuid.UUID(str(run_id))
        with session_scope() as db:
            repo = TrainingRepo(db)
            if status == "running":
                repo.mark_started(rid)
            elif status == "completed":
                repo.mark_completed(
                    rid,
                    final_metrics=fields.get("final_metrics") or {},
                    duration_sec=fields.get("duration_sec"),
                )
            elif status == "failed":
                repo.mark_failed(rid, fields.get("error", ""))
    except Exception:  # noqa: BLE001
        logger.warning(
            "training run status update skipped (best-effort): run_id=%s status=%s",
            run_id, status,
        )


@celery_app.task(name="koipa.train_classifier")
def train_classifier_task(spec_kwargs: dict | None = None, run_id: str | None = None) -> dict:
    """재학습 — 교정 반영 → 학습 → 등록 → 게이트 → (조건부)활성 → 반영분만 소비.

    doc/36 본개발 #1 (A2-①②·C-ver):
      A2-①: unconsumed corrections를 {text,label}로 재빌드해 학습셋에 병합(반영). 그리고
             **실제 학습에 반영된 correction_id만** 소비한다(반영 없이 소비 = 유실, 차단).
      A2-②/C-ver: 학습 모델을 ModelVersion으로 등록하고 배포 합격선 게이트(fnr_high·f1)를
             평가. 활성(activate)은 게이트 통과 + settings.retrain_auto_activate(기본 False)
             둘 다일 때만 — 미검증 모델 자동배포 차단.
    """
    from koipa.config import settings  # noqa: PLC0415

    # [재학습 토폴로지 가드 · 심층방어] 분류기 학습은 학습 노드에서만 수행한다:
    #   enable_training(지재원 full-train, URGENT/weekly 자동재학습) 또는
    #   enable_incremental_retrain(고객사 onprem-local, 통제된 야간 증분 배치 — 2026-07 결정).
    # 둘 다 아니면(순수 추론 노드) 어떤 경로로 enqueue되든 학습을 수행하지 않는다(무거운 trainer
    # import도 생략). active_learning_tick / nightly_incremental_retrain_tick 가드와 belt+suspenders.
    # 주의: 이 관문을 통과해도 재학습본은 후보로만 등록되며 자동 서빙되지 않는다(activate 게이트 불변).
    if not (settings.enable_training or settings.enable_incremental_retrain):
        logger.warning(
            "train_classifier_task skipped — training disabled (inference-only node, "
            "deploy_profile=%s)", settings.deploy_profile,
        )
        return {"skipped": "training_disabled"}

    from koipa.modules.m4_training.trainer import TrainSpec, train_classifier  # noqa: PLC0415
    from koipa.modules.m6_evaluation.active_learning import consume_corrections_for_run  # noqa: PLC0415
    from koipa.modules.m6_evaluation.corrections_rebuild import (  # noqa: PLC0415
        build_labeled_rows_from_corrections,
        merge_into_train_jsonl,
    )
    from koipa.services.training_service import register_and_gate_model  # noqa: PLC0415

    spec_kwargs = dict(spec_kwargs or {})

    # ── [A2-①] 교정→라벨 재빌드 + 학습셋 병합(train만 증강, 홀드아웃 불변) ──────────
    rebuild = build_labeled_rows_from_corrections()
    incorporated_ids: list[int] = []
    if getattr(rebuild, "rows", None):
        try:
            import uuid as _uuid  # noqa: PLC0415
            _spec = TrainSpec()
            base_train = spec_kwargs.get("train_path") or _spec.train_path
            # [누수가드 강제] val/test(홀드아웃)와 doc_id·본문이 겹치는 교정 행을 train append에서
            # 제외 — train↔eval 평가 누수 차단(fail-open→fail-closed). deploy gate가 깨끗한
            # 홀드아웃에서 fnr_high를 재도록 보장(C-eval 정합). 옛 호출은 holdout_paths 미전달로
            # 누수 차단이 fail-open(경고만)이었다.
            holdout_paths = [
                spec_kwargs.get("val_path") or _spec.val_path,
                spec_kwargs.get("test_path") or _spec.test_path,
            ]
            out_path = f"{settings.retrain_dataset_dir}/train_corr_{_uuid.uuid4().hex[:8]}.jsonl"
            merged = merge_into_train_jsonl(
                base_train, rebuild, out_path, holdout_paths=holdout_paths
            )
            if merged:
                spec_kwargs["train_path"] = merged
                # [#6] merge 가 홀드아웃/캡 제외 후 실제 편입된 교정만 소비(제외분은 미소비→다음 tick
                # 재유입, 무음 유실·감사 오도 방지). incorporated_correction_ids=None 이면 전량 폴백.
                inc = getattr(rebuild, "incorporated_correction_ids", None)
                incorporated_ids = list(inc if inc is not None else rebuild.correction_ids)
                logger.info(
                    "train_classifier: %d corrections(%d docs) merged into train set",
                    len(incorporated_ids), rebuild.row_count,
                )
        except Exception:  # noqa: BLE001
            logger.exception("corrections merge failed — 기본 학습셋으로 진행")
            incorporated_ids = []

    # 소비 FK·감사용 실제 TrainingRun (학습 전 생성). API /train submit 이 넘긴 run_id 가 있으면
    # 그 행을 채택해 상태를 갱신한다(별도 run 생성 시 API 행이 영구 'queued' 고아가 되던 것 차단).
    if run_id is not None:
        try:
            import uuid as _uuid  # noqa: PLC0415
            run_uuid = run_id if hasattr(run_id, "hex") else _uuid.UUID(str(run_id))
        except Exception:  # noqa: BLE001
            run_uuid = _create_training_run_guarded(
                spec_kwargs, total_samples=_train_rows_count(spec_kwargs) or 0
            )
    else:
        run_uuid = _create_training_run_guarded(
            spec_kwargs, total_samples=_train_rows_count(spec_kwargs) or 0
        )
    _mark_training_run(run_uuid, status="running")

    # ── 학습 ────────────────────────────────────────────────────────────────────
    # [진행률 배선 2026-08-08] 이 run_id 를 넘기면 트레이너가 HF TrainerState 를 읽어
    # JobStore(같은 키)에 스텝·에폭·예상완료를 기록한다 → GET /train/jobs/{id} 가 노출.
    # API hyperparams 로 들어온 값이 아니라 워커가 붙이는 것이므로 여기서 주입한다.
    # run_uuid 가 None(DB 미가용)이면 넣지 않는다 — 진행률은 부가 정보이고 학습은 계속돼야 한다.
    if run_uuid is not None:
        spec_kwargs.setdefault("progress_run_id", str(run_uuid))
    spec = TrainSpec(**spec_kwargs)
    try:
        report = train_classifier(spec)
    except OSError as exc:
        # [#5 재학습 토폴로지 fail-safe] 배포 컨테이너가 datasets(학습셋 입력) 미마운트(ENOENT) 또는
        # artifacts(출력) read-only(EROFS/EACCES) 면 train 이 여기서 떨어진다(prod 이미지는 datasets
        # 미COPY·dual compose 는 artifacts:ro). 과거엔 raise → celery 재시도로 02:00 야간 tick 크래시-
        # 루프였다. 토폴로지 미배선은 '학습 실패'가 아니라 '미가용'이므로 skip(warn)로 흡수한다 —
        # 서빙 무영향(활성 게이트 불변), compose 에 datasets rw + artifacts writable 배선 시 자동 재동작.
        import errno as _errno  # noqa: PLC0415
        # datasets 미마운트 = FileNotFoundError(errno 미설정 케이스 포함, 타입으로 확정).
        # artifacts read-only/권한 = EROFS/EACCES/EPERM(errno). 디스크풀 등은 제외(실패로 raise).
        _topology_errnos = {_errno.EACCES, _errno.EPERM, _errno.EROFS}
        if isinstance(exc, FileNotFoundError) or getattr(exc, "errno", None) in _topology_errnos:
            _mark_training_run(
                run_uuid, status="failed",
                error=f"skipped: retrain topology unavailable ({type(exc).__name__}: {exc})",
            )
            logger.warning(
                "train_classifier_task skipped — 재학습 토폴로지 미배선(datasets 마운트/artifacts "
                "쓰기권한 확인): %s. 서빙 무영향 · compose 배선 시 자동 재동작.", exc,
            )
            return {"skipped": "retrain_topology_unavailable", "reason": f"{type(exc).__name__}: {exc}"}
        # 그 외 OSError(디스크풀 등)는 실학습 실패 → 종전대로 실패 마킹 후 raise.
        _mark_training_run(run_uuid, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    except Exception as exc:  # noqa: BLE001
        _mark_training_run(run_uuid, status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    out = dict(report.__dict__)
    out["corrections_incorporated"] = len(incorporated_ids)

    # ── [C-ver/A2-②] 등록 + 배포 게이트(활성은 게이트 통과 + opt-in 시에만) ────────
    model_uri = None
    try:
        _mv = getattr(report, "model_version", None)
        _od = getattr(spec, "output_dir", None)
        if _mv and _od:
            model_uri = f"{_od}/{_mv}"
    except Exception:  # noqa: BLE001
        model_uri = None
    # [죽음의 나선 #4] 자동활성(register_and_gate_model)은 실(locked_gold_eval) readiness 를 요구한다
    # (eval_block). 워커가 locked jsonl readiness 를 산출해 넘겨야 auto-activate 분기가 도달 가능하다 —
    # 미전달(None)이면 eval_block 이 상시 True 로 자동활성이 영구 차단된다(dead wiring). best-effort:
    # 산출 실패는 None(=차단 유지=fail-secure). 합성-only 단계엔 locked 가 비어 ready=False=차단(정직).
    try:
        from koipa.modules.m6_evaluation.locked_readiness import locked_eval_readiness
        _eval_ready = bool(locked_eval_readiness().get("ready"))
    except Exception:  # noqa: BLE001
        _eval_ready = None
    try:
        out["deploy"] = register_and_gate_model(
            report,
            training_run_id=run_uuid,
            training_data_count=_train_rows_count(spec_kwargs),
            model_uri=model_uri,
            eval_ready=_eval_ready,
        )
    except Exception:  # noqa: BLE001
        logger.exception("register_and_gate_model failed — 등록/게이트 생략")
        out["deploy"] = {"registered": False, "reason": "exception"}

    # 학습·게이트 성공 → 상태 completed (소비 성패와 무관 — 소비는 아래 별도 처리).
    _mark_training_run(
        run_uuid,
        status="completed",
        final_metrics={
            "corrections_incorporated": len(incorporated_ids),
            "deploy": out.get("deploy"),
        },
    )

    # ── [A2-①] 소비 — 실제 반영된 교정만, 실제 run_id로 (반영 없으면 소비 안 함=무손실) ──
    out["corrections_run_id"] = str(run_uuid) if run_uuid else None
    if not incorporated_ids or run_uuid is None:
        out["corrections_consumed"] = 0
        if rebuild.correction_ids and run_uuid is None:
            logger.info("교정 반영했으나 TrainingRun 미생성(DB) — 소비 보류(다음 tick 재시도, 무손실)")
        return out
    try:
        n = consume_corrections_for_run(run_uuid, correction_ids=incorporated_ids)
        out["corrections_consumed"] = n
        if n > 0:
            logger.info("train_classifier: consumed %d incorporated corrections under run_id=%s", n, run_uuid)
    except Exception:  # noqa: BLE001
        logger.exception("consume_corrections_for_run failed (run_id=%s) — corrections 누적 가능", run_uuid)
        out["corrections_consumed"] = -1
    return out


@celery_app.task(name="koipa.deliver_outbox_tick")
def deliver_outbox_tick(limit: int = 50) -> dict:
    """Outbox webhook 배송 주기 트리거.

    enqueue된 webhook을 실제로 송신하는 워커. beat가 60초마다 호출.
    배송 실패는 outbox 내부에서 지수 백오프 후 재시도, max_attempts 초과 시 DLQ stream으로.
    """
    from koipa.services.outbox import deliver_once, get_outbox_store, http_send_via_httpx
    store = get_outbox_store()
    out = deliver_once(store, http_send=http_send_via_httpx, limit=limit)
    if out["dlq"] > 0:
        logger.warning("outbox tick: %s — DLQ 발생", out)
    elif out["sent"] > 0 or out["failed"] > 0:
        logger.info("outbox tick: %s", out)
    return out


@celery_app.task(name="koipa.ensure_partitions_tick")
def ensure_partitions_tick(months_ahead: int = 3) -> dict:
    """월별 RANGE 파티션 롤오버 (#5) — beat가 매일 호출.

    향후 N개월 파티션을 미리 생성해 _default 비대화·프루닝 무력화를 막는다.
    CREATE IF NOT EXISTS라 멱등. failed가 있으면 롤오버 지연(런북 필요)이라 경고.
    """
    from koipa.services.partitions import ensure_partitions  # noqa: PLC0415

    out = ensure_partitions(months_ahead=months_ahead)
    failed = out.get("failed") or []
    if failed:
        logger.warning("ensure_partitions_tick: 일부 파티션 생성 실패 — %s", failed)
    # [P0 관측성] 워커→API 브리지: 롤오버 실패는 워커 beat 에서만 계산돼 API 무가시(로그뿐)
    # 였다 → 실패 건수를 Redis 게시 → API _refresh_partition_gauges 재노출(PartitionEnsureFailed).
    # db_unavailable(status)면 게시 skip(측정 불가 ≠ 실패 0, 거짓 all-clear 방지).
    if out.get("status") == "ok":
        from koipa.services.worker_metrics_bridge import PARTITION_ENSURE, publish_signal
        publish_signal(PARTITION_ENSURE, {"failed": len(failed), "ensured": len(out.get("ensured") or [])})
    return out


@celery_app.task(name="koipa.auto_rollback_tick")
def auto_rollback_tick() -> dict:
    """C-ver 자동 롤백 주기 점검 — 활성 모델 라이브 미탐 회귀 시 직전 활성으로 복귀.

    evaluate_rollback_need로 baseline 대비 라이브 fnr_high 회귀를 판정하고,
    settings.auto_rollback_enabled(기본 False)이고 회귀가 확인되면 rollback_active_model 실행.
    기본은 판정만 하고 롤백은 안 함(동작 보존) — beat가 주기 호출.
    """
    from koipa.config import settings  # noqa: PLC0415
    from koipa.services.training_service import (  # noqa: PLC0415
        evaluate_rollback_need,
        rollback_active_model,
    )

    decision = evaluate_rollback_need()
    out = dict(decision)
    out["auto_rollback_enabled"] = bool(getattr(settings, "auto_rollback_enabled", False))
    if decision.get("should_rollback") and out["auto_rollback_enabled"]:
        reason = (
            f"auto-rollback: live fnr_high {decision.get('live_fnr_high')} > "
            f"baseline {decision.get('baseline_fnr_high')} + tol {decision.get('tolerance')}"
        )
        out["rollback"] = rollback_active_model(reason)
        logger.warning("auto-rollback executed: %s", out["rollback"])
    else:
        logger.info("auto_rollback_tick: %s (enabled=%s)", decision.get("reason"), out["auto_rollback_enabled"])
    return out


@celery_app.task(name="koipa.drift_tick")
def drift_tick(limit: int = 200, threshold: float = 0.5) -> dict:
    """A4: 운영 임베딩 drift 주기 점검 — Celery beat가 호출.

    drift_monitor.run_drift_check가 train centroid + 최근 운영 표본을 비교하고
    Prometheus gauge에 직접 set. alert=True면 알람 룰이 페이지.

    [스코프 결정 2026-08-08] settings.drift_detection_enabled 기본 OFF — 드리프트 감지는
    요건이 아니다(RTM 무행 · 담당 요건 FUN-003·004·005·022·023·024 어디에도 없음).
    끈 상태에선 no-op 으로 즉시 반환한다. 표본이 적을 때 의미 없는 경보를 내던 것도 함께 멎는다
    (실측: sample=2 · kl=4.8614 · alert=True — 표본 2건짜리 판정은 신호가 아니다).
    근거·복원 방법은 config.py 의 drift_detection_enabled 주석 참조.
    """
    from koipa.config import settings as _settings  # noqa: PLC0415
    if not bool(getattr(_settings, "drift_detection_enabled", False)):
        return {"skipped": "drift_detection_disabled"}

    from koipa.services.drift_monitor import export_to_prometheus, run_drift_check
    report = run_drift_check(limit=limit, threshold=threshold)
    out = report.to_dict()
    logger.info(
        "drift_tick: sample=%d kl=%.4f cosine_mean=%.4f alert=%s",
        report.sample_size, report.kl_divergence, report.cosine_mean, report.alert,
    )
    # [P0 관측성] 워커→API 브리지: DRIFT_* 게이지는 워커 레지스트리(비스크랩)에만 set 되므로
    # exposition 을 Redis 에 게시 → API _refresh_drift_gauges 가 읽어 재노출(best-effort).
    from koipa.services.worker_metrics_bridge import DRIFT_REPORT, publish_signal
    publish_signal(DRIFT_REPORT, export_to_prometheus(report))
    return out


@celery_app.task(name="koipa.beat_heartbeat_tick")
def beat_heartbeat_tick() -> dict:
    """beat 생존 신호 — 매 60초 Redis 에 heartbeat 게시(bridge 가 checked_at 자동 부가).

    beat 스케줄러가 죽으면 게시가 멈춰 API 가 koipa_beat_heartbeat_age_seconds 로 노후를
    노출하고 BeatHeartbeatStale 알람이 페이지한다. 감사체인검증·drift·파티션 게이지가 beat
    사망 시 마지막 안전값에 무음 동결되던 사각지대(beat-down 자체 무알람)를 보완한다.
    """
    from koipa.services.worker_metrics_bridge import HEARTBEAT, publish_signal
    return {"published": publish_signal(HEARTBEAT, {})}


@celery_app.task(name="koipa.verify_audit_chain_tick")
def verify_audit_chain_tick(limit: int = 100000) -> dict:
    """NFR-SEC-01: 감사 로그 hash chain 정기 무결성 검증 — Celery beat 일별 호출.

    verify_chain이 broken>0 검출 시 koipa_audit_chain_broken_total 증가(P0 AuditChainBroken
    알람) + warning 로그. 변조(과거 row 삭제·삽입·재배열·payload 변조) 의심을 운영에 노출한다.
    """
    from koipa.services.audit_chain import verify_chain
    res = verify_chain(limit=limit)
    logger.info(
        "verify_audit_chain_tick: total=%d verified=%d broken=%d first_break=%s truncated=%s",
        res.total_rows, res.verified, res.broken, res.first_break_audit_id, res.scan_truncated,
    )
    # [P0 관측성] 워커→API 브리지: AUDIT_CHAIN_BROKEN_TOTAL 은 워커 레지스트리(비스크랩)에만
    # inc 되므로 authoritative full-scan 결과를 Redis 에 게시 → API _refresh_audit_integrity_gauges
    # 가 읽어 현재-상태 게이지로 재노출(P0 AuditChainBroken 알람이 실제로 발화하게 함).
    from koipa.services.worker_metrics_bridge import AUDIT_INTEGRITY, publish_signal
    publish_signal(AUDIT_INTEGRITY, {
        "broken": res.broken,
        "nil_hash_rows": res.nil_hash_rows,
        "integrity_ok": res.integrity_ok(),
        "total_rows": res.total_rows,
        "scan_truncated": res.scan_truncated,
    })
    return {
        "total_rows": res.total_rows,
        "verified": res.verified,
        "broken": res.broken,
        "first_break_audit_id": res.first_break_audit_id,
        "ok": res.ok(),
        "scan_truncated": res.scan_truncated,
    }


@celery_app.task(name="koipa.active_learning_tick")
def active_learning_tick(mode: str = "auto") -> dict:
    """P1-A5: Active learning 주기 트리거.

    mode="auto":     beat 30분 주기. URGENT 도달 시 train_classifier 자동 enqueue.
    mode="snapshot": 일별 스냅샷. 학습 트리거 없이 status만 기록.
    mode="dry":      평가만 수행. 호출용.

    재학습 트리거 정책:
    - URGENT_RETRAIN: 즉시 train_classifier_task enqueue (보안 미탐 누적)
    - RETRAIN_RECOMMENDED: 누적 ≥ recommended_threshold 일 때만 weekly window에 큐잉
      (간단히는 weekday=월요일 새벽에 한번)
    - OK: nothing
    """
    from datetime import datetime
    from koipa.modules.m6_evaluation.active_learning import evaluate_retraining_need

    status = evaluate_retraining_need()
    payload = status.to_dict()
    payload["mode"] = mode
    payload["ts"] = datetime.utcnow().isoformat() + "Z"

    if mode == "snapshot":
        logger.info("active-learning daily snapshot: %s", payload)
        return payload

    if mode == "dry":
        return payload

    # auto mode — [재학습 토폴로지 가드] 분류기 자동 재학습은 enable_training=True 노드(지재원
    # full-train)에서만 발화한다. 고객사(onprem-local, enable_training=False)는 설계상 추론+비모수
    # 전용이므로 자동 재학습을 금지한다. status 평가·기록은 위에서 끝났으므로(진단·집계 리포트용
    # 으로 유지) 여기서는 train_classifier_task enqueue만 차단한다 — 옛 'beat 미게이트로 고객사에서도
    # 재학습' 동작을 task 레벨에서 막아 코드를 설계에 정합시킨다(죽음의 나선 고객사 차단).
    from koipa.config import settings as _settings  # noqa: PLC0415

    if not _settings.enable_training:
        payload["triggered"] = "SKIP_TRAINING_DISABLED"
        return payload

    if status.retrain_status == "URGENT_RETRAIN":
        logger.warning("URGENT_RETRAIN triggered: %s", status.reason)
        try:
            train_classifier_task.apply_async(kwargs={"spec_kwargs": None})
            payload["triggered"] = "URGENT_RETRAIN"
        except Exception:  # noqa: BLE001
            logger.exception("train_classifier_task enqueue failed")
            payload["triggered"] = "ENQUEUE_FAILED"
    elif status.retrain_status == "RETRAIN_RECOMMENDED":
        now = datetime.utcnow()
        # 월요일 03~05시 범위에만 weekly 트리거 (beat tz 한국 기준)
        if now.weekday() == 0 and 3 <= now.hour < 5:
            logger.info("weekly RETRAIN_RECOMMENDED triggered: %s", status.reason)
            try:
                train_classifier_task.apply_async(kwargs={"spec_kwargs": None})
                payload["triggered"] = "RETRAIN_RECOMMENDED"
            except Exception:  # noqa: BLE001
                logger.exception("train_classifier_task enqueue failed")
                payload["triggered"] = "ENQUEUE_FAILED"
        else:
            payload["triggered"] = "SKIP_WAIT_WEEKLY_WINDOW"
    else:
        payload["triggered"] = "NONE"

    return payload


@celery_app.task(name="koipa.nightly_incremental_retrain_tick")
def nightly_incremental_retrain_tick() -> dict:
    """고객사(onprem-local) 야간 증분 재학습 트리거 — enable_incremental_retrain=True 노드 전용.

    2026-07 결정(cpu-incremental-retrain-measured): 고객사 현장서 사람검수 교정을 기존 학습셋에
    섞어 CPU 풀 파인튜닝을 야간 배치로 무인 실행. enable_training(지재원)이 여는 URGENT 드리프트
    자동재학습(active_learning 30분틱)과는 별개 축 — 이 틱은 통제된 야간 1회만 발화한다.

    안전:
    - 플래그 OFF(지재원 full-train·순수 추론 노드)면 no-op.
    - unconsumed 교정이 0이면 skip — 새 신호 없는 야간 재학습(CPU ~1h)을 낭비하지 않는다.
    - 재학습본은 train_classifier_task 에서 **후보로만** 등록됨(retrain_auto_activate=False 불변).
      실제 서빙 전환은 사람서명 locked-eval / 감사 force 로만(activate 게이트 불변).
    """
    from datetime import datetime  # noqa: PLC0415
    from koipa.config import settings as _settings  # noqa: PLC0415
    from koipa.modules.m6_evaluation.active_learning import evaluate_retraining_need  # noqa: PLC0415

    payload = {
        "task": "nightly_incremental_retrain",
        "ts": datetime.utcnow().isoformat() + "Z",
        "deploy_profile": _settings.deploy_profile,
    }

    # [토폴로지 가드] 야간 증분 재학습은 enable_incremental_retrain 노드(고객사)에서만 발화.
    # 지재원(full-train)은 이 플래그 False → no-op(자체 enable_training/active_learning 경로 사용).
    if not _settings.enable_incremental_retrain:
        payload["triggered"] = "SKIP_INCREMENTAL_DISABLED"
        return payload

    # 새 교정 없으면 skip — base 재현뿐인 야간 재학습(CPU 고비용)을 낭비하지 않는다.
    # 평가 실패 시 보수적 skip(불확실 상태에서 무거운 재학습을 돌리지 않음).
    try:
        status = evaluate_retraining_need()
        payload["unconsumed_total"] = status.unconsumed_total
        payload["retrain_status"] = status.retrain_status
    except Exception:  # noqa: BLE001
        logger.exception("nightly incremental: evaluate_retraining_need failed — 보수적 skip")
        payload["triggered"] = "SKIP_EVAL_FAILED"
        return payload

    if status.unconsumed_total <= 0:
        payload["triggered"] = "SKIP_NO_NEW_CORRECTIONS"
        return payload

    try:
        train_classifier_task.apply_async(kwargs={"spec_kwargs": None})
        payload["triggered"] = "NIGHTLY_INCREMENTAL"
        logger.info(
            "nightly incremental retrain enqueued (unconsumed=%d, profile=%s)",
            status.unconsumed_total, _settings.deploy_profile,
        )
    except Exception:  # noqa: BLE001
        logger.exception("nightly incremental: train_classifier_task enqueue failed")
        payload["triggered"] = "ENQUEUE_FAILED"

    return payload
