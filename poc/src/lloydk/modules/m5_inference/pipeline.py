from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lloydk.config import settings
from lloydk.schemas.common import Grade, GradeRegistry
from lloydk.schemas.classify import EvidenceSpan, EvaluationFactors, RagContextHit
from lloydk.modules.m2_preprocess import split as _chunk_split
from lloydk.modules.m3_labeling.pipeline import LabelingPipeline
from lloydk.obs.otel import span  # 수동 span — OTel 미설치/미활성 시 완전 no-op

logger = logging.getLogger(__name__)

_PUBLIC_SOURCE_TOKENS = {
    "court_decision",
    "public_disclosure",
    "published_patent",
    "\ud310\ub840",  # 판례
    "\uacf5\uc2dc",  # 공시
    "\ucc44\uc6a9\uacf5\uace0",  # 채용공고
    "\ubcf4\ub3c4\uc790\ub8cc",  # 보도자료
    "\uacf5\uac1c\ud2b9\ud5c8",  # 공개특허
    "\ub4f1\ub85d\ud2b9\ud5c8",  # 등록특허
    "\uacf5\uac1c\uacf5\ubcf4",  # 공개공보
    "\ud2b9\ud5c8\uacf5\ubcf4",  # 특허공보
}
_PUBLIC_SOURCE_NEGATIVE_TOKENS = {
    "draft",
    "planned",
    "internal",
    "private",
    "unpublished",
    "nonpublic",
    "non-public",
    "\ubbf8\uacf5\uc2dc",  # 미공시
    "\ubbf8\uacf5\uac1c",  # 미공개
    "\ube44\uacf5\uac1c",  # 비공개
    "\ucd08\uc548",  # 초안
    "\uc608\uc815",  # 예정
}


def _source_prior_is_public(src: object) -> bool:
    text = str(src or "").strip().lower()
    if not text:
        return False
    tokens = {
        t for t in re.split(r"[^0-9a-zA-Z_\uac00-\ud7a3-]+", text)
        if t
    }
    if text in _PUBLIC_SOURCE_NEGATIVE_TOKENS or tokens & _PUBLIC_SOURCE_NEGATIVE_TOKENS:
        return False
    if any(t.startswith("\ubbf8") and t[1:] in _PUBLIC_SOURCE_TOKENS for t in tokens):
        return False
    return text in _PUBLIC_SOURCE_TOKENS or bool(tokens & _PUBLIC_SOURCE_TOKENS)


def _record_gate_fail_open(gate: str) -> None:
    """[obs] 서빙 파이프라인 게이트가 예외로 fail-open(미적용)했음을 가시화 — best-effort.

    metadata-floor·FNR-safe override 같은 상향/라우팅 게이트가 조용히 실패하면 비밀이 낮은
    등급을 무음 유지할 수 있다("게이트ON=가시성ON" 위반). classify_service 와 동일한
    SERVING_GATE_FAIL_OPEN_TOTAL 카운터에 gate 라벨로 기록한다 — 분류 제어흐름은 그대로
    (예외는 계속 삼켜 fail-safe 유지), 가시성만 추가. 메트릭/로그 실패는 무시.
    """
    try:
        from lloydk.api.prom_metrics import SERVING_GATE_FAIL_OPEN_TOTAL  # noqa: PLC0415
        SERVING_GATE_FAIL_OPEN_TOTAL.labels(gate=gate).inc()
    except Exception:  # noqa: BLE001
        pass
    logger.debug("serving gate fail-open (예외로 미적용): %s", gate)


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
    # [agreement-gate 재사용] run()이 이미 산출한 **원시 룰등급**(rule_result.grade) — 합의
    # 게이트가 룰엔진을 두 번 돌리지 않게 노출. label과 별개(label은 모델/청크집계/override
    # 결과, rule_grade는 단일패스 raw 룰등급). None이면 호출부가 폴백 재계산.
    rule_grade: Optional[str] = None
    # [transparency] 원시 모델 판정(override/cap/floor 적용 이전 _run_model argmax).
    # 모델 미로드(rule-fallback) 시 None. label과 별개(label은 최종 결합 결과).
    model_grade: Optional[str] = None


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
        self._model_temperature: float | None = None  # [A1] 모델 동봉 temperature.json 자동로드값
        # [calibration-visibility] 서빙 보정 상태 — None=rule-fallback(보정 N/A), True=동봉/주입
        # 보정 적용, False=무보정(T=1.0) 서빙(무음실패 위험). _apply_bundle_calibration에서 셋.
        self.calibrated: bool | None = None
        self._calibration_source: str = "rule-fallback"
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
        # [A1] 모델 아티팩트 동봉 temperature.json 자동 로드(보정 자동연결) + 무보정 가시화.
        # 보정 스크립트(calibrate_classifier.py)가 모델 폴더에 {"temperature": T}를 남기면
        # 서빙이 자동으로 logit/T 적용. settings.classifier_temperature가 명시(≠1.0)면 그게 우선.
        self._calibration_source = self._apply_bundle_calibration()

    def _apply_bundle_calibration(self) -> str:
        """모델 dir 동봉 temperature.json을 로드하고 무보정 서빙을 가시화.

        반환(보정 출처): "bundle"=동봉 T 적용 · "env"=classifier_temperature 주입 ·
        "uncalibrated"=둘 다 없어 T=1.0 무보정 서빙(loud-warn).

        실모델이 로드됐는데 보정이 없으면 OOD 과신으로 softmax conf가 부풀어 conf 게이트(0.7)가
        보정 안 된 값 위에 서고, 고등급(TS/S1)이 과신 자동확정으로 무음미탐될 수 있다. 종전엔
        temperature.json 부재가 *완전 무음*이라, 로드 시 1회 loud-warn으로 드러낸다(서빙 T 계산
        자체는 불변 — _temperature()가 진실 소스, 본 메서드는 가시성·상태표시만 추가).
        """
        import json as _json  # noqa: PLC0415

        self._model_temperature = None
        if self.model_dir is not None:
            try:
                _tpath = self.model_dir / "temperature.json"
                if _tpath.exists():
                    _t = float(_json.loads(_tpath.read_text(encoding="utf-8")).get("temperature", 0))
                    if _t > 0:
                        self._model_temperature = _t
                        self.calibrated = True
                        return "bundle"
                    logger.warning(
                        "[calibration] %s/temperature.json 의 temperature=%r (<=0) — 무시(무보정 폴백).",
                        self.model_dir, _t,
                    )
            except Exception as _exc:  # noqa: BLE001
                logger.warning(
                    "[calibration] %s/temperature.json 읽기 실패: %s — 무보정 폴백.",
                    self.model_dir, _exc,
                )
        # 동봉 보정 없음 — settings.classifier_temperature(≠1.0) 주입이 있으면 그것으로 보정.
        try:
            from lloydk.config import settings as _settings  # noqa: PLC0415
            _env_t = float(getattr(_settings, "classifier_temperature", 1.0))
        except Exception:  # noqa: BLE001
            _env_t = 1.0
        if _env_t > 0 and abs(_env_t - 1.0) > 1e-9:
            self.calibrated = True
            return "env"
        # 무보정(T=1.0) 서빙 — 무음실패 위험. loud-warn.
        self.calibrated = False
        logger.warning(
            "[calibration] 활성 모델(%s)에 사용 가능한 temperature.json이 없고 "
            "classifier_temperature=1.0 — 무보정(T=1.0) 서빙. OOD 과신으로 고등급(TS/S1) "
            "무음미탐 위험. 학습 후 calibrate_classifier.py로 temperature.json을 동봉하거나 "
            "CLASSIFIER_TEMPERATURE를 주입하세요.",
            self.model_dir,
        )
        return "uncalibrated"

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

    @property
    def _escalation_tau(self) -> float | None:
        """서빙 escalation τ (C-esc). None=순수 argmax(동작 보존). 0<τ<1만 유효."""
        try:
            from lloydk.config import settings  # noqa: PLC0415
            t = settings.classifier_escalation_tau
            if t is None:
                return None
            t = float(t)
            if 0.0 < t < 1.0:
                return t
        except Exception:  # noqa: BLE001
            pass
        return None

    def _code_at(self, idx: int) -> str:
        g = self._id2label[idx]
        return g.value if hasattr(g, "value") else str(g)

    def _select_pred_idx(self, probs) -> int:
        """[C-esc] 예측 인덱스 선택 — escalation τ 또는 argmax.

        τ=None: 순수 argmax(기존 동작). τ 설정 시: 가장 심각한 등급(GRADE_ORDER 작은 값)부터
        검사해 prob ≥ τ 인 첫 등급 채택, 없으면 argmax 폴백. eval_fnr_threshold_sweep와 동일 규칙.
        probs는 list/torch.Tensor 모두 허용(float 인덱싱). most-severe-wins 집계가 반영된 벡터.
        """
        n = len(self._id2label)

        def _argmax() -> int:
            return max(range(n), key=lambda i: float(probs[i]))

        tau = self._escalation_tau
        if tau is None:
            return _argmax()
        from lloydk.modules.m3_labeling.seeds import GRADE_ORDER  # noqa: PLC0415
        for i in sorted(range(n), key=lambda j: GRADE_ORDER.get(self._code_at(j), 999)):
            if float(probs[i]) >= tau:
                return i
        return _argmax()

    @property
    def _temperature(self) -> float:
        """서빙 softmax temperature scaling 계수.

        분류기 softmax는 학습에 없던 새 문체(OOD)에서 과신(overconfident)하는 경향이
        있어, confidence 게이트(0.7)가 보정 안 된 값 위에 설 수 있다. T>1이면 logit을
        나눠 분포를 부드럽게 해 과신을 완화(calibration)한다. 기본 1.0 = 무보정(기존
        동작 보존). 평가에서 측정한 temperature를 settings.classifier_temperature로 주입.
        """
        try:
            from lloydk.config import settings  # noqa: PLC0415
            t = float(settings.classifier_temperature)
            if t > 0 and abs(t - 1.0) > 1e-9:
                return t  # .env로 명시 주입된 값이 최우선
        except Exception:  # noqa: BLE001
            pass
        # [A1] settings 미지정(=1.0)이면 모델 동봉 temperature.json 사용(보정 자동연결).
        if self._model_temperature and self._model_temperature > 0:
            return self._model_temperature
        return 1.0

    def run(
        self,
        text: str,
        use_rag: bool = False,
        rag_namespace: Optional[str] = None,
        metadata: Optional[dict] = None,
        return_evidence: bool = True,
    ) -> InferenceResult:
        _model_raw_grade: Optional[str] = None
        if self._model is not None:
            result = self._run_model(text, use_rag, return_evidence)
            _model_raw_grade = result.label.value if hasattr(result.label, "value") else str(result.label)
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
                            # [M-renorm] override 등급을 strict argmax로 만들고 재정규화 +
                            # confidence=scores[label] 재계산 (label/scores/confidence 정합).
                            new_scores, new_conf = self._enforce_label_consistency(
                                result.scores, override_grade.value, floor=0.7
                            )
                            result = InferenceResult(
                                label=override_grade,
                                confidence=new_conf,
                                scores=new_scores,
                                factors=result.factors,
                                evidence=result.evidence,
                                rag_context=result.rag_context,
                                model_version=result.model_version,
                                warnings=result.warnings + [
                                    f"fnr-safe override: rule {override_grade.value} score={override_score:.1f}"
                                ],
                                rule_grade=result.rule_grade,
                            )
                except Exception:  # noqa: BLE001
                    # FNR-safe 상향이 예외로 미적용 → 모델의 낮은 등급 유지(무음 미탐 위험). 가시화.
                    _record_gate_fail_open("fnr_safe_override")
        else:
            result = self._run_rule_fallback(text, return_evidence)

        # Source-type prior: 판례/공개 문서는 비공지성 실패로 정의상 하향 등급 —
        # 모델의 상위등급 과분류를 방어. metadata.source_type/source가 공개 소스일 때만 적용.
        # settings.source_prior_enabled (config 기본 True=운영 활성) 시 발동.
        # 아래 getattr 2번째 인자(False/"S2")는 속성 자체가 없을 때만 쓰는 방어용
        # 폴백이지 운영 기본값이 아니다 — 운영 기본은 config.py 기준 활성·S3.
        #
        # cap 레벨은 settings.source_prior_cap_grade로 선택 (config 기본 "S3"):
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
                _PUBLIC_SOURCES = {  # noqa: F841 - legacy token list kept for local context
                    "court_decision", "판례", "public_disclosure", "공시", "채용공고", "보도자료",
                    "공개특허", "등록특허", "공개공보", "특허공보", "published_patent",
                }
                if _source_prior_is_public(src):
                    from lloydk.modules.m3_labeling.seeds import GRADE_ORDER as _GRADE_ORDER_LOCAL  # noqa: PLC0415
                    cap_code = (getattr(_s, "source_prior_cap_grade", "S2") or "S2").upper()
                    if cap_code not in _GRADE_ORDER_LOCAL:
                        cap_code = "S2"
                    cap_rank = _GRADE_ORDER_LOCAL[cap_code]
                    cur_rank = _GRADE_ORDER_LOCAL.get(result.label.value, 99)
                    if cur_rank < cap_rank:  # 예측이 cap보다 상위(숫자 작음)면 cap으로 하향
                        pre_cap_code = result.label.value
                        cap_warnings = [
                            f"source-prior: {src!r} is public → grade capped at {cap_code}"
                        ]
                        # [②·③ 충돌] 출처 cap(하향)이 강한 상향 신호(TS/S1)를 덮으면 자동
                        # 확정하지 않는다 — cap-conflict 경고를 남겨 classify_service가
                        # needs_review로 라우팅(진짜 공개문서는 사람 확인, 내부 비밀의 '공개'
                        # 오태깅은 silent miss로 새지 않게). 출처 cap은 상향 가드를 무인으로 안 덮음.
                        if pre_cap_code in ("TS", "S1"):
                            cap_warnings.append(
                                f"cap-conflict: public-source cap overrode high content grade "
                                f"{pre_cap_code} → routing to human review (verify provenance)"
                            )
                        result.warnings = list(result.warnings) + cap_warnings
                        # [M-renorm] cap 등급을 strict argmax로 만들고 재정규화. cap은 하향이라
                        # confidence는 그 의미(불확실성↑)를 보존해 0.7 상한을 추가로 적용.
                        new_scores, new_conf = self._enforce_label_consistency(
                            result.scores, cap_code, floor=0.6
                        )
                        result = InferenceResult(
                            label=Grade[cap_code],
                            confidence=min(new_conf, 0.7),
                            scores=new_scores,
                            factors=result.factors,
                            evidence=result.evidence,
                            rag_context=result.rag_context,
                            model_version=result.model_version,
                            warnings=result.warnings,
                            rule_grade=result.rule_grade,
                        )
        except Exception:  # noqa: BLE001
            # source-prior cap(하향)이 예외로 미적용 → 공개출처 문서가 상위등급 유지. 가시화.
            _record_gate_fail_open("source_prior_cap")

        # [metadata-floor] ICD R6 S/V/M 메타데이터 상향 게이트 (opt-in, 기본 OFF).
        # 내용 분류기는 비밀관리성(M)·출처(S)를 못 본다 — 인사·재무 비밀이 일상 내부문서(S2)로
        # 보이는 사각지대(golden500 S1→S2 미탐 7건, 룰·모델 공유)가 그 결과. KL ICD가 주는
        # 보안표시·접근범위로 그 축을 보완한다(ICD §3·§4):
        #  (1) security_marking 명시표기 우선(§4.2): 표기 등급이 예측보다 *높으면* 그 등급으로 상향
        #      floor(안전하게 높은 쪽 — 하향은 안 함, 출처 cap은 위 source_prior가 담당).
        #  (2) 표기 없고 access_scope가 제한적인데 예측이 낮으면 'metadata-access-conflict' →
        #      needs_review(§4.4 — 관리수준 높은 문서를 낮게 자동확정하지 않음).
        # 메타데이터 오류/부재는 silent 폴백(기존 동작 보존). source_prior(하향) 다음에 적용.
        try:
            from lloydk.config import settings as _ms  # noqa: PLC0415
            if getattr(_ms, "metadata_floor_enabled", False) and metadata:
                from lloydk.modules.m3_labeling.seeds import GRADE_ORDER as _ORD  # noqa: PLC0415
                cur = result.label.value if hasattr(result.label, "value") else str(result.label)
                mark = str(metadata.get("security_marking", "") or "").strip().lower()
                scope = str(metadata.get("access_scope", "") or "").strip().lower()
                _MARK = {"top_secret": "TS", "secret": "S1", "confidential": "S2"}
                if mark in _MARK and _ORD[_MARK[mark]] < _ORD.get(cur, 99):
                    floor_code = _MARK[mark]
                    new_scores, new_conf = self._enforce_label_consistency(
                        result.scores, floor_code, floor=0.7
                    )
                    result = InferenceResult(
                        label=Grade[floor_code], confidence=new_conf, scores=new_scores,
                        factors=result.factors, evidence=result.evidence,
                        rag_context=result.rag_context, model_version=result.model_version,
                        warnings=result.warnings + [
                            f"metadata-floor: security_marking={mark} → grade raised {cur}→{floor_code} (ICD §4.2 명시표기 우선)"
                        ],
                        rule_grade=result.rule_grade,
                    )
                elif mark in ("", "none") and scope in ("approved_only", "designated", "department"):
                    low_thr = "S1" if scope == "approved_only" else "S2"
                    if _ORD.get(cur, 99) > _ORD[low_thr]:
                        result.warnings = list(result.warnings) + [
                            f"metadata-access-conflict: access_scope={scope}(제한 접근)인데 예측 {cur} → 검수 라우팅 (ICD §4.4)"
                        ]
        except Exception:  # noqa: BLE001 — 메타데이터 처리 오류는 분류를 막지 않음(fail-safe)
            # metadata-floor 상향/access-conflict 라우팅이 예외로 미적용 → 비밀이 낮은 등급을
            # 무음 유지할 수 있다(shipping-ON 게이트). 분류는 계속하되 가시화(운영자 신호).
            _record_gate_fail_open("metadata_floor")

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

        # [transparency] 원시 모델 판정(override/cap/floor 이전)을 최종 결과에 부착 — 룰·모델·최종 대조용.
        result.model_grade = _model_raw_grade
        return result

    @staticmethod
    def _enforce_label_consistency(
        scores: dict[str, float], label: str, floor: float
    ) -> tuple[dict[str, float], float]:
        """[M-renorm] override/cap 후 scores·argmax·confidence 정합 강제 (fail-SECURE).

        FNR-safe override(고등급 0.7)·source-prior cap(0.6)은 한 등급 score만 max()로
        바꾸고 재정규화/argmax 재확정을 하지 않아, label은 TS인데 scores argmax는 S3가
        되는 등 불일치(미탐 은폐)가 가능했다. 여기서:

          1. label의 score를 floor 이상으로 올리되, **반드시 strict argmax**가 되도록
             다른 모든 등급보다 epsilon만큼 크게 만든다(동률도 label 우선 → 미탐 방지).
          2. scores 합=1로 재정규화.
          3. confidence = 재정규화된 scores[label] 반환.

        반환: (정규화된 scores, confidence). scores는 항상 합≈1, argmax==label.
        """
        s = {k: max(float(v), 0.0) for k, v in scores.items()}
        s.setdefault(label, 0.0)
        # label을 다른 모든 등급보다 엄격히 크게 (strict argmax 보장).
        others_max = max((v for k, v in s.items() if k != label), default=0.0)
        eps = 1e-6
        s[label] = max(s[label], floor, others_max + eps)
        total = sum(s.values())
        if total > 0:
            s = {k: v / total for k, v in s.items()}
        else:  # 모든 score 0 — label에 전량 부여 (fail-SECURE)
            s = {k: (1.0 if k == label else 0.0) for k in s}
        return s, float(s[label])

    def _rule_grade_scores_to_prob(self, grade_scores: dict) -> list[float] | None:
        """[#23] 룰 등급점수(grade_scores) → self._id2label 인덱스 정렬 확률벡터.

        모델 경로의 청크 집계 헬퍼(_aggregate_chunk_probs/_select_pred_idx)는
        '인덱스가 등급에 정렬된 prob 벡터'를 입력으로 받는다. 룰 엔진은 등급별
        원시 점수(grade_scores)를 주므로, 합으로 나눠 [0,1] 확률분포로 변환하고
        _id2label 인덱스 순서에 맞춰 벡터화한다(softmax 출력과 동일한 형태).
        모든 점수 0이면 None(해당 청크는 신호 없음 → 집계에서 0벡터로 처리).
        """
        total = float(sum(v for v in grade_scores.values() if v and v > 0))
        if total <= 0:
            return None
        vec: list[float] = []
        for i in range(len(self._id2label)):
            code = self._code_at(i)
            vec.append(max(float(grade_scores.get(code, 0.0)), 0.0) / total)
        return vec

    def _run_rule_fallback(self, text: str, return_evidence: bool) -> InferenceResult:
        try:
            from lloydk.api.prom_metrics import RULE_FALLBACK_TOTAL  # noqa: PLC0415
            RULE_FALLBACK_TOTAL.inc()
        except Exception:  # noqa: BLE001
            pass
        # evidence/factors는 전체 문서 기준 룰 라벨링에서 가져온다(표시 정합 보존).
        lab = self.labeling.label(text)
        warnings = ["model weights not loaded — using rule-based fallback"]

        # [sparse-evidence gate] rule confidence(top/total)는 단일·저가중 키워드 1개만 매칭돼도
        # 한 등급에 전 질량이 몰리면 1.0이 된다. 즉 '단 하나의 약한 매치'가 conf=1.0으로 자동확정돼
        # 저신뢰 게이트(conf<0.7)를 통과하는 silent FNR이 생긴다(골든셋 TS 5건: TS신호 0인데
        # S1/S2로 conf=1.0 확정). 절대 룰 점수(ev)가 settings.rule_fallback_min_evidence 미만이면
        # 경고를 남겨 classify_service가 confidence와 무관하게 needs_review로 라우팅한다.
        # ev==0(무신호)은 conf=0으로 이미 저신뢰 게이트가 잡으므로 0<ev<floor 구간만 표기한다.
        try:
            _min_ev = float(settings.rule_fallback_min_evidence)
        except Exception:  # noqa: BLE001
            _min_ev = 0.0
        if _min_ev > 0:
            _ev_total = sum((lab.rule_result.grade_scores if lab.rule_result else {}).values())
            if 0.0 < _ev_total < _min_ev:
                warnings.append(
                    f"sparse-evidence: rule total score {_ev_total:.2f} < {_min_ev:.2f} "
                    "— thin single-match basis, routed to human review (FNR-safe)"
                )

        # [#23] rule-fallback도 모델 경로와 동일하게 청크 most-severe-wins + escalation τ 적용.
        # 기존엔 전체 문서를 1회 라벨링해 grade_scores를 단순 합 정규화 → lab.grade를 그대로
        # 라벨로 써서 청크 most-severe(_aggregate_chunk_probs)·τ(_select_pred_idx)를 우회했다.
        # 모델 미로드 환경(다수 PoC)에서 한 청크에 든 핵심 비밀이 다수의 평범한 청크에 희석돼
        # 미탐(FNR) 위험이 모델 경로보다 컸다. → 문서를 청크 분할, 청크별 룰 점수를 등급
        # 확률분포로 변환해 모델 경로의 동일 헬퍼에 태운다(FNR-safe: 미탐↓, 단조 비후퇴).
        # 실패(예: torch 부재)는 silent 폴백 — 기존 단일패스 동작을 그대로 보존한다.
        pred = None
        scores: dict[str, float] | None = None
        conf: float | None = None
        try:
            import torch  # noqa: PLC0415

            chunks = chunk_text(text, settings.max_seq_len * 3, settings.chunk_overlap)
            chunk_texts = [c.text for c in chunks] or [text]
            chunk_weights = [max(len(t.strip()), 1) for t in chunk_texts]

            n_labels = len(self._id2label)
            chunk_vecs: list[list[float]] = []
            for ct in chunk_texts:
                gs = self.labeling.engine.label(ct).grade_scores
                vec = self._rule_grade_scores_to_prob(gs)
                chunk_vecs.append(vec if vec is not None else [0.0] * n_labels)

            chunk_probs = torch.tensor(chunk_vecs, dtype=torch.float32)
            # 모델 경로와 동일 집계: 고등급(severe_agg_codes 기본 TS·S1) max-pooling.
            doc_prob = self._aggregate_chunk_probs(chunk_probs, chunk_weights)
            total = float(doc_prob.sum())
            norm = (doc_prob / total) if total > 0 else doc_prob
            # 모든 청크가 무신호(합=0)면 룰 신호가 없는 것 → 단일패스 폴백으로.
            if float(norm.sum()) > 0:
                # [C-esc] τ 설정 시 동일 severity-ordered 규칙으로 라벨 선택, τ=None이면 argmax.
                pred_idx = self._select_pred_idx(norm)
                pred = self._id2label[pred_idx]
                scores = {self._code_at(i): round(float(norm[i]), 4) for i in range(n_labels)}
                conf = min(max(float(norm[pred_idx]), 0.0), 1.0)
        except Exception:  # noqa: BLE001 — 청크 집계 실패는 단일패스 폴백으로(동작 보존)
            pred = None
            scores = None
            conf = None

        if pred is None or scores is None or conf is None:
            # 단일패스 폴백 — grade_scores를 합계로 나눠 [0,1] 확률로 변환 (기존 동작 보존).
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
                warnings=warnings,
                rule_grade=getattr(lab.rule_result, "grade", None),
            )

        # [#23] 청크 집계로 선택한 등급이 단일패스(lab.grade)보다 높으면(승격) 경고를 남겨
        # 청크 희석 미탐을 방어했음을 표기. 표시 S/V/M factors도 선택 등급에 정합화해
        # 'S0·V0·M0인데 TS' 같은 모순 표기를 막는다(모델 경로 A3와 동일 의도).
        lab_code = lab.grade.value if hasattr(lab.grade, "value") else str(lab.grade)
        pred_code = pred.value if hasattr(pred, "value") else str(pred)
        factors = lab.factors
        if pred_code != lab_code:
            warnings = warnings + [
                f"chunk severe-agg/escalation: rule grade {lab_code} → {pred_code} "
                "(most-severe-wins over chunks; FNR-safe)"
            ]
            # [FIX-E] 약어-only 승격 백스톱 — 청크 집계가 고등급으로 승격했는데 그 등급의
            # 근거가 _HIGH_RISK_PATTERNS 영문 약어 부스트(CVD·N2O·EUV 등)뿐이고 한국어
            # 시드 근거가 전무하면(예: 공개특허 본문의 범용 공정약어) 자동확정을 막고 검수
            # 라우팅한다. 등급은 절대 내리지 않음(하향 없음·라벨 유지) → FNR-safe. 한국어
            # 시드(관리표시/공정레시피 등)가 하나라도 있으면 태그를 붙이지 않아 진짜 기밀의
            # 자동확정을 그대로 보존한다. 전용 태그라 cap-conflict 메트릭/의미와 분리된다.
            if pred_code in self._SEVERE_AGG_CODES and self._promotion_is_abbrev_only(
                lab, pred_code
            ):
                warnings = warnings + [
                    f"abbrev-only-escalation: {pred_code} 승격이 영문 약어 부스트에만 근거"
                    " (한국어 시드 근거 없음) — 자동확정 보류·검수 라우팅 (등급 무변경, FNR-safe)"
                ]
            if factors is not None and pred_code in ("TS", "S1", "S2", "S3"):
                try:
                    from lloydk.modules.m3_labeling.rule_engine import (  # noqa: PLC0415
                        svm_levels_for_grade,
                    )
                    s2, v2, m2 = svm_levels_for_grade(pred_code)
                    factors = EvaluationFactors.from_factor_scores(
                        {"SECRECY": float(s2), "VALUE": float(v2), "MANAGEMENT": float(m2)}
                    )
                except Exception:  # noqa: BLE001
                    factors = lab.factors

        return InferenceResult(
            label=pred,
            confidence=conf,
            scores=scores,
            factors=factors,
            evidence=lab.evidence if return_evidence else [],
            model_version="rule-fallback-v0",
            warnings=warnings,
            rule_grade=getattr(lab.rule_result, "grade", None),
        )

    @staticmethod
    def _promotion_is_abbrev_only(lab, promoted_code: str) -> bool:
        """[FIX-E] 승격 등급이 _HIGH_RISK_PATTERNS 약어 부스트에만 근거하는지(한국어 시드 無).

        규약(rule_engine): 시드 매칭은 MatchedKeyword.start is None, 약어 부스트는
        start=mo.start()가 채워진다(has_real_evidence와 동일 판별자). 승격 등급 코드의
        매치 중 start is None(=시드)이 하나라도 있으면 '실 근거 있음' → False(자동확정 보존).
        전부 start 있음(=약어 부스트)이면 True(검수 라우팅 대상). 매치가 없거나 rule_result
        미가용이면 보수적으로 False(승격 자동확정 유지 — 백스톱이 오히려 FNR을 만들지 않게).
        """
        rr = getattr(lab, "rule_result", None)
        matches = getattr(rr, "matched_keywords", None) if rr is not None else None
        if not matches:
            return False
        promoted = [m for m in matches if getattr(m, "grade", None) == promoted_code]
        if not promoted:
            # 문서 전체엔 승격등급 시드가 없음(청크에서만 떴을 수 있음). 이 경우도 약어-only로
            # 간주해 검수 라우팅(등급은 그대로) — 문서수준 시드 근거가 없으니 자동확정 보류가 안전.
            return True
        return all(getattr(m, "start", None) is not None for m in promoted)

    # 청크 어그리게이션에서 most-severe-wins를 적용할 고등급 코드.
    # 이 등급들의 문서확률은 청크 평균이 아니라 max(가장 강한 청크)로 잡아,
    # 수식 한 문단 같은 핵심 비밀이 다수의 평범한 청크에 희석되어 미탐되는 것을 막는다.
    # settings.severe_agg_codes로 조정 가능(기본 TS·S1 — S2·S3는 표준 집계).
    @property
    def _SEVERE_AGG_CODES(self) -> tuple:
        try:
            from lloydk.config import settings  # noqa: PLC0415
            codes = getattr(settings, "severe_agg_codes", None)
            return tuple(codes) if codes else ("TS", "S1")
        except Exception:  # noqa: BLE001
            return ("TS", "S1")

    def _aggregate_chunk_probs(self, chunk_probs, chunk_weights):
        """청크별 softmax → 문서 확률 벡터 (most-severe-wins, fail-SECURE).

        [M-agg] 단순 비가중 평균은 핵심 비밀이 담긴 한 청크(예: 수식 한 문단)를
        다수의 평범한 청크에 희석시켜 미탐을 만든다. 그래서:

          - 저등급(S2/S3 등): 길이 가중 평균 — 일반 동작 보존.
          - 고등급(TS/S1): 가장 강한 청크의 확률을 반영(max) — 한 청크의 강한
            비밀 신호가 평균에 묻히지 않게. argmax는 이 보강된 벡터에서 재확정.

        반환 벡터는 비정규(고등급 max 보강으로 합>1 가능)일 수 있다 — 이건 의도적이다.
        호출부는 이 벡터로 argmax/confidence를 잡으며, confidence는 [0,1]로 clamp한다.
        """
        import torch

        if chunk_probs.ndim == 1:
            chunk_probs = chunk_probs.unsqueeze(0)
        n_chunks = chunk_probs.shape[0]

        # 길이 가중 평균 (가중치 부재/0합이면 균등 평균으로 폴백).
        w = None
        if chunk_weights and len(chunk_weights) == n_chunks:
            w = torch.tensor(chunk_weights, dtype=chunk_probs.dtype)
            if float(w.sum()) <= 0:
                w = None
        if w is not None:
            weighted = (chunk_probs * w.unsqueeze(1)).sum(dim=0) / w.sum()
        else:
            weighted = chunk_probs.mean(dim=0)

        doc_prob = weighted.clone()
        # 고등급: max 청크 확률로 보강 (most-severe-wins). 단조 비감소 — 평균보다
        # 낮아지지 않으므로 미탐 방향으로 후퇴할 수 없다.
        # [B3 검토 후 보류] 짧은 노이즈 청크 과분류를 막으려 최소길이 필터를 시도했으나,
        # 짧은 *비밀* 청크(예: "마스터 키: …")도 함께 배제돼 미탐(FNR)을 유발 → 영업비밀
        # 시스템은 과분류가 안전 방향이므로 필터 미적용. 전체 청크 severe-max 유지(FNR 우선).
        chunk_max = chunk_probs.max(dim=0).values
        severe_idx = {
            i for i, g in self._id2label.items()
            if (g.value if hasattr(g, "value") else str(g)) in self._SEVERE_AGG_CODES
        }
        for i in severe_idx:
            doc_prob[i] = torch.maximum(doc_prob[i], chunk_max[i])
        return doc_prob

    def _encode_windows(self, batch: list[str]):
        """배치를 토큰 윈도우로 인코딩 → (BatchEncoding, sample_mapping).

        [#chunk-trunc] char-청크(≈max_seq_len*3 자)는 max_seq_len(512) 토큰을 넘길 수 있고,
        truncation=True 만 쓰면 초과분이 '조용히' 잘려(char overlap 로도 못 메우는 gap 밴드 발생)
        그 구간의 **모델-only 의미 비밀**이 미탐된다(FNR). fast 토크나이저의
        return_overflowing_tokens 로 초과 청크를 여러 윈도우(각 ≤max_seq_len, stride=chunk_overlap
        토큰 겹침)로 **무손실** 분할한다. sample_mapping[i]=i번째 윈도우의 원 배치-청크 인덱스
        (길이 가중치 승계용). slow 토크나이저/미지원/실패 시 기존 truncation(청크당 1윈도우)으로
        폴백해 동작을 보존한다(무손실은 아니나 회귀 없음 — best-effort 상향).
        """
        if getattr(self._tokenizer, "is_fast", False):
            try:
                stride = min(int(getattr(settings, "chunk_overlap", 64)), settings.max_seq_len // 4)
                enc = self._tokenizer(
                    batch, truncation=True, max_length=settings.max_seq_len,
                    stride=max(0, stride), return_overflowing_tokens=True,
                    padding=True, return_tensors="pt",
                )
                sample_map = enc.pop("overflow_to_sample_mapping").tolist()
                return enc, sample_map
            except Exception:  # noqa: BLE001 — 오버플로 미지원/실패 → truncation 폴백(동작 보존)
                pass
        enc = self._tokenizer(
            batch, truncation=True, max_length=settings.max_seq_len,
            padding=True, return_tensors="pt",
        )
        return enc, list(range(len(batch)))

    def _run_model(self, text: str, use_rag: bool, return_evidence: bool) -> InferenceResult:
        import torch
        import torch.nn.functional as F

        chunks = chunk_text(text, settings.max_seq_len * 3, settings.chunk_overlap)
        chunk_texts = [c.text for c in chunks] or [text]
        # 길이 가중치: 짧은 노이즈 청크가 평균을 흔들지 않도록 char 수로 가중.
        # 0 길이/공백 청크는 1로 floor (가중치 0이 되어 사라지지 않게).
        chunk_weights = [max(len(t.strip()), 1) for t in chunk_texts]

        probs = []
        # win_weights: 오버플로 윈도잉으로 한 청크가 N윈도우로 쪼개지면 각 윈도우가 원 청크의
        # 길이 가중치를 승계 → chunk_probs 행수와 정합. (severe max-pool은 가중 무관 = FNR 핵심 보존)
        win_weights: list[int] = []
        # span은 torch.no_grad() **안쪽**에 둔다 — 전체 forward가 grad 비활성 유지(필수).
        # 속성은 스칼라·비민감(model_version·청크수·device)만. OTel 미활성 시 no-op.
        with torch.no_grad():
            with span(
                "ml.classifier.forward",
                model_version=str(self.model_dir.name) if self.model_dir else "model",
                n_chunks=len(chunk_texts),
                device=str(self._device),
            ):
                for batch_start in range(0, len(chunk_texts), 8):
                    batch = chunk_texts[batch_start:batch_start + 8]
                    enc, sample_map = self._encode_windows(batch)
                    enc = enc.to(self._device)
                    logits = self._model(**enc).logits
                    # 서빙 캘리브레이션: T>1이면 분포를 부드럽게 해 OOD 과신 완화. T=1.0=무보정.
                    temp = self._temperature
                    if temp != 1.0:
                        logits = logits / temp
                    probs.append(F.softmax(logits, dim=-1).cpu())
                    win_weights.extend(chunk_weights[batch_start + i] for i in sample_map)
        chunk_probs = torch.cat(probs)  # [n_windows, n_labels]
        doc_prob = self._aggregate_chunk_probs(chunk_probs, win_weights)
        # 표시용 scores는 합=1로 재정규화 (모든 인덱스를 같은 상수로 나눠 순서 보존).
        total = float(doc_prob.sum())
        norm = (doc_prob / total) if total > 0 else doc_prob
        # [C-esc] 예측 인덱스: escalation τ(가장 심각한 등급 중 prob≥τ) 또는 argmax(τ=None=동작보존).
        # most-severe-wins 집계가 반영된 norm에서 확정. τ는 평가 스윕과 동일 규칙(서빙↔평가 정합).
        pred_idx = self._select_pred_idx(norm)
        pred = self._id2label[pred_idx]
        scores = {g.value: float(norm[i]) for i, g in self._id2label.items()}
        conf = min(max(float(norm[pred_idx]), 0.0), 1.0)

        lab = self.labeling.label(text)  # 보조 evidence/factors
        # use_rag은 run() 레벨에서 통합 처리 — 본 함수 시그니처는 호환 위해 유지.
        del use_rag
        # [A3] 모델 등급 ↔ 룰 factors 정합. 룰이 미탐(곱=0/낮음)인데 모델이 고등급이면
        # 표시 S/V/M을 모델 등급에 정합화(+경고) — 'S0·V0·M0인데 TS' 모순 표기 방지.
        factors = lab.factors
        a3_warn: list[str] = []
        pred_code = pred.value if hasattr(pred, "value") else str(pred)
        if factors is not None and pred_code in ("TS", "S1", "S2", "S3"):
            try:
                from lloydk.modules.m3_labeling.rule_engine import grade_from_svm, svm_levels_for_grade  # noqa: PLC0415
                fsv = int(float(getattr(factors, "secrecy", 0)))
                fvv = int(float(getattr(factors, "value", 0)))
                fmv = int(float(getattr(factors, "management", 0)))
                if grade_from_svm(fsv, fvv, fmv) != pred_code:
                    s2, v2, m2 = svm_levels_for_grade(pred_code)
                    factors = EvaluationFactors.from_factor_scores(
                        {"SECRECY": float(s2), "VALUE": float(v2), "MANAGEMENT": float(m2)}
                    )
                    a3_warn = [f"factors aligned to model grade {pred_code} (rule under-detected S/V/M)"]
            except Exception:  # noqa: BLE001
                factors = lab.factors
        return InferenceResult(
            label=pred,
            confidence=conf,
            scores=scores,
            factors=factors,
            evidence=lab.evidence if return_evidence else [],
            model_version=str(self.model_dir.name) if self.model_dir else "model",
            warnings=a3_warn,
            rule_grade=getattr(lab.rule_result, "grade", None),
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

        # tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진이라 per-customer 경계 없음).
        # 무스코핑 전역 검색 — filter 없음.
        filter_ = None

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
