#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""등급 서명 배치를 만든다 — 모집단·층화를 **먼저 잠그고** 모델 예측을 숨긴다.

## 왜 (2026-09-14)

"고등급 36건 중 미탐 1건이니 10건만 더 서명받으면 요건을 주장할 수 있다" 고 말했다가
바로 철회했다. **모델 결과를 본 뒤에 표본을 보강하면 선택 편향**이다. 올바른 순서는

    모집단·층화 기준 잠금 → 모델 예측 숨김 → 권한 있는 검토자가 서명 → GOLD 잠금
    → 배포 모델로 한 번 평가 → 학습에서 영구 제외

이 도구는 그 순서를 **파일 구조로 강제한다.** 선정은 잠근 모집단 + 고정 시드로만 하고,
검수자에게 나가는 팩에는 라벨·예측·문서종류가 **들어갈 자리가 없다**.

## 표면 칸을 뺀다

2026-08-08 품질 파일럿 팩(`datasets/proxy_gold/blind_quality_pilot/`)에는
`document_type` 이 그대로 실려 있었다. 품질 점수를 묻는 팩이라 문제가 없었지만,
**등급을 묻는 팩에서는 그것이 답을 알려준다** — 문서종류 단서만으로 등급 정확도가
99.9% 까지 나온 실측이 있다(2026-09-12). 그래서 팩에는 본문과 review_id 만 넣는다.

## 학습 겹침은 제외하고, 제외 건수를 남긴다

서명받은 뒤 "그거 학습셋에 있던 건데요" 가 되면 그 배치는 통째로 폐기다.
[[eval_authority]] 게이트가 `training_overlap_count > 0` 을 BLOCKED 로 잡는다.

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/build_signoff_batch.py \
        --source "datasets/proxy_gold/**/*.jsonl" \
        --require-origin public_real,organization_real,customer_real \
        --grade TS,S1 --n 100 --seed 20260914 \
        --out datasets/signoff_batch/b20260914

모집단이 비면 **배치를 만들지 않고** 무엇이 모자란지 적는다 — 그게 오늘의 상태다.
"""
from __future__ import annotations

import argparse
import glob as globmod
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

#: 지금 학습에 쓰이는 풀 — 여기 있는 본문은 서명 대상에서 뺀다.
TRAIN_POOLS = (
    "datasets/gold_real/train_subset.jsonl",
    "datasets/labeled_p1_v5_clean/train.jsonl",
    "datasets/labeled_p1_v5_clean/val.jsonl",
    "datasets/labeled_p1_v5_clean/test.jsonl",
)

TEXT_KEYS = ("text", "content", "body")
LABEL_KEYS = ("label", "target", "grade", "gold", "y")

#: 검수자에게 나가면 안 되는 칸. 등급을 알려주거나 추측하게 만든다.
FORBIDDEN_IN_PACK = (
    "label", "target", "grade", "gold", "y", "intended_label", "expected_grade",
    "document_type", "label_source", "label_provenance", "predicted", "confidence",
    "scores", "model_grade", "rule_grade", "evidence_spans", "grade_rationale",
    "llm_rationale", "why_ts", "candidate_grades", "reason",
)

MIN_CHARS = 50


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def _h(t: str) -> str:
    return hashlib.sha256(_norm(t).encode("utf-8")).hexdigest()


def _rows(path: Path):
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _text(row: dict) -> str:
    for k in TEXT_KEYS:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _label(row: dict) -> str:
    for k in LABEL_KEYS:
        v = row.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _train_hashes() -> set[str]:
    out: set[str] = set()
    for rel in TRAIN_POOLS:
        for row in _rows(POC / rel):
            t = _text(row)
            if t:
                out.add(_h(t))
    return out


def collect_population(sources: list[str], *, origins: set[str], grades: set[str],
                       train: set[str]) -> tuple[list[dict], Counter]:
    """모집단을 만든다. **제외 사유를 전부 센다** — 0건이면 왜 0건인지 말해야 한다."""
    drop: Counter = Counter()
    seen: dict[str, dict] = {}
    for pattern in sources:
        for p in globmod.glob(str(POC / pattern), recursive=True):
            path = Path(p)
            if path.suffix != ".jsonl":
                continue
            for row in _rows(path):
                t = _text(row)
                if not t or len(t) < MIN_CHARS:
                    drop["본문 없음·너무 짧음"] += 1
                    continue
                lb = _label(row)
                if grades and lb not in grades:
                    drop[f"등급 대상 아님({lb or '없음'})"] += 1
                    continue
                origin = str(row.get("document_origin") or "")
                if origins and origin not in origins:
                    drop[f"출처 대상 아님({origin or '없음'})"] += 1
                    continue
                hh = _h(t)
                if hh in train:
                    drop["학습셋에 있음"] += 1
                    continue
                if hh in seen:
                    drop["본문 중복"] += 1
                    continue
                seen[hh] = {
                    "text_sha256": hh,
                    "text": t,
                    "origin": origin,
                    "hidden_label": lb,
                    "source_path": path.relative_to(POC).as_posix(),
                    "original_doc_id": str(row.get("doc_id") or row.get("id") or ""),
                    "source_reference": row.get("source_reference"),
                    "source_agency": row.get("source_agency"),
                    "retrieved_at": row.get("retrieved_at"),
                }
    return list(seen.values()), drop


def stratified_pick(pool: list[dict], n: int, seed: int, by: str) -> tuple[list[dict], dict]:
    """층화 추출. 층은 **모델 출력이 아니라 문서 속성**으로만 나눈다."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in pool:
        buckets[str(r.get(by) or "(없음)")].append(r)
    rng = random.Random(seed)
    for v in buckets.values():
        v.sort(key=lambda r: r["text_sha256"])   # 시드 없이도 결정적
        rng.shuffle(v)
    picked: list[dict] = []
    plan: dict[str, int] = {}
    keys = sorted(buckets)
    if not keys:
        return [], {}
    per = max(1, n // len(keys))
    for k in keys:
        take = min(per, len(buckets[k]))
        plan[k] = take
        picked.extend(buckets[k][:take])
    # 남는 자리는 큰 층에서 채운다
    i = 0
    while len(picked) < n:
        k = keys[i % len(keys)]
        nxt = plan[k]
        if nxt < len(buckets[k]):
            picked.append(buckets[k][nxt])
            plan[k] += 1
        elif all(plan[x] >= len(buckets[x]) for x in keys):
            break
        i += 1
    return picked[:n], plan


def write_batch(out: Path, picked: list[dict], manifest: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    pack, key, answer = [], [], []
    for i, r in enumerate(picked, 1):
        rid = f"SG{i:04d}"
        # ⚠ 팩에는 본문과 id 만 — 금지 칸은 애초에 담지 않는다
        pack.append({"review_id": rid, "text": r["text"]})
        key.append({
            "review_id": rid, "text_sha256": r["text_sha256"],
            "original_doc_id": r["original_doc_id"], "source_path": r["source_path"],
            "origin": r["origin"], "hidden_label": r["hidden_label"],
        })
        answer.append({
            "review_id": rid,
            "grade": None,               # TS/S1/S2/S3 또는 "판정보류"
            "secrecy": None, "value": None, "management": None,   # 0/1/2 또는 "확인 안 됨"
            "evidence_start": None, "evidence_end": None, "evidence_quote": None,
            "rule_id": None,             # 적용한 규칙
            "not_higher_reason": None,   # 왜 한 등급 위가 아닌가
            "not_lower_reason": None,    # 왜 한 등급 아래가 아닌가
            "policy_version": None,
            "signer_id": None,
            "signer_role": None,         # holder | client | orderer | vendor
            "signed_at": None,
        })

    def _dump(name: str, rows: list[dict]) -> None:
        (out / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    _dump("review_pack.jsonl", pack)
    _dump("blind_key.jsonl", key)
    _dump("answer_template.jsonl", answer)
    (out / "population_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "REVIEW_INSTRUCTIONS.md").write_text(_INSTRUCTIONS, encoding="utf-8")

    # 팩에 금지 칸이 새지 않았는지 자기검사 — 검사기가 헛돌면 초록불이 거짓말이 된다
    leaked = {k for row in pack for k in row if k in FORBIDDEN_IN_PACK}
    if leaked:
        raise RuntimeError(f"팩에 금지 칸이 실렸다: {sorted(leaked)}")


_INSTRUCTIONS = """# 등급 서명 안내

`review_pack.jsonl` 만 검수자에게 제공하십시오.
`blind_key.jsonl` 은 **모든 서명이 끝나기 전까지** 제공하지 마십시오.

## 적어야 하는 것 (`answer_template.jsonl`)

문서마다 아래를 채웁니다. **모르면 비워 두지 말고 "확인 안 됨" 이라고 적으십시오** —
추측해서 고르면 등급이 바뀝니다.

| 칸 | 무엇 |
|---|---|
| `grade` | TS · S1 · S2 · S3 · 판정보류 |
| `secrecy` / `value` / `management` | 0 · 1 · 2 · 확인 안 됨 |
| `evidence_start` / `evidence_end` / `evidence_quote` | 그렇게 본 근거가 본문 어디인가 |
| `rule_id` | 적용한 규칙 |
| `not_higher_reason` / `not_lower_reason` | 왜 한 등급 위·아래가 아닌가 |
| `policy_version` | 어느 판 기준으로 판단했는가 |
| `signer_id` / `signer_role` | 누가 · 어느 자격으로 |

## 지켜야 할 것

- 문서의 출처나 모델 예측을 **추정하지 마십시오.** 본문과 주어진 사실만 봅니다.
- 다른 검수자의 답을 보지 마십시오. 일치도를 재려면 독립 판단이어야 합니다.
- `signer_role` 은 `holder`(비밀 보유 기업) · `client`(고객사) · `orderer`(발주처) ·
  `vendor`(수행사) 중 하나입니다. **vendor 서명은 GOLD 로 치지 않습니다** — 자기채점입니다.
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", required=True, help='예: "datasets/**/*.jsonl"')
    ap.add_argument("--require-origin", default="",
                    help="쉼표 구분. 비우면 출처 제한 없음(권장하지 않음)")
    ap.add_argument("--grade", default="TS,S1", help="대상 등급(숨긴 라벨 기준). 비우면 전부")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260914)
    ap.add_argument("--strata", default="origin", help="층화 기준 필드")
    ap.add_argument("--out", default=None, help="배치 폴더")
    args = ap.parse_args()

    origins = {x.strip() for x in args.require_origin.split(",") if x.strip()}
    grades = {x.strip() for x in args.grade.split(",") if x.strip()}

    print("학습풀 적재 중…")
    train = _train_hashes()
    print(f"  학습풀 고유본문 {len(train):,}건")
    print()
    print("모집단 수집 — 조건을 **먼저** 잠근다")
    print(f"  source={args.source} · origin={sorted(origins) or '(제한없음)'} · "
          f"grade={sorted(grades) or '(전부)'} · seed={args.seed} · 층화={args.strata}")
    pool, drop = collect_population(args.source, origins=origins, grades=grades, train=train)

    print()
    print("=" * 78)
    print(f"모집단 {len(pool)}건")
    print("=" * 78)
    for k, v in drop.most_common(12):
        print(f"  제외 {v:>8,}  {k}")

    manifest = {
        "locked_at_seed": args.seed,
        "sources": args.source,
        "require_origin": sorted(origins),
        "target_grades": sorted(grades),
        "strata_field": args.strata,
        "requested_n": args.n,
        "population_size": len(pool),
        "excluded": dict(drop),
        "train_pools": list(TRAIN_POOLS),
        "train_pool_hashes": len(train),
        "note": "모집단·층화는 모델 예측을 보기 전에 잠갔다. 팩에는 라벨·예측·문서종류가 없다.",
    }

    if not pool:
        print()
        print("⛔ 모집단이 0건이라 배치를 만들지 않는다.")
        print("   무엇이 있어야 하는가 —")
        print("   · 서명 대상은 **실문서**여야 한다(document_origin 이 public/organization/customer_real).")
        print("   · 그런데 고등급(TS/S1) 고유본문 50,685건 중 실문서 표기는 고유 1건이고")
        print("     그마저 학습셋에 있다(실측 2026-09-14). 즉 지금 리포에 후보가 없다.")
        print("   · 판례는 본문이 실문서지만 우리 입장이 '정답 S3' 이라 고등급 후보가 못 된다.")
        print("   → 경로 B(자체 사내 문서) 수집이 유일하게 오늘 시작 가능한 길이다.")
        if args.out:
            out = POC / args.out
            out.mkdir(parents=True, exist_ok=True)
            (out / "population_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n   모집단 명세만 저장: {out.relative_to(POC)}/population_manifest.json")
        return 1

    picked, plan = stratified_pick(pool, args.n, args.seed, args.strata)
    manifest["strata_plan"] = plan
    manifest["selected_n"] = len(picked)
    manifest["selected_sha256"] = hashlib.sha256(
        "".join(sorted(r["text_sha256"] for r in picked)).encode()).hexdigest()

    print()
    print(f"선정 {len(picked)}건 · 층별 {plan}")
    if not args.out:
        print("(--out 이 없어 파일을 쓰지 않았다)")
        return 0
    out = POC / args.out
    write_batch(out, picked, manifest)
    print(f"\n저장: {out.relative_to(POC)}/")
    print("  review_pack.jsonl        ← 검수자에게 제공")
    print("  answer_template.jsonl    ← 검수자가 채움")
    print("  blind_key.jsonl          ⚠ 서명 완료 전까지 제공 금지")
    print("  population_manifest.json · REVIEW_INSTRUCTIONS.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
