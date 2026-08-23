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
    assert "guardWrite('검증 기준문서 생성')" in admin


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

# [2026-08-22] 07 은 화면 목록에서 뺐다(사용자 결정) - 비공개 M&A 메모라 모델의 S2 판정을
# 화면이 먼저 말해 주는 자리에 두지 않는다. 파일은 남기고 판정은 scripts/check_demo_docs.py
# 가 계속 확인한다(합의 게이트로 검수행인지가 abstain 범위 회귀 신호).
_DEMO_SAMPLES_OFF_SCREEN = ("07_HYBRID_semantic_secret.docx",)
_DEMO_SAMPLES_ON_SCREEN = tuple(s for s in _DEMO_SAMPLES if s not in _DEMO_SAMPLES_OFF_SCREEN)


def test_merged_demo_section_kept_every_sample():
    """통합하다 샘플을 흘리면 시연 대본이 어긋난다 — 화면 목록과 파일을 따로 본다."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    missing = [s for s in _DEMO_SAMPLES_ON_SCREEN if s not in page]
    assert not missing, f"흡수 과정에서 빠진 샘플: {missing}"
    for name in _DEMO_SAMPLES:      # 화면에서 뺀 것도 파일은 있어야 한다(점검기가 태운다)
        assert (STATIC / "demo_docs" / name).exists(), name


def test_merged_demo_section_exists_and_is_linked():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="sec-parse"' in page, "흡수한 구역이 없다"
    assert 'href="./parse_demo.html"' not in page, "같은 페이지 안에서 옛 화면으로 나간다"
    assert 'href="#sec-parse"' in page, "구역으로 가는 내부 링크가 없다"


def test_merged_demo_wiring_lives_in_the_module():
    """[2026-08-24] 파싱·분류 시연을 §1·§2 로 합치면서 인라인 전역 스크립트를 없앴다.

    합치기 전에는 같은 엔드포인트(POST /documents/analyze)를 두 코드가 각각 호출하고
    등급 결과 카드를 두 벌 그렸다. 지금은 배선이 app.js(ES module) 한 곳이다.
    다시 인라인 스크립트가 생기면 그 이중 구조가 되살아난 것이므로 여기서 막는다.
    """
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "파싱·분류 시연 스크립트" not in page, "인라인 시연 스크립트가 되살아났다"
    assert 'type="module" src="./app.js"' in page, "모듈 배선이 없다"
    for fn in ("analyzeFile", "analyzeLargeAsync", "persistToQueue", "renderParseDetail"):
        assert fn in app, f"app.js 로 옮긴 기능이 없다: {fn}"
    # 파일 샘플은 인라인 onclick 이 아니라 data 속성 + 리스너로 배선한다(모듈엔 전역이 없다).
    assert "loadSample(" not in page, "옛 전역 함수 호출이 남아 있다"
    assert page.count("data-doc=") >= 6, "참고 샘플 배선(data-doc)이 빠졌다"


def test_demo_reflect_marker_is_preserved():
    """실적재 마커를 바꾸면 「데모 데이터 초기화」가 이 화면이 만든 문서를 못 지운다.

    admin.py 의 DEMO_CREATED_BY='demo-console' 하나로 물리삭제 범위가 고정돼 있다.
    """
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "demo-console" in page, "화면 안내에서 마커가 빠졌다"
    assert "demo-console" in app, "실적재 요청에서 마커가 빠졌다"


def test_demo_button_grouping_matches_measured_expectations():
    """시연 버튼의 자동확정/검수 묶음이 점검기(check_demo_docs.py)의 기대와 같은가.

    왜(2026-08-22). 이 화면의 버튼 묶음은 실측으로 두 번 어긋났다 - 처음은 배치가 낡아서,
    두 번째는 서빙 설정이 바뀌어서(온도 3.0→2.03 · 룰 무근거 abstain). 화면과 점검기가
    따로 놀면 "점검은 통과했는데 화면은 거짓말"이 된다. 둘을 한 자리에서 묶어 둔다.
    실제 판정과 맞는지는 scripts/check_demo_docs.py 가 문서를 태워서 본다(모델 필요).
    """
    import sys

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from check_demo_docs import DEMO_EXPECTATIONS

    page = (STATIC / "index.html").read_text(encoding="utf-8")
    wrong = []
    for exp in DEMO_EXPECTATIONS:
        line = next((ln for ln in page.splitlines()
                     if exp["file"] in ln and "sample-btn" in ln), None)
        if exp.get("on_screen") is False:
            if line is not None:
                wrong.append(f"{exp['file']}: 화면에서 빼기로 한 문서인데 버튼이 있다")
            continue
        if line is None:
            wrong.append(f"{exp['file']}: 버튼이 없다")
            continue
        # ok = 자동확정으로 광고, bad = 검수로 광고
        advertised = "staging" if "sample-btn ok" in line else "needs_review"
        if advertised != exp["status"]:
            wrong.append(f"{exp['file']}: 화면은 {advertised} 인데 기대는 {exp['status']}")
    assert not wrong, "시연 버튼 묶음이 점검기 기대와 다르다: " + "; ".join(wrong)


def test_init_functions_do_not_reference_removed_elements():
    """init 이 부르는 함수가 없는 요소를 만지면 **그 뒤 초기화가 통째로 멈춘다.**

    실측 2026-08-23. 「분류 실행」의 샘플 드롭다운을 화면에서 뺐는데 populateSamples() 초기화는
    남겨 두었다. init 순서가 populateSamples() → checkHealth() 라, 여기서 TypeError 가 나며
    checkHealth 가 실행되지 않았다 — 서버 검수 임계가 '—' 로 남고 프로파일 배너도 안 떴다.
    화면 절반이 죽는데 원인은 지운 요소 하나였고, 화면에는 오류 표시가 없었다.
    """
    import re

    page = (STATIC / "admin.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([\w-]+)"', page))
    init = re.search(r"/\* init \*/(.*?)</script>", page, re.S)
    assert init, "init 블록을 찾지 못했다 — 이 테스트의 전제가 깨졌다"
    called = re.findall(r"^\s*(\w+)\(\);", init.group(1), re.M)
    assert called, "init 에서 호출하는 함수를 찾지 못했다"

    missing = []
    for fn in called:
        body = re.search(r"function %s\s*\([^)]*\)\s*\{(.*?)\n\}" % re.escape(fn), page, re.S)
        if not body:
            continue
        src = body.group(1)
        for ref in sorted(set(re.findall(r"\$\('([\w-]+)'\)", src))):
            if ref in ids:
                continue
            # 요소가 없어도 되는 경우 — 그 변수에 null 가드가 있으면 안전하다.
            var = re.search(r"(?:const|let|var)\s+(\w+)\s*=\s*\$\('%s'\)" % re.escape(ref), src)
            guarded = bool(var) and re.search(
                r"if\s*\(\s*!\s*%s\s*\)\s*return" % re.escape(var.group(1)), src)
            if not guarded:
                missing.append(f"{fn}() → #{ref}")
    assert not missing, "init 함수가 없는 요소를 가드 없이 만진다: " + ", ".join(missing)
