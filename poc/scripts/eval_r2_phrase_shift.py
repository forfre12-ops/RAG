#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""표현 이동 시험(PREREG_R2.md D) — 1차 문서로 학습한 모델을 표현이 바뀐 2차 문서에 적용한다.

모델
  · BASE : R576E10 (개발 800건만 학습, 에폭 10) 분할 5개 × 시드 42·43
  · MIX  : 개발 800건 + 1차 파일럿 가족 4/5 를 섞어 학습(에폭 10) 분할 5개 × 시드 42·43   (run_mock1000_pilotmix.py run)
  · TFIDF: 1차 검증 문서 233건 전체로 학습한 글자 n-gram TF-IDF + 로지스틱
적용 대상: 2차 문서 중 블라인드 판정이 명세와 일치한 문서(검증 통과) — 분할 모델 5개의 예측을 모아(문서 수×5) 시드별로 지표를 낸다.
1차 안에서(같은 표현)의 정확도는 pilotmix_result_20260921.txt (파일럿 판독) 참조 — 차이가 표현 의존도다.
사용:  python scripts/eval_r2_phrase_shift.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_mock1000_cv import G, K, POC, TAU, prf  # noqa: E402

OUT = POC / "reports" / "mock1000_dev800"
R1 = POC / "reports" / "CLAUDE_DOCGEN_20260921"
R2 = POC / "reports" / "CLAUDE_DOCGEN_R2_20260921"


def load_verified(d: Path) -> list[dict]:
    docs = [json.loads(x) for x in (d / "pilot_docs_checked.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    ok = {r["doc_key"] for r in json.loads((d / "pilot_judge_rows.json").read_text(encoding="utf-8")) if r["ok"]}
    return [x for x in docs if x["doc_key"] in ok]


def line(name: str, m: dict) -> str:
    return (f"  {name:<30} 정확도 {m['acc']:.1%} · macro P/R/F1 {m['macroP']:.1%}/{m['macroR']:.1%}/{m['macroF1']:.1%} · 고등급 미탐 {m['hi_miss']}/{m['hi_n']} = {m['hi_miss'] / m['hi_n']:.1%}"
            f" · 재현율 TS/S1/S2/S3 " + "/".join(f"{m['per'][g]['R']:.0%}" for g in G))


def main() -> int:
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: PLC0415
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression

    settings.classifier_escalation_tau = TAU
    tgt_name = os.environ.get("SHIFT_TARGET", "R2")                    # R2(기본) 또는 R3(학습에 쓰지 않는 표현 시험셋)
    tgt_dir = {"R2": R2, "R3": POC / "reports" / "CLAUDE_DOCGEN_R3_20260921"}[tgt_name]
    r2 = load_verified(tgt_dir)                                        # 시험 대상(변수명 유지)
    r1 = load_verified(R1) + (load_verified(R2) if tgt_name == "R3" else [])   # 글자 n-gram 학습 문서
    L = [f"표현 이동 시험 · {tgt_name} 검증 문서 {len(r2)}건(등급별 " + str({g: sum(1 for x in r2 if x['grade'] == g) for g in G}) + f") · TF-IDF 학습 문서 {len(r1)}건 · τ={TAU}"]
    res = {}
    models = [("BASE(파일럿 미학습)", "model_R576E10_s{s}"), ("MIX(1차 섞어 학습)", "model_MIX_s{s}"), ("MIX2(1·2차 섞어 학습)", "model_MIX2_s{s}")]
    want = os.environ.get("SHIFT_MODELS", "BASE,MIX").split(",")
    for tag, dirname in [m for m in models if m[0].split("(")[0] in want]:
        for s in (42, 43):
            recs = []
            ok = True
            for k in range(K):
                root = OUT / "rand" / f"fold{k}" / dirname.format(s=s)
                if not list(root.glob("v-*/model.safetensors")):
                    ok = False
                    break
                pipe = InferencePipeline(model_dir=str(sorted(root.glob("v-*"))[-1]))
                for x in r2:
                    res_ = pipe.run(x["text"], metadata=None)
                    code = res_.label.value if hasattr(res_.label, "value") else str(res_.label)
                    recs.append({"label": x["grade"], "pred": code})
            if not ok:
                L.append(f"  {tag} 시드 {s}: 모델이 아직 없다")
                continue
            m = prf(recs, lambda r: r["pred"])
            res[(tag, s)] = m
            L.append(line(f"{tag} 시드 {s}", m))
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=200000)
    clf = LogisticRegression(C=10, max_iter=3000, class_weight="balanced").fit(vec.fit_transform([x["text"] for x in r1]), [x["grade"] for x in r1])
    pred = clf.predict(vec.transform([x["text"] for x in r2]))
    L.append(line("글자 n-gram TF-IDF(1차→2차)", prf([{"label": x["grade"], "pred": p} for x, p in zip(r2, pred)], lambda r: r["pred"])))
    text = "\n".join(L)
    (OUT / f"{tgt_name.lower()}_phrase_shift_result_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
