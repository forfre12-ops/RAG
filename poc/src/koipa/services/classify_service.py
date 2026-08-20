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

from koipa.modules.m2_preprocess.chunker import Chunk as _PreprocessChunk
from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline
from koipa.modules.m5_inference.pipeline import InferencePipeline, InferenceResult
from koipa.obs.otel import span  # #29: 비즈니스(수동) span 헬퍼 — 미설치/미활성 시 no-op
from koipa.repositories.chunk_repo import ChunkRepo
from koipa.repositories.classify_repo import ClassifyRepo
from koipa.schemas.classify import ClassifyRequest, ClassifyResponse

# A3: SSE/스트리밍에서 진척 단계를 실제로 노출하기 위한 콜백 시그니처.
# 호출 측이 None을 넘기면 기존 동작과 동일(no-op).
StageCallback = Callable[[str], None]

logger = logging.getLogger(__name__)

import re as _re  # noqa: E402

# [FIX-D] 공개특허공보 마스트헤드 탐지 — 서지헤더 3요소가 문서 '머리'에 동시 존재할 때만 True.
# 정의상 공개(published) 문서라 source-prior 캡(→S3)의 입력이 된다. 엄격 탐지 자체가 FNR
# 가드다: (1) 진짜 내부 기밀은 KIPO 공보 마스트헤드를 달지 않는다, (2) 공보를 '인용/편찬'한
# 내부 문서(S2 편찬물)는 문서 머리에 단일 공보 서지헤더를 갖지 않는다(머리 1500자로 한정).
_PATENT_INID_RE = _re.compile(r"\((?:11|12|19|21|43|45|54|57)\)")
_PATENT_GAZETTE_TOKENS = ("공개특허공보", "등록특허공보", "공개실용신안공보", "등록실용신안공보")


def _is_published_patent_gazette(text: str) -> bool:
    """KIPO 공개/등록 특허공보 마스트헤드인가 — 엄격(3요소·머리 위치 한정).

    공보 종별 표기(공개특허공보(A) 등)는 진짜 공보라면 문서 최상단 서지헤더에 온다 →
    첫 300자로 한정해, 내부 기밀이 본문에서 타 특허공보를 '인용'만 한 경우(편찬물)의
    오탐을 배제한다. 특허청·INID 코드는 머리 1500자 내면 인정. 오탐(false-positive)이
    나더라도 source-prior 캡은 TS/S1을 cap-conflict→needs_review로 보내므로 무음 하향은
    없다. 미탐(false-negative)은 해당 문서가 TS+검수로 남을 뿐이라 FNR-safe 방향이다.
    """
    if not text:
        return False
    head = text[:1500]
    masthead = text[:300]  # 공보 종별 표기는 문서 최상단에만 인정(중간 인용 배제)
    has_gazette = any(tok in masthead for tok in _PATENT_GAZETTE_TOKENS)
    has_office = "특허청" in head  # 예: 대한민국특허청(KR)
    has_inid = bool(_PATENT_INID_RE.search(head))  # 서지 INID 코드 (11)공개번호 등
    return has_gazette and has_office and has_inid


def _try_uuid_str(value: str | None) -> "uuid.UUID | None":
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _testing_env() -> bool:
    return os.environ.get("TESTING", "").strip().lower() in {"1", "true", "yes", "on"} or bool(
        os.environ.get("PYTEST_CURRENT_TEST")
    )


def _skip_optional_db_work() -> bool:
    if not _testing_env():
        return False
    try:
        from koipa.db import database_reachable_fast  # noqa: PLC0415

        return not database_reachable_fast()
    except Exception:  # noqa: BLE001
        return False


def _active_model_dir() -> Optional[str]:
    """활성 ModelVersion.model_uri가 로컬 디렉토리면 그 경로, 아니면 None (C-ver 서빙 배선).

    register_and_gate/rollback이 활성화한 버전을 서빙이 실제로 집어들게 한다. model_uri가
    없거나(미등록)·원격 URI거나·존재하지 않는 경로면 None → 호출부가 env로 폴백(동작 보존).
    DB 미가용·예외는 모두 None(폴백). 베스트에포트.
    """
    try:
        from pathlib import Path  # noqa: PLC0415

        from koipa.db import session_scope  # noqa: PLC0415
        from koipa.repositories import TrainingRepo  # noqa: PLC0415
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
    from koipa.config import settings  # noqa: PLC0415
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

    def reload_rules(self) -> dict:
        """서빙 룰 엔진을 DB(tb_level_keywords) 기준으로 재구성 — 키워드 CRUD 후 핫리로드 (FUN-023).

        reload_model 과 달리 분류기 가중치는 건드리지 않고 룰 시드만 갱신(경량). 룰 엔진은
        LabelingPipeline 생성 시 build_rule_engine_from_db() 로 한 번 만들어져 이 싱글턴에 캐시되므로,
        태깅 변경을 재기동 없이 반영하려면 명시적 리로드가 필요하다. 다중 워커면 이 요청을 처리한
        워커에만 즉시 반영된다(GradeRegistry.invalidate 와 동일 한계 — 나머지는 재기동/다음 리로드까지 stale).
        반환: 재로드 여부·현재 시드 수.
        """
        from koipa.modules.m3_labeling.rule_engine import (  # noqa: PLC0415
            build_rule_engine_from_db,
        )

        self.inference.labeling.engine = build_rule_engine_from_db()
        seed_count = len(getattr(self.inference.labeling.engine, "seeds", []) or [])
        logger.info("classify rule engine reloaded from DB: seeds=%d", seed_count)
        return {"reloaded": True, "seed_count": seed_count}

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
                "koipa.has_doc_id": bool(req.doc_id),
                "koipa.has_content": bool(req.content),
                "koipa.use_rag": bool(req.use_rag),
                # model_version은 model_dir 폴더명에서 안전 도출(없으면 rule-fallback).
                # InferencePipeline 자체엔 model_version 속성이 없으므로 직접 참조 금지.
                "koipa.model_dir": (
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
            review_flagged = False
            if not content:
                content, _doc_status = self._fetch_content_by_doc_id(req.doc_id)
                # [P0#3 후속] ingestion 열화추출(OCR/저품질)로 격리된 문서(processing_status)를
                # 서빙 진입에서 존중 — 자동확정 금지(무음 자동분류 방지).
                review_flagged = self._ingestion_review_flagged(_doc_status)
            else:
                # 본문이 직접 주어진 경우에도, 이미 적재된 문서(UUID doc_id)가 ingestion 단계에서
                # 열화추출로 needs_review 격리됐으면 그 격리를 존중한다(FNR-safe·additive). 비-UUID
                # doc_id(예: /analyze filename, 콘솔 샘플)는 적재문서가 아니라 조회 없이 skip —
                # 기존 트래픽(전부 비-UUID+content) 무영향. LocalStorage URI 재읽기 우회 경로에서
                # ingestion 격리가 유실되지 않게 한다(정본 doc_id-serving 과 status 정합).
                review_flagged = self._ingestion_flagged_for_doc(req.doc_id)
            if not content:
                # fail-SECURE (미탐 절대 금지): 본문을 못 읽으면 등급을 판단할 수 없다.
                # 과거엔 label="S3"(공개)로 폴백했는데, 이는 '읽지 못한 비밀문서를 공개로
                # 흘리는' 전형적 미탐 경로였다. 보수적으로 최고등급(Grade.TS)으로 격리하고
                # status를 needs_review로 두어 사람 검수 큐에 강제 노출 — 절대 공개(S3)로
                # 떨어뜨리지 않는다. (ClassifyResponse.label은 Grade 필수라 UNCLASSIFIED를
                # 표현할 수 없으므로, 가장 안전한 '최고등급 + 검수필요'로 표현한다.)
                from koipa.schemas.common import Grade  # noqa: PLC0415
                top_grade = self._highest_grade()
                warnings_acc = [
                    f"content empty and doc_id={req.doc_id!r} not found in storage —"
                    f" cannot classify; fail-SECURE isolating at highest grade"
                    f" ({top_grade}) and routing to human_review (never defaults to public/S3)"
                ]
                self._record_grade(top_grade)
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
            # human_review / nkt_designated 등 is_verified=True 라벨
            # 우선순위: human_review > nkt_designated > … (koipa/codex는 2026-07-03 감사로
            # LLM 심판 동급 강등 — classify_repo._LABEL_SOURCE_PRIORITY 참조)
            verified_label = self._get_verified_label(req.doc_id)
            if verified_label is not None:
                from koipa.schemas.common import Grade  # noqa: PLC0415
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
                self._record_grade(verified_label.level_code)
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
            eff_meta = self._effective_metadata(req, content=cleaned)
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
            # ── 검수 라우팅 게이트 → 최종 status를 persist 전에 확정 ─────────────────
            # 게이트가 산출한 needs_review 를 DB 행에 실제로 반영하려면 status 를
            # create_classification 에 원자적으로 넘겨야 한다. 과거엔 게이트가 persist
            # 뒤에 돌아 status 가 응답에만 실리고 DB 행은 staging 으로 남았고,
            # GET /review-queue(FUN-024)는 status IN (needs_review, …)만 조회하므로
            # 서빙게이트 검수건을 영영 못 받는 하프와이어링(무음 미탐)이 있었다.
            # 게이트는 pred/cleaned/review_flagged 만 참조(inference_id 불필요)하므로
            # persist 앞으로 안전하게 이동한다.
            #
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

            # [P0#3 후속] ingestion 단계서 열화추출로 needs_review/failed 격리된 문서는 자동확정 금지.
            # ingestion 게이트가 쓴 processing_status 를 서빙이 읽지 않아 격리문서가 그대로 자동분류되던
            # 하프와이어링 마감. 등급은 검수자 참고로 산출하되 status만 격리(등급 무변경·FNR-safe).
            if review_flagged:
                status = "needs_review"
                warnings_acc.append(
                    "document flagged at ingestion (degraded extraction: OCR/low-quality) —"
                    " routed to human review, not auto-confirmed"
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

            # [abbrev-only-escalation | FIX-E] 청크 severe-agg가 고등급으로 승격했으나 그 근거가
            # _HIGH_RISK_PATTERNS 영문 약어 부스트(CVD·N2O·EUV 등)뿐이고 한국어 시드 근거가 전무한
            # 경우 — pipeline이 남긴 신호. 공개특허/기술문서의 범용 공정약어 밀도만으로 최고등급
            # 자동확정되는 과분류(예: 공개특허공보 → TS)를 사람이 확인하게 한다. 등급은 무변경
            # (하향 없음) — 자동확정만 차단하므로 FNR-safe. 전용 태그라 cap-conflict 계열과 분리.
            if status != "needs_review" and any(
                "abbrev-only-escalation" in w for w in warnings_acc
            ):
                status = "needs_review"
                warnings_acc.append(
                    "abbrev-only-escalation: high grade rested on abbreviation boosts only"
                    " (no Korean seed) — routed to human review (grade unchanged, FNR-safe)"
                )

            # [metadata-access-conflict] ICD 접근범위가 제한적인데 내용 예측이 낮은 경우 — pipeline이
            # 남긴 신호 — confidence와 무관하게 검수 라우팅(ICD §4.4: 관리수준 높은 문서를 낮게
            # 자동확정하지 않음). security_marking 상향 floor는 pipeline에서 이미 등급에 반영됨.
            # [무음 빈본문] 추출은 됐는데 판정할 만한 본문이 없는 문서는 자동확정하면
            # 안 된다. 실측 2026-08-15: 구형 HWP 3.x 서식이 본문 1~5자(객체 placeholder)
            # 로 나오는데 오류가 없어 그대로 S3 로 자동확정됐다 - 무음 미탐의 모양이다.
            if status != "needs_review" and any(
                "body_below_classifiable_threshold" in w for w in warnings_acc
            ):
                status = "needs_review"
                warnings_acc.append(
                    "body-below-threshold: 판정할 만한 본문이 없다(표·글상자 전용이거나 "
                    "구형 HWP 3.x 가능) - 내용 없이 자동확정하지 않고 검수로 보낸다"
                )

            if status != "needs_review" and any("metadata-access-conflict" in w for w in warnings_acc):
                status = "needs_review"
                warnings_acc.append(
                    "metadata-access-conflict: restricted access_scope vs low predicted grade — routed to human review"
                )

            # [ICD 규약값 적합성] 규약 밖의 값은 **거부하지 않고 드러낸다.** 422 로 막으면
            # 그 문서가 아예 분류되지 않아 더 나쁘다. 그러나 조용히 무시해서도 안 된다 —
            # 실측 2026-08-14: ICD §3.1 의 source_type="public" 을 배포본이 인식하지
            # 못했는데 아무 신호도 나지 않았고, 인수 팩을 실제로 태워 보고서야 공개
            # 사업공고문이 TS 로 나오는 것을 발견했다.
            #
            # security_marking·access_scope 는 **상향 게이트의 입력**이라 못 읽으면
            # 상향이 안 걸려 미탐이 된다. 그 경우에만 검수로 보낸다. source_type 은
            # 하향(cap) 입력이라 못 읽어도 미탐 방향이 아니므로 경고만 남긴다.
            try:
                from koipa.modules.m3_labeling.rule_engine import (  # noqa: PLC0415
                    validate_icd_metadata as _validate_icd,
                )
                icd_warns = _validate_icd(getattr(req, "metadata", None))
                if icd_warns:
                    warnings_acc.extend(icd_warns)
                    if status != "needs_review" and any("미탐 위험" in w for w in icd_warns):
                        status = "needs_review"
            except Exception:  # noqa: BLE001 — 적합성 검사 실패가 분류를 막지 않는다
                pass

            # [요소 모델 섀도] v8 을 같은 입력에 나란히 돌려 **계량만** 한다.
            # 등급도 status 도 바꾸지 않는다 — 배포본은 등급 우선·요소 후행이고 v8 은
            # 요소 우선이라 두 구조를 바로 합치면 결정이 바뀐다. 그 전에 두 모델이
            # 얼마나 다른지를 알아야 하고, 모르고 거부 조건을 걸면 검수량이 얼마나
            # 늘지 예측할 수 없다.
            #
            # 방향이 중요하다 — v8 이 더 높게 보면 v5 미탐 의심(1차 목표에 직결),
            # 더 낮게 보면 v5 과분류 의심이다. 경고에 그 방향을 남긴다.
            factor_shadow = None
            try:
                from koipa.config import settings as _fs  # noqa: PLC0415
                if getattr(_fs, "factor_shadow_enabled", False) and getattr(_fs, "factor_model_dir", ""):
                    from koipa.modules.m5_inference.pipeline import (  # noqa: PLC0415
                        _source_prior_is_public as _is_pub,
                    )
                    from koipa.modules.m5_inference.factor_model import (  # noqa: PLC0415
                        apply_serving_gate,
                        get_factor_inference,
                        shadow_compare,
                    )

                    _inf = get_factor_inference(
                        _fs.factor_model_dir,
                        base=getattr(_fs, "factor_model_base", "kakaobank/kf-deberta-base"),
                        max_len=int(getattr(_fs, "factor_model_max_len", 768)),
                    )
                    _out = _inf.predict(cleaned)
                    if _out is not None:
                        _codes, _probs = _out
                        _pred = apply_serving_gate(
                            _codes, _probs,
                            metadata=getattr(req, "metadata", None),
                            tau=float(getattr(_fs, "factor_tau", 0.99)),
                            kappa=float(getattr(_fs, "factor_kappa", 0.99)),
                            source_is_public=_is_pub(
                                (getattr(req, "metadata", None) or {}).get("source_type")
                                if isinstance(getattr(req, "metadata", None), dict) else None
                            ),
                        )
                        _v5 = pred.label.value if hasattr(pred.label, "value") else str(pred.label)
                        factor_shadow = shadow_compare(_v5, _pred)
                        warnings_acc.append(
                            "factor-shadow: v8="
                            + factor_shadow["factor_grade"]
                            + " vs v5=" + str(_v5)
                            + " · " + factor_shadow["direction"]
                            + " · conf=" + str(factor_shadow["min_confidence"])
                            + " (계량 전용 — 등급·상태 미변경)"
                        )
            except Exception:  # noqa: BLE001 — 섀도 실패가 분류를 막지 않는다
                factor_shadow = None

            # [agreement-gate] 등급차등 + 룰·모델 합의 게이트 (opt-in, 기본 off).
            # conf 단독 자동확정은 신뢰성이 측정으로 부정됨(golden500: AUROC 0.58, 자동확정
            # 정밀도 63%, 고등급 미탐 46). 확신을 conf가 아니라 *독립 신호(룰 합의)*에서 얻는다:
            #   · 예측이 공개등급(S3): conf만으로 자동확정 허용(S3 conf 정밀도 94%, 과소분류 불가)
            #   · 예측이 그 외(TS/S1/S2): 룰엔진과 등급이 합의해야 자동확정, 불일치면 검수
            # 측정(golden500): 자동확정 정밀도 63→81%, 고등급 미탐 46→8. 등급은 무인으로 바꾸지
            # 않고 검수 라우팅만 한다. 룰 산출 실패는 silent 폴백(게이트가 죽어도 분류는 진행).
            if status != "needs_review" and any("s2-underclass-risk" in w for w in warnings_acc):
                status = "needs_review"
                warnings_acc.append(
                    "s2-underclass-risk: internal/non-public signals with S3 prediction — routed to human review"
                )

            # [gate-fail-open] 미탐 방향 게이트가 예외로 미적용된 경우 — pipeline 이 남긴 신호.
            # 종전엔 프로메테우스 카운터만 올렸다. 그건 사후에 사람이 대시보드를 봐야 알고,
            # 그 사이 문서는 자동확정으로 나간다 — 게이트가 죽은 줄 모른 채 미탐이 흐른다.
            # 방향별로 갈랐다: 상향/라우팅 게이트(fnr_safe_override · ts_tie_break ·
            # metadata_floor · s2_underclass_risk)의 실패만 검수로 보내고, 하향 게이트
            # (source_prior_cap)는 실패해도 과분류 쪽이라 현행 유지한다 — 안전 방향 실패에
            # 검수부담만 붙이지 않는다. 등급은 무변경(FNR-safe): 예외 상황에서 등급을
            # 추측하면 새 오류원이 된다. 관련: m5_inference.pipeline._MISS_DIRECTION_GATES
            if status != "needs_review" and any("gate-fail-open" in w for w in warnings_acc):
                status = "needs_review"
                warnings_acc.append(
                    "gate-fail-open: an underclassification-side serving gate did not apply"
                    " — routed to human review (grade unchanged, FNR-safe)"
                )

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

            # [Phase 2] 유사도 escalation — 사람이 더 높은 등급으로 검증한 문서와 매우 유사하면
            # needs_review 라우팅(등급 무변경·opt-in 기본 off·전 경로 fail-open). 가장 비싼 게이트
            # (임베딩+벡터검색+DB)라 체인 마지막에 두고, 앞 게이트가 이미 올렸으면 스킵된다.
            if status != "needs_review":
                sim = self._similarity_escalation_gate(req.doc_id, cleaned, pred.label)
                if sim is not None:
                    status = "needs_review"
                    warnings_acc.append(sim)

            # 게이트가 확정한 최종 status로 영속화(원자적). needs_review 는 여기서 DB 행에
            # 실제 기록되어 GET /review-queue 검수 큐에 노출된다(예전엔 항상 staging 이었음).
            notify("persist")
            inference_id, persist_warnings = self._try_persist(
                req, pred, chunks=chunks, status=status,
            )
            warnings_acc.extend(persist_warnings)
            notify("finalize")

            logger.info(
                "classify done: doc_id=%s inference_id=%s label=%s confidence=%.3f status=%s",
                req.doc_id, inference_id, pred.label, float(pred.confidence), status,
            )

            self._record_grade(pred.label)
            return ClassifyResponse(
                inference_id=inference_id,
                doc_id=req.doc_id,
                label=pred.label,
                confidence=pred.confidence,
                scores=pred.scores,
                evaluation_factors=pred.factors,
                factors_source=self._factors_source(warnings_acc),
                rule_evaluation_factors=getattr(pred, "rule_factors", None),
                evidence=pred.evidence,
                rag_context_used=pred.rag_context,
                model_version=pred.model_version,
                elapsed_ms=0,
                status=status,
                warnings=warnings_acc,
                rule_grade=getattr(pred, "rule_grade", None),
                model_grade=getattr(pred, "model_grade", None),
                decision_path=self._decision_path(pred, status, warnings_acc),
            )

    @staticmethod
    def _decision_path(pred, status: str, warnings: list[str]) -> str:
        """룰·모델·최종 결합이 어떻게 결정됐는지 한 줄로 설명(시연/투명성).

        label(최종)이 rule_grade/model_grade와 어떻게 관계되는지 warnings 근거로 분류한다.
        """
        w = " ".join(warnings or [])
        final = pred.label.value if hasattr(pred.label, "value") else str(pred.label)
        rule = getattr(pred, "rule_grade", None)
        model = getattr(pred, "model_grade", None)
        if model is None:
            return "rule-only (모델 미로드 — 룰 엔진 단독 판정)"
        if "fnr-safe override" in w:
            return f"rule-override (룰이 {rule} 강하게 잡아 모델 {model}을 안전 상향 → {final})"
        if "metadata-floor" in w:
            return f"metadata-floor (ICD 보안표기로 {model}→{final} 상향)"
        if "cap-conflict" in w or "metadata-access-conflict" in w:
            return f"escalation (신호 충돌 → 검수 라우팅; 룰 {rule} · 모델 {model})"
        if "source-prior" in w:
            return f"source-cap (공개 출처 → {final}로 하향; 모델 {model})"
        if rule == model == final:
            return f"agreement (룰·모델 모두 {final} 일치 → 자동 확정)"
        if status == "needs_review":
            # [2026-08-20] 종전 문구는 "escalation (룰 X · 모델 Y → 검수 라우팅)" 이었다.
            # **불일치가 검수 라우팅의 원인이라고 읽힌다.** 사실이 아니다 — 이 함수는 status 가
            # 이미 needs_review 로 정해진 뒤에 불리고, 게이트 조건(classify_service:328~470)에
            # 룰↔모델 불일치는 들어 있지 않다. 실측 2026-08-20: 검수 대상 120건 중 검수로 간
            # 67건이 **전부 low-confidence 하나**로 걸렸다(나머지 여섯 조건 0건).
            # 원인은 warnings 가 말한다. 여기서는 두 엔진이 갈렸다는 **사실만** 적는다.
            return f"룰 {rule} · 모델 {model} 불일치 (검수 사유는 아래 경고 참조)"
        return f"combined (룰 {rule} · 모델 {model} → 최종 {final})"

    @staticmethod
    def _highest_grade() -> str:
        """가장 높은(가장 비밀) 등급 코드 반환 — fail-SECURE 격리용.

        GradeRegistry.get_codes()는 level_order 오름차순(= 비밀이 가장 높은 등급이
        선두)으로 반환하므로 [0]이 최고등급. DB 미가용/빈 목록이면 Grade.TS로 폴백.
        """
        try:
            from koipa.schemas.common import Grade, GradeRegistry  # noqa: PLC0415
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
            from koipa.config import settings  # noqa: PLC0415
            if not getattr(settings, "agreement_gate_enabled", False):
                return None
            from koipa.schemas.common import GradeRegistry  # noqa: PLC0415
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
        except Exception as exc:  # noqa: BLE001 — 룰엔진 미가용·오류는 기존 자동확정 유지(fail-safe)
            logger.debug("agreement-gate fail-open (rule unavailable): %s", exc)
            self._record_gate_fail_open("agreement")
            return None
        return None

    @staticmethod
    def _record_gate_fail_open(gate: str) -> None:
        """[obs] 검수 게이트가 예외로 fail-open(자동확정 통과)했음을 가시화 — best-effort.

        무음이면 고등급 자동확정이 게이트 없이 진행됐는지 안 보인다. 1차 가시성은 호출부 debug
        로그, 운영 신호는 이 카운터(gate 라벨). 메트릭 실패는 분류 경로 무영향.
        """
        try:
            from koipa.api.prom_metrics import (  # noqa: PLC0415
                SERVING_GATE_FAIL_OPEN_TOTAL,
            )
            SERVING_GATE_FAIL_OPEN_TOTAL.labels(gate=gate).inc()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _record_grade(label) -> None:
        """[obs] 서빙 등급 분포 카운터 — classify가 반환하는 최종 등급을 1건당 1회 증가.

        kpi-v2 '분류 등급 분포' 패널 백킹. 라이브 FNR(정답 필요 — confirm/relabel 확정 경로에
        배선됨, c750b40)과 달리 정답이 불요한 단순 예측 분포라 서빙 시점에 정직하게 채워진다.
        fail-SECURE 격리(본문 미판독→최고등급)·검증라벨
        경로도 '반환된 등급'이라 포함 — 저장장애發 고등급 격리 급증도 이 분포에 보인다.
        best-effort: 메트릭 실패는 분류 경로 무영향.
        """
        try:
            from koipa.api.prom_metrics import CLASSIFY_GRADE_TOTAL  # noqa: PLC0415
            grade = label.value if hasattr(label, "value") else str(label)
            CLASSIFY_GRADE_TOTAL.labels(grade=grade).inc()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _llm_second_opinion(text: str, model_label) -> str | None:
        """모델 자동확정(비-TS)에 대한 LLM 2차의견. 더 높은 등급 제시 시 검수 사유 문자열 반환.

        반환 None = 라우팅 변경 없음(게이트 비활성·이미 TS·LLM이 동급/하급·LLM 오류).
        FNR-safe: LLM이 **더 높은(낮은 order)** 등급을 제시할 때만 검수로 올린다(승급 아님).
        모든 예외는 None으로 흡수 — LLM이 죽어도 분류·기존 자동확정 동작을 막지 않는다(fail-safe).
        """
        try:
            from koipa.config import settings  # noqa: PLC0415
            if not getattr(settings, "model_secondopinion_llm_enabled", False):
                return None
            from koipa.schemas.common import GradeRegistry  # noqa: PLC0415
            order = GradeRegistry.get_order()
            top_code = next(iter(order), "TS")  # 가장 높은(비밀) 등급 코드
            model_code = model_label.value if hasattr(model_label, "value") else str(model_label)
            if model_code == top_code:
                return None  # 이미 최고등급 자동확정 — 과소분류 위험 없음
            from koipa.modules.m3_labeling.llm_labeler import LLMLabeler  # noqa: PLC0415
            llm = LLMLabeler().label(text, purpose="second_opinion")
            if llm.grade not in order:
                return None
            if order.get(llm.grade, 99) < order.get(model_code, 99):
                return (
                    f"llm-secondopinion: model auto-confirmed {model_code} but LLM proposes higher "
                    f"{llm.grade} (conf={llm.confidence:.2f}) — routed to human review (FNR-safe)"
                )
        except Exception as exc:  # noqa: BLE001 — LLM 미가용·오류는 기존 자동확정 유지(fail-safe)
            logger.debug("llm-secondopinion fail-open (LLM unavailable): %s", exc)
            ClassifyService._record_gate_fail_open("llm_second_opinion")
            return None
        return None

    @staticmethod
    def _review_confidence_threshold() -> float:
        try:
            from koipa.config import settings as _settings  # noqa: PLC0415
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
            from koipa.db import SessionLocal  # noqa: PLC0415
            from koipa.db.models import AuditLog  # noqa: PLC0415
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
            from koipa.api.prom_metrics import (  # noqa: PLC0415
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
            from koipa.db import SessionLocal  # noqa: PLC0415
            from koipa.repositories.classify_repo import ClassifyRepo  # noqa: PLC0415
            from koipa.db.models import ClassificationLevel  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            doc_uuid = _try_uuid_str(doc_id_str)
            if doc_uuid is None:
                return None
            if _skip_optional_db_work():
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
            from koipa.config import settings  # noqa: PLC0415
            if not getattr(settings, "verified_label_content_reuse", True):
                return None
            from koipa.db.models import Document  # noqa: PLC0415
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
        status: str = "staging",
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
        if _skip_optional_db_work():
            self._inc_persist_failure("db_unavailable")
            warns.append("persistence skipped: db unavailable")
            return uuid.uuid4(), warns

        # session_scope import는 함수 안에서 — settings.database_url 변경 가능성·테스트 격리
        try:
            from koipa.db import session_scope  # noqa: PLC0415
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
                    status=status,
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
                from koipa.db import session_scope  # noqa: PLC0415

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

    @staticmethod
    def _ingestion_review_flagged(processing_status: str | None) -> bool:
        """저장된 문서 상태가 ingestion 단계 검수 격리(OCR/저품질 열화추출)인지 — 순수 판정.

        needs_review/failed = 열화추출로 격리된 문서 → 서빙 진입에서 자동확정 금지(무음 자동분류
        방지). 그 외(processed 등)·None(상태 미상)은 격리 아님(과차단 방지).
        """
        return processing_status in ("needs_review", "failed")

    def _ingestion_flagged_for_doc(self, doc_id_str: str) -> bool:
        """적재된 문서(UUID doc_id)의 processing_status 가 ingestion 검수격리인지 — best-effort.

        본문을 직접 넘겨 분류할 때(스토리지 재읽기 우회 경로)도 ingestion 격리를 존중하기 위한
        조회. 비-UUID doc_id(적재문서 아님)·DB미가용·미존재·예외는 모두 False(격리 아님) —
        과차단·비용을 피하고 기존 비-UUID 트래픽엔 조회조차 하지 않는다.
        """
        doc_uuid = self._parse_doc_uuid(doc_id_str)
        if doc_uuid is None:
            return False
        if _skip_optional_db_work():
            return False
        try:
            from koipa.db import session_scope  # noqa: PLC0415
            from koipa.repositories.document_repo import DocumentRepo  # noqa: PLC0415
            with session_scope() as db:
                doc = DocumentRepo(db).get(doc_uuid)
                status = getattr(doc, "processing_status", None) if doc is not None else None
            return self._ingestion_review_flagged(status)
        except Exception as exc:  # noqa: BLE001
            logger.debug("_ingestion_flagged_for_doc failed (non-critical): %s", exc)
            return False

    def _fetch_content_by_doc_id(self, doc_id: str) -> tuple[str, str | None]:
        """doc_id로 documents.normalized_text_uri → storage에서 텍스트 읽기.

        Returns (content, processing_status). processing_status 는 저장된 문서 상태로,
        needs_review/failed(ingestion 열화추출 격리)면 호출자가 자동확정을 막고 검수 라우팅한다.
        storage·DB·문서 미가용 시 ("", None) — 호출자가 처리.
        """
        doc_uuid = self._parse_doc_uuid(doc_id)
        if doc_uuid is None:
            return "", None
        if _skip_optional_db_work():
            return "", None
        try:
            from koipa.adapters.storage import build_storage  # noqa: PLC0415
            from koipa.services.document_ingestion_service import (  # noqa: PLC0415
                DocumentIngestionService,
            )
            from koipa.db import session_scope  # noqa: PLC0415
            from koipa.repositories.document_repo import DocumentRepo  # noqa: PLC0415
        except ImportError:
            return "", None
        status: str | None = None
        try:
            with session_scope() as db:
                doc = DocumentRepo(db).get(doc_uuid)
                if doc is None:
                    return "", None
                status = getattr(doc, "processing_status", None)
                if not doc.normalized_text_uri:
                    return "", status
                uri = doc.normalized_text_uri
            # file:// URI → LocalStorage, s3:// or minio:// → MinioStorage
            storage = build_storage()
            # 형식: s3://<bucket>/<key> 또는 minio://<host>/<bucket>/<key> 또는 file://<bucket>/<key>
            import re as _re  # noqa: PLC0415
            m = _re.match(r"(?:s3|minio|file)://([^/]+)/(.+)", uri)
            if not m:
                return "", status
            bucket, key = m.group(1), m.group(2)
            # [호환] 2026-08-02 이전 LocalStorage.uri 는 저장 루트까지 URI 에 넣어
            # `file://.storage/documents-normalized/<hash>/normalized.txt` 를 만들었다.
            # 그대로 파싱하면 bucket 이 루트로 잡혀 `.storage/.storage/…` 를 읽다 실패한다
            # (read-back 상시 실패 → fail-secure 로 TS+needs_review 세탁).
            # DB 에 이미 쌓인 옛 URI 를 마이그레이션 없이 읽도록 앞 성분을 벗긴다.
            #
            # storage 의 root 를 보고 판단하면 안 된다 — 폐쇄망 기본 구성은
            # EncryptingStorage 가 LocalStorage 를 감싸고 있어 래퍼에 root 가 없다.
            # (실제로 그 이유로 이 호환 처리가 한 번 죽었다.) 버킷명은 적재 쪽이
            # 정하는 고정 상수이므로 그것으로 판정한다.
            _BUCKETS = (
                DocumentIngestionService.NORM_BUCKET,
                DocumentIngestionService.RAW_BUCKET,
            )
            while bucket not in _BUCKETS and "/" in key:
                bucket, key = key.split("/", 1)
            data = storage.get(bucket, key)
            text = data.decode("utf-8", errors="replace")
            if not text:
                # [#4] normalized_text_uri 는 있는데(위 가드 통과) read-back 이 빈 본문 —
                # 원문 저장 볼륨 미마운트/미공유(재생성 시 원문 소실·api↔worker 미공유)의
                # 전형적 신호다. 무음 '빈 본문'으로 흘리면 인프라 장애가 fail-secure(TS+
                # needs_review) 라우팅으로 세탁돼 안 보인다 → 격리는 유지하되 노출한다.
                logger.warning(
                    "content read-back EMPTY for doc_id=%s uri=%s — 원문 저장 볼륨 "
                    "미마운트/미공유 의심(#4); fail-secure 격리는 유지",
                    doc_id, uri,
                )
                self._inc_persist_failure("content_readback_empty")
            return text, status
        except Exception as exc:  # noqa: BLE001
            # [#4] uri 는 존재했는데 storage.get 이 실패 = '본문 없음'이 아니라 read-back 자체가
            # 깨진 것(볼륨 미마운트/백엔드 장애). 과거엔 debug 로 삼켜 fail-secure 격리와 구분
            # 불가했다 → warning+메트릭으로 인프라 신호를 가시화(등급 판단은 그대로 fail-secure).
            logger.warning(
                "_fetch_content_by_doc_id read-back FAILED for doc_id=%s: %s "
                "(원문 저장 볼륨 미마운트/백엔드 장애 의심 — fail-secure 유지)",
                doc_id, exc,
            )
            self._inc_persist_failure("content_readback_error")
            return "", status

    @staticmethod
    def _inc_persist_failure(reason: str) -> None:
        try:
            from koipa.api.prom_metrics import CLASSIFY_PERSIST_FAILURE_TOTAL  # noqa: PLC0415
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
            from koipa.api import prom_metrics as _pm  # noqa: PLC0415
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
            # [2026-08-15] 룰 경로 자신이 "요소 시드 미검출 · 콘텐츠등급 기반 추정" 이라고
            # 공시했는데 응답은 그것을 rule_evidenced 라 부르고 있었다. 룰 엔진에서 s_lv·v_lv
            # 는 content_grade(키워드 argmax 등급)에서 역산되므로(`strong = content_grade in
            # ("TS","S1")`) 그 값은 법리 근거가 아니라 등급의 재진술이다. M 축에는 같은 공시가
            # 이미 있었고 S·V 만 빠져 있었다.
            #
            # 실측 규모(RULE_EXTRACTOR_DIAGNOSIS 2026-08-12): v3 final_800 에서 secrecy·value
            # 둘 다 낮게봄 84.6% · 과검출 0.0% · VALUE 누산점수는 300건 전부 0.0.
            # 시드 보강·semantic·임계탐색이 모두 막혀(같은 문서 §7) 탐지 자체는 못 고친다.
            # 고칠 수 있는 것은 **탐지 못 한 것을 탐지했다고 말하지 않는 것**이다.
            if "독립 근거 없음" in w and "콘텐츠등급 기반 추정" in w:
                return "model_estimated"
        return "rule_evidenced"

    @staticmethod
    def _kill_gate_brake(label) -> "str | None":
        """[번들 E] kill-gate 안전브레이크 — 억제 대상이면 검수 사유 문자열, 아니면 None.

        should_suppress_autoconfirm(고등급 + tripped + flag ON) 충족 시 needs_review 라우팅
        사유를 반환하고 메트릭 증가. 미충족·예외는 None(동작 보존 — 게이트가 죽어도 분류는 진행).
        """
        try:
            from koipa.modules.m6_evaluation.kill_gate import (  # noqa: PLC0415
                should_suppress_autoconfirm,
            )
            grade = label.value if hasattr(label, "value") else str(label)
            if not should_suppress_autoconfirm(grade):
                return None
            try:
                from koipa.api.prom_metrics import (  # noqa: PLC0415
                    KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL,
                )
                KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL.labels(grade=grade).inc()
            except Exception:  # noqa: BLE001
                pass
            return (
                "kill-gate-brake: high-grade auto-confirm suppressed while kill-gate tripped "
                "— routed to human review"
            )
        except Exception as exc:  # noqa: BLE001 — kill-gate 미가용·오류는 자동확정 유지(fail-open)
            # 다른 서빙 게이트(agreement/llm_second_opinion/similarity_escalation)와 동일하게
            # 게이트 계통장애가 무음 no-op으로 숨지 않게 가시화(게이트=가시성 계약).
            logger.debug("kill-gate brake fail-open (kill-gate unavailable): %s", exc)
            ClassifyService._record_gate_fail_open("kill_gate")
            return None

    # ------------------------------------------------------------
    # [Phase 2] 유사도 escalation 게이트 (등급 무변경 — 검수 라우팅만)
    # ------------------------------------------------------------

    # 권위(사람/법적) 검증 출처 — get_verified_document_label 우선순위 1~2(human_review·
    # nkt_designated)만 escalation 참조로 인정. koipa_case_based는 2026-07-03 감사에서 판례 인용
    # 조작(손작성 시나리오)이 확인돼 강등(golden_tiers.SYNTHETIC_PROXY_SOURCES) — codex_review/
    # public_definitive/rule_llm_agreement/llm_judge_* 와 함께 제외(‘사람·정부지정이 검증한 더
    # 높은 등급’ 신호만 채택). S1/S2 escalation 커버리지는 고객사 human_review 누적이 정당한 경로.
    _AUTHORITATIVE_LABEL_SOURCES = frozenset(
        {"human_review", "nkt_designated"}
    )

    def _similarity_escalation_gate(self, doc_id, text, model_label) -> "str | None":
        """유사도 escalation — 자동확정 부적격이면 검수 사유 문자열, 적격/비활성이면 None.

        들어온 문서가 *권위(사람 검수·법적 지정) 출처가 더 높은(더 비밀) 등급으로 검증한 다른
        문서와 매우 유사*하면 (dense 코사인 ≥ τ) needs_review로 라우팅한다. 등급(pred.label/
        scores)은 **절대 바꾸지 않는다** — exact-match override의 '유사' 아날로그를 *신호*로만
        쓴다(전파 0·poisoning 0). FNR-safe: 이웃이 모델 예측보다 **엄격히 더 비밀**일 때만
        올린다(동급/하급은 절대 발동 안 함).

        반환 None = 라우팅 변경 없음(게이트 비활성·이미 최고등급·본문 없음·이웃 없음·임계 미만·
        이웃이 동급/하급·사람검증 이웃 없음·임베딩/검색/DB 오류). 모든 예외는 fail-open(분류 진행)
        하고 SERVING_GATE_FAIL_OPEN_TOTAL{gate='similarity_escalation'}로 가시화한다.

        ⚠️ 반드시 store.search(dense 코사인 = 1-cosine_distance)만 쓴다 — expand_then_search/
        search_hybrid 점수는 RRF/리랭커라 τ(코사인)와 비교 불가(잘못 쓰면 상시/전무 발동).
        """
        try:
            from koipa.config import settings  # noqa: PLC0415

            if not getattr(settings, "similarity_escalation_enabled", False):
                return None  # 기본 OFF — 임베딩/검색 비용 전에 즉시 종료
            # 점수 의미 일치: store.search가 *raw 코사인*을 주는 백엔드에서만 동작(τ=코사인 기준).
            # es는 _score=(1+cos)/2라 같은 τ가 더 낮은 실코사인에서 발동(의미 어긋남) → no-op
            # (es는 레거시·기본 아님, opt-in 게이트라 안전). pg(1-거리)·inmemory(코사인)만 raw 코사인.
            backend = str(getattr(settings, "vector_backend", "pg") or "pg").lower()
            if backend not in ("pg", "inmemory"):
                return None
            from koipa.schemas.common import GradeRegistry  # noqa: PLC0415

            order = GradeRegistry.get_order()  # 낮을수록 더 비밀(TS=1)
            model_code = model_label.value if hasattr(model_label, "value") else str(model_label)
            top_code = next(iter(order), "TS")
            if model_code == top_code:
                return None  # 이미 최고등급 — 과소분류 불가, 검색 불요
            if not text:
                return None

            # dense 임베딩 + dense 검색(코사인). hybrid/reranker 금지(점수 의미 불일치).
            from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
            from koipa.adapters.vectorstore import build_store  # noqa: PLC0415

            result = build_embedder().embed([text])
            vectors = getattr(result, "vectors", None) or result
            vec = vectors[0] if vectors else None
            if not vec:
                return None
            collection = getattr(settings, "rag_default_collection", "docs")
            top_k = int(getattr(settings, "rag_default_top_k", 5) or 5)
            tau = float(getattr(settings, "similarity_escalation_tau", 0.92))
            hits = build_store().search(collection, vec, top_k=top_k, filter=None)
            # [obs] 게이트가 실제 검색을 수행함(routed와 분리) — enabled인데 참조 없어 inert인
            # 상태를 'ran'>0·'routed'=0으로 구분(무실데이터 단계 가시화).
            self._inc_similarity_escalation("ran")

            self_id = str(doc_id or "")
            seen: set[str] = set()
            for hit in hits or []:
                score = float(getattr(hit, "score", 0.0) or 0.0)
                if score < tau:
                    continue  # 고정밀 임계 미만 — 참조로 안 씀
                payload = getattr(hit, "payload", None) or {}
                nid = str(
                    payload.get("doc_id")
                    or payload.get("source_doc")
                    or getattr(hit, "id", "")
                    or ""
                )
                if not nid or nid == self_id or nid in seen:
                    continue  # 자기 자신(또는 동일 doc의 다른 청크)·중복 제외
                seen.add(nid)
                neighbor_code = self._verified_human_grade(nid)
                if neighbor_code is None:
                    continue  # 사람검증 등급 없음(머신라벨/미검증) → 참조 불가
                if order.get(neighbor_code, 99) < order.get(model_code, 99):
                    # 이웃이 모델 예측보다 엄격히 더 비밀 → 사람 확인(등급은 무변경).
                    self._inc_similarity_escalation("routed")
                    return (
                        f"similarity-escalation: model auto-confirmed {model_code} but an authoritatively-"
                        f"verified (human/legal) similar doc {nid} is graded higher {neighbor_code} "
                        f"(cos={score:.2f} >= {tau:.2f}) — routed to human review (FNR-safe, grade unchanged)"
                    )
            return None
        except Exception as exc:  # noqa: BLE001 — 임베딩/검색/DB 오류는 fail-open(분류 진행)
            logger.debug("similarity-escalation fail-open: %s", exc)
            self._record_gate_fail_open("similarity_escalation")
            return None

    @staticmethod
    def _verified_human_grade(neighbor_doc_id: str) -> "str | None":
        """이웃 doc의 *사람/권위* 검증 등급 코드 — 없거나 머신라벨이면 None.

        exact-match override가 쓰는 동일 primitive(get_verified_document_label)로 검증라벨을
        가져와, labeled_by가 권위 출처(_AUTHORITATIVE_LABEL_SOURCES)일 때만 등급코드를 반환한다.
        file_hash 폴백(_verified_label_by_content)은 쓰지 않는다(그건 exact-match의 몫).
        예외는 None(이웃 1건 스킵 — 게이트 전체를 죽이지 않음) + fail-open 카운터로 가시화한다
        (systemic 라벨-DB 장애를 무음 no-op으로 숨기지 않음 — 게이트 contract 준수).
        """
        try:
            from sqlalchemy import select  # noqa: PLC0415

            from koipa.db import SessionLocal  # noqa: PLC0415
            from koipa.db.models import ClassificationLevel  # noqa: PLC0415
            from koipa.repositories.classify_repo import ClassifyRepo  # noqa: PLC0415

            nid = _try_uuid_str(neighbor_doc_id)
            if nid is None:
                return None
            db = SessionLocal()
            try:
                dl = ClassifyRepo(db).get_verified_document_label(nid)
                if dl is None or dl.labeled_by not in ClassifyService._AUTHORITATIVE_LABEL_SOURCES:
                    return None
                level = db.execute(
                    select(ClassificationLevel).where(
                        ClassificationLevel.level_id == dl.level_id
                    )
                ).scalar_one_or_none()
                return level.level_code if level is not None else None
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_verified_human_grade skipped: %s", exc)
            # systemic 라벨-DB 장애가 무음 no-op으로 숨지 않게 가시화(게이트 fail-open contract).
            ClassifyService._record_gate_fail_open("similarity_escalation")
            return None

    @staticmethod
    def _inc_similarity_escalation(action: str = "routed") -> None:
        """[obs] 유사도 escalation 가시화 — best-effort(메트릭 실패가 게이트를 막지 않음).

        action='ran'(게이트가 실제 검색 수행)·'routed'(검수 라우팅 발동). ran>0·routed=0이면
        '활성인데 참조(권위 고등급 유사문서)가 없어 inert'를 의미 — disabled/empty와 구분된다.
        """
        try:
            from koipa.api.prom_metrics import (  # noqa: PLC0415
                SIMILARITY_ESCALATION_TOTAL,
            )

            SIMILARITY_ESCALATION_TOTAL.labels(action=action).inc()
        except Exception:  # noqa: BLE001
            pass

    def _effective_metadata(self, req: ClassifyRequest, content: str | None = None) -> dict:
        """분류 metadata 구성 — 요청 metadata에 저장된 문서 출처(provenance)를 보강.

        Gate-1(출처 게이트, source-prior)은 metadata.source_type/source가 있을 때만
        발동한다(InferencePipeline.run). 업로드→ingest→doc_id 분류 흐름에서는 요청에
        출처가 없으므로, documents.metadata_에 적재된 source_type/source를 여기서
        hydrate해 게이트가 실제로 발동하도록 한다.

        - 요청이 출처를 명시했으면 그대로 둔다(요청 우선 — 덮어쓰지 않음).
        - [FIX-D] 출처가 없으면 본문 마스트헤드에서 공개특허공보 서지헤더를 탐지해
          source_type='공개특허'를 합성 주입 → 기존 source-prior 캡이 발동(TS/S1→S3 cap +
          cap-conflict→needs_review). raw PDF 업로드처럼 provenance 메타가 없는 경로 보완.
          엄격 탐지(3요소·머리 한정)라 진짜 기밀·공보 편찬물은 트리거되지 않음(FNR-safe).
        - best-effort: DB 미가용·문서 부재·content 직접분류(doc_id 없음)면 요청 metadata 그대로.
        """
        meta = dict(req.metadata or {})
        # [ICD 3필드] 종전에는 source_type 이 있으면 여기서 바로 반환해 **관리성 두 필드를
        # DB 에서 영영 안 읽었다.** 업로드 경로로 들어온 문서는 security_marking·access_scope
        # 가 metadata_ 에 저장돼 있어도 분류 때 못 쓰였다는 뜻이다.
        #
        # 관리성은 본문에서 관측되지 않는 축이고(실측: 실문서 17~21% 만 표시 보유), 정본에서
        # S1 은 (2,2,0) 하나뿐이라 M 이 0 으로 확정되지 않으면 S1 이 구조적으로 도달 불가다.
        # 그 경로를 막고 있었다.
        #
        # 이제 세 필드를 각각 본다 — 요청에 있으면 그것이 우선이고, 없는 것만 DB 에서 채운다.
        if all(meta.get(k) for k in ("source_type", "security_marking", "access_scope")):
            return meta
        # [FIX-D] 본문 마스트헤드 기반 공개출처 합성 주입 (요청/DB 출처가 없을 때만)
        text_for_masthead = content if content is not None else (req.content or "")
        if _is_published_patent_gazette(text_for_masthead):
            meta["source_type"] = "공개특허"
            logger.info(
                "FIX-D: published-patent gazette masthead detected → source_type='공개특허'"
                " (source-prior cap will apply, doc_id=%s)", req.doc_id,
            )
            # ⚠ 여기서 반환하지 않는다. 출처를 알아냈다고 관리성까지 아는 것은 아니고,
            #   업로드 때 저장된 security_marking·access_scope 는 아래 DB 하이드레이션
            #   에서만 온다. 종전에는 여기서 끊겨 그 두 필드가 유실됐다.
        doc_uuid = self._parse_doc_uuid(req.doc_id)
        if doc_uuid is None:
            return meta
        if _skip_optional_db_work():
            return meta
        try:
            from koipa.db import session_scope  # noqa: PLC0415
            from koipa.repositories.document_repo import DocumentRepo  # noqa: PLC0415

            with session_scope() as db:
                doc = DocumentRepo(db).get(doc_uuid)
                stored = getattr(doc, "metadata_", None) if doc is not None else None
                if isinstance(stored, dict):
                    if not (meta.get("source_type") or meta.get("source")):
                        src = stored.get("source_type") or stored.get("source")
                        if src:
                            meta["source_type"] = src
                    # ICD §3.2·§3.3 — 업로드 때 저장된 관리성 근거를 분류로 넘긴다.
                    for key in ("security_marking", "access_scope"):
                        if not meta.get(key) and stored.get(key):
                            meta[key] = stored[key]
        except Exception as exc:  # noqa: BLE001
            logger.debug("_effective_metadata hydrate skipped (non-critical): %s", exc)
        return meta
