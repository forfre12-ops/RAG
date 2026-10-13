#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""대용량 문서 처리시간이 **CPU 코어 수에 어떻게 줄어드는가**.

왜 필요한가(2026-09-13). PER-002("100쪽 30초")를 못 맞추는 문제에서 선택지를 둘로만 보고
있었다 — 목표를 협의하거나, 추론 GPU 를 사양에 넣거나. 그런데 실측은 **4스레드 기준**이다
(211: 100쪽 167초). 코어를 늘리면 닿는지 아무도 재지 않았다.

    완전 비례라면  8코어 84초 · 16코어 42초 · 32코어 21초
    → 32코어면 목표 안이다. **그러면 답은 "CPU 사양을 적는다" 가 된다.**

비례가 어디서 꺾이는지가 이 도구가 내는 값이다. 실제로는 메모리 대역폭·스레드 동기화 때문에
어느 지점부터 안 줄어든다. 그 지점을 알아야 "몇 코어를 요구할지"를 숫자로 말할 수 있다.

⚠ 이 PC 코어 수를 넘겨 재면 의미가 없다 — 물리 코어 안에서만 잰다.
⚠ GPU 를 끈다(`CUDA_VISIBLE_DEVICES=`). 안 끄면 스레드 수와 무관하게 GPU 로 돈다
   — 2026-09-10 에 실제로 한 번 헛measurement 했다.

사용:
    python scripts/measure_cpu_thread_scaling.py <모델경로> --pages 100 --threads 2 4 8 16
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time

# GPU 를 확실히 끈다 — torch import 전에 해야 먹는다.
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DEPLOY_PROFILE", "onprem-local")
os.environ["CLASSIFIER_DEVICE"] = "cpu"

from pathlib import Path  # noqa: E402

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

_PARA = (
    "본 문서는 사내 검토 기록으로 작성되었다. 대상 공정의 조건과 측정 결과를 함께 정리하고, "
    "예외가 발생한 구간은 별도 표본으로 분리해 원인을 확인한다. 확정되지 않은 가정은 결론과 "
    "구분해 적고, 근거가 되는 원시 기록의 위치를 함께 남긴다. "
)


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="CPU 스레드 수별 대용량 문서 처리시간")
    ap.add_argument("model")
    ap.add_argument("--pages", type=int, default=100)
    ap.add_argument("--chars-per-page", type=int, default=1800)
    ap.add_argument("--threads", type=int, nargs="+", default=[2, 4, 8, 16])
    ap.add_argument("--repeat", type=int, default=2, help="스레드 수마다 몇 번 재나(최솟값을 쓴다)")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    import torch

    from koipa.modules.m5_inference.pipeline import InferencePipeline

    cores = os.cpu_count() or 1
    text = (_PARA * ((a.pages * a.chars_per_page) // len(_PARA) + 1))[:a.pages * a.chars_per_page]
    print("이 PC 논리코어 %d개 · 문서 %d쪽(%d자) · 회차마다 %d번 재서 최솟값"
          % (cores, a.pages, len(text), a.repeat))
    if torch.cuda.is_available():
        print("⛔ GPU 가 여전히 보인다 — 이 측정은 의미가 없다. CUDA_VISIBLE_DEVICES 확인할 것")
        return 1

    rows = []
    base = None
    for n in a.threads:
        if n > cores:
            print("  %2d스레드 — 건너뜀(논리코어 %d개를 넘는다)" % (n, cores))
            continue
        torch.set_num_threads(n)
        pipe = InferencePipeline(model_dir=a.model)
        if pipe._model is None:
            print("⛔ 모델이 안 올라왔다 — 규칙 경로로 도는 가짜 측정이다")
            return 1
        pipe.run(text[:4000])  # 워밍업
        best = min(_timed(pipe, text) for _ in range(a.repeat))
        base = base or best
        rows.append({"threads": n, "seconds": round(best, 1),
                     "speedup": round(base / best, 2),
                     "pages_in_30s": int(a.pages * 30 / best)})
        print("  %2d스레드  %6.1f초   배속 %4.2f×   30초 안에 %3d쪽"
              % (n, best, base / best, rows[-1]["pages_in_30s"]))
        del pipe

    if len(rows) > 1:
        first, last = rows[0], rows[-1]
        ideal = last["threads"] / first["threads"]
        print("\n  %d→%d스레드: 이상적이면 %.1f배 · 실제 %.2f배 (효율 %.0f%%)"
              % (first["threads"], last["threads"], ideal, last["speedup"],
                 last["speedup"] / ideal * 100))

        # ⚠ 외삽하지 않는다. 첫 판은 "이 효율이면 47스레드 필요" 라고 찍었는데, 같은 표에
        #   16스레드가 8스레드보다 **느리다**고 적혀 있었다. 포화한 곡선에 일정 효율을
        #   가정해 늘리면 없는 길을 있다고 말하게 된다.
        best = min(rows, key=lambda r: r["seconds"])
        saturated = best is not rows[-1]
        print("  가장 빠른 지점: %d스레드 %.1f초 (30초 안에 %d쪽)"
              % (best["threads"], best["seconds"], best["pages_in_30s"]))
        if saturated:
            print("  ⇒ **%d스레드에서 포화한다** — 그 위로는 더 안 줄어든다(%d스레드가 오히려 느림)."
                  % (best["threads"], rows[-1]["threads"]))
            print("     코어를 더 요구해도 100쪽 30초에 닿지 않는다. CPU 사양은 답이 아니다.")
        else:
            print("  ⇒ 아직 포화하지 않았다 — 더 많은 코어에서 다시 잴 것. "
                  "이 표만으로 필요한 코어 수를 외삽하지 말 것.")

    if a.json:
        target = _POC / a.json
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(
            {"pages": a.pages, "chars": len(text), "cores": cores, "rows": rows},
            ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        print("\n기록: %s" % target)
    return 0


def _timed(pipe, text: str) -> float:
    start = time.perf_counter()
    pipe.run(text)
    return time.perf_counter() - start


if __name__ == "__main__":
    raise SystemExit(main())
