"""Celery tasks — 비동기 분류·합성·학습 트리거.

API → Redis 큐 → Worker가 무거운 작업을 수행. Redis 없으면 task는 동기 호출용 함수로도 사용 가능.

부분 실패 처리 (2026-05 추가):
- classify_async / synthesize_batch / golden_build_task: bind=True + max_retries=2 + 지수 백오프
  (countdown = 2 ** attempts → 1초, 2초. 최악 대기 3초)
  ⚠ [2026-09-06 정정] 여기 max_retries=3 이라 적혀 있었는데 실제 데코레이터는 셋 다 2 다.
    숫자를 세는 시험이 없어 아무도 몰랐다 — tests/test_worker_retry_contract.py 가 이제 막는다.
- 모든 retry 실패 시 보상 트랜잭션: 이미 처리된 결과를 status="partial"로 JobStore에 기록
"""

from __future__ import annotations

import json
import logging
from typing import Any

from koipa.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# 본문이 LLM 응답이 아닌 것으로 만들어졌다는 표식. 생성기가 찍고
# (m1_synthesis/generator.py) 학습 편입 게이트가 같은 값을 배제한다
# (services/synthesis_service.TRAINING_EXCLUDED_LABEL_SOURCES).
_LLM_FALLBACK_SOURCES = frozenset({"noop_fallback", "llm_nonjson"})


def _record_job_done(
    job_id: str | None,
    *,
    results: list[dict],
    completed: int | None = None,
    extra: dict[str, Any] | None = None,
    status: str = "done",
) -> None:
    """Best-effort JobStore terminal transition for Celery worker paths.

    [2026-09-10] status 를 인자로 뺐다. 종전에는 무조건 "done" 이었고, 그래서 합성에서
    LLM 이 3회 재시도 끝에 실패해도 잡이 성공으로 끝났다 — 화면에서 진짜 성공과 구분이
    안 됐다. 기본값이 "done" 이라 다른 호출부는 그대로다.
    """
    if not job_id:
        return
    try:
        import uuid as _uuid

        from koipa.services.job_store import get_default_store

        fields: dict[str, Any] = {
            "status": status,
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


# 아직 학습셋 판이 정해지지 않은 상태의 자리표시. 빌드가 실제 판 이름으로 한 줄 더 쌓는다
# (append-only 라 이 줄은 남는다 — "만들어졌다"와 "학습셋에 들어갔다"는 다른 사실이다).
_JOB_PENDING_VERSION = "pending-review"


def _coverage_cell(grade: str, domain: str | None) -> dict | None:
    """요청 시점의 (등급 x 도메인) 격자 칸. 못 읽으면 None 을 돌려준다.

    왜 요청 시점이냐. 나중에 다시 계산하면 이미 채워진 뒤라 **원래 얇았다는 사실이
    사라진다.** "이 문서는 그때 1건뿐이던 칸을 메우려고 만들었다"가 되짚을 수 있어야
    나중에 골든셋이 생겼을 때 빈 칸 채우기의 값어치를 잴 수 있다.

    격자를 못 읽어도 생성·적재를 막지 않는다 - 근거 기록은 부가 정보다.
    """
    try:
        from koipa.services.synth_coverage import (  # noqa: PLC0415
            coverage_report,
            row_domain,
        )

        report = coverage_report()
        if not report.get("available"):
            return {"available": False, "reason": report.get("reason")}
        canon = row_domain({"domain": domain or ""})
        key = f"{grade}|{canon}"
        n = int((report.get("grid") or {}).get(key, 0))
        thin = {(c["grade"], c["domain"]): c for c in report.get("thin", [])}
        empty = {(c["grade"], c["domain"]) for c in report.get("empty", [])}
        cell = thin.get((grade, canon))
        return {
            "available": True,
            "grade": grade,
            "domain": canon,
            "n": n,
            "real": int(cell["real"]) if cell else None,
            "was_empty": (grade, canon) in empty,
            "was_thin": cell is not None,
            "min_per_cell": report.get("min_per_cell"),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("커버리지 칸 기록 실패 - 근거 없이 적재한다: %s", exc)
        return None


def _persist_synth_samples(
    docs: list, *, job_id: str | None, screen: dict | None = None
) -> int:
    """[P0#1] 생성 문서를 검수큐(tad_sm_syn_doc_mng)에 적재 — generate→queue→review 루프 마감.

    이전 워커는 list[dict]만 반환하고 SynthRepo.create_sample 을 호출하지 않아, 검수큐가 비어
    운영학습(유일 자동화 레버)의 입력원이 단절돼 있었다. 여기서 각 SynthDoc 을 검수 대기(pending_review)
    상태로 적재하며, 본문 출처 마커(label_source/parse_error)를 보존한다.

    best-effort: DB 미가용/오류는 로깅만(워커를 죽이거나 retry→재생성 유발하지 않음). 생성은 이미
    성공했으므로 적재 실패는 loud ERROR 로 가시화하되 예외를 전파하지 않는다.

    [2026-09-05] 품질 결과·프롬프트 버전을 함께 남긴다. 종전에는 create_sample() 이 받는
    quality_score·quality_report·*_prompt_version 다섯 칸을 아무도 채우지 않아, 검수자가
    "이 문서가 어떤 검사를 통과해 여기 있는가"를 화면에서 알 수 없었다.

    Args:
        screen: screen_batch() 결과. 없으면 품질 칸을 비운 채 적재한다(하위호환).

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

            # 프롬프트 버전 — 내용 해시. **세 칸은 tad_pm_prmpt_ver_mng 를 가리키는 외래키라
            # 행을 먼저 등록해야 한다**(실측: IntegrityError 1452). 등록에 실패하면 값을
            # 비우고 적재는 계속한다 — 버전 기록 때문에 생성 결과를 잃으면 안 된다.
            _pv_body = _pv_outline = _pv_qc = None
            try:
                from koipa.modules.m1_synthesis.generator import (  # noqa: PLC0415
                    GRADE_SITUATION_PROMPTS, OUTLINE_SYSTEM_PROMPT,
                    OUTLINE_TEMPLATE, SYSTEM_PROMPT,
                    body_prompt_version, outline_prompt_version,
                )

                _pv_body = body_prompt_version()
                _pv_outline = outline_prompt_version()
                # QC 는 LLM 프롬프트가 아니라 계량 게이트다(synth_quality.screen_batch).
                # 그래도 어느 게이트를 통과했는지는 같은 방식으로 되짚을 수 있어야 한다.
                _pv_qc = "metric-gate-v1"
                _tpl = SYSTEM_PROMPT + "\n\n" + json.dumps(
                    GRADE_SITUATION_PROMPTS, ensure_ascii=False, sort_keys=True
                )
                synth_repo.upsert_prompt(
                    _pv_body, chain_stage="body", template=_tpl,
                    created_by="worker", notes="내용 해시 자동 등록",
                )
                if _pv_outline != _pv_body:
                    # [2026-09-10] 개요 단계가 자기 프롬프트를 갖게 됐다(다단계 생성).
                    # 종전에는 두 값이 같아 이 가지가 돌지 않았고, 그래서 template 에
                    # 본문 프롬프트가 들어가 있어도 티가 안 났다 — 이제 개요 프롬프트를
                    # 등록한다. 여기에 본문 것을 넣으면 버전 해시와 내용이 어긋난다.
                    synth_repo.upsert_prompt(
                        _pv_outline, chain_stage="outline",
                        template=OUTLINE_SYSTEM_PROMPT + "\n\n" + OUTLINE_TEMPLATE,
                        created_by="worker", notes="내용 해시 자동 등록",
                    )
                synth_repo.upsert_prompt(
                    _pv_qc, chain_stage="qc",
                    template="synth_quality.screen_batch — 등급명 노출·길이 누출·tell 커버 계량 게이트",
                    created_by="worker", notes="LLM 프롬프트 아님(계량 게이트)",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("프롬프트 버전 등록 실패 — 버전 칸을 비우고 적재 계속: %s", exc)
                _pv_body = _pv_outline = _pv_qc = None

            # 배치는 한 (등급, 도메인) 조합으로 만들어진다 — 첫 문서에서 읽는다.
            grade = str(getattr(docs[0], "target_grade", "") or "") if docs else ""
            domain = getattr(docs[0], "domain", None) if docs else None
            # 품질 결과 — screen_batch 가 이미 잰 것을 행에 남긴다.
            _sc = screen or {}
            _metrics = _sc.get("metrics") or {}
            _verdict = _sc.get("batch_verdict")
            _q_report = {
                "batch_verdict": _verdict,
                "metrics": _metrics,
                "gate": "synth_quality.screen_batch",
                # [2026-09-07] **왜 이 칸을 만들었는가**를 함께 남긴다.
                #
                # 합성의 쓸모는 '빈 칸 채우기'로 좁혀져 있다(양으로 늘리는 것은 실측으로
                # 막혔다 — 합성-only 로 학습해 실문서를 재면 F1 0.26). 그런데 학습 행에
                # "이 문서는 S1×배터리 칸이 1건이라 만들었다"가 남지 않아, 나중에 사람이
                # 서명한 골든셋이 생겨도 **빈 칸 채우기가 실제로 도움이 됐는지 되짚을 수
                # 없었다.** 오늘 A/B 가 "이 자로는 구분이 안 된다"로 끝난 것과 같은 종류의
                # 문제다 — 나중에 판정하려면 지금 근거를 남겨야 한다.
                #
                # 요청 시점의 격자 상태를 그대로 박아 둔다(그때 얼마나 얇았나). 나중에
                # 다시 계산하면 이미 채워진 뒤라 원래 얇았다는 사실이 사라진다.
                "coverage_at_request": _coverage_cell(grade, domain),
            } if _sc else None
            # 점수는 "어떤 검사를 통과했는가"를 한 숫자로. 걸린 문서는 애초에 여기 오지
            # 않는다(admit 만 넘어온다).
            #
            # [2026-09-05] 세 갈래로 나눈다(monimo 세션 지적). 종전에는 ok 가 아니면 전부
            # 0.5 라 **"게이트를 못 돌렸다"와 "표본이 모자라 코퍼스 지표를 판정 안 했다"가
            # 같은 값**이었다 — 점수만 보고는 게이트가 깨진 것을 알 수 없었다.
            #   1.0  코퍼스 지표까지 통과
            #   0.5  표본 부족으로 코퍼스 지표 미판정(문서 단위 검사는 통과)
            #   0.0  게이트를 못 돌렸다 — 검사받지 않은 문서다(학습 편입 불가)
            if not _sc:
                _q_score = None
            elif _verdict == "ok":
                _q_score = 1.0
            elif _verdict == "gate_error":
                _q_score = 0.0
            else:
                _q_score = 0.5

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
                sd = synth_repo.create_sample(
                    target_level_id=level_id,
                    llm_provider=str(getattr(d, "llm_provider", "") or "unknown"),
                    llm_model=str(getattr(d, "llm_model", "") or "unknown"),
                    generated_content=str(getattr(d, "body", "") or ""),
                    generated_outline=(getattr(d, "title", None) or None),
                    doc_type=(getattr(d, "domain", None) or None),
                    label_source=getattr(d, "label_source", None),
                    parse_error=getattr(d, "parse_error", None),
                    quality_score=_q_score,
                    quality_report=_q_report,
                    # 개요 버전은 **개요 단계를 실제로 거친 문서에만** 남긴다.
                    # 단발 생성은 개요 프롬프트를 부른 적이 없다 — 값을 채우면
                    # "이 프롬프트로 만들었다"는 거짓 기록이 된다.
                    outline_prompt_version=(
                        _pv_outline
                        if str(getattr(d, "generation_mode", "single")) == "multi_step"
                        else None
                    ),
                    body_prompt_version=_pv_body,
                    qc_prompt_version=_pv_qc,
                )
                persisted += 1
                # [2026-09-05] 어느 생성 작업이 만든 문서인지 잇는다. 종전에는 응답이
                # synth_job_id 를 주는데 그 뒤로 이어지는 곳이 없어, "이 작업이 몇 건
                # 만들었나 · 어느 문서인가"를 물을 수 없었다. 판 이름은 아직 없다
                # (학습셋 빌드 때 정해진다) — 그래서 pending 자리표시로 둔다.
                if job_id:
                    try:
                        synth_repo.record_dataset_membership(
                            [sd.sample_id], _JOB_PENDING_VERSION,
                            synth_job_id=job_id,
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning("job 연결 기록 실패: job_id=%s", job_id, exc_info=True)
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
    llm_provider: str | None = None,
) -> list[dict]:
    """합성 문서 N건 생성 + 누출 게이트 + 검수큐 적재.

    SyntheticDocGenerator 내부도 best-effort지만, 전체 호출 실패 시 retry.
    생성 성공 시 tad_sm_syn_doc_mng(검수 대기)에 적재해 generate→queue→review 루프를 잇는다(P0#1).
    부분 결과가 있으면 보상 트랜잭션으로 partial 기록.

    [2026-09-05 누출 게이트] 적재 **전**에 services/synth_quality.screen_batch 로 잰다.
    측정기(koipa.dataset_leakage)는 전부터 있었지만 학습셋 빌더에만 걸려 있어, 합성은
    만들어서 그대로 검수큐로 갔다. 그 결과 옛 산출물의 33.8%가 본문에 자기 등급명을 노출한
    채 쌓였다(S1 94.3%) — 검수자가 답이 적힌 문서를 읽으면 검수가 확인 절차가 된다.
    걸린 문서는 **버리지 않고** 적재에서만 뺀다(생성 비용은 이미 들었고, 무엇이 왜 걸렸는지는
    job 기록에 남긴다).
    """
    from koipa.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator
    from koipa.services.synth_quality import screen_batch

    partial: list[dict] = []
    try:
        # [2026-09-05] 요청한 provider 로 생성한다. 없으면 전역 설정값(종전 동작).
        #
        # ⚠ **못 만들면 실패로 끝낸다.** 앞선 판은 전역 설정으로 되돌렸는데 그것이 틀렸다 —
        #   OpenAI 를 요청했는데 키가 없어 Anthropic 으로 생성되면 조용한 오작동이고,
        #   되돌려 만든 문서는 어느 모델 산인지 아무도 모르는 채 검수큐에 쌓인다.
        #   여기서 죽으면 잡이 실패로 남고 사유가 보인다.
        _llm = None
        if llm_provider:
            from koipa.adapters.llm import build_provider  # noqa: PLC0415

            try:
                _llm = build_provider(llm_provider)
            except Exception as exc:
                raise RuntimeError(
                    "요청한 LLM provider %r 로 생성할 수 없다(%s: %s). "
                    "전역 설정으로 되돌리지 않는다 — 요청한 모델과 실제 쓴 모델이 갈리면 "
                    "산출물의 출처를 알 수 없게 된다." % (
                        llm_provider, type(exc).__name__, exc,
                    )
                ) from exc
        gen = SyntheticDocGenerator(llm=_llm)
        # 어느 설정으로 만들었는지 남긴다. 설정이 꺼져 있는데 켜진 줄 알고 결과를 읽는
        # 사고를 막는다 — 켜도 무동작인 플래그를 '안전'으로 읽은 전례가 있다(2026-09-10).
        logger.info(
            "synth 생성 설정: 구조화출력=%s · 동시성=%d · 다단계=%s (job_id=%s)",
            gen.structured_output, gen.concurrency, gen.multi_step, job_id,
        )
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
        # [누출 게이트] 재고 통과분만 적재한다. 게이트 자체의 실패가 생성 결과를 버리게
        # 하면 안 되므로 예외는 흡수하고 전량 통과로 되돌린다(fail-open · 사유는 로그).
        try:
            screen = screen_batch([(d.target_grade, d.body) for d in docs])
        except Exception as exc:  # noqa: BLE001
            logger.warning("synth 누출 게이트 실패 — 전량 적재로 진행(fail-open): %s", exc)
            screen = {"admit": list(range(len(docs))), "flagged": [],
                      "metrics": {}, "batch_verdict": "gate_error"}

        admit = set(screen["admit"])
        admitted_docs = [d for i, d in enumerate(docs) if i in admit]
        if len(admitted_docs) != len(docs):
            logger.warning(
                "synth 누출 게이트: %d/%d 건만 검수큐로 — verdict=%s reasons=%s",
                len(admitted_docs), len(docs), screen["batch_verdict"],
                sorted({f["reason"] for f in screen["flagged"]}),
            )

        # [P0#1] 검수큐 적재 — best-effort(예외 미전파: retry→재생성 방지). 루프 마감.
        persisted = _persist_synth_samples(admitted_docs, job_id=job_id, screen=screen)

        # [2026-09-10] LLM 이 실제로 답했는가를 잡 상태에 남긴다.
        #
        # 종전에는 LLM 이 3회 재시도 끝에 실패해도 이 자리에서 무조건 done 이었다.
        # 생성기는 그때 자리표시 본문(noop_fallback)이나 파싱 못 한 원문(llm_nonjson)을
        # 본문으로 쓰고 검수 큐에 올린다 — 사람이 그것을 검수하게 되는데 화면·API
        # 어디에도 "실패해서 대체했다"는 말이 없었다(로그에만 있었다).
        #
        # 전부 대체본이면 failed, 섞였으면 partial 이다. 만든 문서는 그대로 두고 상태만
        # 사실대로 적는다 — 지우면 무엇이 잘못됐는지 볼 수 없다.
        _fb_sources = [
            str(getattr(d, "label_source", "") or "")
            for d in docs
            if getattr(d, "label_source", None) in _LLM_FALLBACK_SOURCES
        ]
        _fb_by_source: dict[str, int] = {}
        for _src in _fb_sources:
            _fb_by_source[_src] = _fb_by_source.get(_src, 0) + 1
        if docs and len(_fb_sources) == len(docs):
            _job_status = "failed"
        elif _fb_sources:
            _job_status = "partial"
        else:
            _job_status = "done"
        if _fb_sources:
            logger.warning(
                "synth: LLM 응답 실패로 대체본 %d/%d 건 — 잡 상태 %s (job_id=%s · %s)",
                len(_fb_sources), len(docs), _job_status, job_id, _fb_by_source,
            )
        _record_job_done(
            job_id,
            results=partial,
            completed=len(partial),
            status=_job_status,
            extra={
                "persisted": persisted,
                # 몇 건이 LLM 응답으로 만들어졌고 몇 건이 대체본인가. 화면이 이 값으로
                # 경고를 띄운다 — 설정(provider 이름)이 아니라 실제 결과로 말해야 한다.
                "llm_fallback": {
                    "total": len(docs),
                    "fallback": len(_fb_sources),
                    "by_source": _fb_by_source,
                },
                # 게이트 결과를 job 에 남긴다 — 화면·감사에서 "왜 40건 만들었는데
                # 검수큐엔 12건인가"를 열어보지 않고 알 수 있어야 한다.
                "leakage_gate": {
                    "verdict": screen["batch_verdict"],
                    "generated": len(docs),
                    "admitted": len(admitted_docs),
                    "flagged": screen["flagged"],
                    "metrics": screen["metrics"],
                },
            },
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
                _mv = fields.get("model_version")
                if _mv is not None and not hasattr(_mv, "hex"):
                    try:
                        _mv = _uuid.UUID(str(_mv))
                    except (ValueError, AttributeError, TypeError):
                        _mv = None      # 형식이 이상하면 연결만 포기한다(완료 기록은 남긴다)
                repo.mark_completed(
                    rid,
                    final_metrics=fields.get("final_metrics") or {},
                    duration_sec=fields.get("duration_sec"),
                    model_version=_mv,
                )
            elif status == "failed":
                repo.mark_failed(
                    rid, fields.get("error", ""),
                    final_metrics=fields.get("final_metrics"),
                    duration_sec=fields.get("duration_sec"),
                )
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
    # [소요시간 2026-08-16] `_mark_training_run(..., "completed")` 이 duration_sec 을
    # 안 넘겨서 `GET /train/jobs` 의 소요시간이 **항상 null** 이었다(tasks.py 의
    # `duration_sec=fields.get("duration_sec")` 가 늘 None 을 받았다). 여기서 시작
    # 시각을 잡아 완료 때 넘긴다. monotonic 이라 시스템 시계 변경에 안 흔들린다.
    import time as _time  # noqa: PLC0415
    _run_t0 = _time.monotonic()

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

    # [상태 정직성 2026-08-16] 학습이 끝난 것과 모델이 등록된 것은 다른 사건이다.
    # 종전에는 register_and_gate_model 이 예외로 죽어도 status="completed" 로 적어
    # 화면(GET /train/jobs)에는 새 모델이 나온 것처럼 보였다.
    # ⚠ 상태 표기만 가른다 — 학습 산출물은 그대로 남고 실패 사유는 final_metrics.deploy 에 있다.
    # ⚠ registered=False 가 정상인 경우가 있다(게이트가 자동활성을 막은 eval_block 등).
    #   그래서 예외로 죽은 경우(reason="exception")만 failed 로 적는다.
    _deploy = out.get("deploy") or {}
    _deploy_crashed = (_deploy.get("reason") == "exception")

    # 학습·게이트 성공 → 상태 completed (소비 성패와 무관 — 소비는 아래 별도 처리).
    # [2026-08-16] 어느 모델이 나왔는지 학습 작업 기록에 남긴다. 종전에는 이 연결이
    # 한 방향뿐이라(tad_mm_mdl_ver_mng.training_run_id 만) `GET /train/jobs` 의
    # model_version 이 항상 null 이었다 - KL 서버에서 재학습이 완주해 v-24c7c02c 가
    # 정상 등록됐는데도 화면에서는 "어떤 모델이 나왔는지" 를 볼 수 없었다.
    _mv = _deploy.get("version_id")
    if _deploy_crashed:
        _mark_training_run(
            run_uuid,
            status="failed",
            error="학습은 끝났으나 모델 등록/게이트가 예외로 실패했다 "
                  "(register_and_gate_model). 등록된 모델이 없다 - 워커 로그의 "
                  "register_and_gate_model failed 스택을 볼 것.",
            duration_sec=int(_time.monotonic() - _run_t0),
            # 학습이 모은 것은 버리지 않는다 - 실패해도 무엇을 했는지는 남겨야 한다.
            final_metrics={
                "corrections_incorporated": len(incorporated_ids),
                "deploy": out.get("deploy"),
            },
        )
    else:
        _mark_training_run(
            run_uuid,
            status="completed",
            model_version=_mv,
            duration_sec=int(_time.monotonic() - _run_t0),
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


@celery_app.task(name="koipa.retention_purge_tick")
def retention_purge_tick() -> dict:
    """운영 로그 보존기간 삭제 — beat 가 매일 호출. 기본 OFF(설정으로 켠다).

    파티션을 쓰지 않기로 하면서(2026-09-05) 오래된 감사로그·LLM 사용량을 지우는 자리가
    비었다. services/retention.purge_expired 가 월 단위로 끊어 앞에서부터 연속 삭제한다.

    status='disabled' 면 아무것도 안 한다 — 그것이 기본값이다(감사 증빙 보호).
    truncated 가 있으면 한 틱 상한(max_slices)에 걸린 것이라 다음 틱이 이어 지운다.
    """
    from koipa.services.retention import purge_expired  # noqa: PLC0415

    out = purge_expired()
    if out.get("status") == "ok" and out.get("deleted"):
        logger.info("retention_purge_tick: %s", out["deleted"])
    if out.get("truncated"):
        logger.info(
            "retention_purge_tick: 한 틱 상한에 걸림 — 다음 틱이 이어 지운다: %s",
            out["truncated"],
        )
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
    if not res.checked:
        # [2026-08-28] DB 미가용이면 게시하지 않는다 — 측정 불가를 broken=0 으로 올리면
        # AUDIT_CHAIN_INTEGRITY_OK 가 1 로 세팅돼 P0 AuditChainBroken 알람이 영구 침묵한다.
        # 직전 게시값이 TTL 안에서 유지되고, 노후는 별도 stale 알람이 잡는다.
        # (ensure_partitions_tick 과 동일 규율)
        logger.warning(
            "verify_audit_chain_tick: DB 미가용 — 무결성 신호 게시 skip(거짓 all-clear 방지)"
        )
        return {
            "checked": False,
            "status": "db_unavailable",
            "total_rows": 0,
            "verified": 0,
            "broken": 0,
            "ok": None,
        }

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


@celery_app.task(
    name="koipa.index_document_vector",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def index_document_vector(self: Any, doc_id: str, force: bool = False) -> dict:
    """문서 대표 벡터를 색인한다 — 유사 문서 조회의 재료를 만든다.

    큐를 타는 이유는 비용이다. 실측(2026-09-09, KURE-v1 CPU 6스레드) 청크당 0.51초,
    100쪽 문서면 약 2분이다. 업로드 응답에 그만큼을 얹으면 검수 화면이 멈춘다.

    **실패해도 업로드·분류·검수는 그대로다.** 유사 문서 조회는 참고 기능이고, 벡터는
    본문에서 언제든 다시 만들 수 있는 파생물이다. 그래서 재시도 두 번 뒤에도 안 되면
    예외를 올려 celery 가 기록하게 두되, 호출부(인제스트)는 이 태스크의 성패를 기다리지
    않는다. 나중에 다시 색인하려면 같은 태스크를 force=True 로 부르면 된다.

    payload 에 본문을 싣지 않고 doc_id 만 보낸다 — 워커가 DB 에서 청크를 읽는다.
    큐에 30MB 본문이 흐르지 않게, 그리고 색인 시점의 최신 청크를 쓰게.
    """
    from koipa.services.document_vector_service import index_document  # noqa: PLC0415

    try:
        return index_document(doc_id, force=force)
    except Exception as exc:  # noqa: BLE001
        logger.warning("문서 벡터 색인 실패 — doc_id=%s err=%s", doc_id, exc)
        raise self.retry(exc=exc, countdown=10 * (self.request.retries + 1)) from exc
