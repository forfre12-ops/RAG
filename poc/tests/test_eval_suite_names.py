# -*- coding: utf-8 -*-
"""평가면 이름이 유일한지 — 한 이름이 두 파일을 가리키면 수치가 뒤섞인다.

왜(2026-09-14). `holdout109` 가 **세 자리에서 서로 다른 뜻**이었다.

    audit_eval_ground_truth.EVAL_SETS     → gold_real/holdout_eval.jsonl
    regression_gate.EVAL_SETS             → _rejudge_claude/holdout109_provenance_corrected.jsonl
    regression_gate.MODEL_DECISION_SETS   → gold_real/holdout_eval.jsonl

앞 둘은 본문 sha1 이 109/109 같은데 **라벨이 24건(22.0%) 다르다**. 즉 두 도구가 낸
'holdout109 정확도' 는 애초에 같은 축의 값이 아니었고, 나란히 놓고 비교한 적이 있다.

이름을 가르는 것과 '어느 쪽이 정본인가' 는 다른 문제다 — 후자는 사람이 정할 일이라
여기서는 이름만 유일하게 만들고, 다시 겹치면 시험이 잡는다.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "scripts"))


def _registries() -> dict[str, dict[str, str]]:
    """이름→경로 표를 들고 있는 자리를 모은다. 새로 생기면 여기에 추가할 것."""
    import audit_eval_ground_truth as agt
    import regression_gate as rg
    return {
        "audit_eval_ground_truth.EVAL_SETS": dict(agt.EVAL_SETS),
        "regression_gate.EVAL_SETS": dict(rg.EVAL_SETS),
        "regression_gate.MODEL_DECISION_SETS": dict(rg.MODEL_DECISION_SETS),
    }


def test_one_name_means_one_file() -> None:
    """같은 이름이 서로 다른 경로를 가리키면 실패한다."""
    by_name: dict[str, set[str]] = defaultdict(set)
    where: dict[str, list[str]] = defaultdict(list)
    for reg, table in _registries().items():
        for name, path in table.items():
            by_name[name].add(path.replace("\\", "/"))
            where[name].append(f"{reg}={path}")
    clashes = {n: sorted(p) for n, p in by_name.items() if len(p) > 1}
    assert not clashes, (
        "한 이름이 여러 파일을 가리킨다 — 수치가 뒤섞인다:\n  "
        + "\n  ".join(f"{n}: {where[n]}" for n in clashes)
    )


def test_every_registered_path_exists_or_is_known_missing() -> None:
    """등록된 경로가 없으면 그 도구는 조용히 빈 결과를 낸다 — 초록불이 거짓말이 된다."""
    missing = []
    for reg, table in _registries().items():
        for name, path in table.items():
            if not (POC / path).exists():
                missing.append(f"{reg}[{name}] = {path}")
    if missing:
        pytest.skip(
            "일부 평가셋이 이 작업본에 없다(다수가 gitignore) — "
            "CI 에서 의미가 생기는 검사다:\n  " + "\n  ".join(missing)
        )


def test_the_two_holdout109_files_still_disagree() -> None:
    """두 파일의 라벨 불일치가 알려진 4건 그대로인지 — 정본이 정해지면 이 시험을 갱신한다.

    수를 고정하는 이유는 '누가 조용히 한쪽을 덮어썼는가' 를 잡기 위해서다.

    갱신(2026-09-22). 불일치는 원래 24건이었다. 2026-09-20 커밋 5137f153 이 holdout_eval.jsonl 에서
    rule_grade=S3 를 LLM 판정이 덮어쓴 22건을 S3 로 정정해 4건으로 줄었다(정정 전 라벨은
    label_before_correction_2026_09_20 필드에 보존). 그 정정은 의도된 것이었고 이 시험이 뒤따라
    갱신되지 않았다 — 개수만이 아니라 남은 문서를 직접 적어 두어, 이 4건 밖의 변화도 잡는다.
    남은 4건: 공개 판결문 3건(source=판례)은 holdout_eval 에서 S1·provenance_corrected 에서 S3,
    금융보고서 1건은 S3 대 S2. 어느 쪽이 정본인지는 아직 정해지지 않았다.
    """
    import json
    a = POC / "datasets/gold_real/holdout_eval.jsonl"
    b = POC / "datasets/gold_real/_rejudge_claude/holdout109_provenance_corrected.jsonl"
    if not (a.exists() and b.exists()):
        pytest.skip("두 파일 중 하나가 없다(gitignore)")

    def load(p):
        out = {}
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            did = str(r.get("doc_id") or "")
            if did:
                out[did] = str(r.get("label") or "")
        return out

    xa, xb = load(a), load(b)
    common = set(xa) & set(xb)
    assert len(common) == 109, f"공통 doc_id 가 {len(common)}건이다"
    diff = sorted(d for d in common if xa[d] != xb[d])
    known = sorted(["23a1f4fa03e6cbd7", "3ee4da9b5ad3ff2c", "677eb3bdbcf20ea3", "f9833fb185fd83fe"])
    assert diff == known, (
        f"라벨 불일치가 알려진 4건 {known} 에서 {len(diff)}건 {diff} 으로 바뀌었다 — "
        "누가 한쪽을 고쳤다면 정본 결정이 있었는지 확인할 것"
    )
