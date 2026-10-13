#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""출처 증거로 S3 정답을 확정한다 — 사람 판단이 필요 없는 유일한 축.

## 왜 (2026-09-14)

우리 정답 계층이 전부 막혀 있다. 채점에 쓰는 평가면의 정답등급이 BRONZE 아니면
NONE 이고 GOLD 는 0건이다. 그래서 어떤 수치도 주장할 수 없다.

그런데 **한쪽 축은 사람 없이 확정된다.**

    공개됐다  =  URL·발행기관·수집시각·원문해시로 **검증 가능한 사실**
              →  비공지성 결여 (부정경쟁방지법 2조2호)
              →  영업비밀 불성립
              →  S3

추측이 하나도 없다. 반대쪽(TS·S1)은 원리적으로 다르다 — 비공지성은 **부재 증명**이고
경제적유용성·비밀관리성은 보유자만 아는 사실이라 문서 밖 기록이 필요하다.

실측(2026-09-14): 4종 증거를 전부 갖추고 학습셋에 없는 고유 문서가 **1,650건** 있는데
현재 정답등급이 `NONE`(출처 기록 없음)이다. `tier_of()` 가 `label_source` 한 필드만 보고
실제 출처 증거는 안 보기 때문이다.

## 등급 체계를 건드리지 않는다

`provenance_gate_s3`(= 출처가 공개임이 확정)는 이미 `TIER_BY_SOURCE` 에 SILVER 로 있고
전 데이터셋에서 7,122행이 쓰고 있다. 새 tier 를 만들지 않고 그 표시를 찍는다.
EVAL_CRITERIA 제1조를 고치지 않고도 NONE → SILVER 가 된다.

⚠ SILVER 가 맞는 등급인가는 **따로 물을 일**이다. '기계 여럿이 합의'(SILVER)와
'사실이 문서로 확인됨'은 성격이 다르다. 이 도구는 그 판단을 하지 않는다.

## 무엇을 확인하고 찍는가

    URL          http/https 로 시작하고 호스트가 있어야 한다
    발행기관      비어 있지 않아야 한다
    수집시각      ISO8601 로 파싱돼야 한다
    원문해시      64자 16진수여야 한다
    학습 겹침      본문 sha256 이 학습풀에 없어야 한다
    제외 목록      evidence/eval_suite_exclusions.jsonl 에 없어야 한다

하나라도 어긋나면 **버리고 사유를 센다.** 통과한 것만 정답으로 찍는다.

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/build_public_s3_truth.py \
        --out datasets/public_s3_truth/eval.jsonl
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob as globmod
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from koipa.eval_authority import load_suite_exclusions  # noqa: E402

#: 이 표시를 찍으면 tier_of() 가 SILVER 로 읽는다(audit_eval_ground_truth.TIER_BY_SOURCE).
LABEL_SOURCE = "provenance_gate_s3"

TRAIN_POOLS = (
    "datasets/gold_real/train_subset.jsonl",
    "datasets/labeled_p1_v5_clean/train.jsonl",
    "datasets/labeled_p1_v5_clean/val.jsonl",
    "datasets/labeled_p1_v5_clean/test.jsonl",
    "datasets/labeled_oss_v1/train.jsonl",
    "datasets/labeled_oss_v1/val.jsonl",
)
TEXT_KEYS = ("text", "content", "body")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
MIN_CHARS = 100


def _h(t: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", t or "").encode("utf-8")).hexdigest()


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


def check_evidence(row: dict) -> tuple[dict | None, str]:
    """출처 증거 4종을 **형식까지** 본다. 있다고 다 되는 것이 아니다."""
    url = str(row.get("source_reference") or row.get("source_url") or "").strip()
    if not url:
        return None, "URL 없음"
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None, "URL 형식 불량"

    agency = str(row.get("source_agency") or "").strip()
    if not agency:
        return None, "발행기관 없음"

    when = str(row.get("retrieved_at") or "").strip()
    if not when:
        return None, "수집시각 없음"
    try:
        _dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        return None, "수집시각 파싱 실패"

    dig = str(row.get("source_sha256") or row.get("raw_html_sha256") or "").strip()
    if not dig:
        return None, "원문해시 없음"
    if not _HEX64.match(dig):
        return None, "원문해시 형식 불량(64자 16진수 아님)"

    return {
        "source_reference": url,
        "source_agency": agency,
        "retrieved_at": when,
        "source_sha256": dig,
        "source_host": p.netloc,
        "published_at": row.get("published_at"),
        "source_license": row.get("source_license"),
    }, ""


def build(*, exclude_paths: set[str]) -> tuple[list[dict], Counter, dict]:
    train: set[str] = set()
    for rel in TRAIN_POOLS:
        for row in _rows(POC / rel):
            t = _text(row)
            if t:
                train.add(_h(t))

    drop: Counter = Counter()
    seen: dict[str, dict] = {}
    scanned = 0
    for p in sorted(globmod.glob(str(POC / "datasets/**/*.jsonl"), recursive=True)):
        rel = Path(p).relative_to(POC).as_posix()
        if rel in exclude_paths:
            drop["제외 목록에 있는 평가면"] += 1
            continue
        if "/signoff_batch/" in rel or "/public_s3_truth/" in rel:
            continue
        for row in _rows(Path(p)):
            if str(row.get("label") or "") != "S3":
                continue
            scanned += 1
            t = _text(row)
            if not t or len(t) < MIN_CHARS:
                drop["본문 없음·너무 짧음"] += 1
                continue
            ev, why = check_evidence(row)
            if ev is None:
                drop[why] += 1
                continue
            hh = _h(t)
            if hh in train:
                drop["학습셋에 있음"] += 1
                continue
            if hh in seen:
                drop["본문 중복"] += 1
                continue
            seen[hh] = {
                "doc_id": str(row.get("doc_id") or hh[:16]),
                "text": t,
                "label": "S3",
                "label_source": LABEL_SOURCE,
                "text_sha256": hh,
                "provenance": ev,
                "lineage": {
                    "from": rel,
                    "rule": "공개 사실이 URL·발행기관·수집시각·원문해시로 검증됨 → 비공지성 결여 → S3",
                    "built_by": "scripts/build_public_s3_truth.py",
                },
            }
    meta = {"scanned_s3_rows": scanned, "train_pool_hashes": len(train)}
    return list(seen.values()), drop, meta


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="정답 파일 저장 경로")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    ex = load_suite_exclusions(POC)
    exclude_paths = {k for k, v in ex.items()
                     if "scoring" in (v.get("excluded_from") or [])}
    print(f"제외 목록에서 채점 금지 {len(exclude_paths)}면을 뺀다")

    rows, drop, meta = build(exclude_paths=exclude_paths)
    if args.limit:
        rows = rows[:args.limit]

    print()
    print("=" * 78)
    print(f"출처 증거로 확정한 S3 정답 — {len(rows)}건")
    print("=" * 78)
    print(f"  훑은 S3 행 {meta['scanned_s3_rows']:,} · 학습풀 고유본문 {meta['train_pool_hashes']:,}")
    print()
    print("  버린 사유(분모를 안 적으면 손으로 고른 목록과 다르지 않다)")
    for k, v in drop.most_common(12):
        print(f"    {v:>8,}  {k}")

    hosts = Counter(r["provenance"]["source_host"] for r in rows)
    agencies = Counter(r["provenance"]["source_agency"] for r in rows)
    print()
    print(f"  출처 호스트 {len(hosts)}종 — 상위 {dict(hosts.most_common(4))}")
    print(f"  발행기관 {len(agencies)}종 — 상위 {dict(agencies.most_common(4))}")

    if len(hosts) <= 1:
        print()
        print("  ⚠ 한계 — 출처 호스트가 1종이다. 이 면은 '공개문서' 가 아니라 그 매체의")
        print("    문체를 잰다. 대표성을 주장하지 말 것. 다른 매체(공시·판례·학술)를 섞어야 한다.")

    print()
    print("  이 정답으로 무엇을 말할 수 있나")
    print(f"    label_source={LABEL_SOURCE} → tier_of() 가 SILVER 로 읽는다")
    print("    SILVER = 상대 비교·회귀 감시용. **성능 주장에는 GOLD 가 필요하다**(제2조)")
    print("    ⚠ '기계 여럿이 합의'(SILVER)와 '사실이 문서로 확인됨'은 성격이 다르다 —")
    print("      등급 체계를 고칠지는 따로 물을 일이고 이 도구는 판단하지 않는다")

    if args.out:
        out = POC / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                       encoding="utf-8")
        man = out.with_name("manifest.json")
        man.write_text(json.dumps({
            "built_at": "2026-09-14",
            "n": len(rows),
            "label_source": LABEL_SOURCE,
            "rule": "공개 사실이 4종 증거로 검증됨 → 비공지성 결여 → S3",
            "evidence_required": ["source_reference(URL)", "source_agency", "retrieved_at", "source_sha256"],
            "train_pools": list(TRAIN_POOLS),
            "excluded_suites": sorted(exclude_paths),
            "scanned_s3_rows": meta["scanned_s3_rows"],
            "dropped": dict(drop),
            "hosts": dict(hosts.most_common(20)),
            "claim_ceiling": "SILVER — 상대 비교·회귀 감시용. 성능 주장 불가(EVAL_CRITERIA 제2조)",
            "known_limitations": [
                ("출처 호스트가 %d종이다. 1종이면 이 면은 '공개문서'가 아니라 그 매체의 "
                 "문체를 잰다 — 대표성 주장 금지." % len(hosts)),
                ("발행기관 %d종은 다양해 보이나 매체가 같으면 문체가 같다. "
                 "다른 매체(공시·판례·학술)를 섞기 전에는 '공개문서 일반' 으로 읽지 말 것."
                 % len(agencies)),
                "정답은 '공개됐다'는 사실이지 '등급이 S3다'라는 사람 판단이 아니다.",
            ],
            "source_host_count": len(hosts),
            "source_agency_count": len(agencies),
            "tool": "scripts/build_public_s3_truth.py",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {out.relative_to(POC)} ({len(rows)}건) · {man.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
