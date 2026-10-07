"""문서 렌더러(mdToHtml)의 한국어 규칙을 입력→출력으로 잠근다.

왜(2026-08-19). console_doc.py 주석에는 실측 근거가 촘촘하다 — 마침표 3,208개 중 68%가
숫자 뒤라 전부에서 끊으면 법령 인용이 깨진다, `가./나./다.` 는 종결어미 `다.` 와 글자가 같다,
원문자는 네 블록에 흩어져 있다. **그런데 그 규칙을 잠근 시험이 하나도 없었다.**
지금 시험은 "함수가 실려 있다 / 예외 없이 돈다" 까지만 본다.

여기서는 렌더러를 node 로 **실행해** 출력 모양을 확인한다. 아래 기대값은 짐작이 아니라
2026-08-19 에 실제 출력을 보고 적은 것이다.

⚠ node 가 없으면 skip 한다.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from koipa.console_doc import DOC_RENDER_JS

_RUNNER = r"""
const fs = require('fs');
eval(fs.readFileSync(process.argv[2], 'utf8'));
const cases = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = {};
for (const k of Object.keys(cases)) out[k] = mdToHtml(cases[k]);
console.log(JSON.stringify(out));
"""

_CASES = {
    # 마침표 3,208개 중 68%가 숫자 뒤다. 전부에서 끊으면 법령 인용이 세 줄로 깨진다.
    "법령인용": "피고는 구 특허법시행령(1983.11.5. 대통령령 제11254호) 제3조에 따라 처리하였다."
                " 원고의 주장은 이유 없다.",
    # `다.` 는 종결어미와 글자가 같다. 앞 글자를 안 보면 항목 표시가 문장 끝으로 오인된다.
    "항목표시": "가. 첫째 항목이다 나. 둘째 항목이다 다. 셋째 항목이다",
    "종결어미": "피고가 관리한 정보는 비밀로 유지되었다. 원고의 청구를 기각한다."
                " 소송비용은 원고가 부담한다.",
    "구획괄호": "【주문】 원고의 청구를 기각한다. 【이유】 피고의 주장이 타당하다.",
    # 원문자는 ①-⑳ · ❶-❿ · ➀-➉ · ➊-➓ 네 블록에 흩어져 있다. 한 블록만 잡으면 놓친다.
    "원문자4블록": "<①총칙> 이 규정은 영업비밀을 다룬다. <❶부칙> 시행일을 정한다."
                   " <➀별표> 서식이다. <➊참고> 끝이다.",
    "기호표시": "□ 목적 이 규정의 목적이다. ○ 범위 적용 범위다.",
    "표시없음": "이것은 아무 표시도 없는 한 덩어리 문장이다. 두 번째 문장이다.",
    "이스케이프": "<script>alert(1)</script> 그리고 **굵게** 와 `코드` 다.",
    "긴덩어리": ("제1조 " + "가나다라마바사아자차 " * 140).strip(),
    "짧은덩어리": ("가나다라마바사아자차 " * 80).strip(),
    "빈본문": "",
}


@pytest.fixture(scope="module")
def out(tmp_path_factory) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 렌더러 규칙 시험을 건너뛴다")
    d = tmp_path_factory.mktemp("renderer")
    (d / "renderer.js").write_text(DOC_RENDER_JS, encoding="utf-8")
    (d / "cases.json").write_text(json.dumps(_CASES, ensure_ascii=False), encoding="utf-8")
    (d / "run.js").write_text(_RUNNER, encoding="utf-8")
    r = subprocess.run([node, str(d / "run.js"), str(d / "renderer.js"), str(d / "cases.json")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, f"렌더러가 죽었다:\n{r.stderr}"
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_statute_citation_stays_on_one_line(out):
    """`1983.11.5. 대통령령` 이 세 줄로 깨지면 안 된다 — 숫자 뒤 마침표에서 끊지 않는다."""
    assert "1983.11.5. 대통령령 제11254호" in out["법령인용"]
    # 끊기는 곳은 한 군데뿐 — `처리하였다.` 뒤
    assert out["법령인용"].count("<br>") == 1
    assert "처리하였다.<br>원고의 주장은" in out["법령인용"]


def test_item_markers_are_not_sentence_ends(out):
    """`가.` `나.` `다.` 는 항목 표시다. `다.` 는 종결어미와 글자가 같아 특히 위험하다."""
    assert out["항목표시"].count("<br>") == 0, out["항목표시"]


def test_korean_sentence_endings_do_split(out):
    """반대로 진짜 종결어미 뒤에서는 끊어야 읽을 수 있다."""
    assert out["종결어미"].count("<br>") == 2, out["종결어미"]


def test_section_markers_start_new_paragraphs_and_get_labelled(out):
    assert out["구획괄호"].count("<p>") == 2
    assert '<b class="lbl">【주문】</b>' in out["구획괄호"]
    assert '<b class="lbl">【이유】</b>' in out["구획괄호"]


@pytest.mark.parametrize("mark", ["①", "❶", "➀", "➊"])
def test_all_four_circled_number_blocks_are_recognised(out, mark):
    """한 블록만 잡으면 ➌ 같은 문자를 놓친다(실측)."""
    assert f'<b class="lbl">&lt;{mark}' in out["원문자4블록"], out["원문자4블록"]


def test_symbol_markers_split_too(out):
    assert out["기호표시"].count("<p>") == 2
    assert '<b class="lbl">□</b>' in out["기호표시"]


def test_plain_text_becomes_one_paragraph_split_by_sentence(out):
    assert out["표시없음"].count("<p>") == 1
    assert out["표시없음"].count("<br>") == 1


def test_markup_is_escaped_but_formatting_still_applies(out):
    """업로드 문서를 그리므로 이스케이프가 먼저다."""
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out["이스케이프"]
    assert "<script>" not in out["이스케이프"]
    assert "<strong>굵게</strong>" in out["이스케이프"]
    assert "<code>코드</code>" in out["이스케이프"]


def test_long_run_without_sentence_ending_is_broken_up(out):
    """인용·법조문이 이어져 종결어미가 없는 구간(실측 최장 3,381자)을 벽으로 남기지 않는다."""
    assert out["긴덩어리"].count("<br>") == 1, "900자 넘는 덩어리가 안 끊겼다"
    assert out["짧은덩어리"].count("<br>") == 0, "900자 이하인데 끊겼다"


def test_empty_text_says_so(out):
    assert "본문이 없습니다" in out["빈본문"]
