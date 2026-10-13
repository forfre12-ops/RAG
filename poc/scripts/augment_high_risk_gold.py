"""Add deterministic high-risk public-scenario gold records.

This supplement is intentionally small and traceable. It expands TS/S1/S2
coverage for boundary testing without using synthetic pipeline markers or
training-set text.

[2026-07-03 감사 교정] 과거 이 스크립트는 지정근거(legal_reference) 없는 손작성
시나리오에 nkt_designated/koipa_case_based를 부여해 외부권위 버킷을 오염시켰다
(external_authority TS 36 중 6건이 위조 provenance — M&A 메모·RLHF 모델 등 국가핵심
기술과 무관한 내용 포함). 전 시나리오 label_source를 curated_scenario로 정직화하고
requires_human_signoff=True를 강제한다. 정본의 기존 위조 6건은
scripts/fix_forged_nkt_provenance.py 로 교정한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


SCENARIOS: list[dict] = [
    {
        "label": "TS",
        "label_source": "curated_scenario",
        "domain": "semiconductor",
        "title": "HBM etch recipe parameter review",
        "body": "HBM stack W/TiN/SiO2 etch recipe: SF6 CHF3 C4F8 Ar gas ratio, RF power, selectivity result, and yield impact. Disclosure would let a competitor reproduce the process window.",
    },
    {
        "label": "TS",
        "label_source": "curated_scenario",
        "domain": "battery",
        "title": "Solid electrolyte composition trial",
        "body": "Li6PS5Cl solid electrolyte synthesis with Li2S P2S5 LiCl molar ratio, milling rpm, thermal profile, ionic conductivity, and scale-up risk for all-solid-state batteries.",
    },
    {
        "label": "TS",
        "label_source": "curated_scenario",
        "domain": "security",
        "title": "HSM master key ceremony procedure",
        "body": "HSM master key generation, quorum rule, FIPS level control, root CA private-key storage, escrow procedure, and emergency rotation checklist.",
    },
    {
        "label": "TS",
        "label_source": "curated_scenario",
        "domain": "business",
        "title": "Non-public acquisition valuation memo",
        "body": "NDA-based M&A due diligence, DCF valuation range, CFO negotiation ceiling, PMI schedule D+30 D+90 D+180, and non-public acquisition price assumptions.",
    },
    {
        "label": "TS",
        "label_source": "curated_scenario",
        "domain": "ai",
        "title": "RLHF reward model weight handover",
        "body": "Foundation-model RLHF reward model weights, preference data sampling rule, safety-tuning failure cases, and restricted release procedure.",
    },
    {
        "label": "TS",
        "label_source": "curated_scenario",
        "domain": "defense",
        "title": "Guidance algorithm validation note",
        "body": "Missile guidance CFAR threshold, MIMO radar signal chain, target tracking logic, classified interface assumptions, and field validation risk.",
    },
    {
        "label": "S1",
        "label_source": "curated_scenario",
        "domain": "sales",
        "title": "Customer database export risk review",
        "body": "VIP customer database, renewal probability, churn score, discounted pricing model, sales-contact history, and partner-specific margin assumptions.",
    },
    {
        "label": "S1",
        "label_source": "curated_scenario",
        "domain": "manufacturing",
        "title": "Yield improvement know-how memo",
        "body": "Production yield improvement know-how, defect-reduction method, process tuning checklist, and equipment setup values used by the factory engineering team.",
    },
    {
        "label": "S1",
        "label_source": "curated_scenario",
        "domain": "software",
        "title": "Internal API credential rotation plan",
        "body": "Internal API credential inventory, source-code module owner list, privileged token rotation plan, and repository access control matrix.",
    },
    {
        "label": "S1",
        "label_source": "curated_scenario",
        "domain": "finance",
        "title": "Private pricing model and margin table",
        "body": "Cost structure, EBITDA bridge, BUY target-price assumption, customer-level discount band, and negotiation floor for strategic accounts.",
    },
    {
        "label": "S1",
        "label_source": "curated_scenario",
        "domain": "legal",
        "title": "Patent license negotiation draft",
        "body": "Patent license term sheet, royalty ceiling, cross-license fallback, settlement threshold, and unpublished infringement analysis.",
    },
    {
        "label": "S1",
        "label_source": "curated_scenario",
        "domain": "bio",
        "title": "GMP process transfer checklist",
        "body": "GMP batch-transfer know-how, DMF preparation note, impurity-control range, supplier qualification status, and process deviation handling rule.",
    },
    {
        "label": "S2",
        "label_source": "curated_scenario",
        "domain": "operations",
        "title": "Internal weekly supplier risk brief",
        "body": "Internal weekly supplier risk brief covering OEM lead time, LNG logistics exposure, budget forecast, vendor delay, and draft mitigation plan.",
    },
    {
        "label": "S2",
        "label_source": "curated_scenario",
        "domain": "planning",
        "title": "Draft business plan review",
        "body": "Draft quarterly business plan, forecast gap, BEV launch schedule, IRA incentive assumption, and internal review comments before executive approval.",
    },
    {
        "label": "S2",
        "label_source": "curated_scenario",
        "domain": "finance",
        "title": "Budget forecast variance memo",
        "body": "Budget forecast, WTI sensitivity, AMPC scenario, EBITDA variance, and internal-only action items for next quarter planning.",
    },
    {
        "label": "S2",
        "label_source": "curated_scenario",
        "domain": "procurement",
        "title": "Vendor negotiation status note",
        "body": "Vendor negotiation draft, supplier quotation, CDMO capacity check, pending purchase order, and internal approval route.",
    },
    {
        "label": "S2",
        "label_source": "curated_scenario",
        "domain": "telecom",
        "title": "Network rollout internal review",
        "body": "Internal review of GHz band rollout, LTE fallback, equipment delivery risk, vendor schedule, and draft launch-readiness checklist.",
    },
    {
        "label": "S2",
        "label_source": "curated_scenario",
        "domain": "market",
        "title": "Weekly market and ETF exposure note",
        "body": "Weekly internal market note with ETF exposure, OECD demand signal, LNG price movement, and internal portfolio response draft.",
    },
]


def _doc_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--gold-real", default="datasets/gold_real/classification_gold.jsonl")
    args = p.parse_args()

    path = Path(args.gold_real)
    existing: set[str] = set()
    rows: list[dict] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            rec = json.loads(line)
            existing.add(rec.get("doc_id", ""))
            rows.append(rec)

    additions: list[dict] = []
    for item in SCENARIOS:
        text = f"{item['title']}\n\n{item['body']}"
        doc_id = _doc_id(text)
        if doc_id in existing:
            continue
        additions.append({
            "doc_id": doc_id,
            "text": text,
            "label": item["label"],
            "source": "public_scenario",
            "domain": item["domain"],
            "label_source": item["label_source"],
            "review_status": "accepted",
            # 손작성 시나리오는 외부권위가 아니다 — 서명 면제 없음(2026-07-03 감사 교정).
            "requires_human_signoff": True,
            "notes": "deterministic high-risk boundary supplement (curated_scenario — 외부권위 아님)",
        })

    if not additions:
        print("[augment-high-risk] no new records")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for rec in additions:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[augment-high-risk] added {len(additions)} records -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
