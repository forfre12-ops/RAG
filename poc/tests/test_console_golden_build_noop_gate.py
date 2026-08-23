"""「검증 기준문서 준비」 카드가 이 서버에서 **되는 것만** 내주는지 본다.

왜(2026-08-23 사용자 지적: "이거 동작도 안 하는데 이렇게 두는 게 맞아?"). 두 가지가 겹쳐
카드의 주 버튼이 아무 일도 못 하고 있었다.

  1) 후보 소스 기본값은 '서버 코퍼스 경로'인데 경로 입력칸(#gold-src-corpus)이 display:none
     으로 시작하고 onchange 에서만 열렸다. 드롭다운을 다른 값으로 바꿨다 되돌리기 전에는
     칸이 아예 보이지 않아 「후보 생성 → 검수」가 늘 "코퍼스 경로를 입력하세요" 로 끝났다.
  2) 이 서버는 llm_provider=noop 이다(223 실측). noop 은 합성 문서 생성용 더미라 응답에
     grade 가 없고 판정이 항상 S3 · 신뢰도 0.5 로 떨어진다 — 등급이 매겨지지 않는다.
     실측(223 배포 이미지, hardened 42건): gold 2 / 보류 40, 그 gold 2 건도 전부 S3.

그래서 화면은 (1) 기본값과 입력칸을 맞추고 (2) 판정 LLM 이 없으면 AI 후보 생성을 **누르기
전에** 잠그고 이 서버에서 실제로 되는 경로(이미 있는 문서 묶음 검수)를 위로 올린다.
카드를 감추지는 않는다 — 감추면 "그 기능이 없다"로 읽힌다.

문자열이 아니라 **실행**으로 확인한다. 배포되는 인라인 <script> 를 그대로 뽑아 최소 DOM
스텁 위에서 돌리고, healthz 응답을 먹여 화면 상태를 관찰한다(test_signoff_screen_runs 와
같은 방식). ⚠ 레이아웃·CSS 는 못 본다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "static"
ADMIN = STATIC / "admin.html"

_HARNESS = r"""
const fs = require('fs');
const html = fs.readFileSync(process.argv[2], 'utf8');
const m = html.match(/<script(?![^>]*type="module")(?![^>]* src=)[^>]*>([\s\S]*?)<\/script>/);
const code = m[1];

function el(id, tag) {
  return {
    id, tagName: (tag || 'div').toUpperCase(),
    innerHTML: '', _text: '', value: '', disabled: false, checked: false,
    style: {}, dataset: {}, options: [], children: [], parentNode: null,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    addEventListener() {}, removeEventListener() {},
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    append() {}, replaceChildren() {}, remove() {},
    insertBefore(node, ref) {
      const kids = this.children;
      const cur = kids.indexOf(node);
      if (cur >= 0) kids.splice(cur, 1);
      const at = ref ? kids.indexOf(ref) : kids.length;
      kids.splice(at < 0 ? kids.length : at, 0, node);
      node.parentNode = this;
      return node;
    },
    compareDocumentPosition(other) {           // 4=FOLLOWING, 2=PRECEDING
      const kids = (this.parentNode || { children: [] }).children;
      const a = kids.indexOf(this), b = kids.indexOf(other);
      if (a < 0 || b < 0) return 0;
      return b > a ? 4 : 2;
    },
    querySelector() { return el('q'); }, querySelectorAll() { return []; },
    closest() { return null; }, matches() { return false; },
    focus() {}, scrollIntoView() {}, getBoundingClientRect() { return { top: 0, height: 0 }; },
    setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    open: false, checkValidity() { return true; },
  };
}
const nodes = {};
function get(id) { return nodes[id] || (nodes[id] = el(id)); }

// 실제 카드의 부모/자식 관계 — 두 블록의 순서 교체를 관찰하려면 필요하다.
const cardBody = el('card-body');
cardBody.appendChild(get('gold-ai-block'));
cardBody.appendChild(get('gold-register-block'));
get('gold-provider').options = ['noop', 'local_openai', 'vllm_qwen', 'anthropic', 'openai']
  .map((v) => ({ value: v, disabled: false }));
get('gold-source').value = 'corpus';          // 화면 기본값과 같게
get('gold-src-corpus').style.display = 'none';  // HTML 인라인 style 과 같게 — init 이 열어야 한다

global.Node = { DOCUMENT_POSITION_FOLLOWING: 4, DOCUMENT_POSITION_PRECEDING: 2 };
global.document = {
  getElementById: get, querySelector() { return null; }, querySelectorAll() { return []; },
  createElement(t) { return el('tmp', t); }, addEventListener() {},
  body: el('body'), documentElement: el('html'), title: '',
};
global.window = {
  addEventListener() {}, matchMedia() { return { matches: false, addEventListener() {} }; },
  open() { return null; }, location: { pathname: '/console', search: '', href: '' },
};
global.location = global.window.location;
global.history = { replaceState() {} };
global.navigator = { clipboard: { writeText() { return Promise.resolve(); } }, userAgent: 'node' };
global.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
global.sessionStorage = global.localStorage;
global.CSS = { escape: (s) => String(s) };
global.alert = () => {}; global.confirm = () => false; global.prompt = () => null;
global.fetch = () => new Promise(() => {});   // 네트워크 없음 — 응답 없이 대기
global.EventSource = function () { return { close() {}, addEventListener() {} }; };
global.requestAnimationFrame = (f) => setTimeout(f, 0);

function snap(tag) {
  return {
    tag,
    goDisabled: get('gold-go').disabled,
    noticeShown: get('gold-ai-blocked').style.display !== 'none',
    noticeHtml: get('gold-ai-blocked').innerHTML,
    order: cardBody.children.map((c) => c.id),
    usableProviders: Array.prototype.filter.call(get('gold-provider').options, (o) => !o.disabled)
      .map((o) => o.value),
  };
}

let error = null;
const probe = `
  global.__snaps = [];
  applyServerConfig({ status:'ok', deploy_profile:'full-train', llm_provider:'noop' });
  global.__snaps.push(snap('noop'));
  applyServerConfig({ status:'ok', deploy_profile:'full-train', llm_provider:'openai' });
  global.__snaps.push(snap('openai'));
`;
try { eval(code + ';;' + probe); } catch (e) { error = e.constructor.name + ': ' + e.message; }
console.log(JSON.stringify({
  error,
  // init 직후 상태 — 기본 소스(코퍼스)의 경로 입력칸이 보이는가.
  corpusBoxDisplay: get('gold-src-corpus').style.display,
  snaps: global.__snaps || [],
}));
"""


@pytest.fixture(scope="module")
def screen(tmp_path_factory) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음 — 화면 실행 시험을 건너뛴다")
    d = tmp_path_factory.mktemp("admin_console")
    harness = d / "run.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    out = subprocess.run([node, str(harness), str(ADMIN)], capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert out.returncode == 0, f"하네스가 죽었다:\n{out.stderr}"
    return json.loads(out.stdout.strip().splitlines()[-1])


def _snap(screen: dict, tag: str) -> dict:
    got = [s for s in screen["snaps"] if s["tag"] == tag]
    assert got, f"{tag} 스냅샷이 없다 — 스크립트가 중간에 죽었을 수 있다: {screen['error']}"
    return got[0]


def test_console_script_runs(screen):
    """예외 하나면 init 이 멈추고 화면 절반이 빈다(2026-08-23 에 실제로 그랬다)."""
    assert screen["error"] is None, screen["error"]


def test_corpus_path_box_is_visible_at_init(screen):
    """기본 소스가 '서버 코퍼스 경로'인데 경로 입력칸이 숨어 있으면 버튼을 눌러도 못 넣는다."""
    assert screen["corpusBoxDisplay"] != "none", (
        "#gold-src-corpus 가 init 직후 숨어 있다 — init 에서 syncGoldenSource() 를 부르는지 확인"
    )


def test_ai_build_is_locked_when_judge_llm_absent(screen):
    """llm_provider=noop → 「후보 생성 → 검수」는 잠기고, 되는 경로가 위로 올라온다."""
    s = _snap(screen, "noop")
    assert s["goDisabled"] is True, "판정 LLM 이 없는데 후보 생성 버튼이 열려 있다"
    assert s["noticeShown"] is True, "왜 못 쓰는지 화면이 말하지 않는다"
    assert "S3" in s["noticeHtml"], "안내에 실제 증상(모든 문서 S3)이 없다"
    assert s["order"][0] == "gold-register-block", (
        "잠긴 블록이 위에 있으면 카드 전체가 막힌 것으로 읽힌다"
    )
    assert s["usableProviders"] == ["noop"], "서버가 못 쓰는 제공자가 선택 가능하다"


def test_ai_build_is_available_when_judge_llm_present(screen):
    """LLM 이 있는 서버(지재원 GPU)에서는 잠그지 않는다 — 잠금이 한쪽으로만 가면 안 된다."""
    s = _snap(screen, "openai")
    assert s["goDisabled"] is False
    assert s["noticeShown"] is False
    assert s["order"][0] == "gold-ai-block"
    assert s["usableProviders"] == ["openai"]
