from __future__ import annotations

from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "src" / "lloydk" / "api" / "static"


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
    page = (STATIC / "parse_demo.html").read_text(encoding="utf-8")
    sample = STATIC / "samples" / "success_table_xlsx.xlsx"
    assert sample.exists()
    assert sample.stat().st_size > 1000
    assert "success_table_xlsx.xlsx" in page
