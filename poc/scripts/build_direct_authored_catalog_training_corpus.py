"""Build catalog-bound training Proxy records without using any LLM."""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from lloydk.proxy_corpus import validate_proxy_record
from scripts.build_direct_authored_training_corpus import (
    CLOSING_VERIFICATION, COMPREHENSIVE_APPENDIX, CONTROL_APPENDICES, LENGTHS,
    SHAPES, VARIANT_FOCI, _shape_indices_for_grade,
)
from scripts.build_direct_authored_training_pilot import CASES, _editorial_audit, _evidence_card
from scripts.build_proxy_scenarios import load_catalog
CATALOG = ROOT / "datasets" / "proxy_gold" / "training_scenario_catalog.v1.json"
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v1.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v1.manifest.json"
FAMILY_COUNT = 225
CATALOG_APPENDIX = """검토 결과를 다음 업무에 전달할 때에는 결론만 복사하지 않고, 결론이 성립한 조건과 제외한 조건을 함께 확인한다. 담당자·시스템·현장 환경이 달라지면 동일한 수치라도 의미가 달라질 수 있으므로, 적용 범위와 재확인 시점을 기록한다. 자료의 원문과 요약본은 보관 목적을 구분하고, 불필요해진 사본은 회수 또는 폐기 상태를 남긴다. 이 과정을 통해 문서가 짧은 판정문이 아니라 원자료·판단·조치의 연결을 보존하는 업무 기록이 되도록 한다. 작성자는 마지막으로 문서에 적힌 역할·수치·일정·접근 범위가 서로 모순되지 않는지 확인하고, 미결 항목은 완료로 표시하지 않는다."""
def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
def _policy(scores: dict[str, int]) -> dict[str, object]:
    s, v, m = int(scores["secrecy"]), int(scores["value"]), int(scores["management"])
    disclosure = (
        "문서의 핵심 내용은 공개 표준과 일반 교육자료로 확인할 수 있으며 별도 비공개 결합정보를 전제하지 않는다.",
        "일부 운영 조건은 내부 자료이나 핵심 원리와 일반 절차는 공개 자료에서도 확인할 수 있어 비공지성 근거가 제한적이다.",
        "구체적인 조건의 결합과 실패 이력은 외부에 공개되지 않았고 보유 조직 밖에서는 전체 조합을 재구성하기 어렵다.",
    )
    value = (
        "공개된 원칙을 적용한 설명 수준으로 독자적 경쟁 우위나 대체하기 어려운 경제적 가치를 확인하기 어렵다.",
        "운영 순서와 대응 우선순위에는 실무상 가치가 있으나 독자적 핵심 기술 수준의 가치라고 단정할 근거는 제한적이다.",
        "구체적 결합을 사용하면 재시험과 조정 시간을 크게 줄일 수 있고 경쟁사는 현장 시험과 실패 이력 탐색을 다시 수행해야 한다.",
    )
    access = (
        "자료는 업무 참고용일 뿐 개별 열람 승인·반출 통제·정기 권한 회수 같은 관리 조치는 운영되지 않는다.",
        "프로젝트 팀과 협력사에 접근을 제한하고 공유 이력은 남기지만 세부 반출 승인과 권한 회수는 일부만 적용한다.",
        "지정 인원만 열람하고 열람·다운로드·반출마다 승인과 이력 대조를 수행하며 권한 회수도 정기 운영한다.",
    )
    return {"scores": {"secrecy": s, "value": v, "management": m}, "disclosure": disclosure[s], "value": value[v], "access": access[m]}
def _text(case: dict[str, str], policy: dict[str, object], shape_index: int, ordinal: int) -> str:
    _shape_id, shape_name, length = SHAPES[shape_index]
    extra = "\n\n".join(CONTROL_APPENDICES[: 5 + LENGTHS[length][2] // 2])
    return f"""# {case['document_type']}
문서 형식: {shape_name} / 기록 차수: {ordinal + 1}
## 검토 범위
본 문서는 {case['context']}에 관한 독립 학습용 사례다. 작성자는 관찰 사실·판단 근거·후속 조치를 구분하고, 일반적인 공개 원칙과 현재 업무에서 확인된 조건을 혼동하지 않는다.
## 관찰 내용
핵심 검토 대상은 {case['detail']}이다. 비교 결과 {case['metric']} 수치가 목표 범위에 있더라도 작성 시점·입력 조건·예외 발생 여부가 다르면 이전 결과와 직접 비교하지 않는다. 역할은 {case['roles']}가 나누어 수행하며 원자료·요약표·변경 이력을 서로 대조한다.
## 공개성·가치·관리 근거
{policy['disclosure']}
{policy['value']}
{policy['access']}
## 적용과 예외 처리
적용 전에는 변경 대상과 제외 대상을 확인하고, 적용 후에는 같은 지표를 두 주기 이상 관찰한다. 예외가 발견되면 영향 범위·임시 조치·복구 기준·재확인 책임자를 별도 기록한다. 협력 조직에 설명이 필요할 때에는 수행에 필요한 범위만 전달하고 전달·회수 여부를 남긴다.
## 기록 관리와 재현성
{extra}
{COMPREHENSIVE_APPENDIX}
## 차수별 확인
{VARIANT_FOCI[ordinal % len(VARIANT_FOCI)]}
{CLOSING_VERIFICATION}
{CATALOG_APPENDIX}
""".strip() + "\n"
def build_records() -> tuple[list[dict[str, object]], dict[str, object]]:
    catalog, scenarios = load_catalog(CATALOG)
    planned: dict[str, list[dict[str, object]]] = defaultdict(list)
    for scenario in sorted(scenarios, key=lambda value: str(value["scenario_id"])):
        planned[str(scenario["label"])].extend([scenario] * int(scenario["target_count"]))
    targets = {"TS": 750, "S1": 750, "S2": 750, "S3": 450}
    assert {grade: len(value) for grade, value in planned.items()} == targets
    rows: list[dict[str, object]] = []
    for grade, items in planned.items():
        for ordinal, (scenario, shape_index) in enumerate(zip(items, _shape_indices_for_grade(grade, len(items)), strict=True)):
            base = CASES[ordinal % len(CASES)]
            scores = {key: int(value) for key, value in dict(scenario["expected_factor_scores"]).items()}
            policy = _policy(scores)
            case = {**base, "document_type": str(scenario["document_type"]), "context": f"{base['context']}에서 {scenario['domain']} 영역의 {scenario['document_type']}를 작성하는 상황", "detail": f"{base['detail']}과 변경 조건·예외 이력의 연결"}
            text = _text(case, policy, shape_index, ordinal)
            shape_id, _shape_name, length = SHAPES[shape_index]
            minimum, maximum = 3000, 5000
            row = {
                "doc_id": f"direct-catalog-{grade.lower()}-{ordinal + 1:04d}",
                "document_family_id": f"direct-catalog-family-{ordinal % FAMILY_COUNT:03d}",
                "scenario_id": str(scenario["scenario_id"]), "factor_profile_id": str(scenario["factor_profile_id"]),
                "family_profile_id": f"direct-{shape_id}", "length_profile_id": f"direct-{length}",
                "requested_profile_min_chars": minimum, "requested_profile_max_chars": maximum,
                "document_type": str(scenario["document_type"]), "domain": str(scenario["domain"]), "industry": "direct-authored-proxy",
                "text": text, "label": grade, "intended_label": grade, "expected_factor_scores": scores,
                "evidence_card": _evidence_card(text, policy, case), "document_origin": "synthetic",
                "source": "direct_authored_proxy", "proxy_role": "confidential_simulation",
                "catalog_split_role": "train_pool_only", "training_use_permitted": True, "evaluation_use_permitted": False,
                "authoring_method": "codex_direct_authored_catalog_training_v1",
                "generation_lineage": ["generator:codex:direct-authored-catalog-training-v1"],
                "decision_bucket": "direct_authored_training_candidate", "gate_version": "direct_authored_quality_v1",
                "primary_judge_model": "codex-editorial-audit-v1", "judging_lineage": ["primary_judge:codex-editorial-audit-v1"],
                "consensus_evidence": _editorial_audit(policy, grade), "requires_manual_audit": False,
                "claim_scope": "Direct-authored Proxy training only; not customer-real evidence, golden evaluation, or Locked Gold.",
            }
            rows.append(row)
    assert len(rows) == 2700 and Counter(str(row["label"]) for row in rows) == targets
    assert len({str(row["text"]) for row in rows}) == len(rows)
    return rows, catalog
def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite immutable output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
def main() -> int:
    rows, catalog = build_records()
    failures = [row["doc_id"] for row in rows if not validate_proxy_record(row, stage="eligible", intended_use="training").ok]
    if failures:
        raise RuntimeError(json.dumps(failures[:20], ensure_ascii=False))
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in rows)
    _write_new(OUT, payload)
    manifest = {"schema": "direct-authored-catalog-training-v1", "records": len(rows), "records_sha256": _sha256(payload), "factor_profiles": len({str(row["factor_profile_id"]) for row in rows}), "scenarios": len({str(row["scenario_id"]) for row in rows}), "catalog_version": catalog["version"], "catalog_sha256": _sha256(CATALOG.read_bytes()), "training_only": True}
    _write_new(MANIFEST, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
