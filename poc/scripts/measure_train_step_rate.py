#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""학습 스텝 속도(s/it)를 실제 학습을 띄워 재고, 총 소요를 스텝 수로 환산한다.

왜 이 도구인가(2026-08-30). 재학습 소요를 물을 때마다 근거가 코드 주석의 옛 로그 한 줄
(`51/1280 [33:33<12:42:44, 37.24s/it]`)뿐이었고, 그 값이 나온 하드웨어 조건이 문서에서
빠졌다 붙었다 했다. 소요는 하드웨어로 갈리므로 그 서버에서 다시 재는 것이 답이다.

전체를 완주시키지 않는다. 30스텝의 s/it 만 읽고 중단한 뒤 스텝 수로 환산한다 —
스텝 수는 ceil(행수 / batch_size) x epochs 로 정해지므로 환산이 성립한다.
환산값은 학습 구간만이다. 에폭마다 도는 평가와 체크포인트 저장은 빠져 있으므로
실제 벽시계 시간은 이보다 길다.

    cd poc && python scripts/measure_train_step_rate.py --rows 1000 --threads 8
    cd poc && python scripts/measure_train_step_rate.py --rows 1000 2042 --threads 4 8 --json out.json

측정 조건은 결과에 함께 남긴다 — CPU capability(AVX 지원)·스레드·행수·batch·epochs.
같은 숫자를 다른 서버에서 얻으려면 그 서버에서 이 명령을 돌린다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent

CHILD = """
import sys, os
sys.path.insert(0, {src!r})
import torch
torch.set_num_threads({threads})
# CPU 실측이다. CUDA_VISIBLE_DEVICES 를 비워도 is_available() 은 True 를 돌려주고
# (device_count 는 0) trainer.py 의 is_bf16_supported() 가 Invalid device id 로 죽는다.
torch.cuda.is_available = lambda: False
from koipa.modules.m4_training.trainer import TrainSpec, train_classifier
print("CAP=%s THREADS=%d" % (
    torch.backends.cpu.get_cpu_capability(), torch.get_num_threads()), file=sys.stderr, flush=True)
train_classifier(TrainSpec(
    train_path={train!r}, val_path={val!r}, test_path={val!r}, output_dir={out!r},
    epochs={epochs}, batch_size={batch}, max_seq_len={max_seq},
    use_mlflow=False, bf16=False,
))
"""

_BAR = re.compile(r"(\d+)/(\d+)\s*\[([0-9:]+)<([0-9:]+),\s*([\d.]+)(s/it|it/s)")


def make_subset(src: Path, n: int, out: Path, seed: int = 20260829) -> int:
    """등급 비율을 유지한 n행 부분집합. n 이 전체 이상이면 원본을 그대로 쓴다."""
    rows = [json.loads(line) for line in src.open(encoding="utf-8")]
    if n >= len(rows):
        picked = rows
    else:
        by = defaultdict(list)
        for r in rows:
            by[r["label"]].append(r)
        rnd = random.Random(seed)
        picked = []
        for _, rs in sorted(by.items()):
            picked += rnd.sample(rs, min(round(n * len(rs) / len(rows)), len(rs)))
        rnd.shuffle(picked)
        picked = picked[:n]
    with out.open("w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(picked)


def measure(train: Path, val: Path, out_dir: Path, *, threads: int, rows: int,
            epochs: int, batch: int, max_seq: int, settle_steps: int,
            budget_s: float, verbose: bool) -> dict:
    expect = math.ceil(rows / batch) * epochs
    src = CHILD.format(src=str(_POC / "src"), threads=threads, train=str(train),
                       val=str(val), out=str(out_dir), epochs=epochs,
                       batch=batch, max_seq=max_seq)
    env = dict(os.environ)
    env.update({
        "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": str(threads),
        "MKL_NUM_THREADS": str(threads), "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1", "TOKENIZERS_PARALLELISM": "false",
    })
    t0 = time.time()
    proc = subprocess.Popen([sys.executable, "-c", src], env=env, cwd=str(_POC.parent),
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, bufsize=0)
    buf, last, cap = b"", None, None
    try:
        while time.time() - t0 < budget_s:
            ch = proc.stderr.read(1)
            if not ch:
                if proc.poll() is not None:
                    break
                continue
            buf += ch
            if ch not in (b"\r", b"\n"):
                continue
            line, buf = buf.decode("utf-8", "replace").strip(), b""
            if line.startswith("CAP="):
                cap = line
            m = _BAR.search(line)
            # 총계로 학습 막대를 가려낸다 — 토큰화 map 막대는 총계가 행수다.
            if not m or int(m.group(2)) != expect:
                continue
            step, rate, unit = int(m.group(1)), float(m.group(5)), m.group(6)
            last = {"step": step, "s_per_it": round(rate if unit == "s/it" else 1 / rate, 3)}
            if verbose:
                print("    step %d · %.2f s/it" % (step, last["s_per_it"]), flush=True)
            if step >= settle_steps:
                break
    finally:
        proc.kill()
    if last is None:
        tail = (buf + (proc.stderr.read() or b"")).decode("utf-8", "replace")
        raise SystemExit("학습 막대를 못 읽었다 — 자식 stderr 끝부분:\n%s" % tail[-2000:])
    sit = last["s_per_it"]
    cap_txt = (cap or "").split()[0][4:] if cap else "unknown"
    return {
        "rows": rows, "threads": threads, "epochs": epochs, "batch_size": batch,
        "max_seq_len": max_seq, "cpu_capability": cap_txt,
        "steps": expect, "s_per_it": sit, "settled_at_step": last["step"],
        "train_only_hours": round(expect * sit / 3600.0, 2),
        "measured_wall_s": round(time.time() - t0, 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=None, help="기본: settings.training_dataset_dir/train.jsonl")
    ap.add_argument("--rows", type=int, nargs="+", default=[1000])
    ap.add_argument("--threads", type=int, nargs="+", default=[os.cpu_count() or 4])
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--max-seq-len", type=int, default=512)
    ap.add_argument("--settle-steps", type=int, default=30, help="이 스텝까지 보고 멈춘다")
    ap.add_argument("--budget-s", type=float, default=1200)
    ap.add_argument("--json", default=None)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    if a.dataset:
        src = Path(a.dataset)
    else:
        sys.path.insert(0, str(_POC / "src"))
        from koipa.config import settings  # noqa: PLC0415
        src = _POC / settings.training_dataset_dir / "train.jsonl"
    if not src.exists():
        raise SystemExit("학습셋이 없다: %s" % src)

    work = _POC / "artifacts_out" / "_steprate"
    work.mkdir(parents=True, exist_ok=True)
    val = work / "val_64.jsonl"
    with val.open("w", encoding="utf-8") as f:
        for i, line in enumerate((src.parent / "val.jsonl").open(encoding="utf-8")):
            if i >= 64:
                break
            f.write(line)

    out = []
    for n in a.rows:
        sub = work / ("train_%d.jsonl" % n)
        actual = make_subset(src, n, sub)
        for t in a.threads:
            if not a.quiet:
                print("측정 — %d행 · %d스레드 (스텝 %d)"
                      % (actual, t, math.ceil(actual / a.batch_size) * a.epochs), flush=True)
            out.append(measure(sub, val, work / ("out_%d_t%d" % (n, t)),
                               threads=t, rows=actual, epochs=a.epochs,
                               batch=a.batch_size, max_seq=a.max_seq_len,
                               settle_steps=a.settle_steps, budget_s=a.budget_s,
                               verbose=not a.quiet))

    print()
    print("%-7s %-7s %-8s %-9s %-7s %s" % ("행수", "스레드", "SIMD", "s/it", "스텝", "학습 구간"))
    for r in out:
        print("%-8d %-8d %-9s %-10.2f %-8d %.1f시간"
              % (r["rows"], r["threads"], r["cpu_capability"], r["s_per_it"],
                 r["steps"], r["train_only_hours"]))
    print()
    print("※ 학습 구간만이다 — 에폭별 평가와 체크포인트 저장은 빠져 있다.")
    print("※ 스텝 수 = ceil(행수 / batch_size) x epochs")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print("기록 %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
