"""constructed_floor 빌더(run_build) — LLM 없는 결정적 배관 테스트.

계약: 생성→결정적 admission 체인에 LLM 심판이 없고(순환 0), 라벨은 floor로만 나가며,
witness 학습누출·상위등급 오염·witness 미포함은 rejected로 사유가 남는다(실패만 정보).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from lloydk.modules.m1_synthesis.witness_taxonomy import (
    WITNESS_TYPES,
    instantiate_witness,
    specs_by_grade,
)


def _load():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    script = scripts_dir / "build_constructed_floor_set.py"
    spec = importlib.util.spec_from_file_location("_build_constructed_floor", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load()


def test_taxonomy_shape_and_authority():
    # v1: TS/S1/S2 각 4유형, S1·S2는 도메인 집합 공유(도메인→등급 shortcut 차단)
    assert {s.grade for s in WITNESS_TYPES} == {"TS", "S1", "S2"}
    s1_domains = {s.domain for s in specs_by_grade("S1")}
    s2_domains = {s.domain for s in specs_by_grade("S2")}
    assert s1_domains == s2_domains  # 도메인으로 S1/S2를 맞출 수 없다
    # TS basis는 외부 열거 인용 필수(권위 계층화)
    for s in specs_by_grade("TS"):
        assert any(m in s.basis for m in ("§9", "고시", "방위산업기술보호법", "산업기술보호법"))
    # 슬롯 인스턴스화는 결정적이고 index마다 달라진다
    s0 = instantiate_witness(WITNESS_TYPES[0], 0)
    assert s0 == instantiate_witness(WITNESS_TYPES[0], 0)
    assert s0 != instantiate_witness(WITNESS_TYPES[0], 1)
    assert "{v" not in s0  # 미치환 슬롯 없음


def test_fake_build_admits_clean_docs_floor_only():
    result = M.run_build(list(WITNESS_TYPES), 2, M._fake_generate, [], log=lambda *_: None)
    stats = result["stats"]
    assert stats["generated_target"] == len(WITNESS_TYPES) * 2
    assert stats["admitted"] == stats["generated_target"] and stats["rejected"] == 0
    for r in result["admitted"]:
        assert r["label_kind"] == "floor"                      # exact 주장 없음
        assert r["label"] == r["intended_grade"]
        assert "constructed_floor" in r["truth_warning"]
        assert r["tier"] == "constructed_floor_eval"           # locked와 혼동 금지


def test_lexicon_leak_rejects_whole_witness():
    specs = specs_by_grade("S2")[:1]
    leaked = instantiate_witness(specs[0], 0)
    result = M.run_build(specs, 2, M._fake_generate, [f"학습 문서에 {leaked} 포함"],
                         log=lambda *_: None)
    # index 0 인스턴스만 학습에 존재 → 그 레코드는 탈락, index 1은 통과
    assert result["stats"]["reject_reason_hist"].get("lexicon_leak_train") == 1
    assert result["stats"]["admitted"] == 1
    assert result["stats"]["lexicon_offenders"][0]["token"] == leaked


def test_contaminated_generation_rejected():
    def bad_gen(spec, token, index):
        if index == 0:
            return f"검토 메모\n\n{token} 관련 내용. 한편 본 건은 극비 프로젝트와 연계된다." + " 상세." * 20
        return M._fake_generate(spec, token, index)

    specs = specs_by_grade("S2")[:1]
    result = M.run_build(specs, 2, bad_gen, [], log=lambda *_: None)
    assert result["stats"]["admitted"] == 1
    assert result["stats"]["reject_reason_hist"].get("upper_grade_contamination") == 1


def test_witness_missing_rejected_and_gen_failure_tolerated():
    def flaky_gen(spec, token, index):
        if index == 0:
            raise RuntimeError("llm down")     # 생성 실패 — 런은 계속
        return "제목\n\n" + ("witness 없는 본문. " * 20)  # witness 미포함 — 탈락

    specs = specs_by_grade("S1")[:1]
    result = M.run_build(specs, 2, flaky_gen, [], log=lambda *_: None)
    assert result["stats"]["gen_fail"] == 1
    assert result["stats"]["admitted"] == 0
    assert result["stats"]["reject_reason_hist"].get("witness_missing_or_negated") == 1
