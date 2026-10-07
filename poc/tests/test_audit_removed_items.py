"""audit_removed_items.py — 삭제한 항목을 현행처럼 적은 문서 자리를 잡는지.

왜 있는가(2026-09-27). 9/26 에 표 4개·칼럼 18개·API 항목을 지웠는데 KL 에 나간 문서 사본에 `external_ref` 입력과 옛 표 이름이 그대로 남았고,
기존 검사기는 어느 것도 이를 못 잡았다. 이 도구가 그 자리를 세고, 아래 마지막 시험이 **저장소의 현행 문서에 그런 자리가 0 인 것**을 지킨다 —
표·칼럼을 지우는 새 판(alembic 삭제 판)을 만들면서 문서를 안 고치면 여기서 알린다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

import audit_removed_items as tool  # noqa: E402

REG = {
    "tb_guides": ("표", "삭제한 표"),
    "external_ref": ("API", "삭제한 API 항목"),
    "otsd_rfrnc_no": ("칼럼", "삭제한 칼럼"),
}


def test_registry_reads_the_drop_migrations_statically():
    tables, cols = tool.dropped_by_migrations()
    assert "tad_gm_guide_ver_mng" in tables                        # b7d3f5a19c24
    assert ("tad_lm_llm_usqty_mng", "kcur_cst") in cols            # e3a7c9f1b5d2 — 리터럴 COLUMNS
    assert ("tad_dm_doc_mng", "otsd_rfrnc_no") in cols
    # 표준 명명 판은 TABLES 가 dict 라 삭제 판으로 읽히면 안 된다
    assert not any(name.startswith("7b3e9d2a4f10") for name in tables.values())


def test_old_table_names_map_back():
    new2old_t, new2old_c = tool.old_names()
    assert new2old_t["tad_gm_guide_ver_mng"] == "tb_guides"
    assert new2old_c[("tad_dm_doc_mng", "otsd_rfrnc_no")] == "external_ref"


def test_registry_skips_names_that_are_still_alive_or_too_common():
    reg = tool.build_registry(live={"doc_id"})
    assert {"tb_guides", "tad_gm_guide_ver_mng", "kcur_cst", "external_ref", "ocr_used"} <= set(reg)
    assert "doc_id" not in reg                                     # 다른 표에서 살아 있는 이름 — 글로는 어느 표 것인지 못 가른다
    assert "kcur_cst" not in tool.build_registry(live={"kcur_cst"})


def test_mention_written_as_current_is_flagged():
    hits = tool.scan_text("<p>문서는 <code>tb_guides</code> 표에 저장한다.</p>", REG)
    assert [h["name"] for h in hits] == ["tb_guides"]
    assert not hits[0]["history"] and hits[0]["kind"] == "표"


def test_history_marker_next_to_the_name_is_not_flagged():
    assert tool.scan_text("<p>tb_guides 표는 2026-09-26 에 삭제했다.</p>", REG)[0]["history"]
    assert tool.scan_text("<p>API 정의서가 받던 external_ref 입력을 걷었다.</p>", REG)[0]["history"]


def test_a_document_that_discloses_the_removal_covers_its_other_mentions():
    text = "<p>tb_guides 에 저장한다.</p>" + "가" * 400 + "<div class='note'>정정: tb_guides 는 삭제했다.</div>"
    hits = tool.scan_text(text, REG)
    assert len(hits) == 2 and all(h["history"] for h in hits)
    # 밝히지 않은 문서는 그대로 잡힌다(대조 — 위 규칙이 아무거나 통과시키는 것이 아님)
    assert not any(h["history"] for h in tool.scan_text("<p>tb_guides 에 저장한다.</p>" + "가" * 400 + "<p>끝</p>", REG))


def test_revision_history_section_and_blocks_are_ignored():
    assert tool.scan_text("<h2><span class='num'>07</span> 개정 이력</h2><p>tb_guides 를 넣었다</p>", REG) == []
    assert tool.scan_text("<section id=\"revisions\"><p>tb_guides</p></section>", REG) == []
    assert tool.scan_text("<script>var a = 'tb_guides'</script><style>.tb_guides{}</style>", REG) == []


def test_name_must_match_on_word_boundaries():
    assert tool.scan_text("<p>tb_guides_extra · my_tb_guides</p>", REG) == []


def test_snapshot_documents_are_counted_apart_and_do_not_fail(tmp_path):
    (tmp_path / "current.html").write_text("<p>tb_guides 에 저장한다.</p>", encoding="utf-8")
    (tmp_path / "기술구현_백서_부록A_DB스키마.html").write_text("<p>tb_guides 에 저장한다.</p>", encoding="utf-8")
    (tmp_path / "계획_V1_DRAFT.md").write_text("tb_guides 를 쓴다", encoding="utf-8")
    result = tool.audit([tmp_path], REG)
    current, snapshot, _history = tool._classify(result)
    assert [Path(p).name for p in current] == ["current.html"]
    assert {Path(p).name for p in snapshot} == {"기술구현_백서_부록A_DB스키마.html", "계획_V1_DRAFT.md"}
    assert tool.main(["--root", str(tmp_path)]) == 1                     # 현행 자리가 있으면 종료 코드 1


def test_repository_docs_have_no_removed_item_written_as_current():
    """저장소의 현행 문서(doc/ · poc/docs/)에 삭제한 표·칼럼·API 항목이 현행처럼 적힌 자리가 없다.

    실패하면: 그 문서를 현행으로 고치거나, 지웠다는 사실을 밝히는 「정정」 상자를 달거나(문서 안에서 한 번 밝히면 나머지가 덮인다),
    날짜 박힌 기록 문서면 audit_removed_items.SNAPSHOTS 에 올린다.
    """
    root = _POC.parent
    if not (root / "doc").is_dir():
        pytest.skip("doc/ 폴더가 없다 — 문서 없이 배포된 사본")
    result = tool.audit([root / "doc", _POC / "docs"], tool.build_registry())
    current, _snapshot, _history = tool._classify(result)
    assert not current, "삭제한 항목을 현행처럼 적은 문서: %s" % {
        str(Path(p).relative_to(root)): sorted({h["name"] for h in hits}) for p, hits in current.items()}
