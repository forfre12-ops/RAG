#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""저장된 섀도 결과를 **어느 체크포인트가 재현하는가** — 가중치로 되짚는다.

왜. `reports/V8_SHADOW_SWEEP.json` 은 모델 경로를 적지 않았다. artifacts/factor_model 에는
체크포인트가 21개 있고(실측 2026-09-08: 4-class 19 · 3-class 2), 서빙 로더는 3-class 를
싣지 않는다. "어느 것으로 잰 값이냐"는 문서로는 답이 안 나오므로 예측으로 답한다.

⚠ 종전 측정 시점(2026-08-15)에는 세척본이 없었으므로 **원본 .md** 로 재현해야 한다.

사용:
    python scripts/which_factor_checkpoint.py --n 25
    python scripts/which_factor_checkpoint.py --n 25 --only artifacts/factor_model/v8_caus
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

ROOT = _POC / "datasets" / "proxy_gold" / "single_document_candidates"


def original_text(doc_id: str, index: dict) -> str | None:
    """세척 전 본문 — `.cleaned.md` 가 아닌 쪽을 고른다."""
    names = index.get(doc_id) or []
    return names[0].read_text("utf-8") if len(names) == 1 else None


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace",
                                  line_buffering=True)
    ap = argparse.ArgumentParser(description="저장된 섀도 결과를 재현하는 체크포인트 찾기")
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--sweep", default="reports/V8_SHADOW_SWEEP.json")
    ap.add_argument("--only", default="", help="한 디렉터리만 검사")
    ap.add_argument("--report", default="reports/V8_CHECKPOINT_IDENTIFY_2026-09-08.json")
    a = ap.parse_args(argv)
    os.environ.setdefault("TESTING", "1")

    # doc_id -> 원본 .md (디렉터리를 한 번만 훑는다 — 파일당 glob 은 2,480개에서 분 단위다)
    index: dict[str, list[Path]] = {}
    for p in ROOT.glob("*.md"):
        if p.name.endswith(".cleaned.md"):
            continue
        index.setdefault(p.name.split("_")[0].replace(".md", ""), []).append(p)

    old = json.loads(Path(a.sweep).read_text("utf-8"))["rows"]
    sample = []
    for r in old:
        t = original_text(r["doc_id"], index)
        if t:
            sample.append((r, t))
        if len(sample) >= a.n:
            break
    print("[data] 표본 %d건 (원본 본문)" % len(sample))
    if not sample:
        print("[error] 원본 본문을 하나도 못 찾았다")
        return 2

    from koipa.config import settings  # noqa: PLC0415
    from koipa.modules.m5_inference.factor_model import (  # noqa: PLC0415
        FactorInference,
        apply_serving_gate,
    )

    dirs = [a.only] if a.only else [
        d for d in sorted(glob.glob("artifacts/factor_model/*"))
        if os.path.isfile(os.path.join(d, "model.pt"))]
    results = []
    for d in dirs:
        inf = FactorInference(d, base=settings.factor_model_base,
                              max_len=settings.factor_model_max_len)
        if not inf.load():
            print("%-34s 로드 거부 — %s" % (d, (inf.load_error or "")[:70]))
            results.append({"dir": d, "loaded": False, "error": inf.load_error})
            continue
        g = f = 0
        for r, t in sample:
            pred = inf.predict(t)
            if pred is None:
                continue
            codes, probs = pred
            fp = apply_serving_gate(codes, probs, metadata=None,
                                    tau=settings.factor_tau, kappa=settings.factor_kappa)
            g += int(fp.serving_grade == r["v8"])
            f += int(fp.named == r["factors"])
        print("%-34s 등급 %2d/%d · 요소 %2d/%d" % (d, g, len(sample), f, len(sample)))
        results.append({"dir": d, "loaded": True, "grade_match": g,
                        "factor_match": f, "n": len(sample)})
        inf._model = None

    hit = [r for r in results if r.get("factor_match") == len(sample)]
    print("\n[결론] 요소까지 전건 일치: %s" % ([r["dir"] for r in hit] or "없음"))
    Path(a.report).parent.mkdir(parents=True, exist_ok=True)
    Path(a.report).write_text(json.dumps(
        {"n": len(sample), "results": results,
         "exact": [r["dir"] for r in hit]}, ensure_ascii=False, indent=1), "utf-8")
    print("[report] %s" % a.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
