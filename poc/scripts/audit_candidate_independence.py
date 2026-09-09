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

■ 제외는 '폐기'가 아니다

  --exclude 는 콘솔 원장에 `exclude`(검수 대상 아님)를 기록한다. 등급을 정하지도, 문서를
  버리지도 않고 **이번 검수 범위에서만** 뺀다. 되돌릴 수 있다(콘솔의 「재검토로 되돌림」).

  기록되는 결정자는 사람이 아니라 도구 이름이다. 데이터 위생 조치이지 검수 판단이 아니라
  사람 이름을 남기면 안 된다. 그 이름은 golden_tiers.is_human_reviewer 가 거부하므로
  **이 결정은 평가정답으로 승격되지 않는다** — 의도한 대로다.

사용:
    python scripts/audit_candidate_independence.py                      # 찾기만
    python scripts/audit_candidate_independence.py --exclude            # 찾고 범위에서 뺀다
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

# 이 도구가 원장에 남기는 결정자. 사람이 아님이 드러나야 하고, is_human_reviewer 가
# 거부하는 이름이어야 한다(그래서 이 결정은 평가정답이 될 수 없다).
AUDIT_ACTOR = "ai_assist:independence-audit"

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
    ap.add_argument("--exclude", action="store_true",
                    help="찾은 후보를 콘솔 원장에 '검수 대상 아님'으로 기록한다(되돌릴 수 있다)")
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

    if args.exclude and hits:
        from koipa.services.proxy_gold_candidate_service import (  # noqa: PLC0415
            ProxyGoldCandidateService,
        )

        svc = ProxyGoldCandidateService(pool_root if pool_root.is_absolute() else _POC / pool_root)
        done = 0
        for h in hits:
            reason = (
                f"학습셋과 같은 원본으로 판정 — 학습 {h['train_doc_id']} 과 "
                f"{h['shared_sentences']}/{h['candidate_sentences']} 문장 공유(비율 {h['ratio']}). "
                "평가 독립성을 위해 이번 검수 범위에서 제외한다."
            )
            try:
                svc.decide(doc_id=h["candidate_doc_id"], action="exclude",
                           reason=reason, actor_id=AUDIT_ACTOR)
                done += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  ! 제외 실패 {h['candidate_doc_id']}: {type(exc).__name__}: {exc}")
        print(f"  검수 범위에서 제외 {done}건 (되돌리기: 콘솔의 「재검토로 되돌림」)")

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
