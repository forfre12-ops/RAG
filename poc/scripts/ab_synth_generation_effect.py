# -*- coding: utf-8 -*-
"""A/B: 지금 생성기로 만든 합성이 분류기를 낫게 하는가 — 한 번도 잰 적이 없다.

왜 필요한가(2026-09-06).
  합성 품질을 재는 자(누출 게이트·코퍼스 지표)는 갖췄는데, **그 자를 통과한 합성이
  모델을 낫게 한다는 실측이 0건**이다. 하나 있는 측정(archive/ab_qwen_gemma_s1.py)은
  게이트가 붙기 전, v6 프롬프트 세대 이전의 것이고 결과도 시드 잡음 안이었다:

      A0 증강 없음   F1 0.3178 ± 0.041   S1 회수율 0.0
      A1 qwen 60건   F1 0.3477 ± 0.041   S1 회수율 0.0625 ± 0.125
      A2 gemma 60건  F1 0.2816 ± 0.068   S1 회수율 0.0

  그 뒤로 프롬프트가 바뀌었고(등급명 금지·상황 유도) 누출 게이트가 붙었다. 그래서
  "지금 만드는 것"은 다른 물건인데, 다른지 여부를 아무도 재지 않았다.

무엇을 재는가.
  A0  기준 학습셋만                      앵커
  A1  기준 + **지금 생성기** S1 N건        한 번도 잰 적 없는 팔
  A2  기준 + 옛 qwen S1 N건 (기존 파일)    기존 실측과 대조하는 앵커

  세 팔 모두 같은 base·같은 하이퍼파라미터·같은 홀드아웃(clean42)이다. 다른 것은
  **추가된 합성 문서**뿐이라 그 효과가 격리된다.

읽는 법 — 이 셋 중 하나가 나온다.
  낫다        ①콘솔 경로 고도화·②법령 접지에 투자할 근거가 생긴다
  차이 없다    합성 신규 생성 중단 방침(2026-08-24)이 옳았다는 증거가 된다
  나쁘다      게이트를 더 조일 자리를 지목해 준다

  ⚠ 어느 쪽이든 **holdout_eval.clean(42건) 기준의 상대 비교**다. 42건은 작고 라벨이
    기계 라벨이므로 절대 성능 주장에 쓰지 않는다. 시드 4개의 표준편차를 함께 본다 —
    차이가 SD 안이면 "차이 없다"로 읽어야 한다.

전제.
  · ollama 가 떠 있고 모델이 적재돼 있을 것(생성은 ollama 가 GPU 를 쓴다)
  · 학습은 **CUDA torch 가 있는 venv** 로 돌린다. poc/.venv 는 CPU 빌드라 몇 시간이
    며칠이 된다 — 기본값은 .venv-gpu 이고 --python 으로 바꿀 수 있다.

재개 가능. 생성 파일·학습 산출물이 이미 있으면 건너뛴다.

사용:
    poc/.venv-gpu/Scripts/python.exe scripts/ab_synth_generation_effect.py --n 60 --seeds 42,43,44,45
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for _p in (str(_HERE), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        setattr(sys, _s, io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

BASE = _ROOT / "datasets" / "gold_real" / "train_subset.jsonl"
HOLDOUT = _ROOT / "datasets" / "gold_real" / "holdout_eval.clean.jsonl"
OLD_QWEN = _ROOT / "datasets" / "ab_s1" / "qwen_s1.jsonl"
WORK = _ROOT / "datasets" / "ab_synth"
ARTROOT = _ROOT / "artifacts" / "ab_synth"
REPORTS = _ROOT / "reports" / "ab_synth"
# 생성 도메인 순환. 약한 칸을 메우는 용도이므로 등급은 S1 하나로 고정한다.
DOMAINS = ["business", "finance", "hr", "legal", "tech", "mixed"]


def log(msg: str) -> None:
    print(f"[ab][{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _norm(text: str) -> str:
    return hashlib.sha1(" ".join((text or "").split())[:400].encode("utf-8")).hexdigest()


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )


def slim(rows: list[dict]) -> list[dict]:
    """트레이너는 text·label 만 읽는다."""
    return [{"text": r["text"], "label": r["label"]} for r in rows]


# ─────────────────────────────────────────────── 생성

def generate_s1(model: str, out: Path, n: int, seed: int) -> dict:
    """지금 생성기로 S1 문서 n건. 재개 가능 — 이미 n건이면 건너뛴다."""
    from koipa.adapters.llm.local_openai_provider import ollama_provider
    from koipa.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator

    existing = load_jsonl(out) if out.exists() else []
    if len(existing) >= n:
        log(f"gen[{model}] 건너뜀 — {len(existing)}건 존재")
        return {"model": model, "n_ok": len(existing), "n_fail": 0, "skipped": True}

    gen = SyntheticDocGenerator(llm=ollama_provider(model=model))
    rows, n_fail, attempts = list(existing), 0, 0
    t0 = time.time()
    cap = 3 * n
    while len(rows) < n and attempts < cap:
        attempts += 1
        dom = DOMAINS[(seed + attempts) % len(DOMAINS)]
        try:
            doc = gen.generate(SynthRequest(target_grade="S1", domain=dom, count=1))[0]
        except Exception as exc:  # noqa: BLE001 — 한 건 실패가 실행을 끝내면 안 된다
            n_fail += 1
            log(f"gen[{model}] 실패 {type(exc).__name__}: {exc}")
            continue
        body = (doc.body or "").strip()
        # 파싱 실패분(noop_fallback·llm_nonjson)은 학습에 못 들어가는 산출물이다 — 여기서 뺀다.
        if len(body) < 200 or doc.pii_violations or doc.label_source:
            n_fail += 1
            continue
        rows.append({
            "text": f"{doc.title}\n\n{body}" if doc.title else body,
            "label": "S1",
            "llm_provider": doc.llm_provider,
            "llm_model": doc.llm_model,
            "domain": dom,
        })
        if len(rows) % 10 == 0:
            write_jsonl(out, rows)
            log(f"gen[{model}] {len(rows)}/{n} ({(time.time() - t0) / max(1, len(rows)):.1f}초/건)")
    write_jsonl(out, rows)
    stat = {"model": model, "n_ok": len(rows), "n_fail": n_fail,
            "sec_total": round(time.time() - t0, 1), "skipped": False}
    log(f"gen[{model}] 완료: {stat}")
    return stat


def screen(rows: list[dict]) -> dict:
    """누출 게이트를 실제로 걸어 본다 — 무엇을 통과시키는지 기록에 남긴다."""
    from koipa.services.synth_quality import screen_batch

    res = screen_batch([(r["label"], r["text"]) for r in rows])
    log(f"gate: verdict={res['batch_verdict']} 통과={len(res['admit'])}/{len(rows)} "
        f"지표={ {k: v for k, v in res['metrics'].items() if k != 'length_by_grade'} }")
    return res


# ─────────────────────────────────────────────── 기준셋

def prepare_base(seed: int) -> tuple[Path, Path, dict]:
    """train_subset → 홀드아웃 누출 제거 → 라벨층화 90/10 분할."""
    work = WORK / f"s{seed}"
    base_train, val = work / "base_train.jsonl", work / "val.jsonl"
    rows = load_jsonl(BASE)
    holds = {_norm(r["text"]) for r in load_jsonl(HOLDOUT)}
    clean = [r for r in rows if _norm(r["text"]) not in holds]
    rng = random.Random(seed)
    by_lbl: dict[str, list[dict]] = {}
    for r in clean:
        by_lbl.setdefault(r["label"], []).append(r)
    tr: list[dict] = []
    va: list[dict] = []
    for _lbl, items in by_lbl.items():
        rng.shuffle(items)
        k = max(1, len(items) // 10)
        va += items[:k]
        tr += items[k:]
    rng.shuffle(tr)
    write_jsonl(base_train, slim(tr))
    write_jsonl(val, slim(va))
    meta = {"base_total": len(rows), "leaked_removed": len(rows) - len(clean),
            "train_n": len(tr), "val_n": len(va),
            "train_dist": dict(Counter(r["label"] for r in tr))}
    log(f"base[s{seed}] train={len(tr)} val={len(va)} 누출제거={meta['leaked_removed']}")
    return base_train, val, meta


# ─────────────────────────────────────────────── 학습·평가

def _model_dir(out_dir: Path):
    cands = sorted(out_dir.glob("v-*/model.safetensors"))
    return cands[-1].parent if cands else None


def train_arm(py: str, name: str, train_path: Path, val_path: Path,
              epochs: int, seq: int, seed: int) -> str:
    out_dir = ARTROOT / name
    found = _model_dir(out_dir)
    if found is not None:
        log(f"train[{name}] 건너뜀 — 이미 있음({found.name})")
        return str(found)
    log(f"train[{name}] 시작")
    t0 = time.time()
    env = dict(os.environ, TESTING="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [py, "scripts/p1_train_classifier.py", "--mode", "full",
         "--train-path", str(train_path), "--val-path", str(val_path),
         "--test-path", str(HOLDOUT), "--output-dir", str(out_dir),
         "--epochs", str(epochs), "--max-seq-len", str(seq),
         "--batch-size", "16", "--seed", str(seed), "--no-mlflow"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=7200, cwd=str(_ROOT), env=env,
    )
    found = _model_dir(out_dir)
    if found is None:
        log(f"train[{name}] 실패({time.time() - t0:.0f}초)\nSTDERR:\n{proc.stderr[-1200:]}")
        raise RuntimeError(f"train {name} failed")
    log(f"train[{name}] 완료 {time.time() - t0:.0f}초 -> {found.name}")
    return str(found)


def eval_arm(py: str, name: str, model_dir: str) -> dict:
    rep = REPORTS / f"{name}.json"
    rep.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TESTING="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [py, "scripts/eval_p1_holdout.py", "--model-dir", model_dir,
         "--holdout", str(HOLDOUT), "--report", str(rep)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=1800, cwd=str(_ROOT), env=env,
    )
    if not rep.exists():
        log(f"eval[{name}] 실패\n{proc.stderr[-800:]}")
        return {"error": proc.stderr[-800:]}
    out = json.loads(rep.read_text(encoding="utf-8"))["ALL"]
    log(f"eval[{name}] f1={out['f1_macro']} fnr={out['fnr_underclass']} "
        f"S1={out['per_class_recall'].get('S1')}")
    return out


# ─────────────────────────────────────────────── 실행

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="지금 생성기 합성이 분류기를 낫게 하는가")
    ap.add_argument("--n", type=int, default=60, help="생성할 S1 문서 수")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--seq", type=int, default=256)
    ap.add_argument("--seeds", default="42,43,44,45")
    ap.add_argument("--model", default="qwen3:14b", help="생성 LLM(ollama)")
    ap.add_argument("--python", default=str(_ROOT / ".venv-gpu" / "Scripts" / "python.exe"),
                    help="학습·평가를 돌릴 인터프리터. CPU 빌드 torch 면 몇 시간이 며칠이 된다")
    args = ap.parse_args(argv)

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    py = args.python
    log(f"=== 합성 증강 효과 A/B (n={args.n} epochs={args.epochs} seeds={seeds}) ===")
    log(f"학습 인터프리터: {py}")

    # 1) 생성 — 시드와 무관하게 한 번만. 이 문서들이 '처치'다.
    cur = WORK / "current_s1.jsonl"
    gstat = generate_s1(args.model, cur, args.n, seeds[0])
    cur_rows = load_jsonl(cur)
    gate = screen(cur_rows)
    admitted = [cur_rows[i] for i in gate["admit"]]
    old_rows = load_jsonl(OLD_QWEN) if OLD_QWEN.exists() else []
    log(f"팔 구성: 현행 {len(admitted)}건(게이트 통과) · 옛 qwen {len(old_rows)}건")

    per_seed: dict[str, list[dict]] = {}
    for seed in seeds:
        base_train, val, _meta = prepare_base(seed)
        base_rows = load_jsonl(base_train)
        work = WORK / f"s{seed}"
        arms = {
            f"A0_base_s{seed}": base_rows,
            f"A1_current_s{seed}": base_rows + slim(admitted),
            f"A2_old_qwen_s{seed}": base_rows + slim(old_rows),
        }
        for name, rows in arms.items():
            if name.startswith("A2") and not old_rows:
                continue
            path = work / f"{name}.jsonl"
            write_jsonl(path, rows)
            model_dir = train_arm(py, name, path, val, args.epochs, args.seq, seed)
            res = eval_arm(py, name, model_dir)
            per_seed.setdefault(name.rsplit("_s", 1)[0], []).append({"seed": seed, **res})

    # 2) 종합 — 평균과 표준편차를 함께 본다. 차이가 SD 안이면 '차이 없다'다.
    agg: dict[str, dict] = {}
    for arm, runs in per_seed.items():
        f1 = [r["f1_macro"] for r in runs if "f1_macro" in r]
        s1 = [r["per_class_recall"]["S1"] for r in runs if "per_class_recall" in r]
        fnr = [r["fnr_underclass"] for r in runs if "fnr_underclass" in r]
        agg[arm] = {
            "n_runs": len(f1),
            "f1_mean": round(statistics.mean(f1), 4) if f1 else None,
            "f1_sd": round(statistics.pstdev(f1), 4) if len(f1) > 1 else 0.0,
            "s1_mean": round(statistics.mean(s1), 4) if s1 else None,
            "s1_sd": round(statistics.pstdev(s1), 4) if len(s1) > 1 else 0.0,
            "fnr_mean": round(statistics.mean(fnr), 4) if fnr else None,
        }

    summary = {
        "config": {"n": args.n, "epochs": args.epochs, "seq": args.seq,
                   "seeds": seeds, "model": args.model, "python": py},
        "generation": gstat,
        "gate": {"verdict": gate["batch_verdict"], "admitted": len(admitted),
                 "generated": len(cur_rows),
                 "metrics": {k: v for k, v in gate["metrics"].items() if k != "length_by_grade"}},
        "per_seed": per_seed,
        "agg": agg,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    log("================ 결과 ================")
    for arm in ("A0_base", "A1_current", "A2_old_qwen"):
        a = agg.get(arm)
        if not a:
            continue
        log(f"{arm:14s} F1 {a['f1_mean']} ± {a['f1_sd']}   "
            f"S1회수 {a['s1_mean']} ± {a['s1_sd']}   미탐 {a['fnr_mean']}  (n={a['n_runs']})")
    log("차이가 SD 안이면 '차이 없다'로 읽을 것. 42건 기준 상대 비교이며 절대 성능이 아니다.")
    log(f"요약 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
