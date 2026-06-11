from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lloydk.config import settings
from lloydk.schemas.common import Grade, GradeRegistry
from lloydk.schemas.classify import EvidenceSpan, EvaluationFactors, RagContextHit
from lloydk.modules.m2_preprocess import split as _chunk_split
from lloydk.modules.m3_labeling.pipeline import LabelingPipeline

logger = logging.getLogger(__name__)


def chunk_text(text: str, size: int = 512, overlap: int = 64):
    """하위호환 wrapper — 새 split()로 위임."""
    return _chunk_split(text, size=size, overlap=overlap)


@dataclass
class InferenceResult:
    label: Grade
    confidence: float
    scores: dict[str, float]
    factors: Optional[EvaluationFactors] = None
    evidence: list[EvidenceSpan] = field(default_factory=list)
    rag_context: list[RagContextHit] = field(default_factory=list)
    model_version: str = "poc"
    warnings: list[str] = field(default_factory=list)


_LABELS = [Grade.TS, Grade.S1, Grade.S2, Grade.S3]


def _get_labels() -> list:
    """GradeRegistry에서 현재 활성 등급 목록 반환.

    DB에 커스텀 등급이 있으면 그것을, 없으면 기본 _LABELS를 반환.
    Grade enum에 없는 코드는 문자열로 반환 (다른 프로젝트 호환).
    """
    codes = GradeRegistry.get_codes()
    labels = []
    for code in codes:
        try:
            labels.append(Grade(code))
        except ValueError:
            labels.append(code)
    return labels or _LABELS


class InferencePipeline:
    """
    PoC 추론기.
    - 학습 가중치가 있으면 transformers 로드, 없으면 M3 라벨링 규칙 기반 점수로 폴백.
    - 청크 어그리게이션: 청크별 softmax 평균.
    """

    def __init__(self, model_dir: str | Path | None = None):
        self.model_dir = Path(model_dir) if model_dir else None
        self.labeling = LabelingPipeline()
        self._model = None
        self._tokenizer = None
        # GradeRegistry 순서는 **rule-fallback 경로 전용** id→등급 매핑.
        # 학습 모델이 로드되면 _load_model에서 모델 config.id2label로 덮어쓴다
        # (softmax 인덱스는 학습 시점 순서에 고정되어 있고 DB level_order와 무관).
        active = _get_labels()
        self._id2label: dict[int, object] = dict(enumerate(active))
        if self.model_dir and self.model_dir.exists():
            self._load_model()

    def _load_model(self):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        self._model = AutoModelForSequenceClassification.from_pretrained(str(self.model_dir))
        # ── id2label은 학습 체크포인트 config에서 가져온다 (fail-closed) ──────────
        # 학습기(m4_training/trainer.py)는 num_labels 인덱스 0..N을 고정 순서
        # [TS,S1,S2,S3]에 매핑해 config.id2label에 baked한다. 추론에서 이 순서를
        # GradeRegistry(DB level_order)로 재구성하면, DB 순서가 다를 때 softmax
        # 인덱스가 엉뚱한 등급에 매핑되어 '미탐(비밀→공개)'을 일으킨다.
        # → 반드시 모델 자신의 config.id2label로 매핑하고, 길이·코드집합이
        #   기대(GradeRegistry 코드집합)와 불일치하면 로드를 거부한다.
        cfg_id2label = self._id2label_from_config()
        if cfg_id2label is None:
            # config 매핑을 신뢰할 수 없으면 모델을 폐기(fail-closed) — rule-fallback로.
            self._model = None
            self._tokenizer = None
            raise ValueError(
                "model config.id2label is missing/invalid or its code set does not match "
                "the active grade registry; refusing to load (fail-closed) to avoid "
                "mis-mapping softmax indices to wrong grades (missed-detection risk)"
            )
        self._id2label = cfg_id2label
        self._model.eval()
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(self._device)

    def _id2label_from_config(self) -> dict[int, object] | None:
        """학습 체크포인트 config.id2label → {int index: Grade|str} 매핑.

        검증(fail-closed):
          - config.id2label가 없으면 None.
          - 인덱스가 0..N-1 연속이 아니면 None.
          - 코드 집합이 현재 활성 등급 코드집합과 다르면 None
            (학습-DB 등급 스키마 mismatch → 인덱스 오매핑 방지).
        매핑 자체는 config 인덱스를 그대로 보존한다(순서를 재정렬하지 않는다).
        """
        cfg = getattr(self._model, "config", None)
        raw = getattr(cfg, "id2label", None)
        if not raw or not isinstance(raw, dict):
            return None

        # config.id2label 키는 int 또는 str("0"..)일 수 있다 — int로 정규화.
        mapping: dict[int, str] = {}
        try:
            for k, v in raw.items():
                mapping[int(k)] = str(v)
        except (ValueError, TypeError):
            return None

        n = len(mapping)
        if n == 0 or sorted(mapping.keys()) != list(range(n)):
            return None  # 인덱스가 0..N-1 연속이 아님 → 신뢰 불가

        # 코드집합 일치 검증: 학습 체크포인트 등급집합 == 현재 활성 등급집합
        active_codes = set(GradeRegistry.get_codes())
        if active_codes and set(mapping.values()) != active_codes:
            return None

        out: dict[int, object] = {}
        for idx in range(n):
            code = mapping[idx]
            try:
                out[idx] = Grade(code)
            except ValueError:
                out[idx] = code  # 커스텀 등급(다른 프로젝트) 호환 — 문자열 보존
        return out

    # FNR-safe override: rule engine이 이 점수 이상으로 TS를 잡으면 모델 결과 무시.
    # 모델이 TS 미학습 도메인(M&A·암호·국방 등)을 S1/S2/S3로 내릴 때 방어.
    # settings.fnr_rule_ts_threshold로 외부 조정 가능. 기본값 3.0.
    @property
    def _FNR_RULE_TS_THRESHOLD(self) -> float:  # type: ignore[override]
        try:
            from lloydk.config import settings  # noqa: PLC0415
            return float(settings.fnr_rule_ts_threshold)
        except Exception:
            return 3.0

    @property
    def _FNR_RULE_S1_THRESHOLD(self) -> float:
        try:
            from lloydk.config import settings  # noqa: PLC0415
            return float(settings.fnr_rule_s1_threshold)
        except Exception:
            return 2.2

    @property
    def _FNR_RULE_S2_THRESHOLD(self) -> float:
        try:
            from lloydk.config import settings  # noqa: PLC0415
            return float(settings.fnr_rule_s2_threshold)
        except Exception:
            return 1.6

    def run(
        self,
        text: str,
        use_rag: bool = False,
        rag_namespace: Optional[str] = None,
        metadata: Optional[dict] = None,
        return_evidence: bool = True,
    ) -> InferenceResult:
        if self._model is not None:
            result = self._run_model(text, use_rag, return_evidence)
            # FNR-safe: rule engine이 TS를 강하게 잡는데 모델이 낮은 등급을 줬으면 TS로 올림.
            if result.label != Grade.TS:
                try:
                    rule_res = self.labeling.engine.label(text)
                    ts_score = rule_res.grade_scores.get("TS", 0.0)
                    s1_score = rule_res.grade_scores.get("S1", 0.0)
                    s2_score = rule_res.grade_scores.get("S2", 0.0)
                    from lloydk.modules.m3_labeling.seeds import GRADE_ORDER  # noqa: PLC0415

                    override_grade = None
                    override_score = 0.0
                    if ts_score >= self._FNR_RULE_TS_THRESHOLD:
                        override_grade, override_score = Grade.TS, ts_score
                    elif s1_score >= self._FNR_RULE_S1_THRESHOLD:
                        override_grade, override_score = Grade.S1, s1_score
                    elif s2_score >= self._FNR_RULE_S2_THRESHOLD:
                        override_grade, override_score = Grade.S2, s2_score

                    if override_grade is not None:
                        if GRADE_ORDER.get(override_grade.value, 99) < GRADE_ORDER.get(result.label.value, 99):
                            result = InferenceResult(
                                label=override_grade,
                                confidence=max(result.confidence, rule_res.confidence),
                                scores={
                                    **result.scores,
                                    override_grade.value: max(result.scores.get(override_grade.value, 0), 0.7),
                                },
                                factors=result.factors,
                                evidence=result.evidence,
                                rag_context=result.rag_context,
                                model_version=result.model_version,
                                warnings=result.warnings + [
                                    f"fnr-safe override: rule {override_grade.value} score={override_score:.1f}"
                                ],
                            )
                except Exception:  # noqa: BLE001
                    pass
        else:
            result = self._run_rule_fallback(text, return_evidence)

        # Source-type prior: 판례/공개 문서는 비공지성 실패로 정의상 하향 등급 —
        # 모델의 상위등급 과분류를 방어. metadata.source_type/source가 공개 소스일 때만 적용.
        # settings.source_prior_enabled=True (기본 False) 시 활성화.
        #
        # cap 레벨은 settings.source_prior_cap_grade로 선택 (기본 "S2"):
        #   "S2": TS/S1 예측을 S2로 cap (부분 완화 — S3 과분류는 안 건드림, FNR 위험 작음).
        #   "S3": TS/S1/S2 예측을 S3로 cap (S3 과분류 완전 완화 — FNR 위험 큼,
        #         판례 기반 S1/S2 시나리오(koipa_case_based)나 국가핵심기술 고시는 손상).
        # 주의: 단순 source 매칭이라 '공개 판례지만 정답이 S1/S2'인 케이스를 망칠 수 있음.
        # 운영 활성화 전 reports/p1_*_source_prior_* 측정으로 F1/FNR trade-off 확인 필수.
        try:
            from lloydk.config import settings as _s  # noqa: PLC0415
            if getattr(_s, "source_prior_enabled", False) and metadata:
                src = metadata.get("source_type", "") or metadata.get("source", "")
                # 공개 출처 = 비공지성 실패(이미 공개됨) → S3 cap. 공개특허/등록특허/공보는
                # '공개를 택한' 문서라 doc/32 §3 설계대로 게이트가 S3로 cap해야 한다
                # (콘텐츠 모델은 기술내용을 고등급으로 보지만 provenance가 공개라 영업비밀 불성립).
                # 정밀 토큰만 등록 — bare "특허"는 내부 '특허전략' 문서를 오인 cap(FNR)할 수 있어 제외.
                _PUBLIC_SOURCES = {
                    "court_decision", "판례", "public_disclosure", "공시", "채용공고", "보도자료",
                    "공개특허", "등록특허", "공개공보", "특허공보", "published_patent",
                }
                if any(s in str(src) for s in _PUBLIC_SOURCES):
                    _GRADE_ORDER_LOCAL = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}
                    cap_code = (getattr(_s, "source_prior_cap_grade", "S2") or "S2").upper()
                    if cap_code not in _GRADE_ORDER_LOCAL:
                        cap_code = "S2"
                    cap_rank = _GRADE_ORDER_LOCAL[cap_code]
                    cur_rank = _GRADE_ORDER_LOCAL.get(result.label.value, 99)
                    if cur_rank < cap_rank:  # 예측이 cap보다 상위(숫자 작음)면 cap으로 하향
                        result.warnings = list(result.warnings) + [
                            f"source-prior: {src!r} is public → grade capped at {cap_code}"
                        ]
                        result = InferenceResult(
                            label=Grade[cap_code],
                            confidence=min(result.confidence, 0.7),
                            scores={**result.scores, cap_code: max(result.scores.get(cap_code, 0), 0.6)},
                            factors=result.factors,
                            evidence=result.evidence,
                            rag_context=result.rag_context,
                            model_version=result.model_version,
                            warnings=result.warnings,
                        )
        except Exception:  # noqa: BLE001
            pass

        # 표적 1 (2026-05-29): use_rag=True면 retrieval facade 호출하여 rag_context 채움.
        # rule-fallback / _run_model 어느 경로든 동일하게 RAG context 보강 — 분류 본문은 안 건드림.
        # 모든 외부 의존(벡터스토어/임베더/reranker) 실패는 silent + 빈 컨텍스트 폴백.
        if use_rag:
            try:
                hits = self._build_rag_context(
                    query=text,
                    namespace=rag_namespace,
                    metadata=metadata,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("rag_context build failed: %s", exc)
                hits = []
            if hits:
                result.rag_context = hits
            else:
                # 빈 결과는 운영 신호 — warning에 한 줄 남기되 분류는 그대로 진행.
                result.warnings = list(result.warnings) + ["rag_context empty (store/encoder unavailable or no hits)"]

        return result

    def _run_rule_fallback(self, text: str, return_evidence: bool) -> InferenceResult:
        try:
            from lloydk.api.prom_metrics import RULE_FALLBACK_TOTAL  # noqa: PLC0415
            RULE_FALLBACK_TOTAL.inc()
        except Exception:  # noqa: BLE001
            pass
        lab = self.labeling.label(text)
        # grade_scores를 합계로 나눠 [0,1] 확률로 변환 (rule engine은 원시 점수 반환)
        raw = (lab.rule_result.grade_scores if lab.rule_result else {}) or {}
        total_raw = sum(raw.values())
        if total_raw > 0:
            scores = {g.value: round(raw.get(g.value, 0.0) / total_raw, 4) for g in _LABELS}
        else:
            scores = {g.value: 0.0 for g in _LABELS}
        return InferenceResult(
            label=lab.grade,
            confidence=lab.confidence,
            scores=scores,
            factors=lab.factors,
            evidence=lab.evidence if return_evidence else [],
            model_version="rule-fallback-v0",
            warnings=["model weights not loaded — using rule-based fallback"],
        )

    def _run_model(self, text: str, use_rag: bool, return_evidence: bool) -> InferenceResult:
        import torch
        import torch.nn.functional as F

        chunks = chunk_text(text, settings.max_seq_len * 3, settings.chunk_overlap)
        chunk_texts = [c.text for c in chunks] or [text]

        probs = []
        with torch.no_grad():
            for batch_start in range(0, len(chunk_texts), 8):
                batch = chunk_texts[batch_start:batch_start + 8]
                enc = self._tokenizer(
                    batch, truncation=True, max_length=settings.max_seq_len,
                    padding=True, return_tensors="pt",
                ).to(self._device)
                logits = self._model(**enc).logits
                probs.append(F.softmax(logits, dim=-1).cpu())
        doc_prob = torch.cat(probs).mean(dim=0)
        scores = {g.value: float(doc_prob[i]) for i, g in self._id2label.items()}
        pred_idx = int(doc_prob.argmax().item())
        pred = self._id2label[pred_idx]
        conf = float(doc_prob[pred_idx])

        lab = self.labeling.label(text)  # 보조 evidence/factors
        # use_rag은 run() 레벨에서 통합 처리 — 본 함수 시그니처는 호환 위해 유지.
        del use_rag
        return InferenceResult(
            label=pred,
            confidence=conf,
            scores=scores,
            factors=lab.factors,
            evidence=lab.evidence if return_evidence else [],
            model_version=str(self.model_dir.name) if self.model_dir else "model",
        )

    # ------------------------------------------------------------
    # 표적 1 — RAG context builder
    # ------------------------------------------------------------

    def _build_rag_context(
        self,
        *,
        query: str,
        namespace: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> list[RagContextHit]:
        """retrieval facade를 안전하게 호출해 RagContextHit 리스트 반환.

        모든 외부 의존(벡터스토어/임베더/reranker)이 실패하거나 부재하면 빈 리스트.
        - store: build_store() — ES 미가용 시 InMemoryStore 자동 폴백 (이미 어댑터가 처리)
        - encode: build_embedder() — HF 모델 로드 실패 시 HashEmbedding 폴백 (이미 어댑터가 처리)
        - collection: namespace 우선, 없으면 settings.rag_default_collection
        - top_k: settings.rag_default_top_k 또는 5
        """
        if not query or not query.strip():
            return []

        # collection 결정
        collection = (namespace or "").strip() or getattr(settings, "rag_default_collection", "docs")
        top_k = int(getattr(settings, "rag_default_top_k", 5))
        method = getattr(settings, "rag_query_expansion_method", "rule")

        # 어댑터 lazy import — m5_inference 모듈 로드 시점 의존 차단
        try:
            from lloydk.adapters.embedding import build_embedder  # noqa: PLC0415
            from lloydk.adapters.vectorstore import build_store  # noqa: PLC0415
            from lloydk.services.retrieval import expand_then_search  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            logger.debug("rag adapters unavailable: %s", exc)
            return []

        try:
            store = build_store()
        except Exception as exc:  # noqa: BLE001
            logger.warning("vectorstore build failed: %s", exc)
            return []

        try:
            embedder = build_embedder()
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedder build failed: %s", exc)
            return []

        def _encode(t: str):
            try:
                result = embedder.embed([t])
                # EmbeddingResult.vectors 또는 list[list[float]]
                vectors = getattr(result, "vectors", None) or result
                return vectors[0] if vectors else []
            except Exception as exc:  # noqa: BLE001
                logger.debug("encode failed: %s", exc)
                return []

        def _encode_batch(texts: list[str]):
            """§1: 확장 쿼리 N개 1회 forward — KURE p50 629ms × 4쿼리 단축."""
            if not texts:
                return []
            try:
                result = embedder.embed(texts)
                vectors = getattr(result, "vectors", None) or result
                return list(vectors)
            except Exception as exc:  # noqa: BLE001
                logger.debug("encode_batch failed: %s", exc)
                return []

        # 테넌트 격리는 fail-CLOSED. 호출자가 멀티테넌트 컨텍스트를 신호(metadata dict
        # 전달)했는데 tenant_id가 비어 있으면 — 테넌트가 '미확정'이므로 무격리 검색으로
        # 떨어뜨리지 않고 빈 컨텍스트를 반환한다(교차테넌트 유출 차단). 분류 본문은
        # run()에서 graceful degradation으로 그대로 진행되고 RAG context만 비게 된다.
        #
        # 구분:
        #   metadata is None  → 멀티테넌트 미관여(단일테넌트/레거시 호출). 필터 없이 검색.
        #   metadata == {}    → 동일하게 미관여로 본다(빈 컨텍스트 시그널 없음).
        #   metadata = {...} (tenant_id 없음/빈값) → 멀티테넌트 요청인데 테넌트 분실 →
        #                       fail-CLOSED(빈 컨텍스트). 이게 H8이 막으려는 fail-open 경로.
        filter_ = None
        if isinstance(metadata, dict) and metadata:
            tenant = metadata.get("tenant_id")
            if not tenant or not str(tenant).strip():
                logger.warning(
                    "rag_context: tenant_id undetermined in a populated metadata context — "
                    "returning empty context (fail-closed) to prevent cross-tenant leakage"
                )
                return []
            filter_ = {"tenant_id": tenant}

        try:
            hits = expand_then_search(
                store=store,
                collection=collection,
                query_text=query,
                encode_batch=_encode_batch,
                encode=_encode,
                method=method,
                top_k=top_k,
                filter=filter_,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("expand_then_search failed: %s", exc)
            return []

        # SearchHit → RagContextHit 변환
        out: list[RagContextHit] = []
        for h in hits:
            payload = h.payload or {}
            source_doc = str(payload.get("doc_id") or payload.get("source_doc") or h.id)
            out.append(RagContextHit(
                source_doc=source_doc,
                chunk_id=str(h.id),
                score=float(h.score),
            ))
        return out
