"""S/V/M 요소 모델(v8) 서빙 — **섀도 실행 전용**으로 먼저 들인다.

왜 배포 패키지 안으로 옮기는가. 지금까지 v8 은 `poc/scripts/` 에만 있어 배포본에서
쓸 수 없었다. 같은 입력에 v5(운영)와 v8(실험)을 나란히 태우려면 여기 있어야 한다.

왜 섀도부터인가. 배포본은 **등급 우선·요소 후행** 구조다 — 모델이 등급을 내고 S/V/M 을
거기 맞춘다(`svm_levels_for_grade`). v8 은 반대로 **요소 우선**이라 등급이 요소에서
나온다. 두 구조를 바로 합치면 결정이 바뀌는데, 그 전에 **두 모델이 얼마나 다른지**를
알아야 한다. 모르고 거부 조건을 걸면 검수량이 얼마나 늘지 예측할 수 없다.

그래서 이 모듈은 등급을 **바꾸지 않는다.** 예측을 내고 불일치를 계량할 뿐이다.

서빙 게이트 3층도 여기 둔다. 재구현하면 연구 경로와 배포 경로의 기준이 갈리기 때문이다
— 출처 prior 때 같은 이유로 배포본 함수를 그대로 썼다.

    1층 출처   공개 출처면 S3. 비공지성은 문서 밖의 사실이다
    2층 단언   하향 단언(absent·lv1)은 p >= kappa 일 때만. 미달이면 unknown
    3층 확정   서빙 등급이 TS 면 확정(미탐 정의상 불가능) · 아니면 세 요소 확정 + conf >= tau

주의. 2층이 lv1 까지 막는 이유는 정본이 TS/S1 에 `s==2 and v==2` 를 요구하기 때문이다.
lv1 단언도 고등급을 깬다(`s=1 v=2 m=2` -> 곱 4 이지만 S2 상한). absent 만 지키면 이
경로가 열린다(실측 2026-08-14: 비밀놓침 3 -> 23).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

FACTORS = ("secrecy", "value", "management")
CLS_ABSENT, CLS_LV1, CLS_LV2, CLS_UNKNOWN = 0, 1, 2, 3
CLS_NAMES = {CLS_ABSENT: "proven_absent", CLS_LV1: "lv1", CLS_LV2: "lv2", CLS_UNKNOWN: "unknown"}
# 하향 단언 — 고등급을 깨는 클래스. 이 둘만 문턱을 요구한다.
DOWNGRADE = (CLS_ABSENT, CLS_LV1)


def cls_to_worst(c: int) -> int:
    """보수적 완성 — 확인되지 않은 요소를 최악값으로 채운다."""
    if c == CLS_ABSENT:
        return 0
    return 2 if c == CLS_UNKNOWN else int(c)


@dataclass
class FactorPrediction:
    """요소 예측 한 건. 등급은 여기서 나오지만 배포 결정에는 안 쓴다(섀도)."""

    codes: tuple
    probs: list
    serving_grade: str
    auto_confirmable: bool
    reasons: list = field(default_factory=list)

    @property
    def named(self) -> dict:
        return {f: CLS_NAMES[c] for f, c in zip(FACTORS, self.codes)}

    @property
    def min_confidence(self) -> float:
        return min(max(p) for p in self.probs) if self.probs else 0.0


def _build_model(base: str, torch, n_classes: int = 4):
    """공유 백본 + 요소별 헤드 3개. 학습 스크립트와 같은 구조여야 가중치가 맞는다."""
    from transformers import AutoModel

    class _FactorModel(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.backbone = AutoModel.from_pretrained(base)
            hidden = self.backbone.config.hidden_size
            self.dropout = torch.nn.Dropout(0.1)
            self.head_secrecy = torch.nn.Linear(hidden, n_classes)
            self.head_value = torch.nn.Linear(hidden, n_classes)
            self.head_management = torch.nn.Linear(hidden, n_classes)

        def forward(self, input_ids, attention_mask):
            out = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
            cls = self.dropout(out.last_hidden_state[:, 0])
            return (self.head_secrecy(cls), self.head_value(cls), self.head_management(cls))

    return _FactorModel()


class FactorInference:
    """요소 모델 추론기. 로드 실패는 조용히 비활성(분류를 막지 않는다)."""

    @staticmethod
    def _hub_kwargs() -> dict:
        """허브에서 받는 로드에만 붙는 인자. 로컬 디렉터리 로드에는 의미가 없다.

        revision 은 설정으로 받는다 — 값을 지어내지 않는다. 폐쇄망은 HF_HUB_OFFLINE=1 로
        허브 접근 자체가 막히지만, 클라우드 배포에는 그 변수가 없어 허브가 열려 있다.
        """
        from koipa.config import settings  # noqa: PLC0415

        rev = (getattr(settings, "hf_model_revision", "") or "").strip()
        return {"revision": rev} if rev else {}

    def __init__(self, model_dir, base: str = "kakaobank/kf-deberta-base",
                 max_len: int = 768) -> None:
        self.model_dir = Path(model_dir) if model_dir else None
        self.base = base
        self.max_len = max_len
        self._model = None
        self._tokenizer = None
        self._device = "cpu"
        self._temperature = None
        self.load_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        """가중치를 싣는다. 실패하면 False 를 돌려주고 이유를 남긴다."""
        if self._model is not None:
            return True
        if not self.model_dir or not (self.model_dir / "model.pt").exists():
            self.load_error = "factor model weights not found: " + str(self.model_dir)
            return False
        try:
            import torch
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(self.base, **self._hub_kwargs())
            model = _build_model(self.base, torch, 4)
            # weights_only=True: model.pt 는 pickle 이라 임의 코드 실행 경로가 열린다.
            # state_dict 만 필요하므로 텐서 외 객체는 아예 역직렬화하지 않는다.
            model.load_state_dict(
                torch.load(self.model_dir / "model.pt", map_location="cpu", weights_only=True)
            )
            model.eval()
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            model.to(self._device)
            self._model = model
            # 요소별 온도가 있으면 싣는다. 없으면 무보정이고 그 사실이 지표에 남는다.
            tpath = self.model_dir / "temperature.json"
            if tpath.exists():
                raw = json.loads(tpath.read_text("utf-8"))
                vals = raw.get("temperature") if isinstance(raw, dict) else None
                if isinstance(vals, dict):
                    self._temperature = [float(vals.get(f, 1.0)) for f in FACTORS]
                elif isinstance(vals, list) and len(vals) == 3:
                    self._temperature = [float(v) for v in vals]
            return True
        except Exception as exc:  # noqa: BLE001 — 요소 모델 부재가 분류를 막지 않는다
            self.load_error = type(exc).__name__ + ": " + str(exc)
            self._model = None
            logger.warning("factor model load failed: %s", self.load_error)
            return False

    def predict(self, text: str):
        """(codes, probs) 를 돌려준다. 실패하면 None — 호출부는 섀도를 건너뛴다."""
        if not self.load():
            return None
        try:
            import torch

            enc = self._tokenizer(text or "", truncation=True, max_length=self.max_len,
                                  padding=True, return_tensors="pt")
            enc = {k: v.to(self._device) for k, v in enc.items()}
            with torch.no_grad():
                logits = self._model(enc["input_ids"], enc["attention_mask"])
            codes = []
            probs = []
            for k, lg in enumerate(logits):
                z = lg[0]
                if self._temperature:
                    z = z / max(1e-6, self._temperature[k])
                p = [float(x) for x in torch.softmax(z, dim=-1).tolist()]
                probs.append(p)
                codes.append(int(max(range(len(p)), key=lambda i: p[i])))
            return (codes[0], codes[1], codes[2]), probs
        except Exception as exc:  # noqa: BLE001
            logger.warning("factor inference failed: %s", exc)
            return None


def apply_serving_gate(codes, probs, *, metadata=None, tau: float = 0.99,
                       kappa: float = 0.99, source_is_public: bool = False):
    """서빙 게이트 3층. 등급과 자동확정 가능 여부를 낸다."""
    from koipa.modules.m3_labeling.rule_engine import (
        grade_from_svm,
        management_from_metadata_dict,
    )

    if source_is_public:
        # 1층 — 비공지성은 문서 밖의 사실이고 공개 여부의 증거는 출처다.
        return FactorPrediction(codes=tuple(codes), probs=probs, serving_grade="S3",
                                auto_confirmable=True, reasons=["layer1:source_public"])

    reasons = []
    c3 = list(codes)
    for k in range(3):
        # 2층 — 하향 단언에만 문턱. unknown 유보는 무료다(보수적 완성이 받는다).
        if c3[k] in DOWNGRADE and probs[k][c3[k]] < kappa:
            c3[k] = CLS_UNKNOWN
            reasons.append("layer2:" + FACTORS[k] + "_downgrade_below_kappa")

    # 2.5층 — 관리성은 본문에서 관측되지 않는다. 시스템이 주면 그것이 근거다.
    m_state, m_lv, m_reason = management_from_metadata_dict(metadata)
    if m_state == "present":
        c3[2] = int(m_lv or 0)
        reasons.append("layer2.5:" + m_reason)
    elif m_state == "proven_absent":
        c3[2] = CLS_ABSENT
        reasons.append("layer2.5:" + m_reason)

    grade = grade_from_svm(*[cls_to_worst(c) for c in c3])
    settled = all(c != CLS_UNKNOWN for c in c3)
    conf_ok = (min(max(p) for p in probs) >= tau) if probs else False
    if grade == "TS":
        # 3층 — 미탐은 낮게 본 오류이고 TS 는 최상단이라 정의상 미탐이 될 수 없다.
        auto, why = True, "layer3:top_grade"
    elif settled and conf_ok:
        auto, why = True, "layer3:settled_and_confident"
    else:
        auto, why = False, "layer3:needs_review"
    reasons.append(why)
    return FactorPrediction(codes=tuple(c3), probs=probs, serving_grade=grade,
                            auto_confirmable=auto, reasons=reasons)


_SINGLETON = {}


def get_factor_inference(model_dir: str, base: str = "kakaobank/kf-deberta-base",
                         max_len: int = 768) -> FactorInference:
    """프로세스당 한 번만 싣는다."""
    key = str(model_dir) + "|" + base + "|" + str(max_len)
    inf = _SINGLETON.get(key)
    if inf is None:
        inf = FactorInference(model_dir, base=base, max_len=max_len)
        _SINGLETON[key] = inf
    return inf


def shadow_compare(deployed_grade: str, pred: FactorPrediction) -> dict:
    """v5 등급과 v8 서빙 등급을 대조한다. 결정은 안 바꾼다.

    보는 것은 방향이다 — v8 이 더 높게 보면 v5 가 놓쳤을 수 있다는 신호이고, 더 낮게
    보면 v5 가 과분류했다는 신호다. 미탐 최소화가 1차 목표이므로 전자가 중요하다.
    """
    order = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
    a, b = order.get(deployed_grade, 99), order.get(pred.serving_grade, 99)
    direction = "agree" if a == b else ("factor_higher" if b < a else "factor_lower")
    return {
        "deployed_grade": deployed_grade,
        "factor_grade": pred.serving_grade,
        "direction": direction,
        "factors": pred.named,
        "min_confidence": round(pred.min_confidence, 4),
        "auto_confirmable": pred.auto_confirmable,
        "reasons": pred.reasons,
    }
