#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""등급 서명 배치를 만든다 — 모집단·층화를 **먼저 잠그고** 모델 예측을 숨긴다.

## 왜 (2026-09-14)

"고등급 36건 중 미탐 1건이니 10건만 더 서명받으면 요건을 주장할 수 있다" 고 말했다가
바로 철회했다. **모델 결과를 본 뒤에 표본을 보강하면 선택 편향**이다. 올바른 순서는

    모집단·층화 기준 잠금 → 모델 예측 숨김 → 권한 있는 검토자가 서명 → GOLD 잠금
    → 배포 모델로 한 번 평가 → 학습에서 영구 제외

이 도구는 그 순서를 **파일 구조로 강제한다.** 선정은 잠근 모집단 + 고정 시드로만 하고,
검수자에게 나가는 팩에는 기계 라벨·예측이 없고, 승인 기준과 실제 증거는 제공한다.

## 표면 칸을 뺀다

2026-08-08 품질 파일럿 팩(`datasets/proxy_gold/blind_quality_pilot/`)에는
`document_type` 이 그대로 실려 있었다. 품질 점수를 묻는 팩이라 문제가 없었지만,
**등급을 묻는 팩에서는 그것이 답을 알려준다** — 문서종류 단서만으로 등급 정확도가
99.9% 까지 나온 실측이 있다(2026-09-12). 기계가 부여한 종류·예측을 제외하되,
실제 출처와 관리 증거까지 숨기지는 않는다. 값이 존재한다고 진위가 검증된 것은 아니다.

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

from koipa.golden_tiers import document_origin  # noqa: E402

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

SOURCE_FIELDS = ("source_reference", "source_agency", "retrieved_at", "source_sha256")
SYSTEM_FIELDS = ("access_scope", "security_marking", "owner_org", "actual_reader_scope")


def evidence_bundle(row: dict) -> dict:
    """Preserve inputs, not model explanations. Presence is not verification."""
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else row
    text = _text(row)
    return {
        "document_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "document_origin": document_origin(row),
        "source": {k: row[k] for k in SOURCE_FIELDS if row.get(k)},
        "system_facts": {k: md[k] for k in SYSTEM_FIELDS if md.get(k)},
        "evidence_verification": "not_verified",
        "missing_system_fields": [k for k in SYSTEM_FIELDS if not md.get(k)],
        "note": "본문 표식과 실제 관리 상태는 다릅니다. 없는 값을 추정해 채우지 마십시오.",
    }


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
                origin = document_origin(row)
                accepted_origins = {"customer_real" if x == "organization_real" else x for x in origins}
                if origins and origin not in accepted_origins:
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
                    "evidence_bundle": evidence_bundle(row),
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
    # Never overwrite a reviewer response or another user's existing batch.
    if out.exists():
        raise FileExistsError(f"Review batch already exists: {out}")
    out.mkdir(parents=True, exist_ok=False)
    reviewer_dir, coordinator_dir = out / "reviewer", out / "coordinator"
    reviewer_dir.mkdir()
    coordinator_dir.mkdir()
    pack, key, answer = [], [], []
    for i, r in enumerate(picked, 1):
        rid = f"SG{i:04d}"
        pack.append({"review_id": rid, "text": r["text"],
                     "evidence_bundle": r.get("evidence_bundle") or evidence_bundle(r),
                     "policy_context": manifest.get("policy_context", {
                         "version": None, "approval_status": "unapproved", "reference": None}),
                     "purpose": manifest.get("purpose", "review_candidate")})
        key.append({
            "review_id": rid, "text_sha256": r["text_sha256"],
            "original_doc_id": r["original_doc_id"], "source_path": r["source_path"],
            "origin": r["origin"], "hidden_label": r["hidden_label"],
        })
        answer.append({
            "review_id": rid,
            "grade": None,               # TS/S1/S2/S3 또는 "판정보류"
            "decision_status": "pending_review",
            "missing_evidence": [], "decision_evidence": [],
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

    def check_keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in FORBIDDEN_IN_PACK:
                    raise ValueError(f"Forbidden machine-label field in reviewer pack: {k}")
                check_keys(v)
        elif isinstance(value, list):
            for v in value:
                check_keys(v)

    check_keys(pack)

    def _dump(directory: Path, name: str, rows: list[dict]) -> None:
        (directory / name).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8")

    _dump(reviewer_dir, "review_pack.jsonl", pack)
    _dump(coordinator_dir, "blind_key.jsonl", key)
    _dump(reviewer_dir, "answer_template.jsonl", answer)
    write_casebook(reviewer_dir, pack)
    (coordinator_dir / "population_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (reviewer_dir / "REVIEW_INSTRUCTIONS.md").write_text(_INSTRUCTIONS, encoding="utf-8")


def write_casebook(reviewer_dir: Path, pack: list[dict]) -> None:
    """Render an existing input pack without touching anyone's answer file."""
    pages = ["# 검수 사례집\n\n모델 예측·기존 라벨을 숨긴 입력 자료입니다. "
             "정책 승인 상태와 증거 확인 여부를 먼저 확인하십시오.\n"]
    for row in pack:
        # Preserve full text; choose a fence that cannot be closed by the source.
        longest = max((len(x) for x in re.findall(r"`+", row["text"])), default=0)
        fence = "`" * max(3, longest + 1)
        pages.append(f"## {row['review_id']}\n\n"
                     f"용도: {row['purpose']}\n\n"
                     f"정책 상태: {row['policy_context'].get('approval_status', 'unapproved')}\n\n"
                     "### 원문\n\n" + fence + "text\n" + row["text"] + "\n" + fence + "\n\n"
                     "### 제공된 증거 및 누락 정보\n\n```json\n" +
                     json.dumps(row["evidence_bundle"], ensure_ascii=False, indent=2) + "\n```\n\n"
                     "판정은 별도 answer_template 사본에 작성하십시오. 확인하지 못한 값은 "
                     "추정하지 말고 missing_evidence에 적으십시오.\n")
    with (reviewer_dir / "CASES.md").open("x", encoding="utf-8") as fh:
        fh.write("\n".join(pages))


_INSTRUCTIONS = """# 등급 서명 안내

검수자에게는 `reviewer/`와 승인된 정책 문서·허용된 실제 증거만 제공합니다.
`coordinator/`의 기존 라벨·선정 원장은 제공하지 않습니다. 폴더 분리는 접근권한 설정을
대신하지 않으므로 전달 담당자가 실제 권한과 전달 범위를 확인해야 합니다.
각 검수자는 별도 응답 사본을 작성하고 상대의 답을 보지 않습니다.

정책이 unapproved/draft면 검수 절차 교정용입니다. 운영 GOLD로 서명·승격하지 않습니다.
이 팩의 system_facts/source는 전달된 값이며, 진위·시점은 추가 확인해야 합니다.

## 적어야 하는 것 (`answer_template.jsonl`)

문서마다 아래를 채웁니다. **모르면 비워 두지 말고 "확인 안 됨" 이라고 적으십시오** —
추측해서 고르면 등급이 바뀝니다.

| 칸 | 무엇 |
|---|---|
| `grade` | TS · S1 · S2 · S3, 확정 불가면 null |
| `decision_status` | pending_review · needs_evidence · adjudication_required · decided |
| `missing_evidence` / `decision_evidence` | 부족한 증거와 각 사실을 입증하는 문서·시스템 참조 |
| `secrecy` / `value` / `management` | 0 · 1 · 2 · 확인 안 됨 |
| `evidence_start` / `evidence_end` / `evidence_quote` | 그렇게 본 근거가 본문 어디인가 |
| `rule_id` | 적용한 규칙 |
| `not_higher_reason` / `not_lower_reason` | 왜 한 등급 위·아래가 아닌가 |
| `policy_version` | 어느 판 기준으로 판단했는가 |
| `signer_id` / `signer_role` | 누가 · 어느 자격으로 |

## 지켜야 할 것

- 문서의 출처나 모델 예측을 **추정하지 마십시오.** 본문과 주어진 사실만 봅니다.
- 다른 검수자의 답을 보지 마십시오. 일치도를 재려면 독립 판단이어야 합니다.
- `signer_role`에 소속과 자격을 적습니다. 내부 전문가도 정책상 권한·독립성·이해충돌
  확인이 필요합니다. 소속 문자열만으로 GOLD 자격을 결정하지 않습니다.
- 승인 기준표를 보는 것은 허용됩니다. 모델 예측·기존 기계 라벨을 답으로 베끼면 안 됩니다.
- 같은 본문이라도 정책·시점·관리 증거가 바뀌면 재판정해야 합니다.
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", action="append", required=True, help='예: "datasets/**/*.jsonl"')
    ap.add_argument("--require-origin", default="",
                    help="쉼표 구분. 비우면 출처 제한 없음(권장하지 않음)")
    ap.add_argument("--grade", default="", help="기존 라벨 필터. 기본은 미분류·S2를 포함한 전부")
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
        "note": "선정 규칙·기존 라벨 필터를 기록함. 실제 평가 전에 모집단 선정 시점 별도 확인 필요.",
        "training_manifest_complete": False,
        "claim_status": "NOT_ASSESSED",
    }

    if not pool:
        print()
        print("⛔ 모집단이 0건이라 배치를 만들지 않는다.")
        print("   무엇이 있어야 하는가 —")
        print("   현재 입력·필터에 맞는 후보가 없습니다. 위 제외 사유를 확인하십시오.")
        print("   후보 0건은 정답 제작 불가능을 뜻하지 않습니다. 미분류·승인된 추가 자료를 검토하십시오.")
        if args.out:
            out = POC / args.out
            out.mkdir(parents=True, exist_ok=False)
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
    print("  reviewer/              ← 검수자에게 제공")
    print("  coordinator/           ⚠ 검수자 전달 금지(기존 라벨 포함)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
