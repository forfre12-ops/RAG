from __future__ import annotations

from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "static"


def test_demo_pages_are_airgap_safe_static_assets():
    for name in ("index.html", "parse_demo.html", "admin.html", "app.js"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in text
        assert "fonts.gstatic.com" not in text
        assert "devkey" not in text


def test_demo_safe_mode_and_golden_builder_are_visible():
    admin = (STATIC / "admin.html").read_text(encoding="utf-8")
    assert 'id="cfg-write-enable"' in admin
    assert "Safe Mode" in admin
    assert "POST /golden/build" in admin
    assert "function startGoldenBuild" in admin
    assert "guardWrite('골든셋 후보 생성')" in admin


def test_parser_demo_has_table_sample_pack():
    """[D3 2026-08-18] 읽는 파일이 parse_demo.html → index.html 로 바뀌었다.

    시연 화면이 분류 콘솔 안의 구역(#sec-parse)으로 흡수됐다. 잠그는 내용은 그대로다 —
    표(xlsx) 샘플이 화면에 걸려 있고 파일도 실재하는가.
    """
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    sample = STATIC / "demo_docs" / "03_S2_supplier_price.xlsx"
    assert sample.exists()
    assert sample.stat().st_size > 1000
    assert "03_S2_supplier_price.xlsx" in page


_DEMO_SAMPLES = (
    "01_TS_semiconductor_euv.docx", "02_S1_recsys_source_license.docx",
    "03_S2_supplier_price.xlsx", "04_S3_press_release.docx",
    "05_S1_tech_transfer.pdf", "06_FAIL_thin_text.txt",
    "07_HYBRID_semantic_secret.docx",
)


def test_merged_demo_section_kept_every_sample():
    """통합하다 샘플을 흘리면 시연 대본이 어긋난다 — 7개가 다 왔는지 본다."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    missing = [s for s in _DEMO_SAMPLES if s not in page]
    assert not missing, f"흡수 과정에서 빠진 샘플: {missing}"
    for name in _DEMO_SAMPLES:
        assert (STATIC / "demo_docs" / name).exists(), name


def test_merged_demo_section_exists_and_is_linked():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="sec-parse"' in page, "흡수한 구역이 없다"
    assert 'href="./parse_demo.html"' not in page, "같은 페이지 안에서 옛 화면으로 나간다"
    assert 'href="#sec-parse"' in page, "구역으로 가는 내부 링크가 없다"


def test_merged_demo_script_stays_classic_not_module():
    """인라인 onclick 이 전역 함수를 부른다 — type=module 로 바꾸면 전부 죽는다."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    i = page.index("[D3] 파싱·분류 시연 스크립트")
    head = page.rindex("<script", 0, i)
    assert "type=" not in page[head:i], "시연 스크립트가 module 로 바뀌었다"
    assert "onclick=" in page, "인라인 onclick 이 사라졌다면 배선을 다시 확인할 것"


def test_demo_reflect_marker_is_preserved():
    """실적재 마커를 바꾸면 「데모 데이터 초기화」가 이 화면이 만든 문서를 못 지운다.

    admin.py 의 DEMO_CREATED_BY='demo-console' 하나로 물리삭제 범위가 고정돼 있다.
    """
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "demo-console" in page
