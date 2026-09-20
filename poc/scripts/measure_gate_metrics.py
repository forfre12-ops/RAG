#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""배포 분류기의 **conf 신뢰성**과 **자동확정 운영점**을 재현 가능하게 잰다.

왜 이 도구가 있는가(2026-09-08). 게이트 백서가 내건 네 숫자
(conf AUROC 0.58 · ECE 0.184 · 자동확정 정답률 63.4% · 검수대상 정답률 56.2%)는
`scripts/archive/gen_gate_whitepaper.py` 의 **하드코딩 상수**로만 남아 있다.
그 값을 만든 측정 스크립트·로그·json 은 리포에 없다(`scripts/audit_gate_numbers.py`
가 그 사실을 센다). 즉 지금까지 이 숫자들은 **다시 돌려볼 수 없었다.**
이 스크립트가 그 자리를 채운다 — 한 명령으로 다시 나온다.

무엇을 재는가 (배포본 artifacts/classifier_p1_v5_clean/v-fe4b386b 기준):
    · 정확도
    · conf AUROC — conf 가 정답/오답을 가르는가 (0.5=무작위)
    · ECE — 보정오차. 쓰인 temperature 를 함께 적는다(T=1.0 · 배포 T · 재적합 T)
    · 자동확정 정밀도·커버리지 — **현행 운영점**(임계 = onprem-local 프로파일의
      review_confidence_threshold, agreement_gate_enabled = 그 프로파일 값)
    · 같은 것을 **폐기된 0.70 임계**에서도 — 이미 제출된 문서와의 연속성 때문에 남긴다

측정 방법은 `reports/calibration_audit_2026-08-25/calib_check.py` 와 같다
(단일 pass raw logits → softmax(T)). 그래야 그 리포트의 수치와 비교된다.
자동확정 판정의 합의 게이트는 **실제 서빙 코드**(ClassifyService._agreement_gate)를
그대로 호출한다 — 시뮬레이션이 아니다.

⚠ 이 도구가 재는 것과 재지 않는 것 (경계를 먼저 긋는다)
    잰다   : 원시 모델 출력 + conf 임계 + 합의 게이트
    안 잰다: 서빙 파이프라인의 나머지 라우팅(escalation tau=0.30 · source_prior cap ·
             metadata_floor · sparse-evidence · s2-underclass-risk · kill-gate).
             그것들은 전부 **needs_review 를 더 만드는 방향**이라, 여기 나오는
             자동확정 커버리지는 실서빙의 **상한**이다. 정밀도는 상·하한 어느 쪽도
             아니다(라우팅이 어떤 건을 빼느냐에 달렸다).

⚠ 판정면(evaluation surface)마다 **누설 여부를 함께 적는다.** 누설된 면에서 나온
  수치는 모델이 아니라 지름길을 잰 것이다. 누설은 기억이 아니라 이 스크립트가
  그 자리에서 **측정**한다(정답 본문 노출 · 등급코드 언급 · 길이 단독 분리).

✔ 방법 동등성 확인(2026-09-08). 같은 셋에서 calib_check.py 의 값과 **자릿수까지 일치**한다:
    hardened42  정확도 0.905 · ECE(T=2.03) 0.0498 · AUROC 0.750
    holdout109  정확도 0.688 · ECE(T=2.03) 0.1825 · AUROC 0.578
    gold777     정확도 0.875 · ECE(T=2.03) 0.0475 · AUROC 0.552
  즉 토크나이저를 직접 여는 아래 우회(⚠ _get_model 주석)로 값이 달라지지 않았다.

사용:
    PYTHONIOENCODING=utf-8 TESTING=1 ./.venv/Scripts/python.exe scripts/measure_gate_metrics.py
    (f:/antigravity/rag/poc 에서 실행)

    ⏱ 이 PC 기준 첫 실행 약 1시간 20분. 대부분이 **추론이 아니라 임포트**다 — 실측
      scipy.stats 140초 · sklearn 122초 · `from transformers import Auto...` 678초 ·
      모델 로드 92초 (2026-09-08, F: 드라이브). 추론은 약 2.8건/초.
      logits 는 reports/<out>/_logits_cache/ 에 저장되므로 **두 번째 실행부터는 추론이 없다.**

    --out DIR       결과 저장 위치 (기본 reports/gate_metrics_2026-09-08)
    --model DIR     모델 디렉터리 (기본 배포본 v-fe4b386b)
    --surface KEY   특정 면만 (여러 번 지정 가능)
    --no-cache      logits 캐시를 쓰지 않고 다시 추론
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
for _p in (str(_HERE), str(_POC / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 서비스/룰엔진을 오프라인으로 띄우기 위한 환경(측정 전용 — 서빙 설정을 바꾸지 않는다).
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("VECTOR_BACKEND", "inmemory")
os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
# 로컬 가중치만 쓴다 — 허브 조회를 시도하면 폐쇄망/오프라인에서 수 분씩 멈춘다.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

GRADES = ("TS", "S1", "S2", "S3")
# level_order asc 가 아니라 '심각도' — 과소분류(미탐) 방향 판정용.
_SEV = {"TS": 3, "S1": 2, "S2": 1, "S3": 0}

DEFAULT_MODEL = "artifacts/classifier_p1_v5_clean/v-fe4b386b"
DEFAULT_OUT = "reports/gate_metrics_2026-09-08"
OBSOLETE_THRESHOLD = 0.70  # 폐기됨(2026-08-24 → 0.50). 제출본 연속성 때문에만 계산한다.

_ANSWER_LEAK = re.compile(r"##\s*등급\s*제안\s*사유\s*:\s*(TS|S1|S2|S3)")
_GRADE_IN_ID = re.compile(r"-(TS|S1|S2|S3)-")

REPRO_CMD = (
    "PYTHONIOENCODING=utf-8 TESTING=1 ./.venv/Scripts/python.exe "
    "scripts/measure_gate_metrics.py"
)


# ----------------------------------------------------------------------------
# 판정면 로더
# ----------------------------------------------------------------------------
def _load_jsonl(rel: str, text_key: str = "text", label_key: str = "label") -> list[dict]:
    p = _POC / rel
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        t, lab = (r.get(text_key) or "").strip(), r.get(label_key)
        if t and lab in GRADES:
            rows.append({"doc_id": str(r.get("doc_id") or ""), "text": t, "label": lab})
    return rows


def _load_clean_candidates() -> list[dict]:
    """정리된 골든 후보 — `eval_on_clean_candidates.load_candidates()` 를 그대로 쓴다.

    같은 로더를 써야 그 스크립트가 낸 일치율과 같은 모집단을 잰 것이 된다.
    """
    from eval_on_clean_candidates import load_candidates  # noqa: PLC0415

    return load_candidates()


# key: (표시명, 로더, 파일경로표기, 알려진 누설 기록)
SURFACES: dict[str, dict] = {
    "clean_candidates": {
        "name": "정리된 골든 후보 (답 노출 제거본)",
        "path": "datasets/proxy_gold/single_document_candidates/*.metadata.json",
        "loader": _load_clean_candidates,
        "known_leak": (
            "2026-09-08 c7b73e4c 로 본문 정답('## 등급 제안 사유: {등급}') 제거. "
            "제거 전에는 988건 중 964건에 정답이 적혀 있었다. 제거 후 현재까지 "
            "알려진 누설 없음 — 다만 정답은 '생성 시 의도 등급'이고 사람 서명은 0건이다."
        ),
    },
    "hardened42": {
        "name": "경화42 (사람라벨·LLM 0건)",
        "path": "datasets/gold_real/holdout_eval.hardened.jsonl",
        "loader": lambda: _load_jsonl("datasets/gold_real/holdout_eval.hardened.jsonl"),
        "known_leak": "알려진 누설 있음 — S3 가 길이 단독으로 100% 분리(hardened42-s3-length-separable, 2026-08-12).",
    },
    "clean42": {
        "name": "clean42 (holdout_eval.clean)",
        "path": "datasets/gold_real/holdout_eval.clean.jsonl",
        "loader": lambda: _load_jsonl("datasets/gold_real/holdout_eval.clean.jsonl"),
        "known_leak": "알려진 누설 있음 — 길이가 등급을 알려주는 4개 홀드아웃 중 하나(2026-09-05).",
    },
    "holdout109": {
        "name": "holdout109 (LLM 라벨)",
        "path": "datasets/gold_real/holdout_eval.jsonl",
        "loader": lambda: _load_jsonl("datasets/gold_real/holdout_eval.jsonl"),
        "known_leak": "알려진 누설 있음 — 길이가 등급을 알려준다(2026-09-05). 라벨도 LLM 판정.",
    },
    "gold777": {
        "name": "gold777 (classification_gold)",
        "path": "datasets/gold_real/classification_gold.jsonl",
        "loader": lambda: _load_jsonl("datasets/gold_real/classification_gold.jsonl"),
        "known_leak": "사람 검수 서명 0/777 (human-review-signoff-integrity). 라벨 다수가 LLM(Qwen) 판정.",
    },
    "v5test": {
        "name": "v5 학습셋 test 분할 (**동일 분포·보정셋**)",
        "path": "datasets/labeled_p1_v5_clean/test.jsonl",
        "loader": lambda: _load_jsonl("datasets/labeled_p1_v5_clean/test.jsonl"),
        "known_leak": (
            "알려진 누설 있음 — 길이가 등급을 알려준다(2026-09-05). 게다가 이 모델의 "
            "학습 분할과 같은 분포이고, temperature.json(n=256)이 적합된 크기와 일치한다 "
            "= 보정을 맞춘 자리에서 보정을 재는 것일 수 있다."
        ),
    },
    "v5val": {
        "name": "v5 학습셋 val 분할 (**동일 분포**)",
        "path": "datasets/labeled_p1_v5_clean/val.jsonl",
        "loader": lambda: _load_jsonl("datasets/labeled_p1_v5_clean/val.jsonl"),
        "known_leak": "동일 분포(학습 검증 분할). 일반화 근거로 쓸 수 없다.",
    },
    # ↓ 백서의 네 숫자(AUROC 0.58 · ECE 0.184 · 63.4% · 56.2%)가 나왔다고 적힌 판정면.
    #   재현 여부를 답하려면 이 면을 반드시 재야 한다.
    "golden500": {
        "name": "golden500 (**원 주장의 판정면**)",
        "path": "datasets/gold/golden500.jsonl",
        "loader": lambda: _load_jsonl("datasets/gold/golden500.jsonl", "body", "target"),
        "known_leak": (
            "합성 스트레스셋 · 라벨 review_status=auto(사람 서명 0). 원 주장이 잰 면이지만 "
            "원 주장은 **다른(v5 이전) 모델**로 쟀다 — 같은 면이어도 같은 모델이 아니다."
        ),
    },
    "golden100": {
        "name": "golden100 적대셋",
        "path": "datasets/gold/golden100_labeled_v3.jsonl",
        "loader": lambda: _load_jsonl("datasets/gold/golden100_labeled_v3.jsonl", "text", "target"),
        "known_leak": "적대적 경계셋 · 합성 · 사람 서명 0. 실트래픽 분포가 아니다. 지름길 셋 — 글자 n-gram 5겹 100.0%(라벨섞기 28.0%, 2026-09-20 실측)라 절대 성능 근거 불가.",
    },
}


# ----------------------------------------------------------------------------
# 지표
# ----------------------------------------------------------------------------
def auroc(conf: list[float], correct: list[bool]) -> float | None:
    """calib_check.py 와 동일한 pairwise 정의(동점 0.5). 값이 비교 가능해야 한다."""
    pos = [c for c, k in zip(conf, correct) if k]
    neg = [c for c, k in zip(conf, correct) if not k]
    if not pos or not neg:
        return None
    wins = sum((1.0 if p > n else 0.5 if p == n else 0.0) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def _leak_ceiling_grade_codes(rows: list[dict]) -> float:
    """본문에 언급된 등급코드 집합만으로 맞힐 수 있는 상한(%).

    clean_candidate_answer_leak.py 의 _leak_ceiling 과 같은 정의(그 값과 비교 가능).
    """
    sig: dict[tuple, Counter] = defaultdict(Counter)
    for r in rows:
        key = tuple(c for c in GRADES if re.search(r"\b%s\b" % c, r["text"]))
        sig[key][r["label"]] += 1
    best = sum(c.most_common(1)[0][1] for c in sig.values())
    return best / max(len(rows), 1) * 100


def _length_only_ceiling(rows: list[dict], n_bins: int = 10) -> dict:
    """길이 **단독**으로 등급을 얼마나 맞힐 수 있는가 — leave-one-out.

    문서를 길이 오름차순 10 분위로 나누고, 각 문서를 **자기 자신을 뺀** 같은 분위의
    다수 등급으로 맞힌다(in-sample 다수결은 작은 n 에서 100% 가 나와 무의미하다).
    비교 기준은 최빈등급 비율(길이를 안 보고 최빈만 찍었을 때).
    """
    n = len(rows)
    if n < n_bins:
        return {"loo_accuracy": None, "majority_rate": None, "n_bins": n_bins}
    order = sorted(range(n), key=lambda i: len(rows[i]["text"]))
    bins: list[list[int]] = [[] for _ in range(n_bins)]
    for rank, idx in enumerate(order):
        bins[min(rank * n_bins // n, n_bins - 1)].append(idx)
    hit = 0
    for bucket in bins:
        cnt = Counter(rows[i]["label"] for i in bucket)
        for i in bucket:
            c2 = Counter(cnt)
            c2[rows[i]["label"]] -= 1
            if not sum(c2.values()):
                continue
            if c2.most_common(1)[0][0] == rows[i]["label"]:
                hit += 1
    maj = Counter(r["label"] for r in rows).most_common(1)[0][1] / n
    return {"loo_accuracy": hit / n, "majority_rate": maj, "n_bins": n_bins}


def _operating_point(rows, preds, conf, gate_flags, threshold, use_gate) -> dict:
    """자동확정 = conf >= 임계 AND (게이트 미사용 or 게이트 통과). 정밀도·커버리지."""
    auto_i = [
        i for i in range(len(rows))
        if conf[i] >= threshold and not (use_gate and gate_flags[i])
    ]
    n = len(rows)
    auto = len(auto_i)
    correct_auto = sum(1 for i in auto_i if preds[i] == rows[i]["label"])
    under = sum(1 for i in auto_i if _SEV[preds[i]] < _SEV[rows[i]["label"]])
    high_leak = sum(
        1 for i in auto_i
        if _SEV[preds[i]] < _SEV[rows[i]["label"]] and rows[i]["label"] in ("TS", "S1")
    )
    auto_set = set(auto_i)
    rev_i = [i for i in range(len(rows)) if i not in auto_set]
    rev_correct = sum(1 for i in rev_i if preds[i] == rows[i]["label"])
    return {
        "threshold": threshold,
        "agreement_gate": bool(use_gate),
        "auto_confirm_n": auto,
        "auto_confirm_coverage": (auto / n) if n else None,
        "auto_confirm_precision": (correct_auto / auto) if auto else None,
        "auto_confirm_underclassified": under,
        "auto_confirm_high_grade_missed": high_leak,
        "review_n": len(rev_i),
        "review_accuracy": (rev_correct / len(rev_i)) if rev_i else None,
    }


# ----------------------------------------------------------------------------
# 추론
# ----------------------------------------------------------------------------
def _cache_key(model_dir: Path, rows: list[dict]) -> str:
    h = hashlib.sha256()
    h.update(str(model_dir.resolve()).encode("utf-8"))
    for r in rows:
        h.update(r["text"].encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


_MODEL_CACHE: dict = {}


def _get_model(model_dir: Path):
    """모델·토크나이저를 한 번만 올린다(판정면마다 다시 올리면 면 수만큼 로드 비용).

    ⚠ 토크나이저는 `tokenizers.Tokenizer.from_file(tokenizer.json)` 로 **직접** 연다.
      `AutoTokenizer.from_pretrained` 은 이 배포본에서 멈춘다 — tokenizer_config.json 에
      `"is_local": false, "local_files_only": false` 가 박혀 있어 transformers 5.13 이
      허브 조회를 시도하고, HF_HUB_OFFLINE=1 을 줘도 20분 이상 반환하지 않았다(2026-09-08 실측).
      AutoTokenizer(use_fast=True) 가 여는 것도 결국 같은 tokenizer.json 이고 CLS/SEP 부착·
      패딩 규칙도 그 파일의 post_processor 에 들어 있으므로 인코딩 결과는 같다.
      calib_check.py 의 `truncation=True, max_length=512, padding=True` 와 동등한 설정을 건다.
    """
    key = str(model_dir.resolve())
    if key not in _MODEL_CACHE:
        from tokenizers import Tokenizer  # noqa: PLC0415
        from transformers import AutoModelForSequenceClassification  # noqa: PLC0415

        t0 = time.perf_counter()
        tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        tok.enable_truncation(max_length=512)
        tok.enable_padding()
        model = AutoModelForSequenceClassification.from_pretrained(str(model_dir)).eval()
        print("   (모델 로드 %.0fs: %s)" % (time.perf_counter() - t0, model_dir), flush=True)
        _MODEL_CACHE[key] = (tok, model)
    return _MODEL_CACHE[key]


def compute_logits(model_dir: Path, rows: list[dict], cache_dir: Path, use_cache: bool):
    """raw logits — calib_check.py 와 동일(truncation 512 · 단일 pass · 온도 미적용)."""
    key = _cache_key(model_dir, rows)
    cf = cache_dir / ("logits_%s.json" % key)
    if use_cache and cf.is_file():
        d = json.loads(cf.read_text("utf-8"))
        if len(d["logits"]) == len(rows):
            # JSON 은 dict 키를 문자열로 만든다 — 되읽을 때 int 로 되돌린다.
            return d["logits"], {int(k): v for k, v in d["id2label"].items()}, True

    import torch  # noqa: PLC0415

    tok, model = _get_model(model_dir)
    id2label = {int(k): v for k, v in model.config.id2label.items()}
    out: list[list[float]] = []
    t0 = time.perf_counter()
    with torch.no_grad():
        for i in range(0, len(rows), 8):
            batch = [r["text"] for r in rows[i:i + 8]]
            enc = tok.encode_batch(batch)
            ids = torch.tensor([e.ids for e in enc])
            mask = torch.tensor([e.attention_mask for e in enc])
            out.extend(model(input_ids=ids, attention_mask=mask).logits.tolist())
            if (i // 8) % 20 == 0:
                print("      추론 %d/%d (%.0fs)" % (len(out), len(rows),
                                                    time.perf_counter() - t0), flush=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cf.write_text(json.dumps({"logits": out, "id2label": id2label}), encoding="utf-8")
    return out, id2label, False


# ----------------------------------------------------------------------------
# 합의 게이트 — 실제 서빙 코드를 호출한다
# ----------------------------------------------------------------------------
class _PredShim:
    """ClassifyService._agreement_gate 가 읽는 필드만 가진 최소 객체.

    rule_grade / rule_has_evidence 를 채워 주면 게이트는 self(=서비스)를 만지지 않는다
    (미설정일 때만 self.inference.labeling.engine 으로 폴백한다 — 코드 697-717행).
    """

    def __init__(self, label: str, rule_grade: str, rule_has_evidence: bool):
        self.label = label
        self.rule_grade = rule_grade
        self.rule_has_evidence = rule_has_evidence


def build_gate_flags(rows: list[dict], preds: list[str], gate_enabled: bool) -> list[bool]:
    """각 문서가 합의 게이트에 걸리는가(True=검수 라우팅). 실제 게이트 코드 호출."""
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m3_labeling.pipeline import LabelingPipeline  # noqa: PLC0415
    from koipa.modules.m3_labeling.rule_engine import has_real_evidence  # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415

    prev = getattr(settings, "agreement_gate_enabled", False)
    settings.agreement_gate_enabled = bool(gate_enabled)
    try:
        if "engine" not in _MODEL_CACHE:
            _MODEL_CACHE["engine"] = LabelingPipeline().engine
        engine = _MODEL_CACHE["engine"]
        flags: list[bool] = []
        t0 = time.perf_counter()
        for i, (r, p) in enumerate(zip(rows, preds)):
            res = engine.label(r["text"])
            rg = res.grade.value if hasattr(res.grade, "value") else str(res.grade)
            shim = _PredShim(p, rg, has_real_evidence(res))
            # unbound 호출 — 게이트는 rule_grade 가 있으면 self 를 참조하지 않는다.
            flags.append(ClassifyService._agreement_gate(None, shim, r["text"]) is not None)
            if i and i % 200 == 0:
                print("      룰/게이트 %d/%d (%.0fs)" % (i, len(rows),
                                                        time.perf_counter() - t0), flush=True)
        return flags
    finally:
        settings.agreement_gate_enabled = prev


# ----------------------------------------------------------------------------
def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="배포 분류기 conf 신뢰성·자동확정 운영점 측정")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--surface", action="append", default=None,
                    choices=sorted(SURFACES), help="특정 면만 (여러 번)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--skip-original", action="store_true",
                    help="원 조건(bpilot_v2 × golden500) 재측정을 건너뛴다")
    a = ap.parse_args(argv)

    from koipa.config import _PROFILE_DEFAULTS  # noqa: PLC0415
    from koipa.modules.m6_evaluation.temperature import (  # noqa: PLC0415
        expected_calibration_error as ECE,
    )
    from koipa.modules.m6_evaluation.temperature import (  # noqa: PLC0415
        find_best_temperature,
        neg_log_likelihood,
        softmax,
    )

    model_dir = (_POC / a.model) if not Path(a.model).is_absolute() else Path(a.model)
    out_dir = (_POC / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = out_dir / "_logits_cache"

    # ── 운영점: 배포 프로파일에서 읽는다(문서에 적힌 값을 베끼지 않는다) ──────────
    prof_name = "onprem-local"  # 폐쇄망 배포본. full-train 도 같은 값(config.py:147-149).
    prof = _PROFILE_DEFAULTS[prof_name]
    cur_threshold = float(prof["review_confidence_threshold"])
    gate_enabled = bool(prof["agreement_gate_enabled"])
    prof_temperature = float(prof["classifier_temperature"])
    tj = json.loads((model_dir / "temperature.json").read_text("utf-8"))
    model_temperature = float(tj["temperature"])
    mcfg = json.loads((model_dir / "config.json").read_text("utf-8"))
    id2label_cfg = {int(k): v for k, v in mcfg.get("id2label", {}).items()}

    header = {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "reproduce_command": REPRO_CMD,
        "script": "poc/scripts/measure_gate_metrics.py",
        "model_dir": str(model_dir),
        "model_version": model_dir.name,
        "model_temperature_json": tj,
        "serving_temperature_used": model_temperature,
        "profile_used_for_operating_point": prof_name,
        "profile_review_confidence_threshold": cur_threshold,
        "profile_agreement_gate_enabled": gate_enabled,
        "profile_classifier_temperature": prof_temperature,
        "temperature_agrees_model_vs_profile": abs(prof_temperature - model_temperature) < 1e-9,
        "obsolete_threshold": OBSOLETE_THRESHOLD,
        "device": "cpu",
        "method": (
            "raw logits(단일 pass·truncation 512) → softmax(T). "
            "calibration_audit_2026-08-25/calib_check.py 와 동일. "
            "합의 게이트는 ClassifyService._agreement_gate 실코드 호출."
        ),
        "not_measured": (
            "서빙의 나머지 라우팅(escalation tau=0.30 · source_prior cap · metadata_floor · "
            "sparse-evidence · s2-underclass-risk · kill-gate)은 포함하지 않는다 — 전부 "
            "needs_review 를 더 만드는 방향이라 여기의 자동확정 커버리지는 실서빙의 상한이다."
        ),
    }

    lines: list[str] = []

    def emit(s: str = "") -> None:
        print(s, flush=True)
        lines.append(s)

    emit("=" * 100)
    emit(" 배포 분류기 게이트 지표 측정   %s" % header["measured_at"])
    emit("=" * 100)
    emit("  모델        : %s" % model_dir)
    emit("  temperature : 모델동봉 %.2f · 프로파일(%s) %.2f · 일치 %s"
         % (model_temperature, prof_name, prof_temperature,
            "예" if header["temperature_agrees_model_vs_profile"] else "아니오"))
    emit("  현행 운영점 : conf >= %.2f · agreement_gate=%s   (config.py _PROFILE_DEFAULTS['%s'])"
         % (cur_threshold, gate_enabled, prof_name))
    emit("  폐기 운영점 : conf >= %.2f (2026-08-24 에 대체됨 — 제출본 연속성용으로만 계산)"
         % OBSOLETE_THRESHOLD)
    emit("  재현        : %s" % REPRO_CMD)
    emit("  안 잰 것    : %s" % header["not_measured"])
    emit()

    keys = a.surface or list(SURFACES)
    results = []
    for key in keys:
        spec = SURFACES[key]
        emit("-" * 100)
        emit("[%s] %s" % (key, spec["name"]))
        emit("   경로: %s" % spec["path"])
        try:
            rows = spec["loader"]()
        except Exception as exc:  # noqa: BLE001
            emit("   ⚠ 로드 실패: %s: %s — 이 면은 측정하지 못했다." % (type(exc).__name__, exc))
            results.append({"key": key, "name": spec["name"], "path": spec["path"],
                            "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        if not rows:
            emit("   ⚠ 0건 — 이 면은 측정하지 못했다(파일 없음·필드 불일치).")
            results.append({"key": key, "name": spec["name"], "path": spec["path"], "n": 0})
            continue

        dist = dict(Counter(r["label"] for r in rows))
        emit("   n=%d  정답분포=%s" % (len(rows), dist))

        # ── 누설 측정 (기억이 아니라 그 자리에서 잰다) ─────────────────────────
        ans_leak = sum(1 for r in rows if _ANSWER_LEAK.search(r["text"]))
        code_ceil = _leak_ceiling_grade_codes(rows)
        len_probe = _length_only_ceiling(rows)
        chars = sorted(len(r["text"]) for r in rows)
        med_by_grade = {}
        for g in GRADES:
            ls = sorted(len(r["text"]) for r in rows if r["label"] == g)
            if ls:
                med_by_grade[g] = ls[len(ls) // 2]
        emit("   [누설 측정] 본문 정답노출 %d건 · 등급코드 언급 상한 %.1f%% · "
             "길이단독 LOO %s (최빈 %s)"
             % (ans_leak, code_ceil,
                ("%.1f%%" % (len_probe["loo_accuracy"] * 100)) if len_probe["loo_accuracy"] is not None else "-",
                ("%.1f%%" % (len_probe["majority_rate"] * 100)) if len_probe["majority_rate"] is not None else "-"))
        emit("   [누설 기록] %s" % spec["known_leak"])
        emit("   [길이] 등급별 중앙값 %s · 전체 중앙값 %d자"
             % (med_by_grade, chars[len(chars) // 2]))

        logits, id2label, cached = compute_logits(model_dir, rows, cache_dir, not a.no_cache)
        if cached:
            emit("   (logits 캐시 사용 — 다시 추론하려면 --no-cache)")
        lab2i = {v: k for k, v in id2label.items()}
        Y = [lab2i[r["label"]] for r in rows]
        pred_i = [max(range(len(l)), key=lambda i: l[i]) for l in logits]
        preds = [id2label[i] for i in pred_i]
        acc = sum(p == y for p, y in zip(pred_i, Y)) / len(Y)
        conf = [softmax(l, model_temperature)[p] for l, p in zip(logits, pred_i)]
        conf_t1 = [softmax(l, 1.0)[p] for l, p in zip(logits, pred_i)]
        correct = [p == y for p, y in zip(pred_i, Y)]
        au = auroc(conf, correct)
        au_t1 = auroc(conf_t1, correct)
        ece_t1 = ECE(logits, Y, 1.0)
        ece_td = ECE(logits, Y, model_temperature)
        best_t = find_best_temperature(logits, Y)
        ece_best = ECE(logits, Y, best_t)

        emit("   정확도 %.4f  | conf AUROC %s (T=%.2f) · %s (T=1.0)"
             % (acc, ("%.4f" % au) if au is not None else "-", model_temperature,
                ("%.4f" % au_t1) if au_t1 is not None else "-"))
        # 예측 분포 — 미탐이 낮을 때 그것이 능력인지 과탐인지 가른다.
        # 2026-09-13: v-0d2e9ad0 이 미탐 0건이었는데 42건 중 40건을 TS 로 찍어서 얻은 값이었다.
        pred_dist = Counter(preds)
        top_label, top_n = pred_dist.most_common(1)[0]
        emit("   예측분포 %s%s"
             % (dict(pred_dist.most_common()),
                ("   ← %s 로 %.0f%% 쏠림 (미탐 수치를 과탐으로 산 것인지 볼 것)"
                 % (top_label, top_n / len(preds) * 100)) if top_n / len(preds) >= 0.60 else ""))
        emit("   ECE   T=1.0 %.4f  ·  T=%.2f(배포) %.4f  ·  재적합T=%.2f → %.4f"
             % (ece_t1, model_temperature, ece_td, best_t, ece_best))
        emit("   NLL   T=1.0 %.4f  ·  T=%.2f %.4f"
             % (neg_log_likelihood(logits, Y, 1.0),
                model_temperature, neg_log_likelihood(logits, Y, model_temperature)))

        gate_flags = build_gate_flags(rows, preds, gate_enabled)
        emit("   합의게이트 발동 %d/%d (%.1f%%)"
             % (sum(gate_flags), len(rows), sum(gate_flags) / len(rows) * 100))

        ops = {
            "current": _operating_point(rows, preds, conf, gate_flags, cur_threshold, gate_enabled),
            "obsolete_0.70_conf_only": _operating_point(rows, preds, conf, gate_flags, OBSOLETE_THRESHOLD, False),
            "obsolete_0.70_with_gate": _operating_point(rows, preds, conf, gate_flags, OBSOLETE_THRESHOLD, True),
            "current_threshold_conf_only": _operating_point(rows, preds, conf, gate_flags, cur_threshold, False),
        }
        emit("   %-34s %8s %10s %10s %8s %8s" %
             ("운영점", "자동확정", "커버리지", "정밀도", "미탐", "검수정답률"))
        for label, op in (
            ("현행 conf>=%.2f + 합의게이트" % cur_threshold, ops["current"]),
            ("(참고) conf>=%.2f 단독" % cur_threshold, ops["current_threshold_conf_only"]),
            ("폐기 conf>=%.2f 단독" % OBSOLETE_THRESHOLD, ops["obsolete_0.70_conf_only"]),
            ("폐기 conf>=%.2f + 합의게이트" % OBSOLETE_THRESHOLD, ops["obsolete_0.70_with_gate"]),
        ):
            emit("   %-34s %8d %9.1f%% %9s %8d %9s" % (
                label, op["auto_confirm_n"], op["auto_confirm_coverage"] * 100,
                ("%.1f%%" % (op["auto_confirm_precision"] * 100)) if op["auto_confirm_precision"] is not None else "-",
                op["auto_confirm_underclassified"],
                ("%.1f%%" % (op["review_accuracy"] * 100)) if op["review_accuracy"] is not None else "-",
            ))
        emit("   자동확정 중 고등급(TS·S1) 무음 미탐: 현행 %d건 · 폐기0.70단독 %d건"
             % (ops["current"]["auto_confirm_high_grade_missed"],
                ops["obsolete_0.70_conf_only"]["auto_confirm_high_grade_missed"]))

        results.append({
            "key": key, "name": spec["name"], "path": spec["path"],
            "n": len(rows), "label_distribution": dist,
            "known_leak_record": spec["known_leak"],
            "leak_probes_measured": {
                "answer_in_body_docs": ans_leak,
                "grade_code_mention_ceiling_pct": round(code_ceil, 2),
                "length_only_loo_accuracy": len_probe["loo_accuracy"],
                "majority_class_rate": len_probe["majority_rate"],
                "length_probe_bins": len_probe["n_bins"],
                "median_chars_by_grade": med_by_grade,
                "median_chars_overall": chars[len(chars) // 2],
            },
            "accuracy": acc,
            "prediction_distribution": dict(pred_dist.most_common()),
            "prediction_top_share": top_n / len(preds),
            "conf_auroc_at_serving_T": au,
            "conf_auroc_at_T1": au_t1,
            "ece_T1": ece_t1,
            "ece_at_serving_T": ece_td,
            "serving_T": model_temperature,
            "refit_T": best_t,
            "ece_at_refit_T": ece_best,
            "nll_T1": neg_log_likelihood(logits, Y, 1.0),
            "nll_at_serving_T": neg_log_likelihood(logits, Y, model_temperature),
            "agreement_gate_triggered": sum(gate_flags),
            "operating_points": ops,
        })
        emit()

    # ── 원 주장 재현 여부 ────────────────────────────────────────────────────
    ORIG = {"auroc": 0.58, "ece": 0.184, "auto_precision": 0.634, "review_accuracy": 0.562}

    # 원 조건 그대로 한 번 더 — **원래 모델**(bpilot_v2)로 **원래 면**(golden500)을 잰다.
    # 백서 §04 가 "학습모델(bpilot_v2) … T*=2.0, ECE 0.131→0.045" 라고 적었고,
    # artifacts/classifier_bpilot_v2/v-137066a1/temperature.json 이 정확히 그 값
    # (2.0053 · 0.1313 → 0.0447)을 갖고 있다 = 원 주장을 만든 모델이 리포에 남아 있다.
    # 현행 배포본에서 재현되지 않더라도 "그때 그 조건에서 나온 값인가"는 답할 수 있다.
    orig_run = None
    if not a.skip_original:
        om = _POC / "artifacts/classifier_bpilot_v2/v-137066a1"
        emit("=" * 100)
        emit(" 원 조건 재측정 — 원래 모델(bpilot_v2 v-137066a1) × 원래 면(golden500)")
        emit("=" * 100)
        if not om.is_dir():
            emit("   ⚠ %s 없음 — 원 조건 재측정을 하지 못했다." % om)
        else:
            otj = json.loads((om / "temperature.json").read_text("utf-8"))
            oT = float(otj["temperature"])
            orows = SURFACES["golden500"]["loader"]()
            ologits, oid2, ocached = compute_logits(om, orows, cache_dir, not a.no_cache)
            ol2i = {v: k for k, v in oid2.items()}
            oY = [ol2i[r["label"]] for r in orows]
            opi = [max(range(len(l)), key=lambda i: l[i]) for l in ologits]
            opreds = [oid2[i] for i in opi]
            oconf = [softmax(l, oT)[p] for l, p in zip(ologits, opi)]
            ocorr = [p == y for p, y in zip(opi, oY)]
            oacc = sum(ocorr) / len(oY)
            oau = auroc(oconf, ocorr)
            oece = ECE(ologits, oY, oT)
            ogate = build_gate_flags(orows, opreds, gate_enabled)
            oop = _operating_point(orows, opreds, oconf, ogate, OBSOLETE_THRESHOLD, False)
            oop_gate = _operating_point(orows, opreds, oconf, ogate, OBSOLETE_THRESHOLD, True)
            emit("   모델 %s · T=%.4f (동봉 temperature.json: ece %.4f→%.4f, n=%s)"
                 % (om.name, oT, otj.get("ece_before"), otj.get("ece_after"), otj.get("n_samples")))
            emit("   n=%d · 정확도 %.4f" % (len(orows), oacc))
            emit("   conf AUROC %s      (원 주장 0.58)"
                 % (("%.4f" % oau) if oau is not None else "-"))
            emit("   ECE(T=%.4f) %.4f     (원 주장 0.184)" % (oT, oece))
            emit("   conf>=0.70 단독 자동확정 정밀도 %.4f  (원 주장 0.634) · 커버리지 %.4f"
                 % (oop["auto_confirm_precision"] or 0, oop["auto_confirm_coverage"]))
            emit("   conf<0.70 검수대상 정답률 %s      (원 주장 0.562)"
                 % (("%.4f" % oop["review_accuracy"]) if oop["review_accuracy"] is not None else "-"))
            emit("   (참고) conf>=0.70 + 합의게이트 정밀도 %s · 커버리지 %.4f  — 백서의 63%%→81%% 대응"
                 % (("%.4f" % oop_gate["auto_confirm_precision"]) if oop_gate["auto_confirm_precision"] is not None else "-",
                    oop_gate["auto_confirm_coverage"]))
            emit()
            orig_run = {
                "model_dir": str(om), "model": om.name, "temperature": oT,
                "temperature_json": otj, "surface": "golden500", "n": len(orows),
                "accuracy": oacc, "conf_auroc": oau, "ece": oece,
                "op_0.70_conf_only": oop, "op_0.70_with_gate": oop_gate,
                "claim_vs_measured": {
                    "auroc": {"claim": ORIG["auroc"], "measured": oau},
                    "ece": {"claim": ORIG["ece"], "measured": oece},
                    "auto_precision": {"claim": ORIG["auto_precision"],
                                       "measured": oop["auto_confirm_precision"]},
                    "review_accuracy": {"claim": ORIG["review_accuracy"],
                                        "measured": oop["review_accuracy"]},
                },
            }
    emit("=" * 100)
    emit(" 원 주장(백서 하드코딩 상수)이 **측정 대상 모델**에서 재현되는가")
    emit("   원 주장: AUROC 0.58 · ECE 0.184 · 자동확정 63.4% · 검수대상 56.2%")
    emit("   원 출처: scripts/archive/gen_gate_whitepaper.py (측정 스크립트·로그 없음)")
    emit("   원 조건: 모델 bpilot_v2 · 면 golden500 · conf>=0.7 conf단독 (백서 §04·§05)")
    emit("   ↓ 아래는 **%s** 로 각 면을 다시 잰 것이다(원 주장의 모델과 다르다)." % model_dir.name)
    emit("=" * 100)
    repro = []
    for r in results:
        if not r.get("n"):
            continue
        ob = r["operating_points"]["obsolete_0.70_conf_only"]
        checks = {
            "AUROC 0.58 (±0.03)": r["conf_auroc_at_serving_T"] is not None
            and abs(r["conf_auroc_at_serving_T"] - ORIG["auroc"]) <= 0.03,
            "ECE 0.184 (±0.02, 배포T)": abs(r["ece_at_serving_T"] - ORIG["ece"]) <= 0.02,
            "자동확정 63.4% (±3pp)": ob["auto_confirm_precision"] is not None
            and abs(ob["auto_confirm_precision"] - ORIG["auto_precision"]) <= 0.03,
            "검수대상 56.2% (±3pp)": ob["review_accuracy"] is not None
            and abs(ob["review_accuracy"] - ORIG["review_accuracy"]) <= 0.03,
        }
        hit = [k for k, v in checks.items() if v]
        repro.append({"surface": r["key"], "matched": hit,
                      "auroc": r["conf_auroc_at_serving_T"],
                      "ece_at_serving_T": r["ece_at_serving_T"],
                      "auto_precision_0.70_conf_only": ob["auto_confirm_precision"],
                      "review_accuracy_0.70_conf_only": ob["review_accuracy"]})
        emit("   %-20s AUROC %-7s ECE %-7s 자동확정 %-7s 검수 %-7s → 재현: %s"
             % (r["key"],
                ("%.3f" % r["conf_auroc_at_serving_T"]) if r["conf_auroc_at_serving_T"] is not None else "-",
                "%.3f" % r["ece_at_serving_T"],
                ("%.1f%%" % (ob["auto_confirm_precision"] * 100)) if ob["auto_confirm_precision"] is not None else "-",
                ("%.1f%%" % (ob["review_accuracy"] * 100)) if ob["review_accuracy"] is not None else "-",
                ", ".join(hit) if hit else "없음"))
    any_full = [x for x in repro if len(x["matched"]) == 4]
    emit()
    emit("   %s 에서 네 값이 **동시에** 재현되는 면: %s"
         % (model_dir.name, ", ".join(x["surface"] for x in any_full) if any_full else "없다"))
    if orig_run:
        tol = {"auroc": 0.03, "ece": 0.02, "auto_precision": 0.03, "review_accuracy": 0.03}
        cvm = orig_run["claim_vs_measured"]
        ok = {k: (v["measured"] is not None and abs(v["measured"] - v["claim"]) <= tol[k])
              for k, v in cvm.items()}
        orig_run["reproduced"] = ok
        orig_run["reproduced_all"] = all(ok.values())
        emit("   원 조건(bpilot_v2 × golden500)에서 재현된 값: %s"
             % (", ".join(k for k, v in ok.items() if v) or "없다"))
        for k, v in cvm.items():
            emit("      %-16s 주장 %.3f  vs  실측 %s  → %s"
                 % (k, v["claim"],
                    ("%.3f" % v["measured"]) if v["measured"] is not None else "-",
                    "재현" if ok[k] else "불일치"))
    emit()
    emit("=" * 100)
    emit(" 읽는 법")
    emit("   · 누설된 면의 수치는 모델이 아니라 지름길을 잰 것이다 — 위 [누설] 줄을 먼저 볼 것.")
    emit("   · 자동확정 커버리지는 실서빙의 **상한**이다(위 '안 잰 것' 참조).")
    emit("   · 정답이 사람 서명인 면은 없다 — 전부 기계라벨 또는 생성 시 의도등급이다.")
    emit("   · 재현: %s" % REPRO_CMD)
    emit("=" * 100)

    payload = {"header": header, "original_claim_reproduction": {
        "claim": ORIG,
        "claim_source": "poc/scripts/archive/gen_gate_whitepaper.py (하드코딩 상수)",
        "claim_model_per_whitepaper": "bpilot_v2 (§04: 'T*=2.0, ECE 0.131→0.045')",
        "claim_surface_per_whitepaper": "golden500 (§05 신뢰도 다이어그램)",
        "per_surface_current_model": repro,
        "fully_reproduced_on_current_model": [x["surface"] for x in any_full],
        "original_conditions_rerun": orig_run,
    }, "surfaces": results}
    (out_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "result.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n[saved] %s" % (out_dir / "result.json"))
    print("[saved] %s" % (out_dir / "result.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
