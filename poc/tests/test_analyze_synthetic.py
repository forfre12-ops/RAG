"""analyze_synthetic.py 단위 테스트."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from analyze_synthetic import (  # noqa: E402
    analyze,
    distribution,
    diversity,
    evenness,
    label_confusion,
    length_stats,
    load_corpus,
    usage_summary,
)


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


def _make_doc(grade="TS", domain="tech", body="x" * 500, predicted=None,
              tokens_in=10, tokens_out=20, cost=0.0, latency=50.0,
              provider="noop", doc_type="기술자료") -> dict:
    return {
        "synth_id": f"id-{grade}-{domain}",
        "target_grade": grade,
        "domain": domain,
        "title": f"Title {grade}",
        "body": body,
        "document_type": doc_type,
        "dept_hint": f"Dept-{domain}",
        "predicted_grade": predicted or grade,
        "label_match": (predicted is None) or (predicted == grade),
        "llm_provider": provider,
        "pii_violations": [],
        "parse_error": None,
        "usage": {
            "input_tokens": tokens_in, "output_tokens": tokens_out,
            "cost_usd": cost, "latency_ms": latency,
        },
    }


@pytest.fixture
def synth_dir(tmp_path: Path) -> Path:
    """40건 균등 합성 fixture (4 등급 × 6 도메인 ≈ 24, 일부 중복으로 40)."""
    d = tmp_path / "synthetic"
    d.mkdir()
    docs = []
    for g in ["TS", "S1", "S2", "S3"]:
        for dom in ["tech", "business", "finance", "hr", "legal", "mixed"]:
            docs.append(_make_doc(grade=g, domain=dom))
        # 추가 4건씩 → 등급당 10
        for _ in range(4):
            docs.append(_make_doc(grade=g, domain="tech"))
    for i, doc in enumerate(docs):
        (d / f"doc-{i:03d}.json").write_text(json.dumps(doc), encoding="utf-8")
    return d


# ─────────────────────────────────────────────────────────────
# load_corpus
# ─────────────────────────────────────────────────────────────


def test_load_corpus_reads_all_json(synth_dir: Path):
    docs = load_corpus(synth_dir)
    assert len(docs) == 40
    assert all("target_grade" in d for d in docs)


def test_load_corpus_skips_invalid_json(tmp_path: Path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "good.json").write_text(json.dumps(_make_doc()), encoding="utf-8")
    (d / "bad.json").write_text("not json", encoding="utf-8")
    docs = load_corpus(d)
    assert len(docs) == 1


# ─────────────────────────────────────────────────────────────
# distribution
# ─────────────────────────────────────────────────────────────


def test_distribution_counts(synth_dir: Path):
    docs = load_corpus(synth_dir)
    dist = distribution(docs)
    assert dist["total"] == 40
    assert dist["by_grade"]["TS"] == 10
    assert dist["by_grade"]["S3"] == 10
    # 각 등급별로 6 도메인 1건 + tech 4건 추가 = tech 5건/등급 × 4등급 = 20
    assert dist["by_domain"]["tech"] == 20
    # 다른 도메인은 1건/등급 × 4등급 = 4
    assert dist["by_domain"]["business"] == 4


def test_distribution_cross_table(synth_dir: Path):
    docs = load_corpus(synth_dir)
    dist = distribution(docs)
    # TS에서 tech 카운트 = 1(균등 분배) + 4(추가) = 5
    # 다른 도메인은 1건씩
    assert dist["by_grade_domain"]["TS"]["tech"] == 5
    assert dist["by_grade_domain"]["TS"]["business"] == 1


# ─────────────────────────────────────────────────────────────
# evenness
# ─────────────────────────────────────────────────────────────


def test_evenness_perfect_balance():
    by_grade = {"TS": 200, "S1": 200, "S2": 200, "S3": 200}
    by_domain = {"a": 100, "b": 100}
    e = evenness(by_grade, by_domain)
    assert e["grade_cv"] == 0.0
    assert e["grade_balanced"] is True


def test_evenness_unbalanced_grades():
    by_grade = {"TS": 500, "S1": 100, "S2": 100, "S3": 100}
    e = evenness(by_grade, {})
    assert e["grade_cv"] > 0.5
    assert e["grade_balanced"] is False


# ─────────────────────────────────────────────────────────────
# length_stats
# ─────────────────────────────────────────────────────────────


def test_length_stats_basic(synth_dir: Path):
    docs = load_corpus(synth_dir)
    s = length_stats(docs)
    assert s["overall"]["count"] == 40
    # 모든 body 동일 길이 500 → stdev=0
    assert s["overall"]["mean"] == 500
    assert s["overall"]["stdev"] == 0.0


def test_length_stats_with_variation():
    docs = [_make_doc(body="x" * 100), _make_doc(body="x" * 200)]
    # 임시 디렉터리 없이 직접 호출
    s = length_stats(docs)
    assert s["overall"]["mean"] == 150
    assert s["overall"]["stdev"] > 0


# ─────────────────────────────────────────────────────────────
# label_confusion
# ─────────────────────────────────────────────────────────────


def test_label_confusion_all_match():
    docs = [_make_doc(grade="TS"), _make_doc(grade="S1")]
    cm = label_confusion(docs)
    assert cm["match_rate"] == 1.0
    assert cm["matrix_rows_target"]["TS"]["TS"] == 1


def test_label_confusion_with_misclassification():
    docs = [
        _make_doc(grade="TS", predicted="TS"),
        _make_doc(grade="TS", predicted="S1"),  # 미스클래스
        _make_doc(grade="S1", predicted="S1"),
    ]
    cm = label_confusion(docs)
    assert cm["match_rate"] == pytest.approx(2 / 3, abs=0.01)
    assert cm["matrix_rows_target"]["TS"]["TS"] == 1
    assert cm["matrix_rows_target"]["TS"]["S1"] == 1


# ─────────────────────────────────────────────────────────────
# usage_summary
# ─────────────────────────────────────────────────────────────


def test_usage_summary_aggregates_tokens(synth_dir: Path):
    docs = load_corpus(synth_dir)
    u = usage_summary(docs)
    # 40건 × (10 in + 20 out) = 400 / 800
    assert u["input_tokens_total"] == 400
    assert u["output_tokens_total"] == 800
    assert u["cost_usd_total"] == 0.0
    assert u["by_provider"]["noop"] == 40


def test_usage_summary_with_cost():
    docs = [
        _make_doc(cost=0.001),
        _make_doc(cost=0.002),
    ]
    u = usage_summary(docs)
    assert u["cost_usd_total"] == pytest.approx(0.003)


# ─────────────────────────────────────────────────────────────
# diversity
# ─────────────────────────────────────────────────────────────


def test_diversity_counts(synth_dir: Path):
    docs = load_corpus(synth_dir)
    div = diversity(docs)
    assert div["document_type_unique"] == 1  # fixture는 단일 doc_type
    assert div["pii_violations_total"] == 0
    assert div["parse_errors"] == 0


def test_diversity_pii_violations_aggregated():
    docs = [
        {**_make_doc(), "pii_violations": ["주민번호"]},
        {**_make_doc(), "pii_violations": ["전화번호", "이메일"]},
    ]
    div = diversity(docs)
    assert div["pii_violations_total"] == 3


# ─────────────────────────────────────────────────────────────
# 통합
# ─────────────────────────────────────────────────────────────


def test_analyze_full_pipeline(synth_dir: Path):
    result = analyze(synth_dir)
    assert "distribution" in result
    assert "length_stats" in result
    assert "label_confusion" in result
    assert "usage_summary" in result
    assert "diversity" in result
    assert "evenness" in result
    assert result["distribution"]["total"] == 40
    assert result["label_confusion"]["match_rate"] == 1.0
    assert result["evenness"]["grade_balanced"] is True


def test_analyze_empty_dir(tmp_path: Path):
    d = tmp_path / "empty"
    d.mkdir()
    result = analyze(d)
    assert result == {}
