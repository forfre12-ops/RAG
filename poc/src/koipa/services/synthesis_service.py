"""Synthesis service — 합성 생성 큐 + 검수 워크플로우.

PoC: /synth/generate는 작업 등록만 (실 생성은 Celery synthesize_batch 또는 dryrun mode).
/synth/queue·/synth/{id}/review는 SampleDocument(DB) 진실 소스.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import uuid
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from koipa.db import session_scope
from koipa.repositories import ClassifyRepo, SynthRepo
from koipa.schemas.synthesis import (
    SynthGenerateRequest,
    SynthGenerateResponse,
    SynthJobStatus,
    SyntheticDocItem,
    SynthQueueResponse,
    SynthReviewRequest,
    SynthReviewResponse,
)
from koipa.services.async_classify_service import _celery_dispatch_available
from koipa.services.job_store import get_default_store

logger = logging.getLogger(__name__)


def _grade_code(grade) -> str:
    """Grade 열거형/문자열 어느 쪽이 와도 코드 문자열로 — 로그·오류 메시지용."""
    return getattr(grade, "value", grade)

# 추정 비용 (development phase, USD/문서) — Claude Sonnet 4.6 기준
# [2026-09-05] 키를 팩토리 이름으로 맞췄다. 종전 vllm_qwen·vllm_exaone 은 build_provider 가
# 모르는 이름이라 API 스키마에서 걷혔다(정본 = config._VALID_LLM_PROVIDER).
# 자체호스팅(로컬 endpoint)은 API 과금이 없어 0 이다.
COST_PER_DOC_USD = {
    "anthropic": 0.012,
    "openai": 0.010,
    "google": 0.008,
    "gemini": 0.008,
    "vllm": 0.0,          # 자체호스팅
    "local_openai": 0.0,
    "ollama": 0.0,
    "lm_studio": 0.0,
    "noop": 0.0,
}

# [W6] 학습 편입 금지 마커 — 검수 승인분이라도 이 본문 출처는 학습셋에서 배제한다.
#   noop_fallback = placeholder 본문(실 LLM 없이 파이프라인 연결 테스트), llm_nonjson = 실 LLM이
#   비-JSON 원문을 줘 raw를 body로 쓴 경우. 둘 다 "정상 생성 문서"가 아니므로 학습에 새면 합성
#   노이즈로 작용한다. 정상 생성은 label_source=None(그 외 값도 허용). P0#1에서 이 마커를 검수큐에
#   보존해 둔 목적이 바로 이 하드 위생 게이트다.
TRAINING_EXCLUDED_LABEL_SOURCES = frozenset({"noop_fallback", "llm_nonjson"})

# [2026-09-05] **provider 로도 막는다.** 위 마커만으로는 못 막는 구멍이 있었다 — 직접 실행 확인:
#   LLM_PROVIDER=noop 생성 → label_source=None · 게이트 통과(학습편입가능=True)
# 마커는 **파싱 실패 경로에서만** 붙는데(generator.py 의 fallback), NoopProvider 는 파싱되는
# JSON 을 준다. 그래서 "테스트용 더미"라고 스스로 선언한 산출물이 승인만 되면 학습셋에 들어갔다.
# 게다가 결정론적이라 같은 본문이 중복으로 들어간다.
#
# provider 는 생성 시점에 확정돼 tad_sm_syn_doc_mng.llm_provider 로 보존된다 — 파싱 성공
# 여부와 무관한 축이라 마커의 구멍을 덮는다.
TRAINING_EXCLUDED_PROVIDERS = frozenset({"noop"})

# [2026-09-05] **게이트를 못 돌린 배치**도 학습에 넣지 않는다(monimo 세션 지적).
# 워커의 누출 게이트는 fail-open 이다 — screen_batch 가 예외를 내면 전량을 검수큐로 넣고
# batch_verdict='gate_error' 만 남긴다. 생성 결과를 버리지 않는 판단은 맞지만, 그 문서가
# 승인만 되면 **검사받지 않은 채로** 학습셋에 들어갔다.
#
# ⚠ gate_error 는 "게이트를 못 돌렸다"이지 "누출이 있다"가 아니다 — 사유를 잡음과 섞지
#   않는다(build_training_rows 가 사유별로 센다).
TRAINING_EXCLUDED_VERDICTS = frozenset({"gate_error"})


# [2026-09-05] **배치 게이트는 코퍼스를 재지 않는다.** screen_batch 는 생성 요청 한 건
# (docs 1~500)을 재는데, 학습 코퍼스는 여러 배치의 승인분이 합쳐진 다른 집합이다.
# 게다가 24건 미만 배치는 코퍼스 지표를 **아예 계산하지 않는다**
# (synth_quality.MIN_DOCS_FOR_CORPUS_METRICS) — 작은 배치에서 거짓 양성이 나기 때문이다.
#
# 그래서 20건씩 나눠 요청하면 게이트는 매번 "표본 부족"으로 통과시키고, 합쳐진 코퍼스는
# 아무도 재지 않는다. 실측(2026-09-05): 리포의 학습셋 중 **23개**가 20건 배치로는 전부
# 'too_small_for_corpus_metrics' 인데 합치면 'corpus_leak' 이다. 예:
#     datasets/labeled_p1_v5_masked/train.jsonl  n=400  length_only_1nn 0.570 (임계 0.55)
#
# 여기서는 **재기만 한다.** 막을지는 호출부가 정한다 — dataset_leakage 모듈이 audit 과
# check_or_raise 를 나눠 둔 것과 같은 분담이다. 값을 반환 dict 에 실어 빌더가 인쇄·차단한다.
def _corpus_leakage(rows: list[dict]) -> dict:
    """합쳐진 학습 코퍼스의 누출 지표. 판정하지 않고 사실만 낸다."""
    from koipa.dataset_leakage import audit  # noqa: PLC0415 - 지연 import(무거운 모듈 회피)

    try:
        return audit((str(r.get("label") or ""), str(r.get("text") or "")) for r in rows)
    except Exception as exc:  # noqa: BLE001
        # 계량이 깨져도 학습셋 방출은 막지 않는다. 다만 **조용히 통과시키지 않는다** —
        # 빌더가 이 키를 보고 "재지 못했다"를 인쇄한다(fail-open 을 눈에 보이게).
        logger.warning("코퍼스 누출 계량 실패: %s", exc)
        return {"documents": 0, "error": "%s: %s" % (type(exc).__name__, exc)}


def _batch_verdict(quality_report) -> str | None:
    """행에 남은 게이트 판정. 워커가 quality_report JSON 에 넣는다."""
    if isinstance(quality_report, dict):
        v = quality_report.get("batch_verdict")
        return str(v) if v else None
    return None


def _coverage_at_request(quality_report) -> dict | None:
    """이 문서를 만들 때 그 (등급 x 도메인) 칸이 얼마나 얇았는가.

    합성의 쓸모는 '빈 칸 채우기'로 좁혀져 있는데(양으로 늘리는 것은 실측으로 막혔다),
    학습 행에 그 근거가 없으면 나중에 사람이 서명한 골든셋이 생겨도 **빈 칸 채우기가
    실제로 도움이 됐는지 되짚을 수 없다.** 워커가 요청 시점의 격자를 행에 박아 둔다.
    """
    if isinstance(quality_report, dict):
        cell = quality_report.get("coverage_at_request")
        return cell if isinstance(cell, dict) else None
    return None


def _is_training_admissible(
    label_source: str | None,
    llm_provider: str | None = None,
    batch_verdict: str | None = None,
) -> bool:
    """검수 승인 합성 샘플이 학습셋에 편입 가능한지 — 본문 출처·검사 위생.

    세 축으로 막는다:
      · label_source   noop_fallback / llm_nonjson  (파싱 실패로 만들어진 본문)
      · llm_provider   noop                          (테스트 전용 더미 provider 산출물)
      · batch_verdict  gate_error                    (누출 게이트를 **못 돌린** 배치)

    셋 다 기본값이 있어 옛 호출은 깨지지 않는다 — 인자를 안 주면 그 축은 검사하지 않는다.
    """
    if (llm_provider or "").strip().lower() in TRAINING_EXCLUDED_PROVIDERS:
        return False
    if (batch_verdict or "").strip() in TRAINING_EXCLUDED_VERDICTS:
        return False
    return label_source not in TRAINING_EXCLUDED_LABEL_SOURCES


# 판 이름 = **생산 조건까지 담은 내용 해시**.
#
# [2026-09-05] 앞선 판은 doc_id·label·text 만 넣었다. 그러면 같은 문서 집합이라도 **다른
# 모델·다른 프롬프트·다른 품질 기준으로 만든 것이 같은 판 이름**을 갖는다. 재현용 이름이
# 재현을 보장하지 못하는 셈이라, 학습 행에 보존하는 생산 조건을 함께 넣는다.
#
# 시각은 넣지 않는다 — 같은 내용이 매번 다른 판이 되면 되짚기가 무의미해진다.
def _dataset_version(rows: list[dict]) -> str:
    digest = hashlib.sha256()
    for r in sorted(rows, key=lambda x: str(x.get("doc_id"))):
        for key in (
            "doc_id", "label", "text",
            "llm_provider", "llm_model", "body_prompt_version",
            "qc_prompt_version", "quality_score",
        ):
            digest.update(str(r.get(key)).encode("utf-8"))
            digest.update(b"|")
        digest.update(b";")
    return "synth-%s-%d" % (digest.hexdigest()[:10], len(rows))


class SynthesisService:
    def __init__(self):
        self.jobs = get_default_store()

    def submit(self, req: SynthGenerateRequest) -> SynthGenerateResponse:
        logger.debug(
            "synth submit enter: target_grade=%s domain=%s count=%d provider=%s actor=%s",
            req.target_grade, req.domain, req.count, req.llm_provider, req.actor.user_id,
        )
        job_id = uuid.uuid4()
        unit = COST_PER_DOC_USD.get(req.llm_provider, 0.01)
        self.jobs.create(
            job_id,
            payload={
                "target_grade": req.target_grade.value,
                "domain": req.domain,
                "count": req.count,
                "llm_provider": req.llm_provider,
                "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "actor": req.actor.user_id,
            },
        )
        # 운영(브로커 가용): 실제 Celery synthesize_batch.delay() 발사 → worker가 생성·검수큐 적재.
        # 테스트/브로커 미가용/eager는 발사하지 않고 작업 등록만(동작·테스트 보존, dryrun 의미).
        #
        # [2026-09-05] 그 사실을 **응답으로도 알린다.** 종전에는 브로커가 없거나 발사가
        # 실패해도 202 만 돌려줘 호출자는 "등록만 되고 생성은 안 됨"을 알 수 없었다.
        dispatched = False
        dispatch_note = "브로커 미가용 — 작업 등록만 됨(생성은 일어나지 않는다)"
        if _celery_dispatch_available():
            try:
                from koipa.workers.tasks import synthesize_batch  # noqa: PLC0415

                # [2026-09-05] llm_provider 를 함께 넘긴다. 종전에는 job payload 와
                # 비용 추정에만 쓰이고 실제 생성은 전역 설정값으로 돌아, **요청자가 고른
                # 모델과 실제로 쓴 모델이 달라질 수 있었다.**
                synthesize_batch.delay(
                    req.target_grade.value,
                    req.count,
                    domain=req.domain,
                    job_id=str(job_id),
                    llm_provider=req.llm_provider,
                )
                logger.info("synth enqueued to celery: job_id=%s count=%d", job_id, req.count)
                dispatched = True
                dispatch_note = None
            except Exception as exc:  # noqa: BLE001
                dispatch_note = "발사 실패 — 작업 등록만 됨(생성은 일어나지 않는다): %s" % (
                    type(exc).__name__,
                )
                logger.warning(
                    "celery enqueue failed for synth — left as registered: job_id=%s",
                    job_id, exc_info=True,
                )
        logger.info(
            "synth submit done: job_id=%s count=%d est_cost_usd=%.4f",
            job_id, req.count, round(unit * req.count, 4),
        )
        return SynthGenerateResponse(
            synth_job_id=job_id,
            expected_count=req.count,
            estimated_cost_usd=round(unit * req.count, 4),
            dispatched=dispatched,
            dispatch_note=dispatch_note,
        )

    def queue(
        self,
        *,
        status: str = "pending",
        limit: int = 50,
        offset: int = 0,
    ) -> SynthQueueResponse:
        # tenant 제거: 격리는 KL 포털 전담 — 합성 큐는 전역 네임스페이스.
        logger.debug(
            "synth queue enter: status=%s limit=%d offset=%d",
            status, limit, offset,
        )
        # status 매핑: API 'pending' → DB 'pending_review'
        db_status = {"pending": "pending_review", "approved": "approved", "rejected": "rejected"}.get(status, "pending_review")

        try:
            with session_scope() as db:
                repo = SynthRepo(db)
                # [C1] pending/approved/rejected 모두 조회 지원. 종전엔 승인/반려가 silent
                # empty([],0)로 떨어져 운영자 검수 이력 조회가 무음 함정이었다.
                samples = repo.list_by_status(db_status, limit=limit, offset=offset)
                # total = limit/offset 무관 전체 건수 (페이지네이션 메타)
                total = repo.count_by_status(db_status)
                items = [
                    SyntheticDocItem(
                        synth_id=s.sample_id,
                        target_grade=self._level_id_to_code(db, s.target_level_id),
                        domain=s.doc_type,
                        llm_provider=s.llm_provider,
                        llm_model=s.llm_model,
                        quality_score=float(s.quality_score) if s.quality_score is not None else None,
                        review_status=s.review_status,
                        corrected_grade=(
                            self._level_id_to_code(db, s.corrected_level_id)
                            if s.corrected_level_id else None
                        ),
                        preview=(s.generated_content or "")[:2000],
                        created_at=s.created_at.isoformat() if s.created_at else None,
                        # 시험 대역은 이 속성을 안 갖고 있을 수 있다 — 없으면 표식 없음.
                        label_source=getattr(s, "label_source", None),
                    )
                    for s in samples
                ]
                return SynthQueueResponse(total=total, items=items)
        except SQLAlchemyError as exc:
            logger.warning("synth queue skipped: %s", exc)
            return SynthQueueResponse(total=0, items=[])

    def review(self, synth_id: uuid.UUID, req: SynthReviewRequest) -> Optional[SynthReviewResponse]:
        logger.debug(
            "synth review enter: synth_id=%s decision=%s actor=%s",
            synth_id, req.decision, req.actor.user_id,
        )
        try:
            with session_scope() as db:
                repo = SynthRepo(db)
                approved = req.decision == "approve"

                # [2026-08-24] corrected_grade 를 실제로 적용한다. 종전에는 요청으로 받고도
                # 아무 데도 쓰지 않아, 검수자가 등급을 고쳐 승인해도 학습행은 원래 목표
                # 등급으로 만들어졌다(사람 교정이 조용히 버려지는 경로).
                corrected_level_id: int | None = None
                if approved and req.corrected_grade is not None:
                    corrected_level_id = ClassifyRepo(db).level_id_by_code(req.corrected_grade)
                    if corrected_level_id is None:
                        # 등급체계에서 비활성화된 코드로 교정하려는 경우. 조용히 무시하면
                        # 예전 결함을 이름만 바꿔 되풀이하는 것이라 호출부에 알린다.
                        raise ValueError(
                            f"corrected_grade '{_grade_code(req.corrected_grade)}' 는 "
                            "현재 등급체계에 없습니다"
                        )

                sd = repo.review(
                    synth_id,
                    approved=approved,
                    reviewed_by=req.actor.user_id,
                    rejection_reason=req.comment if not approved else None,
                    corrected_level_id=corrected_level_id,
                )
                if sd is None:
                    logger.info("synth review: sample not found — synth_id=%s", synth_id)
                    return None
                applied_level_id = sd.corrected_level_id or sd.target_level_id
                applied_grade = self._level_id_to_code(db, applied_level_id)
                logger.info(
                    "synth review done: synth_id=%s final_status=%s applied_grade=%s corrected=%s",
                    synth_id, sd.review_status, applied_grade,
                    sd.corrected_level_id is not None,
                )
                return SynthReviewResponse(
                    synth_id=synth_id,
                    final_status=sd.review_status,
                    applied_grade=applied_grade,
                    # [2026-09-05] 이 문서가 들어간 판 **전부**. 단일 값 필드는 한 문서가
                    # 여러 판에 들어간 이력을 표현하지 못했고 늘 비어 있었다.
                    dataset_versions=(
                        repo.dataset_versions_of(synth_id)
                        if hasattr(repo, "dataset_versions_of") else []
                    ),
                )
        except SQLAlchemyError as exc:
            logger.warning("synth review skipped: synth_id=%s err=%s", synth_id, exc)
            return None

    def job_status(self, synth_job_id) -> "SynthJobStatus":
        """생성 작업 1건 — 상태 + 그 작업이 만든 문서.

        [2026-09-05] 종전에는 물을 수 없던 질문이다. 응답이 synth_job_id 를 주는데
        조회 경로가 없었고 문서 쪽에도 job 칸이 없었다.
        """
        jid = uuid.UUID(str(synth_job_id))
        rec = {}
        try:
            rec = self.jobs.get(jid) or {}
        except Exception:  # noqa: BLE001
            logger.warning("잡 조회 실패: job_id=%s", jid, exc_info=True)

        sample_ids: list = []
        try:
            with session_scope() as db:
                sample_ids = SynthRepo(db).samples_of_job(jid)
        except SQLAlchemyError as exc:
            logger.warning("잡 문서 조회 실패: job_id=%s err=%s", jid, exc)

        return SynthJobStatus(
            synth_job_id=jid,
            status=str(rec.get("status") or "unknown"),
            sample_ids=sample_ids,
            persisted=rec.get("persisted"),
            leakage_gate=rec.get("leakage_gate"),
            llm_fallback=rec.get("llm_fallback"),
            error=rec.get("error"),
        )

    def build_training_rows(
        self, *, limit: int | None = None, stamp_version: bool = False
    ) -> dict:
        """[W6] 검수 승인 + 정상 산출 합성 샘플 → 학습 행. generate→queue→review→train 루프 마감.

        사람이 승인한 합성 문서를 학습셋 행({doc_id,text,label,source,domain})으로 변환한다.
        본문 출처 마커로 placeholder(noop_fallback)·비-JSON(llm_nonjson) 산출을 배제해 합성
        노이즈의 학습 유입을 차단한다(하드 위생 게이트 — P0#1이 보존한 label_source의 소비처).
        스냅샷 시맨틱: 매 호출이 승인 전체에서 재빌드(결정적·중복 없음).

        [2026-09-05] 판 이름(dataset_version)을 함께 낸다. 내용 해시라 같은 승인 집합이면
        같은 값이 나온다 — 시각이 아니라 내용으로 판을 가른다. stamp_version=True 면 그 값을
        각 승인본 행에 되써서 "이 문서가 어느 셋에 들어갔나"를 되짚을 수 있게 한다.
        **자동 학습 편입이 아니다** — 방출한 것을 기록만 한다.

        Returns: {"rows":[...], "approved_total":n, "included":n,
                  "excluded_noise":n, "excluded_empty":n, "excluded_gate_error":n,
                  "grade_corrected":n, "dataset_version":str, "corpus_leakage":{...}}
                 — 무음 드롭 없이 사유별 카운트 노출.

        [2026-09-05] corpus_leakage 를 함께 낸다. 생성 시점 배치 게이트는 요청 한 건만
        재고 24건 미만이면 코퍼스 지표를 아예 계산하지 않는다 — 합쳐진 이 코퍼스는
        지금까지 아무도 재지 않았다.
        """
        try:
            with session_scope() as db:
                repo = SynthRepo(db)
                approved_total = repo.count_by_status("approved")

                # 승인분 전량 로드(검수 큐는 사람 처리량에 묶여 규모 작음) — 페이지네이션 방어.
                samples = []
                page, page_size = 0, 1000
                while True:
                    batch = repo.list_by_status(
                        "approved", limit=page_size, offset=page * page_size
                    )
                    if not batch:
                        break
                    samples.extend(batch)
                    if len(batch) < page_size:
                        break
                    page += 1

                # level_id → code 매핑 1회 선적재(행마다 4쿼리 방지).
                crepo = ClassifyRepo(db)
                code_by_level: dict[int, str] = {}
                for code in ("TS", "S1", "S2", "S3"):
                    lid = crepo.level_id_by_code(code)
                    if lid is not None:
                        code_by_level[lid] = code

                rows: list[dict] = []
                excluded_noise = 0
                excluded_empty = 0
                # 게이트를 못 돌린 건은 **따로** 센다 — 잡음과 섞으면 게이트가 몇 번
                # 깨졌는지를 나중에 셀 수 없다(rag-f1 지적).
                excluded_gate_error = 0
                # [2026-09-06] 누출 의심 배치에서 온 승인분은 **막지 않고 센다.**
                #   누출은 배치 구성의 성질이라, 그 문서가 합본에서도 누출을 만드는지는
                #   합본을 봐야 안다 — 빌더가 합쳐진 코퍼스에 검사를 다시 돌린다
                #   (--strict 면 exit 1). 여기서 행 단위로 영구 차단하면 과차단이 자리만
                #   옮기는 셈이고, 그 문서를 다른 조합으로 다시 쓸 길이 막힌다.
                #   대신 몇 건이 그 출신인지 **반드시 보이게** 낸다(무음 통과 금지).
                from_leaky_batch = 0
                for s in samples:
                    _verdict = _batch_verdict(getattr(s, "quality_report", None))
                    if _verdict == "corpus_leak":
                        from_leaky_batch += 1
                    if _verdict in TRAINING_EXCLUDED_VERDICTS:
                        excluded_gate_error += 1  # 검사받지 않은 문서 — 잡음과 다른 사유
                        continue
                    if not _is_training_admissible(
                        s.label_source, getattr(s, "llm_provider", None), _verdict
                    ):
                        excluded_noise += 1  # noop_fallback/llm_nonjson/noop = 학습 편입 금지
                        continue
                    text = (s.generated_content or "").strip()
                    if not text:
                        excluded_empty += 1  # 빈 본문 방어(무음 드롭 금지·카운트 노출)
                        continue
                    # 검수자가 고친 등급이 있으면 그것이 라벨이다. 사람이 승인 단계에서
                    # 내린 판단이 생성 요청 시점의 목표 등급보다 뒤이고 더 정확하다.
                    label_level_id = s.corrected_level_id or s.target_level_id
                    rows.append({
                        "doc_id": str(s.sample_id),
                        "text": text,
                        "label": code_by_level.get(label_level_id, "S3"),
                        "source": "synthetic",
                        "domain": s.doc_type,
                        "label_source": s.label_source,
                        "grade_corrected": s.corrected_level_id is not None,
                        # [2026-09-05] 생산 이력 — 학습 행에서도 되짚을 수 있게 함께 낸다.
                        #
                        # 기존 학습셋 2,554행에는 이 칸이 없어 "어느 LLM 산인가"를 데이터에서
                        # 되짚을 수 없다(로컬 대 상용 비교를 하려 해도 기준선이 없다).
                        # 앞으로 만드는 행부터는 남는다.
                        "llm_provider": getattr(s, "llm_provider", None),
                        "llm_model": getattr(s, "llm_model", None),
                        "body_prompt_version": getattr(s, "body_prompt_version", None),
                        "qc_prompt_version": getattr(s, "qc_prompt_version", None),
                        # 만든 근거. 나중에 "빈 칸 채우기가 도움이 됐나"를 되짚을 유일한 실마리다.
                        "coverage_at_request": _coverage_at_request(
                            getattr(s, "quality_report", None)
                        ),
                        "quality_score": (
                            float(_qs) if (_qs := getattr(s, "quality_score", None)) is not None
                            else None
                        ),
                    })
                    if limit is not None and len(rows) >= limit:
                        break

                grade_corrected = sum(1 for r in rows if r["grade_corrected"])
                logger.info(
                    "synth training rows built: approved=%d included=%d "
                    "excluded_noise=%d excluded_empty=%d grade_corrected=%d "
                    "from_leaky_batch=%d",
                    approved_total, len(rows), excluded_noise, excluded_empty,
                    grade_corrected, from_leaky_batch,
                )
                dataset_version = _dataset_version(rows)

                if stamp_version and rows:
                    added = repo.record_dataset_membership(
                        [r["doc_id"] for r in rows], dataset_version,
                    )
                    logger.info(
                        "학습셋 판 기록(append-only): version=%s rows=%d added=%d",
                        dataset_version, len(rows), added,
                    )

                return {
                    "rows": rows,
                    "dataset_version": dataset_version,
                    "approved_total": approved_total,
                    "included": len(rows),
                    "excluded_noise": excluded_noise,
                    "excluded_empty": excluded_empty,
                    # 누출 게이트를 못 돌린 배치의 건수. "게이트가 걸렀다"와 다른 사실이라
                    # 잡음과 섞지 않는다 — 이 값이 0 이 아니면 게이트가 깨진 적이 있다.
                    "excluded_gate_error": excluded_gate_error,
                    # 누출 의심 배치(corpus_leak)에서 온 승인분 수. **막지 않고 센다** -
                    # 누출은 배치 구성의 성질이라 합본에서도 그런지는 합본을 봐야 안다
                    # (아래 corpus_leakage 가 그 자리다). 0 이 아니면 빌더가 그 사실을
                    # 인쇄해야 한다 - 조용히 섞이면 나중에 되짚을 수 없다.
                    "from_leaky_batch": from_leaky_batch,
                    # 검수자가 등급을 고친 건수. 카운트로 내보내야 "교정이 반영됐다"를
                    # 학습셋을 열어 보지 않고도 확인할 수 있다.
                    "grade_corrected": grade_corrected,
                    # 합쳐진 코퍼스의 누출 지표. 배치 게이트가 재지 못하는 자리다(위 주석).
                    "corpus_leakage": _corpus_leakage(rows),
                }
        except SQLAlchemyError as exc:
            logger.warning("build_training_rows skipped: %s", exc)
            # [2026-09-05] 이 경로에도 dataset_version 을 낸다. 앞선 판은 이 키를 빼서
            # 호출 스크립트가 무조건 읽다가 KeyError 로 끝났다 — DB 오류가 KeyError 로
            # 둔갑하면 진짜 사유가 가려진다.
            return {
                "rows": [],
                "dataset_version": _dataset_version([]),
                "approved_total": 0,
                "included": 0,
                "excluded_noise": 0,
                "excluded_empty": 0,
                "excluded_gate_error": 0,
                "grade_corrected": 0,
                # 오류 경로에도 같은 키를 낸다 — 호출부가 조건 없이 읽는다.
                "corpus_leakage": {"documents": 0},
            }

    @staticmethod
    def _level_id_to_code(db, level_id: int) -> str:
        repo = ClassifyRepo(db)
        for code in ("TS", "S1", "S2", "S3"):
            if repo.level_id_by_code(code) == level_id:
                return code
        return "S3"
