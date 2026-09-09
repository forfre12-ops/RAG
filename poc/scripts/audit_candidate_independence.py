#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검증 후보가 학습셋과 **같은 원본**인지 전수로 찾는다 — 찾고, 원하면 검수 범위에서 뺀다.

왜(2026-09-09). 감리 회신 5(5)에 "문장을 공유하는 후보가 20건, 그중 동일 문서로 의심되는
것이 3건이었습니다. 해당 3건은 확인 후 검증셋에서 제외하겠습니다"라고 적었다. 그 확인과
제외를 사람이 손으로 하면 재구성 뒤에 다시 못 한다. 도구로 남긴다.

■ 판정 기준은 새로 만들지 않는다

  koipa.holdout_independence 의 값을 그대로 쓴다 — 짝과 공유 문장 >= SAME_SOURCE_MIN_SHARED
  이고 max(공유/후보, 공유/학습) >= SAME_SOURCE_MIN_RATIO. 눈으로 확인한 참 6건·거짓 4건을
  여백 80% 대 33% 로 가른 검증된 기준이다(그 모듈 주석 참조).

⚠ 그 모듈의 `_same_source_documents` 는 **examples 를 3건으로 자른다**(hits[:3]).
  건수(documents)는 온전하지만 목록은 잘린다. 제외하려면 목록이 전부 필요하므로 여기서는
  같은 기준으로 **직접 전수 계산한다.** 2026-09-09 실측에서는 마침 3건이라 둘이 같았는데,
  그 우연에 기대면 다음에 10건이 나올 때 7건을 놓친다.

■ 막는 것은 '검수'가 아니라 '평가정답 편입'이다

  처음엔 콘솔 원장에 exclude(검수 대상 아님)를 기록했다. 그런데 그중 하나가 **KL 에 이미
  전달한 검수 배치 120건** 안에 있었고 그 배치는 등급 균형(30/30/30/30)이라 S3 가 29 로
  깨졌다(tests/test_review_batch_filter.py 가 잡았다).

  문제를 다시 보면 갈린다 — 그 문서를 **검수하는 것은 유효하다**(사람이 등급을 판단하는
  데 지장이 없다). 안 되는 것은 그 결과를 **평가정답으로 쓰는 것**이다. 학습에 쓴 문서로
  성능을 재면 그 수치가 부풀려진다.

  그래서 --block-eval 은 검수를 건드리지 않고 `evidence/eval_independence_exclusions.jsonl`
  에 사유와 함께 기록한다. console_signoff.build_promotion_inputs 가 그 목록을 읽어 승격
  단계에서 거른다. 목록을 evidence/ 에 두는 이유는 datasets/·reports/ 가 gitignore 라
  증적이 안 남기 때문이다 — "왜 이 문서가 평가정답에서 빠졌나"는 감리에서 받는 질문이다.

사용:
    python scripts/audit_candidate_independence.py                      # 찾기만
    python scripts/audit_candidate_independence.py --block-eval         # 찾고 평가정답 편입을 막는다
    python scripts/audit_candidate_independence.py --json reports/x.json
"""
from __future__ import annotations

try:  # 콘솔 출구 고정 — cp949 에서 em dash 하나에 죽지 않게
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))

# 차단 목록 — console_signoff.eval_blocked_doc_ids 가 읽는 자리와 같아야 한다.
EXCLUSIONS = _POC / "evidence" / "eval_independence_exclusions.jsonl"

DEFAULT_TRAIN = "datasets/labeled_p1_v5_clean"
DEFAULT_POOL = "datasets/proxy_gold/single_document_candidates"


def load_train(root: Path) -> list[dict]:
    rows: list[dict] = []
    for name in ("train.jsonl", "val.jsonl", "test.jsonl"):
        p = root / name
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def load_pool(root: Path) -> list[dict]:
    """콘솔과 **같은 우선순위**로 본문을 고른다 — content_revision_path 가 있으면 그쪽."""
    rows: list[dict] = []
    for mp in sorted(glob.glob(str(root / "*.metadata.json"))):
        meta = json.loads(Path(mp).read_text(encoding="utf-8"))
        doc_id = str(meta.get("doc_id") or "")
        rev = str(meta.get("content_revision_path") or "").strip()
        body = root / rev if rev else None
        if body is None or not body.is_file():
            cands = glob.glob(str(root / f"{doc_id}*.md"))
            if len(cands) != 1:
                continue
            body = Path(cands[0])
        rows.append({
            "doc_id": doc_id,
            "text": body.read_text(encoding="utf-8"),
            "label": meta.get("intended_label"),
            "document_origin": meta.get("document_origin"),
        })
    return rows


def find_same_source(train: list[dict], pool: list[dict]) -> list[dict]:
    """같은 원본으로 보이는 (후보, 학습) 짝을 **전부** 돌려준다. 상한 없음."""
    from koipa.holdout_independence import (  # noqa: PLC0415
        SAME_SOURCE_MIN_RATIO,
        SAME_SOURCE_MIN_SHARED,
        _normalize_sentences,
    )

    train_sents = [_normalize_sentences(r.get("text") or "") for r in train]
    inverted: dict[str, list[int]] = {}
    for i, sents in enumerate(train_sents):
        for s in sents:
            inverted.setdefault(s, []).append(i)

    hits: list[dict] = []
    for row in pool:
        sents = _normalize_sentences(row.get("text") or "")
        if not sents:
            continue
        counts: dict[int, int] = {}
        for s in sents:
            for i in inverted.get(s, ()):
                counts[i] = counts.get(i, 0) + 1
        if not counts:
            continue
        idx, shared = max(counts.items(), key=lambda kv: kv[1])
        if shared < SAME_SOURCE_MIN_SHARED:
            continue
        ratio = max(shared / len(sents), shared / (len(train_sents[idx]) or 1))
        if ratio < SAME_SOURCE_MIN_RATIO:
            continue
        hits.append({
            "candidate_doc_id": row["doc_id"],
            "candidate_label": row.get("label"),
            "candidate_origin": row.get("document_origin"),
            "train_doc_id": str(train[idx].get("doc_id")),
            "train_label": train[idx].get("label"),
            "train_source": train[idx].get("source"),
            "shared_sentences": shared,
            "candidate_sentences": len(sents),
            "ratio": round(ratio, 4),
        })
    return sorted(hits, key=lambda h: -h["ratio"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--train", default=DEFAULT_TRAIN)
    ap.add_argument("--pool", default=DEFAULT_POOL)
    ap.add_argument("--block-eval", action="store_true",
                    help="찾은 후보의 평가정답 편입을 막는다(검수는 그대로 둔다)")
    ap.add_argument("--json", help="결과를 이 경로에 저장")
    args = ap.parse_args()

    train_root = Path(args.train)
    pool_root = Path(args.pool)
    train = load_train(train_root if train_root.is_absolute() else _POC / train_root)
    pool = load_pool(pool_root if pool_root.is_absolute() else _POC / pool_root)
    hits = find_same_source(train, pool)

    print("=" * 72)
    print(f" 학습셋 {len(train):,}행  ↔  검증 후보 {len(pool):,}건")
    print("=" * 72)
    if not hits:
        print("  같은 원본으로 보이는 후보 없음.")
    for h in hits:
        print(f"  후보 {h['candidate_doc_id']}  [{h['candidate_label']}·{h['candidate_origin']}]")
        print(f"    ↔ 학습 {h['train_doc_id']}  [{h['train_label']}·{h['train_source']}]")
        print(f"       공유 {h['shared_sentences']}/{h['candidate_sentences']} 문장 · 비율 {h['ratio']}")
    print(f"\n  같은 원본 의심 {len(hits)}건 / 후보 {len(pool):,}건")

    if args.block_eval and hits:
        import datetime as _dt

        EXCLUSIONS.parent.mkdir(parents=True, exist_ok=True)
        already = set()
        if EXCLUSIONS.exists():
            for line in EXCLUSIONS.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        already.add(json.loads(line).get("doc_id"))
                    except json.JSONDecodeError:
                        continue
        added = 0
        with EXCLUSIONS.open("a", encoding="utf-8", newline="\n") as fh:
            for h in hits:
                if h["candidate_doc_id"] in already:
                    continue
                fh.write(json.dumps({
                    "doc_id": h["candidate_doc_id"],
                    "reason": "학습셋과 같은 원본",
                    "train_doc_id": h["train_doc_id"],
                    "shared_sentences": h["shared_sentences"],
                    "candidate_sentences": h["candidate_sentences"],
                    "ratio": h["ratio"],
                    "tool": "audit_candidate_independence.py",
                    "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                }, ensure_ascii=False, sort_keys=True) + "\n")
                added += 1
        print(f"  평가정답 편입 차단 {added}건 추가 (이미 있던 것 {len(hits) - added}건)")
        print(f"  목록: {EXCLUSIONS}")
        print("  ⚠ 검수는 그대로다 — 이 문서들은 콘솔에 계속 뜨고 등급을 확정할 수 있다.")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "train_rows": len(train), "pool_rows": len(pool),
            "same_source": hits,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  JSON 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
