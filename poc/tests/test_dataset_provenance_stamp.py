"""학습셋 빌더가 doc_id·출처를 찍는가 — 그리고 **실문서를 합성으로 찍지 않는가.**

왜 이 시험이 있는가(2026-09-05). 배포본 학습셋 2,554행 중 **840행이 doc_id 도
document_origin 도 없었다**(88.4% 가 unknown). 전부 두 코퍼스에서 왔다:

    rag_corpus_v2   720행   본문이 [가상기업A] 를 쓰는 생성물
    bilingual_en    120행   같은 문서의 영어판("[Company A]" · "Level 1 Secret")

⚠ 840행을 한꺼번에 synthetic 으로 찍기 전에 각각을 열어 봤다. bilingual 원본
  (labeled_p1_bilingual_exp/train.jsonl 6,711행)에는 **실제 판례가 3,554행 섞여 있다.**
  통째로 찍었으면 실문서를 합성으로 오기재할 뻔했다 — 그날 아침 학습셋에서 찾아낸 것과
  똑같은 오류를 내가 만드는 셈이었다. 실제로 뽑히는 120행은 영문 비중 30% 초과분뿐이고
  판례 검출 0건이라 합성이 맞다.

이 시험이 지키는 것은 그 경계다. 출처는 **반출 게이트가 읽는 축**이라
(koipa.golden_tiers.may_send_to_commercial_llm) 틀리게 찍으면 안전 판단이 틀어진다.
"""
from __future__ import annotations

import json

import pytest

from scripts.build_p1_v5_clean import (
    is_public_ruling,
    load_english,
    load_rag,
)
from koipa.golden_tiers import ORIGIN_PUBLIC_REAL, ORIGIN_SYNTHETIC, document_origin


def _write_rag(tmp_path, name, body, grade="S1", match=True):
    p = tmp_path / (name + ".json")
    p.write_text(json.dumps({
        "title": "제목", "body": body, "target_grade": grade,
        "label_match": match, "domain": "business",
    }, ensure_ascii=False), encoding="utf-8")
    return p


def test_rag_corpus_rows_get_doc_id_and_origin(tmp_path):
    _write_rag(tmp_path, "business_S1_000", "본 문서는 [가상기업A] 의 자료입니다. " * 6)
    rows, skipped = load_rag(tmp_path)
    assert skipped == 0 and len(rows) == 1
    r = rows[0]
    assert r["doc_id"] == "rag_corpus_v2/business_S1_000", r["doc_id"]
    assert document_origin(r) == ORIGIN_SYNTHETIC


def test_rag_doc_id_is_stable_across_builds(tmp_path):
    """파일명이 곧 신원 — 두 번 돌려도 같은 id 가 나온다."""
    _write_rag(tmp_path, "business_S2_007", "가상기업A 의 내부 검토 자료. " * 8, grade="S2")
    a = load_rag(tmp_path)[0][0]["doc_id"]
    b = load_rag(tmp_path)[0][0]["doc_id"]
    assert a == b == "rag_corpus_v2/business_S2_007"


def _write_bilingual(tmp_path, rows):
    p = tmp_path / "bilingual.jsonl"
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return p


def test_english_rows_get_doc_id_and_origin(tmp_path):
    src = _write_bilingual(tmp_path, [
        {"label": g, "text": "This document is Level %d Secret material of [Company A] "
                             "covering pricing structure and process know-how." % (i + 1)}
        for i, g in enumerate(("TS", "S1", "S2", "S3"))
    ])
    rows = load_english(src, 1)
    assert len(rows) == 4
    for r in rows:
        assert r["doc_id"].startswith("bilingual_en/"), r["doc_id"]
        assert document_origin(r) == ORIGIN_SYNTHETIC


def test_english_doc_id_is_content_derived_not_positional(tmp_path):
    """같은 본문은 순서가 바뀌어도 같은 id — 빌드마다 신원이 흔들리면 추적이 안 된다."""
    texts = ["This is Level 1 Secret material of [Company A] regarding pricing. %d" % i
             for i in range(3)]
    a = _write_bilingual(tmp_path / "a" if False else tmp_path, [{"label": "TS", "text": t} for t in texts])
    ids_1 = sorted(r["doc_id"] for r in load_english(a, 3))
    b = _write_bilingual(tmp_path, [{"label": "TS", "text": t} for t in reversed(texts)])
    ids_2 = sorted(r["doc_id"] for r in load_english(b, 3))
    assert ids_1 == ids_2


def test_english_loader_only_takes_english_heavy_rows(tmp_path):
    """한국어 판례가 섞인 원본에서 판례를 끌어오지 않는다 — 이것이 오기재를 막는 경계다."""
    ruling = ("권리범위확인 【심판청구인, 상고인】 조중환 외 9인 【피심판청구인】 특허청장 "
              "【주 문】 상고를 기각한다. 【이 유】 상고이유를 판단한다. " * 3)
    assert is_public_ruling({"text": ruling}), "전제: 이 본문은 판례로 검출된다"
    src = _write_bilingual(tmp_path, [
        {"label": "TS", "text": ruling},
        {"label": "TS", "text": "This document is Level 1 Secret material of [Company A] "
                                "describing the cost structure and supplier terms."},
    ])
    rows = load_english(src, 5)
    assert len(rows) == 1, "한국어 판례가 영어 몫으로 뽑혔다"
    assert not is_public_ruling(rows[0])
    assert document_origin(rows[0]) == ORIGIN_SYNTHETIC


def test_public_real_origin_is_never_overwritten_by_the_stamp():
    """찍기는 두 합성 로더 안에서만 한다 — 실문서 출처를 건드리지 않는다."""
    real = {"text": "판결문 본문", "source": "판례"}
    assert document_origin(real) == ORIGIN_PUBLIC_REAL


@pytest.mark.parametrize("origin", [ORIGIN_SYNTHETIC, ORIGIN_PUBLIC_REAL])
def test_stamped_origins_are_valid_gate_values(origin):
    """반출 게이트가 읽는 축이라 값이 어휘 안에 있어야 한다."""
    from koipa.golden_tiers import _VALID_ORIGINS

    assert origin in _VALID_ORIGINS
