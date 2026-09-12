"""anchor_corpus (B 어셈블러) — 외부 사실 앵커 정규화·필수토큰·로더·dedup·제외정책 테스트."""
from __future__ import annotations

import json

from koipa.modules.m6_evaluation.anchor_corpus import (
    AnchorSource,
    extract_body_fact_tokens,
    extract_required_tokens,
    extract_required_tokens_detailed,
    load_anchor_corpus,
    normalize_gold_real,
    normalize_nkt,
)


# ---- NKT 정규화 ----

def test_nkt_valid_grade_normalized():
    raw = {"text": "플라즈마 공정챔버 모니터링", "label": "TS",
           "grade_basis": "nkt:AACACB:TS-kw:반도체", "domain": "반도체", "app_no": "2001-1"}
    rec = normalize_nkt(raw)
    assert rec is not None
    assert rec.anchor_grade == "TS"
    assert rec.source == "anchor_nkt"
    assert rec.anchor_id == "nkt:2001-1"
    assert "반도체" in rec.required_tokens  # TS-kw 추출


def test_nkt_invalid_grade_rejected():
    assert normalize_nkt({"text": "x", "label": "S9"}) is None
    assert normalize_nkt({"text": "", "label": "TS"}) is None  # 빈 텍스트


# ---- gold_real 정규화 ----

def test_gold_real_uses_doc_id_and_all_grades():
    raw = {"doc_id": "abc123", "text": "내부 경영실적 (미확정)", "label": "S2",
           "legal_reference": "KOIPA §3.2.2", "evidence_spans": []}
    rec = normalize_gold_real(raw)
    assert rec is not None
    assert rec.anchor_grade == "S2"
    assert rec.anchor_id == "gold:abc123"
    assert rec.source == "holdout_gold"


def test_gold_real_expected_grade_fallback():
    rec = normalize_gold_real({"doc_id": "d", "text": "t", "expected_grade": "S1"})
    assert rec is not None and rec.anchor_grade == "S1"


# ---- 필수토큰 추출(D용) ----

def test_required_tokens_from_evidence_span():
    text = "EUV 노광 레시피 파라미터. 본 자료는 국가핵심기술 소분류 대상."
    raw = {"text": text, "evidence_spans": [{"start": 0, "end": 13, "factor": "VALUE"}]}
    toks = extract_required_tokens(raw)
    assert toks  # 근거 구간에서 토큰 추출
    assert "EUV" in toks[0]


def test_required_tokens_empty_when_no_signal():
    # 근거·grade_basis·domain 전무 → 빈 리스트(D가 '검사 불가'로 처리)
    assert extract_required_tokens({"text": "아무 내용"}) == []


def test_required_tokens_domain_fallback():
    toks = extract_required_tokens({"text": "t", "domain": "이차전지"})
    assert toks == ["이차전지"]
    # mixed/unknown은 폴백 안 함
    assert extract_required_tokens({"text": "t", "domain": "mixed"}) == []


# ---- 토큰 출처(provenance) — 역방향 적격성 판정 기반 ----

def test_token_provenance_title_vs_midbody():
    text = "문서 제목입니다. 그리고 본문에 EUV 레시피 사실이 있다."
    detailed = extract_required_tokens_detailed({
        "text": text,
        "evidence_spans": [
            {"start": 0, "end": 7},                 # 제목(start=0)
            {"start": text.index("EUV"), "end": text.index("EUV") + 8},  # 본문 중간
        ],
    })
    srcs = {tok: src for tok, src in detailed}
    assert any(s == "evidence@0" for s in srcs.values())   # 제목
    assert any(s == "evidence@N" for s in srcs.values())   # 본문 중간


def test_token_provenance_domain_and_kw():
    d = extract_required_tokens_detailed({"text": "t", "grade_basis": "nkt:AB:TS-kw:반도체"})
    assert ("반도체", "ts_kw") in d
    d2 = extract_required_tokens_detailed({"text": "t", "domain": "이차전지"})
    assert d2 == [("이차전지", "domain")]


def test_record_carries_token_sources():
    rec = normalize_nkt({"text": "x", "label": "TS", "grade_basis": "nkt:AB:TS-kw:우주", "app_no": "P1"})
    assert rec.token_sources == ["ts_kw"]
    assert len(rec.token_sources) == len(rec.required_tokens)


# ---- 본문 구체값(body_fact) — 역방향 적격 강토큰(gold 구조화문서 한정) ----

def test_body_fact_extracts_numeric_specifics():
    text = "수율 개선 메모\n\n불량률 2.3%, 수율 목표 97% 달성, 라인 3개 증설."
    toks = extract_body_fact_tokens(text)
    assert any("2.3%" in t for t in toks)
    assert any("97%" in t for t in toks)


def test_body_fact_skips_title_line():
    # 제목(첫 줄)의 수치는 제외 — 본문만 본다.
    text = "2024 3분기 보고서\n\n영업이익 12% 증가."
    toks = extract_body_fact_tokens(text)
    assert any("12%" in t for t in toks)


def test_nkt_excludes_body_facts_gold_includes():
    body = "수율 메모\n\n불량률 2.3%, 수율 97% 달성."
    nkt = normalize_nkt({"text": body, "label": "S1", "app_no": "P1"})
    gold = normalize_gold_real({"text": body, "label": "S1", "doc_id": "D1"})
    assert "body_fact" not in nkt.token_sources           # NKT는 부수숫자라 끔
    assert "body_fact" in gold.token_sources              # gold 구조화문서는 포함


def test_include_body_facts_flag():
    d_on = extract_required_tokens_detailed({"text": "t\n\n수율 97% 달성"}, include_body_facts=True)
    d_off = extract_required_tokens_detailed({"text": "t\n\n수율 97% 달성"}, include_body_facts=False)
    assert any(s == "body_fact" for _, s in d_on)
    assert all(s != "body_fact" for _, s in d_off)


# ---- 로더 + dedup + 제외정책 ----

def _write_jsonl(p, rows):
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")


def test_loader_dedups_by_anchor_id(tmp_path):
    f = tmp_path / "nkt.jsonl"
    _write_jsonl(f, [
        {"text": "a", "label": "TS", "app_no": "P1"},
        {"text": "a-dup", "label": "TS", "app_no": "P1"},  # 같은 app_no → dedup
        {"text": "b", "label": "S1", "app_no": "P2"},
    ])
    recs = load_anchor_corpus([AnchorSource("nkt.jsonl", "nkt", "anchor_nkt")], root=tmp_path)
    assert len(recs) == 2
    assert {r.anchor_id for r in recs} == {"nkt:P1", "nkt:P2"}


def test_loader_skips_missing_file(tmp_path):
    recs = load_anchor_corpus([AnchorSource("nope.jsonl", "nkt", "anchor_nkt")], root=tmp_path)
    assert recs == []


def test_unknown_kind_raises(tmp_path):
    f = tmp_path / "x.jsonl"
    _write_jsonl(f, [{"text": "a", "label": "TS"}])
    try:
        load_anchor_corpus([AnchorSource("x.jsonl", "trade_secret_raw", "x")], root=tmp_path)
        assert False, "trade_secret raw는 등급앵커 normalizer 없음 → 실패해야"
    except ValueError as e:
        assert "unknown anchor source kind" in str(e)
