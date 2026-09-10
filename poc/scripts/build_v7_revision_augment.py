#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""v7 학습셋 — v6(판례 교정본)에 '개정'·'규정' 어휘를 가진 **기존** 고등급 행을 더한다. 신규 생성 0.

왜 이 도구가 있는가(2026-09-11). v6 는 공개 판례를 S3 로 바로잡으면서 '개정' 어휘를 가진 고등급
예시까지 걷어내, 그 문구가 S3 의 단서로 굳었다. 대상 문구 하나만 바꾸는 개입 실험에서
해당 고등급 문서 27건의 S3 판정이 100%→0%, 반대로 넣으면 0%→100% 였다
(scripts/measure_court_fix_miss_shift.py). 판례 교정은 유지하고, 그 어휘가 고등급에도 쓰인다는
예시를 기존 데이터에서 되돌려 준다.

원칙
  - 평가면과 겹치는 행은 넣지 않는다: 정리된 골든 후보 1,055 · 공개 S3 챌린지 300 · gold_real ·
    holdout. 본문 해시가 같거나, 평가면에서 흔하지 않은 정규화 문장을 3개 이상 공유하면 뺀다.
  - 후보 템플릿 문구('규정 개정 검토')가 든 행은 뺀다 — 평가면의 그 문구를 외우게 하면 개선이 부풀려진다.
  - --with-extra 로 다른 JSONL(예: 재무·인사 TS 합성)을 더해 v7b 를 만든다. 두 개입을 한 판에
    겹치지 않도록 판을 따로 만든다(개입 축을 겹치면 무엇이 효과였는지 못 가른다).

사용:
    python scripts/build_v7_revision_augment.py --out datasets/labeled_p1_v7a_revision
    python scripts/build_v7_revision_augment.py --out datasets/labeled_p1_v7b_revision_tsfh \\
        --with-extra datasets/synth_ts_fin_hr_20260911/samples.jsonl
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import io
import json
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))
sys.path.insert(0, str(_POC / "src"))

BASE = _POC / "datasets" / "labeled_p1_v6_court"
_EVAL_PATH = re.compile(r"golden_review|proxy_gold|gold_real|holdout|eval|candidates|signoff|locked|labeled_p1_v[5-9]|synth_ts_fin_hr")
_SENT = re.compile(r"[.!?\n。]+")
_TEMPLATE = "규정 개정 검토"
_VOCAB = ("개정", "규정")


def _h(t: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", t).strip().encode("utf-8")).hexdigest()


def _sents(t: str) -> set[str]:
    return {re.sub(r"\s+", "", s) for s in _SENT.split(t) if len(re.sub(r"\s+", "", s)) >= 15}


def _jsonl(p: Path) -> list[dict]:
    out = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _eval_texts() -> list[str]:
    from eval_on_clean_candidates import load_candidates  # noqa: PLC0415

    texts = [r["text"] for r in load_candidates()]
    for pat in ("datasets/proxy_gold/public_s3_challenges/**/*.jsonl", "datasets/gold_real/*.jsonl"):
        for f in glob.glob(str(_POC / pat), recursive=True):
            texts += [d.get("text") or "" for d in _jsonl(Path(f))]
    return [t for t in texts if t]


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="v7 학습셋 구성(기존 고등급 '개정' 행 보강)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-grade", type=int, default=40, help="TS·S1 각각 더할 행 수")
    ap.add_argument("--max-share-per-source", type=float, default=0.34)
    ap.add_argument("--with-extra", action="append", default=[], help="그대로 더할 JSONL(v7b 용)")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args(argv)

    train, val, test = (_jsonl(BASE / f"{s}.jsonl") for s in ("train", "val", "test"))
    seen = {_h(d.get("text") or "") for d in train + val + test}
    ev = _eval_texts()
    ev_hash = {_h(t) for t in ev}
    df: Counter = Counter()
    for t in ev:
        df.update(_sents(t))
    ev_sents = {s for s, n in df.items() if n <= 5}   # 평가면에서 흔한 상투문(서식)은 겹침으로 세지 않는다

    pool: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    drop = Counter()
    files = [f for f in glob.glob(str(_POC / "datasets" / "**" / "*.jsonl"), recursive=True)
             if not _EVAL_PATH.search(f.replace("\\", "/"))]
    for f in files:
        src = Path(f).relative_to(_POC / "datasets").parts[0]
        for d in _jsonl(Path(f)):
            g, t = d.get("label"), d.get("text") or ""
            if g not in ("TS", "S1") or not any(v in t for v in _VOCAB):
                continue
            h = _h(t)
            if h in seen:
                drop["학습·검증·시험셋과 같은 본문"] += 1
                continue
            if h in ev_hash:
                drop["평가면과 같은 본문"] += 1
                continue
            if _TEMPLATE in t:
                drop["후보 템플릿 문구"] += 1
                continue
            if len(_sents(t) & ev_sents) >= 3:
                drop["평가면과 문장 3개 이상 공유"] += 1
                continue
            seen.add(h)
            pool[g].append((src, {"text": t, "label": g, "augment_source": f.replace("\\", "/").split("poc/")[-1],
                                  "augment_reason": "v7_revision_vocab"}))

    rng = random.Random(a.seed)
    added: list[dict] = []
    for g in ("TS", "S1"):
        cands = pool[g]
        rng.shuffle(cands)
        cap = max(1, int(a.per_grade * a.max_share_per_source))
        per_src: Counter = Counter()
        for src, row in cands:
            if len([r for r in added if r["label"] == g]) >= a.per_grade:
                break
            if per_src[src] >= cap:
                continue
            per_src[src] += 1
            added.append(row)
    extra = []
    for p in a.with_extra:
        extra += [{"text": d["text"], "label": d["label"], "augment_source": p, "augment_reason": "extra"}
                  for d in _jsonl(_POC / p) if d.get("text") and d.get("label")]

    out = _POC / a.out
    out.mkdir(parents=True, exist_ok=True)
    new_train = train + added + extra
    (out / "train.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in new_train), encoding="utf-8")
    for s in ("val", "test"):
        shutil.copyfile(BASE / f"{s}.jsonl", out / f"{s}.jsonl")

    def vocab(rows):
        c = Counter()
        for r in rows:
            if "개정" in (r.get("text") or ""):
                c[r.get("label")] += 1
        return dict(c)

    manifest = {
        "base": str(BASE.relative_to(_POC)), "base_rows": len(train),
        "added_vocab_rows": dict(Counter(r["label"] for r in added)),
        "added_by_source": dict(Counter(r["augment_source"].split("/")[1] if "/" in r["augment_source"] else r["augment_source"] for r in added)),
        "extra_rows": len(extra), "extra_sources": a.with_extra,
        "train_rows": len(new_train), "dropped": dict(drop),
        "pool_size": {g: len(v) for g, v in pool.items()},
        "개정_rows_by_label_before": vocab(train), "개정_rows_by_label_after": vocab(new_train),
        "eval_texts": len(ev), "seed": a.seed,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
