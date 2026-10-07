# -*- coding: utf-8 -*-
"""ts_tie_break 발동 조건을 만족하는 문서가 평가면에 몇 건인지 센다.

실측 2026-09-14: 세 면 251건 중 **0건**. 가장 가까운 동점도 TS/S1 차이가 0.0347 로
문턱(margin 0.005)의 7배 멀다. 즉 이 손잡이는 현행 배포본·이 세 면에서 무동작이고,
서빙 A/B 의 "델타 0" 은 효과가 아니라 **판정 불가**다.

왜. 서빙 A/B 에서 세 면 모두 델타 0 이 나왔는데, 그것이
  (a) 규칙이 발동했으나 결과가 같았다  인지
  (b) 규칙이 발동할 문서가 아예 없었다  인지
  (c) 플래그가 서버에 안 걸렸다          인지
구별되지 않는다. '안 바뀐 입력은 불변이 아니라 측정 안 됨' 규율에 따라 직접 센다.

발동 조건(pipeline.py:_apply_ts_tie_break):
    label == "S1"  AND  ts > min_ts(0.05)  AND  ts + margin(0.005) >= s1
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

POC = Path(__file__).resolve()
for _ in range(6):
    POC = POC.parent
    if (POC / "src" / "koipa").is_dir():
        break
POC = Path(r"F:\antigravity\rag\poc")
sys.path.insert(0, str(POC / "src"))
os.environ.setdefault("DEPLOY_PROFILE", "onprem-local")
os.environ.setdefault("CLASSIFIER_MODEL_DIR", "artifacts/classifier_p1_v5_clean/v-fe4b386b")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.chdir(POC)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from koipa.config import settings  # noqa: E402
from koipa.modules.m5_inference.pipeline import InferencePipeline  # noqa: E402

FACES = {
    "holdout109": "datasets/gold_real/holdout_eval.jsonl",
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "golden100": "datasets/gold/golden100_labeled_v3.jsonl",
}
TEXT_KEYS = ("text", "content", "body")
MIN_TS = float(getattr(settings, "ts_tie_break_min_ts_score", 0.05))
MARGIN = float(getattr(settings, "ts_tie_break_margin", 0.005))


def rows(path: Path):
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except ValueError:
            continue


def text_of(row: dict) -> str:
    for k in TEXT_KEYS:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def main() -> int:
    print(f"model_dir = {settings.classifier_model_dir}")
    print(f"조건: label==S1 AND ts > {MIN_TS} AND ts + {MARGIN} >= s1")
    pipe = InferencePipeline(model_dir=settings.classifier_model_dir or None)
    grand = {"n": 0, "s1": 0, "fire": 0, "noscore": 0}
    for name, rel in FACES.items():
        p = POC / rel
        n = s1 = fire = noscore = 0
        near = []
        for row in rows(p):
            t = text_of(row)
            if not t:
                continue
            n += 1
            res = pipe.run(t)
            label = res.label.value if hasattr(res.label, "value") else str(res.label)
            sc = res.scores or {}
            if not sc:
                noscore += 1
                continue
            if label != "S1":
                continue
            s1 += 1
            ts_p, s1_p = float(sc.get("TS", 0.0)), float(sc.get("S1", 0.0))
            if ts_p > MIN_TS and ts_p + MARGIN >= s1_p:
                fire += 1
            near.append((round(ts_p, 4), round(s1_p, 4)))
        near.sort(key=lambda x: -(x[0]))
        print(f"\n[{name}] n={n} · S1예측={s1} · 발동={fire} · scores없음={noscore}")
        if near:
            print("   S1 예측 문서의 (TS, S1) 상위 5 — TS 가 클수록 동점에 가깝다")
            for a, b in near[:5]:
                print(f"     TS={a:.4f}  S1={b:.4f}  차={b - a:+.4f}")
        grand["n"] += n
        grand["s1"] += s1
        grand["fire"] += fire
        grand["noscore"] += noscore
    print(f"\n=== 합계 · 문서 {grand['n']}건 중 S1 예측 {grand['s1']}건 · "
          f"발동 조건 충족 {grand['fire']}건 · scores 없음 {grand['noscore']}건 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
