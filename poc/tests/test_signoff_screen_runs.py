"""검수·서명 화면이 **실제로 실행되는지** 본다 — 문자열이 아니라 동작으로.

왜(2026-08-18 사고). mdToHtml 을 공용 모듈로 뽑으면서 그것이 부르는 mdInline·mdEsc 를
두고 왔다. 화면은 첫 문단에서 `ReferenceError: mdInline is not defined` 로 죽어 후보를
**한 건도 못 그렸다.** 그런데 골든·콘솔 계열 시험 423건이 전부 통과했다 — 렌더된 HTML 에
문자열이 있는지만 봤기 때문이다.

그래서 여기서는 배포되는 <script> 를 그대로 뽑아 node 로 **실행한다.** 최소 DOM 스텁만
주고, 예외 없이 #grid 가 채워지는지 확인한다. 브라우저 없이 잡을 수 있는 것은 여기까지다
(레이아웃·CSS 는 못 본다). 그래도 '화면이 통째로 비는' 부류는 이 시험이 잡는다.

⚠ node 가 없으면 skip 한다 — 파이썬 시험 환경에 node 를 강제하지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from koipa.golden_review_html import render_signoff_html

_HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const dataJson = html.match(/<script id="data" type="application\/json">([\s\S]*?)<\/script>/)[1];
const i = html.lastIndexOf('<script>') + '<script>'.length;
const j = html.lastIndexOf('</script>');
const code = html.slice(i, j);

// 최소 DOM 스텁 — textContent 대입이 innerHTML 에도 반영되게 해서 화면 반영을 관찰한다.
function el(id) {
  return { id, innerHTML: '', _text: '',
    get textContent() { return this._text; },
    set textContent(v) { this._text = v; this.innerHTML = String(v); },
    style: {}, classList: { add() {}, remove() {}, toggle() {} },
    dataset: {}, disabled: false, checked: false,
    addEventListener() {}, closest() { return null; }, matches() { return false; },
    append() {}, replaceChildren() {} };
}
const nodes = {};
global.document = {
  getElementById(id) {
    if (id === 'data') { const d = el('data'); d._text = dataJson; return d; }
    return nodes[id] || (nodes[id] = el(id));
  },
  querySelectorAll() { return []; }, querySelector() { return null; },
  createElement() { return el('tmp'); }, addEventListener() {},
};
global.window = { addEventListener() {} };
global.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
global.CSS = { escape: (s) => String(s) };
global.fetch = () => new Promise(() => {});   // 신원 조회·preflight 는 응답 없이 대기

const probe = process.argv[3] ? fs.readFileSync(process.argv[3], 'utf8') : '';
let error = null;
try { eval(code + probe); } catch (e) { error = e.constructor.name + ': ' + e.message; }
console.log(JSON.stringify({
  error,
  candidates: JSON.parse(dataJson).length,
  grid: (nodes.grid || { innerHTML: '' }).innerHTML,
  deccount: (nodes.deccount || { innerHTML: '' }).innerHTML,
  decbreak: (nodes.decbreak || { innerHTML: '' }).innerHTML,
}));
"""

_GOLD = [
    {"doc_id": "a", "label": "S2", "text": "【주문】 원고의 청구를 기각한다. 이유는 다음과 같다."},
    {"doc_id": "c", "label": "TS", "text": "영업비밀 관리규정을 정한다. 대외 반출을 금한다."},
]
_PENDING = [{"doc_id": "b", "label": "TS", "text": "룰과 LLM 이 합의하지 못한 후보다."}]


@pytest.fixture(scope="module")
def screen(tmp_path_factory) -> dict:
    """서명 화면을 렌더해 node 로 실행하고 결과를 돌려준다."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 화면 실행 시험을 건너뛴다")
    d = tmp_path_factory.mktemp("signoff")
    page = d / "signoff.html"
    page.write_text(
        render_signoff_html(_GOLD, job_id="J", post_url="/p", min_per_grade=5, pending=_PENDING),
        encoding="utf-8")
    harness = d / "run.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    out = subprocess.run([node, str(harness), str(page)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, f"하네스가 죽었다:\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_script_runs_without_throwing(screen):
    """예외 하나면 render() 가 멈추고 화면이 통째로 빈다."""
    assert screen["error"] is None, screen["error"]


def test_candidates_are_actually_drawn(screen):
    """#grid 가 비어 있으면 검수자는 아무것도 못 본다 — 2026-08-18 에 실제로 그랬다."""
    assert screen["candidates"] == 3
    assert len(screen["grid"]) > 0, "#grid 가 비어 있다"
    for doc_id in ("a", "c", "b"):
        assert f'data-id="{doc_id}"' in screen["grid"], f"{doc_id} 카드가 없다"


def test_pending_card_has_no_decision_form(screen):
    """합의 미달 후보는 보이되 결정 폼이 없어야 한다."""
    assert "scard pending" in screen["grid"]
    assert 'name="dec-b"' not in screen["grid"], "보기 전용 후보에 결정 라디오가 붙었다"
    assert 'name="dec-a"' in screen["grid"]


def test_progress_and_breakdown_land_in_different_places(screen):
    """gate 의 큰 글씨에는 진행 숫자만 — 등급별 내역까지 넣으면 제목 자리가 무너진다."""
    assert screen["deccount"] == "0 / 2", screen["deccount"]
    assert "승격 예정" in screen["decbreak"]
    assert "합의 미달 1건" in screen["decbreak"]


# ── 「미결정」 필터 · 렌더 캐시 (2026-08-19) ──────────────────────────────────────
_PROBE_FILTER = """
const cards = () => (document.getElementById('grid').innerHTML.match(/class="scard/g) || []).length;
const seen = { all: cards(), todoLabel0: document.getElementById('fTodo').innerHTML };
DEC['a'] = { decision: 'approve' };
st = 'todo'; render(); seen.todo = cards(); seen.todoLabel = document.getElementById('fTodo').innerHTML;
st = 'done'; render(); seen.done = cards();
st = 'all';  render(); seen.back = cards();
console.log(JSON.stringify(seen));
"""


@pytest.fixture(scope="module")
def filtered(tmp_path_factory) -> dict:
    """결정을 하나 넣고 「미결정 / 결정함」 필터를 눌러 본다."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 화면 실행 시험을 건너뛴다")
    d = tmp_path_factory.mktemp("filter")
    page = d / "signoff.html"
    page.write_text(
        render_signoff_html(_GOLD, job_id="J", post_url="/p", min_per_grade=5, pending=_PENDING),
        encoding="utf-8")
    (d / "run.js").write_text(_HARNESS, encoding="utf-8")
    (d / "probe.js").write_text(_PROBE_FILTER, encoding="utf-8")
    out = subprocess.run([node, str(d / "run.js"), str(page), str(d / "probe.js")],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[0])


def test_undecided_filter_narrows_the_list(filtered):
    """120건짜리 회차를 나눠 하면 남은 것을 찾아 스크롤해야 했다 — 그것을 없앤 필터."""
    assert filtered["all"] == 3          # 서명 대상 2 + 보기 전용 1
    assert filtered["todo"] == 1         # a 를 결정했으니 c 만 남는다
    assert filtered["done"] == 1
    assert filtered["back"] == 3


def test_view_only_candidates_are_never_counted_as_undecided(filtered):
    """보기 전용은 결정 폼이 없다 — 미결정에 넣으면 아무리 눌러도 줄지 않는 잔여가 생긴다."""
    assert filtered["todoLabel0"] == "미결정 2"
    assert filtered["todoLabel"] == "미결정 1"


def test_render_is_cached_and_search_is_debounced():
    """필터·검색마다 문서 120건을 다시 변환하면 타이핑이 끊긴다.

    실측(후보 120건·각 2,100자): 캐시 없이 render() 31.2ms -> 캐시 후 3.2ms.
    디바운스가 없으면 두 글자만 쳐도 그 비용이 두 번 든다.
    """
    html = render_signoff_html(_GOLD, job_id="J", post_url="/p", pending=_PENDING)
    assert "const MDC={}" in html and "function mdOnce(r)" in html
    assert html.count("+mdOnce(r)+") == 2, "카드 두 종류가 캐시를 안 쓴다"
    assert html.count("mdToHtml(r.text)") == 1, "mdOnce 정의 밖에서 직접 변환한다"
    assert "clearTimeout(qTimer)" in html and "setTimeout(" in html
