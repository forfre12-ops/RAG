"""ClassifyService — OpenAPI POST /classify의 도메인 오케스트레이션.

흐름:
  1. PreprocessPipeline로 텍스트 정규화
  2. InferencePipeline로 등급 예측 + 근거 추출
  3. **best-effort DB 영속화** (doc 존재 + DB 가용 시에만)
     실패해도 응답은 정상 — warnings에 안내만 추가 (테스트·dryrun 환경 보호)
  4. ClassifyResponse 반환 (inference_id = DB classification_id 또는 신규 UUID)
"""

from __future__ import annotations

import logging
import os
import uuid
import warnings
from typing import Callable, Optional

from sqlalchemy.exc import SQLAlchemyError

from lloydk.modules.m2_preprocess.chunker import Chunk as _PreprocessChunk
from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline
from lloydk.modules.m5_inference.pipeline import InferencePipeline, InferenceResult
from lloydk.obs.otel import span  # #29: 비즈니스(수동) span 헬퍼 — 미설치/미활성 시 no-op
from lloydk.repositories.chunk_repo import ChunkRepo
from lloydk.repositories.classify_repo import ClassifyRepo
from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse

# A3: SSE/스트리밍에서 진척 단계를 실제로 노출하기 위한 콜백 시그니처.
# 호출 측이 None을 넘기면 기존 동작과 동일(no-op).
StageCallback = Callable[[str], None]

logger = logging.getLogger(__name__)


def _try_uuid_str(value: str | None) -> "uuid.UUID | None":
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _active_model_dir() -> Optional[str]:
    """활성 ModelVersion.model_uri가 로컬 디렉토리면 그 경로, 아니면 None (C-ver 서빙 배선).

    register_and_gate/rollback이 활성화한 버전을 서빙이 실제로 집어들게 한다. model_uri가
    없거나(미등록)·원격 URI거나·존재하지 않는 경로면 None → 호출부가 env로 폴백(동작 보존).
    DB 미가용·예외는 모두 None(폴백). 베스트에포트.
    """
    try:
        from pathlib import Path  # noqa: PLC0415

        from lloydk.db import session_scope  # noqa: PLC0415
        from lloydk.repositories import TrainingRepo  # noqa: PLC0415
        with session_scope() as db:
            active = TrainingRepo(db).get_active()
            uri = getattr(active, "model_uri", None) if active else None
        if uri:
            p = Path(str(uri))
            if p.exists() and p.is_dir():
                logger.info("serving from active ModelVersion: %s", uri)
                return str(p)
            logger.warning("active ModelVersion model_uri not a local dir (%s) — env fallback", uri)
    except Exception:  # noqa: BLE001
        logger.debug("active model resolution failed — env fallback")
    return None


def _resolve_serving_model_dir() -> Optional[str]:
    """서빙 모델 디렉토리 결정 — 활성 ModelVersion 우선, 없으면 env classifier_model_dir.

    settings.serving_prefer_active_model=False 또는 TESTING이면 env 직행(빠름·결정론).
    """
    from lloydk.config import settings  # noqa: PLC0415
    env_dir = getattr(settings, "classifier_model_dir", "") or None
    if os.environ.get("TESTING") or not getattr(settings, "serving_prefer_active_model", True):
        return env_dir
    return _active_model_dir() or env_dir


class ClassifyService:
    _instance: "ClassifyService | None" = None

    def __init__(self):
        self.preprocess = PreprocessPipeline()
        # Phase 3 (5070 Ti 풀가동) — 학습 가중치 디렉토리가 settings 또는
        # 환경변수에 명시되면 InferencePipeline 이 자동 로드. 미명시·미존재 시
        # rule-fallback 그대로(기존 동작 호환).
        # [C-ver] 활성 ModelVersion이 있으면 그 model_uri를 우선(activate/rollback이 서빙에 반영).
        model_dir = _resolve_serving_model_dir()
        self.inference = InferencePipeline(model_dir=model_dir)

    @classmethod
    def get_instance(cls) -> "ClassifyService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reload_model(self) -> dict:
        """서빙 추론 파이프라인을 **활성 ModelVersion 기준으로 재구성** (런타임 핫리로드).

        activate_model_version/rollback 후 프로세스 재기동 없이 새 모델을 서빙에 반영한다
        (싱글톤이라 init 시점 모델을 유지하던 한계 해소). admin 엔드포인트(POST /admin/model/reload)가
        호출. 반환: 재로드 후 model_dir·model_version·로드여부.
        """
        new_dir = _resolve_serving_model_dir()
        self.inference = InferencePipeline(model_dir=new_dir)
        loaded = getattr(self.inference, "_model", None) is not None
        version = (str(self.inference.model_dir.name)
                   if getattr(self.inference, "model_dir", None) else "rule-fallback")
        logger.info("classify model reloaded: dir=%s version=%s loaded=%s", new_dir, version, loaded)
        return {"reloaded": True, "model_dir": new_dir, "model_version": version, "model_loaded": loaded}

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def classify(
        self,
        req: ClassifyRequest,
        *,
        on_stage: StageCallback | None = None,
    ) -> ClassifyResponse:
        """텍스트를 등급 분류.

        on_stage: 단계 진입 시 호출되는 콜백. 단계 이름 = stages_emitted 참조.
                  SSE 스트리밍(/classify/stream) 등에서 진척 송신용. 없으면 no-op.
        """
        # #29: classify 진입 span — 비민감 식별값만(문서 유무·모델버전·use_rag).
        #      문서 본문 등 민감정보는 절대 부착하지 않는다. OTel 미활성 시 no-op.
        with span(
            "classify",
            **{
                "lloydk.has_doc_id": bool(req.doc_id),
                "lloydk.has_content": bool(req.content),
                "lloydk.use_rag": bool(req.use_rag),
                # model_version은 model_dir 폴더명에서 안전 도출(없으면 rule-fallback).
                # InferencePipeline 자체엔 model_version 속성이 없으므로 직접 참조 금지.
                "lloydk.model_dir": (
                    str(self.inference.model_dir.name)
                    if getattr(self.inference, "model_dir", None) else "rule-fallback"
                ),
            },
        ):
            notify = on_stage or (lambda _stage: None)

            logger.debug(
                "classify enter: doc_id=%s use_rag=%s content_len=%d",
                req.doc_id, req.use_rag, len(req.content or ""),
            )
            notify("extract")
            # content가 없으면 normalized_text_uri에서 읽어오기 (doc_id 전용 분류 경로)
            content = req.content
            if not content:
                content = self._fetch_content_by_doc_id(req.doc_id)
            if not content:
                # fail-SECURE (미탐 절대 금지): 본문을 못 읽으면 등급을 판단할 수 없다.
                # 과거엔 label="S3"(공개)로 폴백했는데, 이는 '읽지 못한 비밀문서를 공개로
                # 흘리는' 전형적 미탐 경로였다. 보수적으로 최고등급(Grade.TS)으로 격리하고
                # status를 needs_review로 두어 사람 검수 큐에 강제 노출 — 절대 공개(S3)로
                # 떨어뜨리지 않는다. (ClassifyResponse.label은 Grade 필수라 UNCLASSIFIED를
                # 표현할 수 없으므로, 가장 안전한 '최고등급 + 검수필요'로 표현한다.)
                from lloydk.schemas.common import Grade  # noqa: PLC0415
                top_grade = self._highest_grade()
                warnings_acc = [
                    f"content empty and doc_id={req.doc_id!r} not found in storage —"
                    f" cannot classify; fail-SECURE isolating at highest grade"
                    f" ({top_grade}) and routing to human_review (never defaults to public/S3)"
                ]
                return ClassifyResponse(
                    inference_id=uuid.uuid4(),
                    doc_id=req.doc_id,
                    label=Grade(top_grade),
                    confidence=0.0,
                    scores={top_grade: 1.0},
                    model_version="none",
                    elapsed_ms=0,
                    status="needs_review",
                    warnings=warnings_acc,
                )
            if req.text_already_preprocessed:
                cleaned = content
            else:
                notify("normalize")
                cleaned = self.preprocess.run_text(content)

            # 표적 2 (2026-05-29): chunks 생성. 영속화는 _try_persist 단계에서 doc 가용 시 시도.
            # PoC PreprocessPipeline.chunk()는 외부 의존 없이 항상 동작.
            try:
                chunks: list[_PreprocessChunk] = self.preprocess.chunk(cleaned) if cleaned else []
            except Exception as exc:  # noqa: BLE001
                logger.debug("classify chunk split failed (fallback []): %s", exc)
                chunks = []

            # ── Corrections 반영: DB에 검증된 라벨이 있으면 모델 추론 스킵 ───────────
            # human_review / koipa_case_based / nkt_designated 등 is_verified=True 라벨
            # 우선순위: human_review > koipa_case_based > llm_judge_consensus > ...
            verified_label = self._get_verified_label(req.doc_id)
            if verified_label is not None:
                from lloydk.schemas.common import Grade  # noqa: PLC0415
                inference_id = uuid.uuid4()
                logger.info(
                    "classify doc_id=%s: verified label applied (labeled_by=%s, level=%s) — inference skipped",
                    req.doc_id, verified_label.labeled_by, verified_label.level_code,
                )
                # Audit trail — best-effort DB 기록
                self._audit_verified_label(
                    doc_id=req.doc_id,
                    level_code=verified_label.level_code,
                    labeled_by=verified_label.labeled_by,
                    reviewer_id=verified_label.labeler_id,
                    inference_id=inference_id,
                )
                notify("finalize")
                return ClassifyResponse(
                    inference_id=inference_id,
                    doc_id=req.doc_id,
                    label=Grade(verified_label.level_code),
                    confidence=float(verified_label.confidence or 0.95),
                    scores={verified_label.level_code: float(verified_label.confidence or 0.95)},
                    model_version=f"human_review:{verified_label.labeled_by}",
                    elapsed_ms=0,
                    status="staging",
                    warnings=[
                        f"verified_label_applied: labeled_by={verified_label.labeled_by},"
                        f" reviewer={verified_label.labeler_id or 'system'},"
                        f" level={verified_label.level_code} — inference skipped"
                    ],
                )

            # InferencePipeline 내부에서 embed → retrieve → llm을 거치므로,
            # 진입/종료 양쪽에 신호를 보내 클라이언트가 long-stage 감지 가능.
            notify("embed")
            notify("retrieve" if req.use_rag else "llm")
            eff_meta = self._effective_metadata(req)
            pred = self.inference.run(
                text=cleaned,
                use_rag=req.use_rag,
                rag_namespace=req.rag_namespace,
                metadata=eff_meta,
                return_evidence=req.return_evidence,
            )
            notify("llm")

            warnings_acc = list(pred.warnings)
            # [번들 A] 메타데이터/출처 게이트 가시성 — 게이트 발동·ICD 메타 존재율을 best-effort
            # 노출. pred.warnings(pipeline 산출)만 본 시점에 측정해 서비스 재라우팅과 중복 없음.
            self._emit_gate_visibility_metrics(eff_meta, warnings_acc)
            notify("persist")
            inference_id, persist_warnings = self._try_persist(req, pred, chunks=chunks)
            warnings_acc.extend(persist_warnings)
            notify("finalize")

            # 저신뢰 검수 라우팅: confidence가 임계 미만이면 needs_review로 표시(거부 아님).
            # 데모 콘솔이 광고하는 'low-confidence 게이트'의 서버측 구현 — 검수자 큐 노출 근거.
            status = "staging"
            threshold = self._review_confidence_threshold()
            if float(pred.confidence) < threshold:
                status = "needs_review"
                warnings_acc.append(
                    f"low-confidence: confidence={float(pred.confidence):.2f} < {threshold:.2f}"
                    " — review recommended"
                )

            # [②·③ 충돌] 출처 cap(하향)이 강한 상향 신호(TS/S1)를 덮은 경우 — pipeline이 남긴
            # cap-conflict 신호 — confidence와 무관하게 검수 라우팅. 진짜 공개문서는 사람이
            # 즉시 확인하고, 내부 비밀의 '공개' 오태깅(silent miss)은 여기서 걸러진다.
            if status != "needs_review" and any("cap-conflict" in w for w in warnings_acc):
                status = "needs_review"
                warnings_acc.append(
                    "cap-conflict: source-cap overrode a high content grade — routed to human review"
                )

            # [sparse-evidence] 룰-폴백 자동확정이 빈약한 증거(단일·저가중 매치 1개)에 기댄 경우 —
            # pipeline이 남긴 sparse-evidence 신호 — confidence(단일매치=1.0일 수 있음)와 무관하게
            # 검수 라우팅. 단 하나의 약한 키워드로 최고신뢰 자동확정되는 silent FNR(골든셋 TS 5건)
            # 을 차단한다. 모델·LLM폴백 없는 폐쇄망 초기상태의 미보정 confidence 경로 방어.
            if status != "needs_review" and any("sparse-evidence" in w for w in warnings_acc):
                status = "needs_review"
                warnings_acc.append(
                    "sparse-evidence: rule auto-confirm rested on thin evidence — routed to human review"
                )

            # [metadata-access-conflict] ICD 접근범위가 제한적인데 내용 예측이 낮은 경우 — pipeline이
            # 남긴 신호 — confidence와 무관하게 검수 라우팅(ICD §4.4: 관리수준 높은 문서를 낮게
            # 자동확정하지 않음). security_marking 상향 floor는 pipeline에서 이미 등급에 반영됨.
            if status != "needs_review" and any("metadata-access-conflict" in w for w in warnings_acc):
                status = "needs_review"
                warnings_acc.append(
                    "metadata-access-conflict: restricted access_scope vs low predicted grade — routed to human review"
                )

            # [agreement-gate] 등급차등 + 룰·모델 합의 게이트 (opt-in, 기본 off).
            # conf 단독 자동확정은 신뢰성이 측정으로 부정됨(golden500: AUROC 0.58, 자동확정
            # 정밀도 63%, 고등급 미탐 46). 확신을 conf가 아니라 *독립 신호(룰 합의)*에서 얻는다:
            #   · 예측이 공개등급(S3): conf만으로 자동확정 허용(S3 conf 정밀도 94%, 과소분류 불가)
            #   · 예측이 그 외(TS/S1/S2): 룰엔진과 등급이 합의해야 자동확정, 불일치면 검수
            # 측정(golden500): 자동확정 정밀도 63→81%, 고등급 미탐 46→8. 등급은 무인으로 바꾸지
            # 않고 검수 라우팅만 한다. 룰 산출 실패는 silent 폴백(게이트가 죽어도 분류는 진행).
            if status != "needs_review":
                ag = self._agreement_gate(pred, cleaned)
                if ag is not None:
                    status = "needs_review"
                    warnings_acc.append(ag)

            # [llm-secondopinion] 모델 경로 사각지대 방어 (opt-in, 기본 off).
            # 학습모델이 비-TS 등급을 자동확정하려 할 때만 LLM 2차의견을 받아, LLM이 더 높은
            # (FNR-safe) 등급을 제시하면 needs_review로 라우팅한다. 보정해도 남는 '확신에 찬
            # 과소분류'(룰·모델 공통 사각지대, 예: 골든셋 TS-10/24)를 사람이 확인하게 만든다.
            # 등급은 무인으로 바꾸지 않음(검수 라우팅만). LLM 미가용·오류는 silent 폴백.
            if status != "needs_review":
                so = self._llm_second_opinion(cleaned, pred.label)
                if so is not None:
                    status = "needs_review"
                    warnings_acc.append(so)

            # [번들 E] kill-gate 안전브레이크 — kill-gate tripped 동안 고등급(TS/S1) 자동확정을
            # needs_review로 억제(등급 무변경·FNR-safe). 합성 평가신호로 운영을 자동정지하지 않고
            # '확신 있는 고등급도 사람이 한 번 더' 로만 보수화 — 조건 풀리면 자동 해제(opt-in 기본 OFF).
            if status != "needs_review":
                brake = self._kill_gate_brake(pred.label)
                if brake is not None:
                    status = "needs_review"
                    warnings_acc.append(brake)

            logger.info(
                "classify done: doc_id=%s inference_id=%s label=%s confidence=%.3f status=%s",
                req.doc_id, inference_id, pred.label, float(pred.confidence), status,
            )

            return ClassifyResponse(
                inference_id=inference_id,
                doc_id=req.doc_id,
                label=pred.label,
                confidence=pred.confidence,
                scores=pred.scores,
                evaluation_factors=pred.factors,
                factors_source=self._factors_source(warnings_acc),
                evidence=pred.evidence,
                rag_context_used=pred.rag_context,
                model_version=pred.model_version,
                elapsed_ms=0,
                status=status,
                warnings=warnings_acc,
            )

    @staticmethod
    def _highest_grade() -> str:
        """가장 높은(가장 비밀) 등급 코드 반환 — fail-SECURE 격리용.

        GradeRegistry.get_codes()는 level_order 오름차순(= 비밀이 가장 높은 등급이
        선두)으로 반환하므로 [0]이 최고등급. DB 미가용/빈 목록이면 Grade.TS로 폴백.
        """
        try:
            from lloydk.schemas.common import Grade, GradeRegistry  # noqa: PLC0415
            codes = GradeRegistry.get_codes()
            if codes:
                # Grade enum으로 표현 가능한 코드만 채택 (응답 schema가 Grade 필수)
                for code in codes:
                    try:
                        Grade(code)
                        return code
                    except ValueError:
                        continue
            return Grade.TS.value
        except Exception:  # noqa: BLE001
            return "TS"

    def _agreement_gate(self, pred, text: str) -> str | None:
        """등급차등 + 룰·모델 합의 게이트. 자동확정 부적격이면 검수 사유 문자열, 적격이면 None.

        반환 None = 라우팅 변경 없음(게이트 비활성 · 공개등급 예측 · 룰==모델 합의 · 룰산출 실패).
        - 공개등급(level_order 최하, 보통 S3) 예측: conf 단독 신뢰(정밀도 94%) → 합의 불요(None).
        - 그 외 등급(TS/S1/S2): 룰엔진 등급 != 모델 등급이면 검수 라우팅(불일치=확신 부족).
        rule==model로 자동확정된 TS/S1은 등급은 유지하되 운영상 표본감사 대상(별도 프로세스).
        모든 예외는 None으로 흡수 — 룰엔진이 죽어도 분류·기존 자동확정을 막지 않는다(fail-safe).

        룰등급은 pred.rule_grade(run()이 이미 산출한 **원시** 룰등급)를 재사용해 룰엔진 2회
        실행을 피한다(특히 semantic 시드 시 문서 임베딩 중복 방지). 미설정 시에만 text로 폴백
        재계산한다. 비교 기준은 보고서 'rule열'(eng.label(text).grade, 원시 룰등급)과 동일.
        """
        try:
            from lloydk.config import settings  # noqa: PLC0415
            if not getattr(settings, "agreement_gate_enabled", False):
                return None
            from lloydk.schemas.common import GradeRegistry  # noqa: PLC0415
            codes = GradeRegistry.get_codes()  # level_order asc(비밀이 선두) → [-1]=최하(공개)
            public_code = codes[-1] if codes else "S3"
            model_label = getattr(pred, "label", pred)
            model_code = model_label.value if hasattr(model_label, "value") else str(model_label)
            if model_code == public_code:
                return None  # 공개등급: conf 단독으로 신뢰(과소분류 위험 없는 최하등급)
            # run()이 노출한 원시 룰등급 재사용 — 없으면 폴백 재계산(룰엔진 1회).
            rule_g = getattr(pred, "rule_grade", None)
            if rule_g is None:
                rule_g = self.inference.labeling.engine.label(text).grade
            rule_code = rule_g.value if hasattr(rule_g, "value") else str(rule_g)
            if rule_code != model_code:
                return (
                    f"agreement-gate: model={model_code} vs rule={rule_code} disagree on "
                    "non-public grade — routed to human review (conf alone insufficient)"
                )
        except Exception:  # noqa: BLE001 — 룰엔진 미가용·오류는 기존 자동확정 유지(fail-safe)
            return None
        return None

    @staticmethod
    def _llm_second_opinion(text: str, model_label) -> str | None:
        """모델 자동확정(비-TS)에 대한 LLM 2차의견. 더 높은 등급 제시 시 검수 사유 문자열 반환.

        반환 None = 라우팅 변경 없음(게이트 비활성·이미 TS·LLM이 동급/하급·LLM 오류).
        FNR-safe: LLM이 **더 높은(낮은 order)** 등급을 제시할 때만 검수로 올린다(승급 아님).
        모든 예외는 None으로 흡수 — LLM이 죽어도 분류·기존 자동확정 동작을 막지 않는다(fail-safe).
        """
        try:
            from lloydk.config import settings  # noqa: PLC0415
            if not getattr(settings, "model_secondopinion_llm_enabled", False):
                return None
            from lloydk.schemas.common import GradeRegistry  # noqa: PLC0415
            order = GradeRegistry.get_order()
            top_code = next(iter(order), "TS")  # 가장 높은(비밀) 등급 코드
            model_code = model_label.value if hasattr(model_label, "value") else str(model_label)
            if model_code == top_code:
                return None  # 이미 최고등급 자동확정 — 과소분류 위험 없음
            from lloydk.modules.m3_labeling.llm_labeler import LLMLabeler  # noqa: PLC0415
            llm = LLMLabeler().label(text, purpose="second_opinion")
            if llm.grade not in order:
                return None
            if order.get(llm.grade, 99) < order.get(model_code, 99):
                return (
                    f"llm-secondopinion: model auto-confirmed {model_code} but LLM proposes higher "
                    f"{llm.grade} (conf={llm.confidence:.2f}) — routed to human review (FNR-safe)"
                )
        except Exception:  # noqa: BLE001 — LLM 미가용·오류는 기존 자동확정 유지(fail-safe)
            return None
        return None

    @staticmethod
    def _review_confidence_threshold() -> float:
        try:
            from lloydk.config import settings as _settings  # noqa: PLC0415
            return float(getattr(_settings, "review_confidence_threshold", 0.7))
        except Exception:  # noqa: BLE001
            return 0.7

    # ------------------------------------------------------------
    # Verified Label Audit Trail
    # ------------------------------------------------------------

    def _audit_verified_label(
        self,
        *,
        doc_id: str,
        level_code: str,
        labeled_by: str,
        reviewer_id: str | None,
        inference_id: "uuid.UUID",
    ) -> None:
        """검증된 라벨 적용 이벤트를 audit_log에 best-effort 기록."""
        try:
            from lloydk.db import SessionLocal  # noqa: PLC0415
            from lloydk.db.models import AuditLog  # noqa: PLC0415
            import datetime as _dt  # noqa: PLC0415

            db = SessionLocal()
            try:
                entry = AuditLog(
                    request_id=inference_id,
                    actor_id=reviewer_id or labeled_by,
                    actor_role="human_review",
                    action="verified_label_applied",
                    target_type="document",
                    target_id=doc_id,
                    payload_hash=None,
                    success=True,
                    occurred_at=_dt.datetime.now(_dt.timezone.utc),
                )
                db.add(entry)
                db.commit()
                logger.debug("audit verified_label_applied: doc_id=%s level=%s", doc_id, level_code)
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.debug("audit write failed (non-critical): %s", exc)
                self._inc_verified_label_audit_skip()
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_audit_verified_label skipped: %s", exc)
            self._inc_verified_label_audit_skip()

    @staticmethod
    def _inc_verified_label_audit_skip() -> None:
        """[QW] verified_label 감사 누락을 메트릭으로 가시화(best-effort). 결정론 경로의
        컴플라이언스 감사가 무음 누락되지 않게 — 값 상승 시 DB/감사 미들웨어 문제 신호."""
        try:
            from lloydk.api.prom_metrics import (  # noqa: PLC0415
                CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL,
            )
            CLASSIFY_VERIFIED_LABEL_AUDIT_SKIP_TOTAL.inc()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------
    # Verified Label Lookup (Corrections 반영)
    # ------------------------------------------------------------

    def _get_verified_label(self, doc_id_str: str) -> "object | None":
        """doc_id에 대한 검증된 DocumentLabel 반환.

        DB 가용 시 조회. 없거나 실패하면 None (모델 추론으로 계속).
        반환 타입: DocumentLabel with .level_code, .labeled_by, .confidence 속성.
        """
        try:
            from lloydk.db import SessionLocal  # noqa: PLC0415
            from lloydk.repositories.classify_repo import ClassifyRepo  # noqa: PLC0415
            from lloydk.db.models import ClassificationLevel  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            doc_uuid = _try_uuid_str(doc_id_str)
            if doc_uuid is None:
                return None

            db = SessionLocal()
            try:
                repo = ClassifyRepo(db)
                dl = repo.get_verified_document_label(doc_uuid)
                if dl is None:
                    # doc_id는 업로드마다 유니크 → 같은 내용 재업로드는 새 doc_id라 위에서 못 잡는다.
                    # 동일 file_hash(동일 바이트)의 다른 문서에 검증 라벨이 있으면 재사용(추론 스킵).
                    dl = self._verified_label_by_content(db, repo, doc_uuid)
                if dl is None:
                    return None
                # level_code 조회
                level = db.execute(
                    select(ClassificationLevel).where(
                        ClassificationLevel.level_id == dl.level_id
                    )
                ).scalar_one_or_none()
                if level is None:
                    return None
                # level_code를 dl에 동적으로 attach (반환값 단순화)
                dl.level_code = level.level_code
                return dl
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_get_verified_label failed (non-critical): %s", exc)
            return None

    @staticmethod
    def _verified_label_by_content(db, repo, doc_uuid) -> "object | None":
        """동일 내용(file_hash) 문서의 검증 라벨 재사용 — doc_id는 업로드마다 유니크하므로,
        같은 내용 재업로드(다른 doc_id)에도 사람이 검증한 등급을 적용한다.

        '유사'가 아니라 **정확히 동일 sha256**만 매칭하므로 다른 등급 전파 위험이 없다(동일
        바이트=동일 등급). settings.verified_label_content_reuse=False면 비활성(기존 doc_id-only).
        모든 예외는 None(추론으로 계속).
        """
        try:
            from lloydk.config import settings  # noqa: PLC0415
            if not getattr(settings, "verified_label_content_reuse", True):
                return None
            from lloydk.db.models import Document  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            cur = db.get(Document, doc_uuid)
            fh = getattr(cur, "file_hash", None) if cur is not None else None
            if not fh:
                return None
            stmt = select(Document.doc_id).where(
                Document.file_hash == fh, Document.doc_id != doc_uuid
            )
            for row in db.execute(stmt).all():
                sib_id = row[0]
                dl = repo.get_verified_document_label(sib_id)
                if dl is not None:
                    return dl
        except Exception as exc:  # noqa: BLE001
            logger.debug("_verified_label_by_content skipped: %s", exc)
            return None
        return None

    # ------------------------------------------------------------
    # Persistence (best-effort)
    # ------------------------------------------------------------

    def _try_persist(
        self,
        req: ClassifyRequest,
        pred: InferenceResult,
        *,
        chunks: list[_PreprocessChunk] | None = None,
    ) -> tuple[uuid.UUID, list[str]]:
        """Best-effort 영속화.

        반환: (inference_id, warning_list)
        - 성공: classifications.classification_id 사용
        - 실패: 새 UUID + warning에 사유 기록 (예외 안 던짐)

        chunks가 주어지고 doc가 가용하면 chunks 테이블에 영속화하고
        Evidence chunk_id를 진짜 chunks row UUID로 매핑(표적 2).
        """
        warns: list[str] = []
        doc_uuid = self._parse_doc_uuid(req.doc_id)

        if doc_uuid is None:
            warns.append(f"persistence skipped: doc_id={req.doc_id!r} is not a UUID")
            return uuid.uuid4(), warns

        # session_scope import는 함수 안에서 — settings.database_url 변경 가능성·테스트 격리
        try:
            from lloydk.db import session_scope  # noqa: PLC0415
        except ImportError as exc:
            self._inc_persist_failure("import_error")
            warns.append(f"persistence skipped: db module unavailable ({exc})")
            return uuid.uuid4(), warns

        try:
            # ── Step 1: classification 영속화 (자체 커밋) ──────────────────────
            # M-classify-tx: classification 을 evidence 와 같은 트랜잭션에 묶으면
            # add_evidence/add_rag_evidence 실패 시 이미 만든 classification 까지
            # 롤백돼 '분류는 됐는데 기록은 사라지는' 미탐성 손실이 생긴다. 그래서
            # classification(+chunks)은 여기서 먼저 commit 해 classification_id 를
            # 확보하고, evidence/RAG-evidence 는 아래 Step 2 에서 best-effort 로
            # 별도 트랜잭션에 적재한다(실패해도 분류 폐기 안 함).
            with session_scope() as db:
                repo = ClassifyRepo(db)
                if not repo.document_exists(doc_uuid):
                    self._inc_persist_failure("no_doc")
                    warns.append(
                        f"persistence skipped: doc_id={doc_uuid} not found"
                    )
                    return uuid.uuid4(), warns

                level_id = repo.level_id_by_code(pred.label)
                if level_id is None:
                    self._inc_persist_failure("no_level")
                    warns.append(f"persistence skipped: unknown level code {pred.label!r}")
                    return uuid.uuid4(), warns

                alternatives = [
                    {"level_code": code, "confidence": float(score)}
                    for code, score in pred.scores.items()
                    if code != pred.label.value
                ]

                # 표적 2: chunks 영속화 (있을 때만). 실패해도 classification 자체는 저장됨.
                chunk_repo = ChunkRepo(db)
                first_chunk_id: uuid.UUID | None = None
                chunk_count: int | None = None
                if chunks:
                    try:
                        ids = chunk_repo.upsert_chunks(
                            doc_id=doc_uuid,
                            chunks=chunks,
                            replace_existing=True,
                        )
                        chunk_count = len(ids)
                        first_chunk_id = ids[0] if ids else None
                    except Exception as exc:  # noqa: BLE001
                        # chunks insert 실패는 분류 자체에는 영향 없음
                        logger.warning("chunk upsert failed (continuing): %s", exc)
                        warns.append(f"chunks persist failed: {type(exc).__name__}")

                cls = repo.create_classification(
                    doc_id=doc_uuid,
                    model_version=pred.model_version,
                    predicted_level_id=level_id,
                    confidence=float(pred.confidence),
                    alternatives=alternatives,
                    chunk_count=chunk_count,
                    rag_used=bool(pred.rag_context),
                    rag_top_k=len(pred.rag_context) or None,
                )
                classification_id = cls.classification_id
                # Evidence chunk_id 결정: chunks가 영속화됐으면 진짜 첫 chunk_id, 아니면 임시 UUID
                evidence_default_chunk = first_chunk_id or uuid.uuid4()
            # ← session_scope 종료 = classification commit 확정. 이후 evidence 실패는
            #   여기 영속화된 classification 을 더는 롤백할 수 없다.

        except SQLAlchemyError as exc:
            self._inc_persist_failure("db_error")
            logger.error(
                "classify persistence db error: doc_id=%s err=%s",
                req.doc_id, type(exc).__name__, exc_info=True,
            )
            warns.append(f"persistence skipped: db error ({type(exc).__name__})")
            return uuid.uuid4(), warns
        except Exception as exc:  # noqa: BLE001
            self._inc_persist_failure("unexpected")
            logger.error(
                "classify persistence unexpected error: doc_id=%s err=%s",
                req.doc_id, type(exc).__name__, exc_info=True,
            )
            warnings.warn(
                f"[classify_service] unexpected persistence error: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            warns.append(f"persistence skipped: unexpected error ({type(exc).__name__})")
            return uuid.uuid4(), warns

        # ── Step 2: evidence / RAG-evidence 영속화 (best-effort, 별도 트랜잭션) ──
        # M-classify-tx: 여기서 실패하더라도 Step 1 에서 이미 commit 된
        # classification 은 보존된다. evidence 는 보강 메타라 손실돼도 분류
        # 결과(등급)는 유지 — 미탐(분류 자체 유실) 위험을 evidence 손실로
        # 격하한다. 실패는 warning 으로만 보고하고 classification_id 는 그대로 반환.
        if pred.evidence or pred.rag_context:
            try:
                from lloydk.db import session_scope  # noqa: PLC0415

                with session_scope() as db2:
                    repo2 = ClassifyRepo(db2)
                    if pred.evidence:
                        repo2.add_evidence_from_spans(
                            classification_id,
                            spans=pred.evidence,
                            default_chunk_id=evidence_default_chunk,
                        )
                    if pred.rag_context:
                        n_rag = repo2.add_rag_evidence_from_hits(
                            classification_id,
                            hits=pred.rag_context,
                            default_chunk_id=evidence_default_chunk,
                        )
                        if n_rag != len(pred.rag_context):
                            warns.append(
                                f"rag evidence partial persist: {n_rag}/{len(pred.rag_context)}"
                            )
            except Exception as exc:  # noqa: BLE001
                # evidence 실패는 classification 을 폐기하지 않는다 — warning 만.
                self._inc_persist_failure("evidence_error")
                logger.warning(
                    "classify evidence persist failed (classification kept): "
                    "classification_id=%s err=%s",
                    classification_id, type(exc).__name__, exc_info=True,
                )
                warns.append(
                    f"evidence persist failed (classification kept): {type(exc).__name__}"
                )

        return classification_id, warns

    def _fetch_content_by_doc_id(self, doc_id: str) -> str:
        """doc_id로 documents.normalized_text_uri → storage에서 텍스트 읽기.

        ingestion 완료 문서는 normalized_text_uri를 갖고 있다.
        storage·DB 미가용 시 빈 문자열 반환 — 호출자가 처리.
        """
        doc_uuid = self._parse_doc_uuid(doc_id)
        if doc_uuid is None:
            return ""
        try:
            from lloydk.adapters.storage import build_storage  # noqa: PLC0415
            from lloydk.db import session_scope  # noqa: PLC0415
            from lloydk.repositories.document_repo import DocumentRepo  # noqa: PLC0415
        except ImportError:
            return ""
        try:
            with session_scope() as db:
                doc = DocumentRepo(db).get(doc_uuid)
                if doc is None or not doc.normalized_text_uri:
                    return ""
                uri = doc.normalized_text_uri
            # file:// URI → LocalStorage, s3:// or minio:// → MinioStorage
            storage = build_storage()
            # URI → bucket/key 분해
            # 형식: s3://<bucket>/<key> 또는 minio://<host>/<bucket>/<key> 또는 file://<bucket>/<key>
            import re as _re  # noqa: PLC0415
            m = _re.match(r"(?:s3|minio|file)://([^/]+)/(.+)", uri)
            if not m:
                return ""
            bucket, key = m.group(1), m.group(2)
            data = storage.get(bucket, key)
            return data.decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            logger.debug("_fetch_content_by_doc_id failed: %s", exc)
            return ""

    @staticmethod
    def _inc_persist_failure(reason: str) -> None:
        try:
            from lloydk.api.prom_metrics import CLASSIFY_PERSIST_FAILURE_TOTAL  # noqa: PLC0415
            CLASSIFY_PERSIST_FAILURE_TOTAL.labels(reason=reason).inc()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _parse_doc_uuid(value: str) -> Optional[uuid.UUID]:
        try:
            return uuid.UUID(value)
        except (ValueError, TypeError, AttributeError):
            return None

    @staticmethod
    def _emit_gate_visibility_metrics(eff_meta: dict, warnings_acc: list[str]) -> None:
        """[번들 A] 메타데이터/출처 게이트 가시성 메트릭 — 전부 best-effort(실패해도 분류 무영향).

        두 축을 노출한다:
          (1) 게이트 실제 발동(METADATA_FLOOR_APPLIED / SOURCE_PRIOR_APPLIED) — pipeline이 남긴
              경고 문자열로 판정(classify 라우팅 검사와 동일 신호 소스).
          (2) 분류 입력의 ICD 메타데이터 존재율(CLASSIFY_METADATA_PRESENT) — 게이트를 켜도 입력
              메타가 없으면 silent no-op임을 켜기 *전에* 보이게 한다. field='none'=게이트 입력 메타 전무.

        서빙 경로(ClassifyService.classify)에서만 호출 — 평가(serving_eval은 pipeline.run 직접
        호출)는 제외되어 운영 신호가 오염되지 않는다.
        """
        try:
            from lloydk.api import prom_metrics as _pm  # noqa: PLC0415
        except Exception:  # noqa: BLE001 — 메트릭 레지스트리 미가용 환경(테스트 일부)은 무해 skip
            return
        # (1) 게이트 발동 — pred.warnings 문자열 매칭(pipeline 산출).
        try:
            joined = " ".join(warnings_acc)
            if "metadata-floor:" in joined:
                _pm.METADATA_FLOOR_APPLIED_TOTAL.labels(action="raised").inc()
            if "metadata-access-conflict" in joined:
                _pm.METADATA_FLOOR_APPLIED_TOTAL.labels(action="access_conflict").inc()
            if "source-prior:" in joined:
                _pm.SOURCE_PRIOR_APPLIED_TOTAL.labels(action="capped").inc()
            if "cap-conflict" in joined:
                _pm.SOURCE_PRIOR_APPLIED_TOTAL.labels(action="cap_conflict").inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("gate-firing metric emit skipped: %s", exc)
        # (2) ICD 메타데이터 존재율 — 게이트 활성 여부와 무관, 분류 1건당 1회.
        try:
            md = eff_meta or {}
            present: list[str] = []
            if str(md.get("security_marking", "") or "").strip():
                present.append("security_marking")
            if str(md.get("access_scope", "") or "").strip():
                present.append("access_scope")
            if str(md.get("source_type", "") or md.get("source", "") or "").strip():
                present.append("source")
            if present:
                for field in present:
                    _pm.CLASSIFY_METADATA_PRESENT_TOTAL.labels(field=field).inc()
            else:
                _pm.CLASSIFY_METADATA_PRESENT_TOTAL.labels(field="none").inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("metadata-presence metric emit skipped: %s", exc)

    @staticmethod
    def _factors_source(warnings_acc: list[str]) -> str:
        """[번들 C] evaluation_factors 출처 판정 — 'model_estimated' | 'rule_evidenced'.

        룰 미탐으로 등급에 맞춰 factor를 역산(svm_levels_for_grade)한 경우(정합화 경고
        'factors aligned to model grade' / 'chunk severe-agg' 존재) 표시 S/V/M은 법리 근거가
        아니라 모델/집계 추정치다 → 컴플라이언스상 구분 표시. 경고가 진실의 단일 소스.
        """
        for w in warnings_acc:
            if "factors aligned to model grade" in w or "chunk severe-agg" in w:
                return "model_estimated"
        return "rule_evidenced"

    @staticmethod
    def _kill_gate_brake(label) -> "str | None":
        """[번들 E] kill-gate 안전브레이크 — 억제 대상이면 검수 사유 문자열, 아니면 None.

        should_suppress_autoconfirm(고등급 + tripped + flag ON) 충족 시 needs_review 라우팅
        사유를 반환하고 메트릭 증가. 미충족·예외는 None(동작 보존 — 게이트가 죽어도 분류는 진행).
        """
        try:
            from lloydk.modules.m6_evaluation.kill_gate import (  # noqa: PLC0415
                should_suppress_autoconfirm,
            )
            grade = label.value if hasattr(label, "value") else str(label)
            if not should_suppress_autoconfirm(grade):
                return None
            try:
                from lloydk.api.prom_metrics import (  # noqa: PLC0415
                    KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL,
                )
                KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL.labels(grade=grade).inc()
            except Exception:  # noqa: BLE001
                pass
            return (
                "kill-gate-brake: high-grade auto-confirm suppressed while kill-gate tripped "
                "— routed to human review"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("kill-gate brake skipped: %s", exc)
            return None

    def _effective_metadata(self, req: ClassifyRequest) -> dict:
        """분류 metadata 구성 — 요청 metadata에 저장된 문서 출처(provenance)를 보강.

        Gate-1(출처 게이트, source-prior)은 metadata.source_type/source가 있을 때만
        발동한다(InferencePipeline.run). 업로드→ingest→doc_id 분류 흐름에서는 요청에
        출처가 없으므로, documents.metadata_에 적재된 source_type/source를 여기서
        hydrate해 게이트가 실제로 발동하도록 한다.

        - 요청이 출처를 명시했으면 그대로 둔다(요청 우선 — 덮어쓰지 않음).
        - best-effort: DB 미가용·문서 부재·content 직접분류(doc_id 없음)면 요청 metadata 그대로.
        """
        meta = dict(req.metadata or {})
        if meta.get("source_type") or meta.get("source"):
            return meta
        doc_uuid = self._parse_doc_uuid(req.doc_id)
        if doc_uuid is None:
            return meta
        try:
            from lloydk.db import session_scope  # noqa: PLC0415
            from lloydk.repositories.document_repo import DocumentRepo  # noqa: PLC0415

            with session_scope() as db:
                doc = DocumentRepo(db).get(doc_uuid)
                stored = getattr(doc, "metadata_", None) if doc is not None else None
                if isinstance(stored, dict):
                    src = stored.get("source_type") or stored.get("source")
                    if src:
                        meta["source_type"] = src
        except Exception as exc:  # noqa: BLE001
            logger.debug("_effective_metadata hydrate skipped (non-critical): %s", exc)
        return meta
