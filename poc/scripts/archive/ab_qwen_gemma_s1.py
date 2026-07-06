"""A/B: Qwen vs Gemma S1 합성 증강이 S1 recall(약점)을 올리는가?

설계 (동일 base·동일 하이퍼파라미터, 생성기만 다름 → 생성기 효과 격리):
  A0  base only            : train_subset (de-leaked)
  A1  base + N Qwen  S1     : train_subset + qwen3:14b  생성 S1 N건
  A2  base + N Gemma S1     : train_subset + gemma3:12b 생성 S1 N건
각 arm을 holdout_eval.clean(42)에서 eval_p1_holdout.py(direct, raw 모델)로 평가.
A1 vs A2를 같은 N에서 비교 → "Qwen vs Gemma 생성기" 직답. A0가 기준 앵커.

resumable: 생성 JSONL·학습 model_dir이 이미 있으면 건너뜀.
생성 후 ollama stop으로 VRAM 회수 후 학습(5070 Ti 16GB 공존).

실행:
  .venv/Scripts/python.exe scripts/ab_qwen_gemma_s1.py --n 60 --epochs 4
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
for p in (str(_HERE), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

# Windows cp949 콘솔에서 비-cp949 문자(em-dash 등) 출력 크래시 방지.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

BASE = Path("datasets/gold_real/train_subset.jsonl")
HOLDOUT = Path("datasets/gold_real/holdout_eval.clean.jsonl")
WORK = Path("datasets/ab_s1")
ARTROOT = Path("artifacts/ab_s1")
REPORTS = Path("reports/ab_s1")
DOMAINS = ["business", "finance", "hr", "legal", "tech", "rnd", "supply", "compliance"]
PY = sys.executable


def log(msg: str) -> None:
    print(f"[ab][{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _norm(text: str) -> str:
    return hashlib.sha1(" ".join((text or "").split())[:400].encode("utf-8")).hexdigest()


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def write_jsonl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def prepare_base(seed: int) -> tuple[Path, Path, dict]:
    """train_subset 로드 → holdout 누출 제거 → 라벨층화 train/val 분할."""
    base_train = WORK / "base_train.jsonl"
    val = WORK / "val.jsonl"
    rows = load_jsonl(BASE)
    holds = {_norm(r["text"]) for r in load_jsonl(HOLDOUT)}
    clean = [r for r in rows if _norm(r["text"]) not in holds]
    leaked = len(rows) - len(clean)
    # 라벨층화 90/10
    import random
    rng = random.Random(seed)
    by_lbl: dict[str, list[dict]] = {}
    for r in clean:
        by_lbl.setdefault(r["label"], []).append(r)
    tr: list[dict] = []
    va: list[dict] = []
    for lbl, items in by_lbl.items():
        rng.shuffle(items)
        k = max(1, len(items) // 10)
        va += items[:k]
        tr += items[k:]
    rng.shuffle(tr)
    # text/label 만 남김(트레이너는 그 둘만 읽음)
    slim = lambda rs: [{"text": r["text"], "label": r["label"]} for r in rs]
    write_jsonl(base_train, slim(tr))
    write_jsonl(val, slim(va))
    meta = {
        "base_total": len(rows), "leaked_removed": leaked, "clean": len(clean),
        "train_n": len(tr), "val_n": len(va),
        "train_dist": dict(Counter(r["label"] for r in tr)),
        "val_dist": dict(Counter(r["label"] for r in va)),
    }
    log(f"base prepared: train={len(tr)} val={len(va)} leaked_removed={leaked} dist={meta['train_dist']}")
    return base_train, val, meta


def generate_s1(model: str, out: Path, n: int, seed: int) -> dict:
    """model로 S1 문서 n건 생성(도메인 순환). resumable: 이미 n건이면 skip."""
    from lloydk.adapters.llm.local_openai_provider import ollama_provider
    from lloydk.modules.m1_synthesis.generator import SyntheticDocGenerator, SynthRequest

    out.parent.mkdir(parents=True, exist_ok=True)
    existing = load_jsonl(out) if out.exists() else []
    if len(existing) >= n:
        log(f"gen[{model}] skip — {len(existing)}건 존재")
        bodies = [r["text"] for r in existing]
        return {"model": model, "n_ok": len(existing), "n_fail": 0, "skipped": True,
                "distinct_2": round(_distinct_n(bodies, 2), 4)}

    gen = SyntheticDocGenerator(llm=ollama_provider(model=model))
    rows = list(existing)
    n_fail = 0
    t0 = time.time()
    attempts = 0
    cap = 3 * n
    while len(rows) < n and attempts < cap:
        attempts += 1
        dom = DOMAINS[(seed + attempts) % len(DOMAINS)]
        try:
            d = gen.generate(SynthRequest(target_grade="S1", domain=dom, count=1))[0]
        except Exception as e:  # noqa: BLE001
            n_fail += 1
            log(f"gen[{model}] err {type(e).__name__}")
            continue
        body = (d.body or "").strip()
        if len(body) < 200 or d.pii_violations:
            n_fail += 1
            continue
        text = f"{d.title}\n\n{body}" if d.title else body
        rows.append({"text": text, "label": "S1", "llm_provider": d.llm_provider,
                     "domain": dom, "parse_error": d.parse_error})
        if len(rows) % 10 == 0:
            write_jsonl(out, rows)  # 중간 저장(resumable)
            log(f"gen[{model}] {len(rows)}/{n}  ({(time.time()-t0)/len(rows):.1f}s/건)")
    write_jsonl(out, rows)
    bodies = [r["text"] for r in rows]
    stat = {"model": model, "n_ok": len(rows), "n_fail": n_fail,
            "sec_total": round(time.time() - t0, 1),
            "distinct_2": round(_distinct_n(bodies, 2), 4)}
    log(f"gen[{model}] done: {stat}")
    return stat


def _distinct_n(texts: list[str], n: int = 2) -> float:
    grams = []
    for t in texts:
        toks = t.split()
        grams += [tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)]
    return len(set(grams)) / len(grams) if grams else 0.0


def ollama_stop(model: str) -> None:
    try:
        subprocess.run(["ollama", "stop", model], capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001
        pass


def _find_model_dir(out_dir: Path):
    """트레이너는 output_dir/v-<해시>/model.safetensors 에 저장. 그 버전 폴더 반환."""
    cands = sorted(out_dir.glob("v-*/model.safetensors"))
    return cands[-1].parent if cands else None


def train_arm(name: str, train_path: Path, val_path: Path, epochs: int, seq: int, seed: int) -> str:
    """arm 학습 — 별도 프로세스(p1_train_classifier CLI)로 GPU 메모리 격리.
    resumable: output_dir/v-*/model.safetensors 있으면 skip. 버전 model_dir 반환."""
    out_dir = ARTROOT / name
    existing = _find_model_dir(out_dir)
    if existing is not None:
        log(f"train[{name}] skip — 이미 학습됨 ({existing.name})")
        return str(existing)
    log(f"train[{name}] 시작 train={train_path}")
    t0 = time.time()
    r = subprocess.run(
        [PY, "scripts/p1_train_classifier.py", "--mode", "full",
         "--train-path", str(train_path), "--val-path", str(val_path),
         "--test-path", str(HOLDOUT), "--output-dir", str(out_dir),
         "--epochs", str(epochs), "--max-seq-len", str(seq),
         "--batch-size", "16", "--no-mlflow"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
    )
    md = _find_model_dir(out_dir)
    if md is None:
        log(f"train[{name}] FAIL ({time.time()-t0:.0f}s)\nSTDERR tail:\n{r.stderr[-800:]}")
        raise RuntimeError(f"train {name} failed")
    log(f"train[{name}] done {time.time()-t0:.0f}s -> {md.name}")
    return str(md)


def eval_arm(name: str, model_dir: str) -> dict:
    rep = REPORTS / f"{name}.json"
    rep.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [PY, "scripts/eval_p1_holdout.py", "--model-dir", model_dir,
         "--holdout", str(HOLDOUT), "--report", str(rep)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    if not rep.exists():
        log(f"eval[{name}] FAIL\n{r.stderr[-500:]}")
        return {"error": r.stderr[-500:]}
    out = json.loads(rep.read_text(encoding="utf-8"))["ALL"]
    log(f"eval[{name}] f1={out['f1_macro']} fnr={out['fnr_underclass']} S1_recall={out['per_class_recall']['S1']}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60, help="모델당 생성할 S1 문서 수")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--seq", type=int, default=256)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    log(f"=== A/B Qwen vs Gemma S1 augmentation (n={args.n} epochs={args.epochs} seq={args.seq}) ===")
    base_train, val, base_meta = prepare_base(args.seed)

    # 1) 생성
    qwen_s1 = WORK / "qwen_s1.jsonl"
    gemma_s1 = WORK / "gemma_s1.jsonl"
    gstat_q = generate_s1("qwen3:14b", qwen_s1, args.n, args.seed)
    gstat_g = generate_s1("gemma3:12b", gemma_s1, args.n, args.seed + 1)
    ollama_stop("qwen3:14b")
    ollama_stop("gemma3:12b")

    # 2) arm 학습셋 구성
    base_rows = load_jsonl(base_train)
    slim = lambda rs: [{"text": r["text"], "label": r["label"]} for r in rs]
    a0 = WORK / "train_A0.jsonl"; write_jsonl(a0, base_rows)
    a1 = WORK / "train_A1.jsonl"; write_jsonl(a1, base_rows + slim(load_jsonl(qwen_s1)))
    a2 = WORK / "train_A2.jsonl"; write_jsonl(a2, base_rows + slim(load_jsonl(gemma_s1)))
    log(f"arms: A0={len(base_rows)} A1={len(base_rows)+gstat_q['n_ok']} A2={len(base_rows)+gstat_g['n_ok']}")

    # 3) 학습 + 4) 평가
    results = {}
    for name, tp in [("A0_base", a0), ("A1_qwen", a1), ("A2_gemma", a2)]:
        md = train_arm(name, tp, val, args.epochs, args.seq, args.seed)
        results[name] = eval_arm(name, md)

    # 5) 종합
    summary = {
        "config": {"n": args.n, "epochs": args.epochs, "seq": args.seq, "seed": args.seed},
        "base": base_meta,
        "generation": {"qwen": gstat_q, "gemma": gstat_g},
        "results": results,
    }
    out = REPORTS / "summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # 표 출력
    def row(name):
        r = results.get(name, {})
        pc = r.get("per_class_recall", {})
        return (f"{name:10s} f1={r.get('f1_macro','-'):<7} fnr={r.get('fnr_underclass','-'):<7} "
                f"TS={pc.get('TS','-'):<6} S1={pc.get('S1','-'):<6} S2={pc.get('S2','-'):<6} S3={pc.get('S3','-')}")
    log("================ RESULT ================")
    for n in ("A0_base", "A1_qwen", "A2_gemma"):
        log(row(n))
    log(f"diversity distinct-2: qwen={gstat_q['distinct_2']} gemma={gstat_g['distinct_2']}")
    log(f"summary -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
