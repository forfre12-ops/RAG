#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""배포 학습셋에 '평범한 업무문서 = S3' 를 넣는다 — 만들어 놓고 안 넣은 처방을 적용한다.

## 왜 (2026-09-14)

평범한 사내문서 500건에서 서빙 과탐이 **86.2%** 다. 기전은
`scripts/diagnose_no_evidence_overcall.py` 가 잰 대로 —
룰은 96.6% 를 맞게 보는데(rule_grade S3 483/500) 모델이 S2 라 하고(conf 중앙 0.754)
합의 게이트가 '룰 근거 0' 이라 개입하지 않는다.

그런데 이 문제를 고칠 데이터가 **이미 있다.** `scripts/gen_mundane_s3.py` 헤더:

    "모델의 S3 가 '판례체 문체' 에만 갇혀 일상문서를 과분류(80%)하는 문제를 고치기 위해
     '평범한 업무문서 = 비밀 아님(S3)' 를 가르칠 학습데이터를 만든다"

그 500건이 간 곳 — step3(제외 목록의 오염 셋) 1,350행 · v7_diverse 196건 ·
**배포 학습셋 v5_clean 0건**. `build_p1_v5_clean.py` 에 mundane 언급이 0건이다.

## ⚠ 평가면을 학습에 넣지 않는다

mundane_s3 500건은 **오늘 과탐을 잰 평가면**이다. 통째로 학습에 넣으면 그 면으로
다시 잴 수 없다(학습 겹침 → judge_model_candidate 가 판정면에서 뺀다).

그래서 본문 해시로 **결정적으로** 나눈다 — 학습 350 · 홀드아웃 150.
같은 시드·같은 규칙이면 언제 돌려도 같은 분할이 나온다.

    학습    v5_clean/train (2,042) + mundane 350 = 2,392행
    검증    v5_clean/val   (256)   그대로
    시험    v5_clean/test  (256)   그대로
    평가면  mundane_holdout 150    ← 학습에 안 들어간 것만

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/build_v8_mundane_trainset.py \
        --out datasets/labeled_v8_mundane
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parent.parent

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

BASE = "datasets/labeled_p1_v5_clean"
MUNDANE = "datasets/mundane_s3/raw.jsonl"
TRAIN_SHARE = 0.70   # 350 / 500


def _h(t: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", t or "").encode("utf-8")).hexdigest()


def _rows(p: Path) -> list[dict]:
    out = []
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def split_mundane(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """본문 해시 순서로 결정적 분할 — 시드도 난수도 쓰지 않는다."""
    tagged = []
    for r in rows:
        t = r.get("text") or ""
        if not t:
            continue
        tagged.append((_h(t), r))
    tagged.sort(key=lambda x: x[0])
    cut = int(len(tagged) * TRAIN_SHARE)
    return [r for _, r in tagged[:cut]], [r for _, r in tagged[cut:]]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="datasets/labeled_v8_mundane")
    args = ap.parse_args()

    base_train = _rows(POC / BASE / "train.jsonl")
    base_val = _rows(POC / BASE / "val.jsonl")
    base_test = _rows(POC / BASE / "test.jsonl")
    mun = _rows(POC / MUNDANE)
    if not base_train or not mun:
        print("기준 학습셋 또는 mundane 이 없다")
        return 2

    m_train, m_hold = split_mundane(mun)

    # 학습에 들어간 것이 홀드아웃에 없는지 — 자기검사(검사기가 헛돌면 초록불이 거짓말이다)
    ht = {_h(r.get("text") or "") for r in m_train}
    hh = {_h(r.get("text") or "") for r in m_hold}
    assert not (ht & hh), "분할이 겹쳤다"

    # 기존 학습셋과도 겹치지 않아야 한다
    base_h = {_h(r.get("text") or "") for r in base_train + base_val + base_test}
    leaked = ht & base_h
    assert not leaked, f"mundane 학습분이 기존 셋과 {len(leaked)}건 겹친다"

    stamped = []
    for r in m_train:
        q = dict(r)
        q.setdefault("label", "S3")
        q["label_source"] = q.get("label_source") or "synthetic_mundane"
        q["tier"] = "silver_train"      # golden_tiers.TRAIN_TIERS 가 허용하는 값
        q["added_by"] = "build_v8_mundane_trainset.py/2026-09-14"
        stamped.append(q)

    out = POC / args.out
    out.mkdir(parents=True, exist_ok=True)

    def dump(name: str, rows: list[dict]) -> None:
        (out / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    dump("train.jsonl", base_train + stamped)
    dump("val.jsonl", base_val)
    dump("test.jsonl", base_test)
    dump("mundane_holdout.jsonl", m_hold)

    lab = Counter(str(r.get("label")) for r in base_train + stamped)
    (out / "manifest.json").write_text(json.dumps({
        "built_at": "2026-09-14",
        "why": "평범한 업무문서 과탐 86.2% — gen_mundane_s3 가 만든 처방을 배포 학습셋에 적용",
        "base": BASE,
        "added": {"from": MUNDANE, "rows": len(stamped), "label": "S3"},
        "split_rule": f"본문 sha256 정렬 후 앞 {TRAIN_SHARE:.0%} 를 학습 — 난수 없음",
        "train_rows": len(base_train) + len(stamped),
        "val_rows": len(base_val), "test_rows": len(base_test),
        "mundane_holdout_rows": len(m_hold),
        "train_label_dist": dict(lab),
        "leak_checks": {"train_vs_holdout": 0, "mundane_vs_base": 0},
        "warning": ("mundane_holdout 150건은 **이 학습셋으로 학습한 모델의 평가면**이다. "
                    "기준선도 같은 150건으로 다시 잡아야 비교가 성립한다."),
        "tool": "scripts/build_v8_mundane_trainset.py",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 76)
    print(f"학습셋 생성 — {out.relative_to(POC)}")
    print("=" * 76)
    print(f"  train  {len(base_train)} + mundane {len(stamped)} = {len(base_train) + len(stamped)}행")
    print(f"  val    {len(base_val)}행 · test {len(base_test)}행 (기준셋 그대로)")
    print(f"  등급분포 {dict(lab)}")
    print()
    print(f"  mundane 홀드아웃 {len(m_hold)}건 → {out.relative_to(POC)}/mundane_holdout.jsonl")
    print("  ⚠ 기준선도 이 150건으로 다시 잡아야 비교가 성립한다")
    print()
    print("  겹침 자기검사 — 학습↔홀드아웃 0건 · mundane↔기존셋 0건 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
