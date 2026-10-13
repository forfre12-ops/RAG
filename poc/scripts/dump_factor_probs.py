#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""요소모델 원시 확률을 한 번만 뽑아 저장한다 — 문턱값(kappa·tau)은 오프라인으로 훑는다.

왜 따로 뽑는가(2026-09-10). `run_factor_shadow_sweep_clean.py` 는 게이트를 통과시킨
**결과**만 남긴다(factors·min_conf). 그래서 "요소가 미확정인 것이 데이터 탓인가
문턱 탓인가"를 그 산출물로는 못 가린다 — kappa 를 바꾼 값을 다시 계산할 수 없다.
원시 codes·probs 를 남겨 두면 추론은 한 번만 돌리고 문턱은 표에서 훑을 수 있다.

배경 실측: factor_kappa=factor_tau=0.99 인데 min_conf 중앙값은 0.7298 이고
0.99 를 넘는 행은 1,055건 중 3건(0.3%)이다. 문턱이 분포보다 두 자릿수 위에 있다.

사용:
    python scripts/dump_factor_probs.py --model artifacts/factor_model/v8_caus
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
for _p in (str(_HERE), str(_POC / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# cp949 콘솔에서 em dash 하나에 죽는 것을 막는다 — 문자를 바꾸지 말고 출구를 고정한다.
# 정본은 scripts/_cli_io.py 한 곳이다(tests/test_scripts_console_encoding.py 가 강제).
try:  # 스크립트로 직접 실행
    from _cli_io import force_utf8_stdio
except ImportError:  # 패키지로 import
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="artifacts/factor_model/v8_caus")
    ap.add_argument("--out", default="tmp/factor_probs_v8_caus.jsonl")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    from eval_on_clean_candidates import _ANSWER, load_candidates
    from koipa.config import settings
    from koipa.modules.m5_inference.factor_model import get_factor_inference

    mdir = Path(a.model)
    if not (mdir / "model.pt").is_file():
        print("[error] 체크포인트 없음: %s" % mdir)
        return 2
    inf = get_factor_inference(str(mdir), base=settings.factor_model_base,
                               max_len=settings.factor_model_max_len)
    if not inf.load():
        print("[error] 로드 실패: %s" % inf.load_error)
        return 2
    print("[model] %s · device=%s · 온도보정=%s"
          % (mdir, inf._device, "적용" if inf._temperature else "없음"))

    rows = load_candidates()
    leaked = sum(1 for r in rows if _ANSWER.search(r["text"]))
    print("[data] 후보 %d건 · 본문 정답 노출 %d건" % (len(rows), leaked))
    if leaked:
        print("[error] 세척면이 아니다 — clean_candidate_answer_leak.py 먼저 돌릴 것")
        return 2
    if a.limit:
        rows = rows[:a.limit]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    n = 0
    with out.open("w", encoding="utf-8") as fh:
        for i, r in enumerate(rows):
            pred = inf.predict(r["text"])
            if pred is None:
                continue
            codes, probs = pred
            fh.write(json.dumps({
                "doc_id": r["doc_id"], "label": r["label"], "origin": r["origin"],
                "codes": list(codes), "probs": [list(p) for p in probs],
            }, ensure_ascii=False) + "\n")
            n += 1
            if (i + 1) % 100 == 0:
                print("  %d/%d · %.1fs" % (i + 1, len(rows), time.perf_counter() - t0))
    print("[done] %d건 → %s · %.1fs" % (n, out, time.perf_counter() - t0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
