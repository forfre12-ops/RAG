"""Build a small, auditable human-style proxy-writing pilot without an LLM.

The texts are deliberately composed from curated archetype clauses plus the
catalog fact card.  They are comparison candidates only: a reviewer must sign
them before they can enter any training or evaluation set.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sys
import tempfile

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from scripts.build_proxy_scenarios import _fact_ledger_for_item, load_catalog  # noqa: E402


ARCHETYPES = {
    "process-optimization": (
        "공정 조건과 현장 검증",
        "조건 조합별 실패 구간과 측정 기록",
        "현장 검증 담당과 품질 책임자",
    ),
    "pricing-policy": (
        "가격 결정 조건과 거래 검토",
        "거래 조건의 예외와 승인 근거",
        "영업 기획 담당과 계약 검토 책임자",
    ),
    "formulation-screen": (
        "배합 조건과 시험 재현성",
        "시험 조건·결과·재현 확인",
        "연구 담당과 시험 책임자",
    ),
    "model-quality": (
        "모델 성능과 배포 전 검증",
        "검증 자료·예외 판단·배포 승인 이력",
        "모델 운영 담당과 검증 책임자",
    ),
    "security-recovery": (
        "보안 대응과 복구 절차",
        "사건 기록·복구 순서·접근 조치",
        "보안 운영 담당과 대응 책임자",
    ),
    "supplier-sourcing": (
        "공급처 선정과 계약 검토",
        "선정 조건·비교 근거·협상 예외",
        "구매 담당과 계약 책임자",
    ),
    "roadmap-capacity": (
        "제품 로드맵과 자원 배분",
        "우선순위·투입 조건·결정 이력",
        "제품 기획 담당과 운영 책임자",
    ),
    "field-validation": (
        "현장 검증과 품질 확인",
        "현장 관찰·검증 기준·개선 조치",
        "현장 검증 담당과 품질 책임자",
    ),
    "workforce-plan": (
        "인력 운영과 역할 배치",
        "역할 배치·운영 제약·승인 근거",
        "인력 운영 담당과 부서 책임자",
    ),
    "deal-evaluation": (
        "거래 조건과 사업성 검토",
        "거래 가정·위험 조건·의사결정 근거",
        "사업 검토 담당과 승인 책임자",
    ),
}


def _sentence(value: object) -> str:
    for sentence in re.split(r"(?<=[.!?])\s*", str(value or "").strip()):
        if sentence and not re.search(r"\d", sentence):
            return sentence
    return ""


def _document(scenario: dict, instance: dict, ordinal: int) -> dict:
    focus, evidence_focus, roles = ARCHETYPES[scenario["archetype_id"]]
    ledger = _fact_ledger_for_item((scenario, instance, {}, ordinal))
    assert ledger is not None
    scores = scenario["expected_factor_scores"]
    access = scenario["evidence_card"]["access_controls"]
    nonpublicity = scenario["evidence_card"]["nonpublicity"]
    value = scenario["evidence_card"]["competitive_value"]
    status = ledger.get("status", "추가 검토 대상")
    metric = ledger["metric_name"]
    management = int(scores["management"])
    if management == 2:
        control_statement = (
            "문서는 승인된 업무공간에서 역할별로만 열람하고, 전달이 필요하면 "
            "목적·수신자·제공 범위를 기록한다. 검토 종료 뒤에는 더 이상 업무상 "
            "필요가 없는 접근 권한을 회수하고, 예외 공유는 책임자의 사전 확인 없이는 진행하지 않는다."
        )
    elif management == 1:
        control_statement = (
            "문서는 관련 부서의 업무공간에서 제한적으로 공유하며, 전달 목적과 "
            "검토 이력을 남긴다. 다만 지정 인원별 승인과 권한 회수가 일관되게 운영되는지는 "
            "후속 확인이 필요하다."
        )
    else:
        control_statement = (
            "문서 보관 위치와 담당 범위는 정리되어 있으나, 접근·반출·권한 회수를 "
            "실제로 통제했다는 객관적 기록은 확인하지 못했다. 이 한계는 등급 판단과 "
            "후속 조치에서 별도로 다룬다."
        )
    body = f"""# {scenario['document_type']} 검토 기록

## 검토 목적

이번 기록은 {focus}에 관한 변경안의 적용 여부를 판단하기 위해 작성했다. 검토자는 {evidence_focus}를 원자료, 검토 메모, 승인 이력과 함께 대조했다. 확인되지 않은 추정이나 외부 자료의 유사 사례로 결론을 대신하지 않고, 이번 검토 범위에서 확인된 업무 기록만 사용한다.

## 검토 방법

검토는 자료의 작성 시점과 변경 이력을 먼저 확인한 뒤, 관찰 결과가 승인 조건과 모순되지 않는지를 점검하는 순서로 진행했다. 담당자 간 의견이 다르면 어느 기록을 근거로 판단했는지 남기고, 확인되지 않은 항목은 확정 의견이 아니라 보완 확인 대상으로 분리한다.

## 업무 맥락과 관찰

{_sentence(scenario['shared_context'])} 관찰 과정에서 담당자는 결과만 나열하지 않고 어떤 조건에서 판단이 달라졌는지와 예외가 다음 조치에 어떻게 연결되는지를 기록했다. {metric}은 변경 전 {ledger['before']}{ledger['unit']}에서 변경 후 {ledger['after']}{ledger['unit']}로 {ledger['change_direction']}했으며, 현재 관측 상태는 {status}이다. 정상 범위는 {ledger.get('normal_lower', '해당 없음')}~{ledger.get('normal_upper', '해당 없음')}{ledger['unit']}로 관리한다.

## 정보 관리와 접근 통제

{nonpublicity} {access} {control_statement}

## 판단과 후속 조치

{value} {_sentence(scenario['harm_potential'])} 따라서 현재 결과는 즉시 확정하지 않고 보완 확인 뒤에만 적용 여부를 다시 결정한다. {roles}은 각각 원자료 대응과 조치 이력의 완결성을 확인하며, 검토 책임자는 정상 범위 충족 여부와 예외 처리 근거를 대조한 뒤 최종 의견을 남긴다.

## 점검표

| 점검 항목 | 확인 내용 | 책임 역할 |
| --- | --- | --- |
| 업무 근거 | {evidence_focus} 확인 | {roles.split('과')[0]} |
| 지표 상태 | {metric}과 정상 범위 대조 | 검증 책임자 |
| 접근 통제 | 열람·반출·권한 회수 기록 확인 | 운영 책임자 |
| 후속 조치 | 예외 보완과 승인 이력 확인 | 검토 책임자 |
"""
    return {
        "doc_id": f"curated-master-{scenario['scenario_id']}-{instance['instance_profile_id']}",
        "text": body.strip(),
        "label": scenario["label"],
        "intended_label": scenario["label"],
        "scenario_id": scenario["scenario_id"],
        "instance_profile_id": instance["instance_profile_id"],
        "document_type": scenario["document_type"],
        "document_family_id": scenario["document_family_id"],
        "document_origin": "synthetic",
        "authoring_method": "codex_curated_fact_card_v1",
        "source_basis": "scenario_catalog_plus_deterministic_fact_ledger",
        "expected_factor_scores": scores,
        "fact_ledger": ledger,
        "requires_manual_audit": True,
        "claim_scope": "comparison candidate only; not human-reviewed gold",
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    catalog, scenarios = load_catalog(root / "datasets/proxy_gold/scenario_catalog.v1.json")
    instances = catalog["instance_profiles"]
    selected: list[dict] = []
    for grade in ("TS", "S1", "S2"):
        by_archetype: dict[str, dict] = {}
        for scenario in sorted(scenarios, key=lambda row: row["scenario_id"]):
            if scenario["label"] == grade:
                by_archetype.setdefault(scenario["archetype_id"], scenario)
        if len(by_archetype) != 10:
            raise ValueError(f"expected ten archetypes for {grade}")
        for index, scenario in enumerate(by_archetype.values()):
            selected.append(_document(scenario, instances[index], index))
    out = root / "datasets/proxy_gold/curated_master/pilot_30.v3.jsonl"
    if out.exists():
        raise FileExistsError(f"refusing to overwrite: {out}")
    payload = b"".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
        for row in selected
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=out.parent, delete=False) as handle:
        handle.write(payload)
        temp = Path(handle.name)
    try:
        os.replace(temp, out)
    finally:
        temp.unlink(missing_ok=True)
    print(json.dumps({"out": str(out), "records": len(selected)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
