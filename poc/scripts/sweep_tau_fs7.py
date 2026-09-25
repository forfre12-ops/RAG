#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FS7 escalation τ 스윕 — 사전 등록 reports/mock_final_train_20260921/PREREG_TAU_SWEEP.md. 출력: tau_sweep_result.txt"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_r6_cross_generator import verified_docs as v6  # noqa: E402
from eval_r8_style_shift import verified as v8  # noqa: E402
from run_mock1000_cv import POC, load_jsonl, prf  # noqa: E402
from run_mock1000_pilotmix import _pipe, _predict  # noqa: E402

OUT = POC / "reports" / "mock_final_train_20260921"
TAUS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50]
SEEDS = (42, 43, 44)


def main() -> int:
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    faces = {"dev154": load_jsonl(OUT / "fs_dev_test.jsonl"), "val67": load_jsonl(OUT / "fs_val.jsonl"), "r8": v8(), "r6": v6()[0]}
    res = {f: {t: [] for t in TAUS} for f in faces}
    for s in SEEDS:
        pipe = _pipe(OUT / f"model_FS7_s{s}")
        for t in TAUS:
            settings.classifier_escalation_tau = t
            for f, rows in faces.items():
                m = prf(_predict(pipe, rows), lambda r: r["pred"])
                res[f][t].append((m["acc"], m["hi_miss"] / m["hi_n"], m["n"]))
        print(f"시드 {s} 완료", flush=True)

    def mean(f, t, i):
        v = res[f][t]
        return sum(x[i] for x in v) / len(v)
    L = ["FS7 escalation τ 스윕 (3시드 평균 · 정확도 / 고등급 하향 미탐) · 사전 등록 PREREG_TAU_SWEEP.md", "τ      " + "  ".join(f"{f:^19}" for f in faces)]
    for t in TAUS:
        L.append(f"{t:<6} " + "  ".join(f"{mean(f, t, 0):6.1%} / {mean(f, t, 1):6.1%} " for f in faces) + ("  ← 배포 프로필" if t == 0.30 else ""))
    tune = lambda t, i: (mean("dev154", t, i) * 154 + mean("val67", t, i) * 67) / 221  # noqa: E731
    base_acc, base_miss = tune(0.30, 0), tune(0.30, 1)
    ok = [t for t in TAUS if base_acc - tune(t, 0) <= 0.01]
    best = sorted(ok, key=lambda t: (tune(t, 1), abs(t - 0.30)))[0]
    L += ["", f"[튜닝 면(개발 154 + val 67 가중)] τ=0.30: 정확도 {base_acc:.1%}·미탐 {base_miss:.1%} → 선택 규칙(정확도 하락 ≤1pt 중 미탐 최저) τ={best}: 정확도 {tune(best, 0):.1%}·미탐 {tune(best, 1):.1%}"]
    vw = lambda t, i: (mean("r8", t, i) * 114 + mean("r6", t, i) * 86) / 200  # noqa: E731
    dm, da = vw(0.30, 1) - vw(best, 1), vw(0.30, 0) - vw(best, 0)
    L.append(f"[검증 면(8차 114 + 6차 86 가중)] τ=0.30: 정확도 {vw(0.30, 0):.1%}·미탐 {vw(0.30, 1):.1%} → τ={best}: 정확도 {vw(best, 0):.1%}·미탐 {vw(best, 1):.1%} (미탐 {100 * dm:+.1f}pt 감소, 정확도 {-100 * da:+.1f}pt)")
    L.append("[판정] " + ("τ=%s 채택 근거 있음(검증 면 미탐 ≥2pt 감소 & 정확도 하락 ≤1pt)" % best if best != 0.30 and dm >= 0.02 and da <= 0.01 else "τ 조정은 근거 없음 — 0.30 유지"))
    (OUT / "tau_sweep_result.txt").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
