"""M5 Inference — rule-fallback 경로 (모델 가중치 없을 때)."""

from __future__ import annotations

import pytest

from koipa.modules.m5_inference import InferencePipeline

pytestmark = pytest.mark.slow


def test_rule_fallback_predicts_all_grades():
    pipe = InferencePipeline()
    cases = [
        ("TS", "특급기밀 핵심 원천기술 M&A 계획 차세대 제품 설계도"),
        ("S1", "1급 비밀 영업비밀 공정 노하우 고객 데이터베이스 마케팅 전략"),
        ("S2", "대외비 내부 검토 분기 매출 사업 계획 거래처 명단"),
        ("S3", "보도자료 공시 채용 공고 회사 소개 이용약관"),
    ]
    for truth, text in cases:
        res = pipe.run(text=text, use_rag=False, metadata={}, return_evidence=True)
        pred = res.label.value if hasattr(res.label, "value") else str(res.label)
        assert pred == truth, f"target={truth}, predicted={pred}"
        assert res.model_version == "rule-fallback-v0"
        assert sum(res.scores.values()) > 0.99  # 확률 합 ≈ 1


def test_evidence_returned_when_requested():
    pipe = InferencePipeline()
    res = pipe.run(text="특급기밀 차세대 제품 설계도", return_evidence=True)
    assert len(res.evidence) > 0


def test_evidence_omitted_when_not_requested():
    pipe = InferencePipeline()
    res = pipe.run(text="특급기밀 차세대 제품 설계도", return_evidence=False)
    assert res.evidence == []


# ── [FIX-E] 약어-only 승격 백스톱 (공개특허 TS 과분류 완화) ─────────────────────
# 청크 severe-agg가 고등급으로 승격했으나 근거가 영문 약어 부스트(CVD·N2O 등)뿐이고
# 한국어 시드 근거가 없으면 abbrev-only-escalation 태그를 남긴다(등급은 무변경).
# 한국어 시드가 있으면 태그를 남기지 않아 진짜 기밀의 자동확정이 보존된다(FNR-safe).

def _abbrev_only_doc() -> str:
    # 공개특허 본문을 모사 — 공개공지 신호(공개특허)가 지배하나, 기술 본문에 CVD/N2O
    # 같은 범용 공정약어가 반복돼 일부 청크가 TS로 뜬다. 한국어 TS 시드는 전무.
    tech = (
        "산화물 반도체 트랜지스터의 박막을 CVD 방식으로 증착하고 N2O 분위기에서 처리한다. "
        "게이트 절연막은 CVD 로 형성하며 채널층도 CVD 공정으로 성막한다. "
    )
    return "공개특허공보 반도체 장치. " + tech * 40 + " 공개특허 공개번호 참조."


def test_fix_e_abbrev_only_promotion_tagged_grade_unchanged():
    pipe = InferencePipeline()
    res = pipe.run(text=_abbrev_only_doc(), use_rag=False, return_evidence=False)
    joined = " ".join(res.warnings or [])
    # 승격이 일어났고(청크 severe-agg), 그 근거가 약어-only → 전용 태그가 붙는다.
    if "chunk severe-agg" in joined:
        assert "abbrev-only-escalation" in joined, (
            "약어-only 승격인데 FIX-E 태그가 없음: " + joined
        )
        # 라벨은 승격 등급 그대로(하향 없음) — FIX-E는 등급을 절대 내리지 않는다.
        assert res.label.value in ("TS", "S1")


def test_fix_e_preserves_autoconfirm_for_korean_seed_secret():
    # 한국어 TS 시드(특급기밀·반도체 공정 레시피 등)가 있으면 약어가 섞여 있어도
    # abbrev-only-escalation 태그를 남기지 않는다 → 자동확정 보존(FNR-safe).
    pipe = InferencePipeline()
    text = (
        "본 자료는 특급기밀이며 반도체 공정 레시피와 EUV 공정 파라미터, 특수 합금 조성비를 "
        "CVD·N2O 공정으로 정리한다. 1급 비밀. "
    ) * 8
    res = pipe.run(text=text, use_rag=False, return_evidence=False)
    joined = " ".join(res.warnings or [])
    assert "abbrev-only-escalation" not in joined, (
        "한국어 시드 기반 고등급인데 약어-only로 오판: " + joined
    )
    assert res.label.value in ("TS", "S1")
