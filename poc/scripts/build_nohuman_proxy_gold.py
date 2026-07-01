from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from analyze_label_noise import _is_ruling  # noqa: E402
from lloydk.golden_tiers import tier_of  # noqa: E402

LABELS = ("TS", "S1", "S2", "S3")
LLM_SOURCES = {"llm_judge_primary", "llm_judge_consensus"}
# 외부 권위 정답(사람서명 대체 가능한 유일한 진짜 정답원): 정부지정 NKT·공개판례/공시.
# 나머지(koipa 손작성 시나리오·rule_llm 합의·codex 리뷰)는 합성/LLM 프록시 — 분류기와 편향을
# 공유할 수 있어(상관오류) '통과'가 실문서 정확도를 보증하지 않는다. 단일 F1로 뭉개지 않도록 분리.
EXTERNAL_AUTHORITY_SOURCES = {"nkt_designated", "public_definitive"}


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def latest_build(pattern: str) -> Path:
    files = sorted(Path("datasets/gold_real/builds").glob(pattern))
    if not files:
        raise FileNotFoundError(f"no files matching datasets/gold_real/builds/{pattern}")
    return files[-1]


def counts(rows: list[dict], key: str) -> dict[str, int]:
    return dict(Counter(str(r.get(key) or "") for r in rows))


def label_counts(rows: list[dict]) -> dict[str, int]:
    base = Counter(r.get("label") for r in rows)
    return {label: base.get(label, 0) for label in LABELS}


def source_label_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        out[str(row.get("label_source") or "")][str(row.get("label") or "")] += 1
    return {src: dict(counter) for src, counter in sorted(out.items())}


def norm_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def train_overlap(rows: list[dict], train_rows: list[dict]) -> dict[str, float | int]:
    train_texts = {norm_text(r.get("text", "")) for r in train_rows if r.get("text")}
    overlap = sum(1 for r in rows if norm_text(r.get("text", "")) in train_texts)
    return {
        "count": overlap,
        "total": len(rows),
        "rate": round(overlap / len(rows), 4) if rows else 0.0,
    }


def annotate(row: dict, nohuman_tier: str, reason: str) -> dict:
    out = dict(row)
    out["nohuman_tier"] = nohuman_tier
    out["nohuman_reason"] = reason
    out["truth_warning"] = (
        "auto_proxy_only_not_locked_gold_eval"
        if nohuman_tier == "proxy_eval"
        else "not_eval_truth"
    )
    return out


def _breakdown_by_authority(rows: list[dict]) -> dict:
    """proxy_eval을 외부권위 정답(nkt+public) vs 합성/LLM 프록시로 분리 — 단일 F1이 둘을 뭉개
    '통과=실문서 안전'으로 오독되는 것을 막는다(합성 프록시는 상관오류로 정확도 미보증)."""
    ext = [r for r in rows if r.get("label_source") in EXTERNAL_AUTHORITY_SOURCES]
    syn = [r for r in rows if r.get("label_source") not in EXTERNAL_AUTHORITY_SOURCES]
    return {
        "external_authority": {
            "count": len(ext), "labels": label_counts(ext),
            "label_source": counts(ext, "label_source"),
            "note": "정부지정·공개 확정 = 사람서명 없이도 진짜 정답. 실측 인용 가능.",
        },
        "synthetic_proxy": {
            "count": len(syn), "labels": label_counts(syn),
            "label_source": counts(syn, "label_source"),
            "note": "손작성/LLM 프록시 = 분류기와 편향 공유 가능(상관오류). 스모크용, 실정확도 근거로 인용 금지.",
        },
    }


def build(
    original: list[dict],
    regate_gold: list[dict],
    regate_review: list[dict],
    train_texts: set[str],
) -> dict:
    regate_gold_ids = {str(r.get("doc_id")) for r in regate_gold}
    review_by_id = {str(r.get("doc_id")): r for r in regate_review}

    original_by_id = {str(r.get("doc_id")): r for r in original}
    proxy_eval: list[dict] = []
    quarantine: list[dict] = []
    silver_train_only: list[dict] = []
    train_leak: list[dict] = []

    quarantine_ids: set[str] = set()
    quarantine_reasons: dict[str, list[str]] = defaultdict(list)
    for row in original:
        doc_id = str(row.get("doc_id"))
        src = row.get("label_source")
        label = str(row.get("label") or "").upper()
        if src in LLM_SOURCES and _is_ruling(row) and label != "S3":
            quarantine_ids.add(doc_id)
            quarantine_reasons[doc_id].append("public_or_ruling_labeled_high_risk_by_llm")

    for row in regate_review:
        doc_id = str(row.get("doc_id"))
        status = str(row.get("status") or row.get("review_status") or "")
        if "ts_downgrade" in status:
            quarantine_ids.add(doc_id)
            quarantine_reasons[doc_id].append("ts_downgrade_suspect")

    for row in regate_gold:
        doc_id = str(row.get("doc_id"))
        # [수정1] quarantine 필터를 proxy 가지에도 적용 — 의심 라벨이 regate_gold에 있어도 eval서 배제.
        if doc_id in quarantine_ids:
            reason = "+".join(sorted(set(quarantine_reasons[doc_id])))
            quarantine.append(annotate(row, "quarantine", reason))
            continue
        # [수정2·핵심] 학습셋과 텍스트가 동일하면 eval에서 배제(leakage). doc_id가 달라도(=doc_id
        # 분리 착시) 텍스트가 같으면 모델이 암기한 것이라 F1을 부풀린다 → train_leak 버킷으로 격리.
        if norm_text(row.get("text", "")) in train_texts:
            train_leak.append(annotate(row, "train_leak", "train_text_overlap_excluded_from_eval"))
            continue
        src = row.get("label_source")
        tier = tier_of(row)
        if tier == "legal_floor":
            reason = f"legal_floor:{src}"
        elif tier == "gold_candidate":
            reason = "rule_llm_agreement_with_evidence"
        else:
            reason = f"regate_gold:{tier}"
        proxy_eval.append(annotate(row, "proxy_eval", reason))

    for doc_id, row in original_by_id.items():
        if doc_id in regate_gold_ids:
            continue
        if doc_id in quarantine_ids:
            reason = "+".join(sorted(set(quarantine_reasons[doc_id])))
            quarantine.append(annotate(row, "quarantine", reason))
            continue
        review = review_by_id.get(doc_id)
        if review:
            status = str(review.get("status") or "needs_review")
            reason = f"regate_demoted:{status}"
        else:
            reason = "not_proxy_eval"
        silver_train_only.append(annotate(row, "silver_train_only", reason))

    manifest = {
        "input_total": len(original),
        "proxy_eval": {
            "count": len(proxy_eval),
            "labels": label_counts(proxy_eval),
            "label_source": counts(proxy_eval, "label_source"),
            "source_label": source_label_counts(proxy_eval),
            "authority_breakdown": _breakdown_by_authority(proxy_eval),
            "note": (
                "leakage-free(학습텍스트·quarantine 사유 제외). authority_breakdown의 "
                "synthetic_proxy는 외부정답 아님 — 실정확도 근거로 단일 F1 인용 금지."
            ),
        },
        "train_leak": {
            "count": len(train_leak),
            "labels": label_counts(train_leak),
            "label_source": counts(train_leak, "label_source"),
            "note": (
                "학습셋과 텍스트 동일 → eval서 배제. doc_id는 달라도 텍스트 동일(doc_id 분리 착시)."
            ),
        },
        "silver_train_only": {
            "count": len(silver_train_only),
            "labels": label_counts(silver_train_only),
            "label_source": counts(silver_train_only, "label_source"),
            "reasons": counts(silver_train_only, "nohuman_reason"),
        },
        "quarantine": {
            "count": len(quarantine),
            "labels": label_counts(quarantine),
            "label_source": counts(quarantine, "label_source"),
            "reasons": counts(quarantine, "nohuman_reason"),
        },
        "locked_gold_eval_count": sum(1 for r in original if tier_of(r) == "locked_gold_eval"),
        "warning": (
            "No human-review locked gold is created here. proxy_eval is a leakage-free "
            "no-human evaluation proxy — smoke/regression only, NOT true gold."
        ),
    }
    return {
        "proxy_eval": proxy_eval,
        "silver_train_only": silver_train_only,
        "quarantine": quarantine,
        "train_leak": train_leak,
        "manifest": manifest,
    }


def render_report(manifest: dict, regate_gold: Path, regate_review: Path) -> str:
    px = manifest["proxy_eval"]
    sv = manifest["silver_train_only"]
    qt = manifest["quarantine"]
    tl = manifest.get("train_leak", {"count": 0})
    ab = px.get("authority_breakdown", {})
    ext_n = ab.get("external_authority", {}).get("count", 0)
    syn_n = ab.get("synthetic_proxy", {}).get("count", 0)
    return "\n".join(
        [
            "# No-Human Proxy Gold Report",
            "",
            "## Verdict",
            "",
            "- locked_gold_eval: 0 (사람서명 없음 — 이건 true gold 아님)",
            f"- proxy_eval (leakage-free): {px['count']} / {manifest['input_total']}",
            f"  - external_authority (nkt+public, 실측 인용 가능): {ext_n}",
            f"  - synthetic_proxy (koipa/rule_llm/codex, 스모크 전용): {syn_n}",
            f"- train_leak (학습중복 → eval서 배제): {tl['count']}",
            f"- silver_train_only: {sv['count']}",
            f"- quarantine: {qt['count']}",
            "- Use proxy_eval for smoke/regression only. Do NOT call it true gold.",
            "",
            "## ⚠️ 단일 F1 인용 금지 (중요)",
            "",
            "- proxy_eval 의 62%가 합성/LLM 프록시(synthetic_proxy)다. 이 라벨은 분류기와 편향을",
            "  공유할 수 있어(correlated error) '통과'가 실문서 정확도를 **보증하지 않는다**.",
            "- 실측 인용이 필요하면 `authority_breakdown.external_authority`(nkt+public) 서브셋의",
            "  지표만 별도로 보고하라. 합성 프록시 포함 단일 F1 = 거짓 확신.",
            "- 반드시 **실제 배포(ship) 모델**로 평가하라. 실데이터-학습 모델(v-dd3abab9 등)로 잰 값은",
            "  고객사에 갈 합성-only 모델(실문서 F1≈0.26)의 성능과 무관하다.",
            "",
            "## Inputs",
            "",
            f"- regate_gold: `{regate_gold}`",
            f"- regate_review: `{regate_review}`",
            "",
            "## Proxy Eval Label Counts",
            "",
            json.dumps(px["labels"], ensure_ascii=False, indent=2),
            "",
            "## Proxy Eval Authority Breakdown",
            "",
            json.dumps(ab, ensure_ascii=False, indent=2),
            "",
            "## Quarantine Reasons",
            "",
            json.dumps(qt["reasons"], ensure_ascii=False, indent=2),
            "",
            "## Train Text Overlap (자기검증: proxy_eval=0 이어야 함)",
            "",
            json.dumps(manifest.get("train_text_overlap", {}), ensure_ascii=False, indent=2),
            "",
            "## Operational Rule",
            "",
            "1. Evaluate only on `proxy_eval_nohuman.jsonl` when human review is unavailable.",
            "2. Report `external_authority` subset metrics separately; never cite a blended single F1 as accuracy.",
            "3. Always evaluate with the actual ship model, not a real-data-trained proxy model.",
            "4. Train may use `silver_train_only_nohuman.jsonl`, but do not report it as accuracy.",
            "5. `train_leak_nohuman.jsonl` and `quarantine_nohuman.jsonl` are excluded from eval by default.",
            "6. September customer documents should run in shadow mode because proxy_eval is distribution-limited.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    parser.add_argument("--regate-gold", default="")
    parser.add_argument("--regate-review", default="")
    parser.add_argument("--train", default="datasets/gold_real/train_subset.jsonl")
    parser.add_argument("--out-dir", default="datasets/gold_real/nohuman_proxy")
    args = parser.parse_args()

    gold_path = Path(args.gold)
    regate_gold = Path(args.regate_gold) if args.regate_gold else latest_build("regate_gold_*.jsonl")
    regate_review = Path(args.regate_review) if args.regate_review else latest_build("regate_review_*.jsonl")
    out_dir = Path(args.out_dir)

    # [수정3] 학습셋은 필수 — 없으면 leakage-free 보장이 불가하므로 조용히 leaky 셋을 만들지 않고 실패.
    train_path = Path(args.train)
    if not train_path.exists():
        print(
            f"ERROR: train set 없음({train_path}). leakage-free proxy_eval 보장 불가 → 중단. "
            "--train 으로 실제 학습셋을 지정하세요.",
            file=sys.stderr,
        )
        return 2
    train_rows = load_jsonl(train_path)
    train_texts = {norm_text(r.get("text", "")) for r in train_rows if r.get("text")}

    result = build(
        load_jsonl(gold_path), load_jsonl(regate_gold), load_jsonl(regate_review), train_texts
    )
    # 자기검증: 배제 후 proxy_eval의 잔여 학습중복은 0이어야 한다(leak 버킷으로 다 빠졌는지 확인).
    result["manifest"]["train_text_overlap"] = {
        "train": str(train_path),
        "proxy_eval": train_overlap(result["proxy_eval"], train_rows),        # 기대: 0
        "train_leak": train_overlap(result["train_leak"], train_rows),        # 기대: 1.0
        "silver_train_only": train_overlap(result["silver_train_only"], train_rows),
        "quarantine": train_overlap(result["quarantine"], train_rows),
    }
    write_jsonl(out_dir / "proxy_eval_nohuman.jsonl", result["proxy_eval"])
    write_jsonl(out_dir / "silver_train_only_nohuman.jsonl", result["silver_train_only"])
    write_jsonl(out_dir / "quarantine_nohuman.jsonl", result["quarantine"])
    write_jsonl(out_dir / "train_leak_nohuman.jsonl", result["train_leak"])
    (out_dir / "manifest.json").write_text(
        json.dumps(result["manifest"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "README.md").write_text(
        render_report(result["manifest"], regate_gold, regate_review),
        encoding="utf-8",
    )

    manifest = result["manifest"]
    residual = manifest["train_text_overlap"]["proxy_eval"]["count"]
    print(f"proxy_eval={manifest['proxy_eval']['count']} (residual_train_overlap={residual}, 기대 0)")
    print(f"train_leak(excluded)={manifest['train_leak']['count']}")
    print(f"silver_train_only={manifest['silver_train_only']['count']}")
    print(f"quarantine={manifest['quarantine']['count']}")
    print(f"out_dir={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
