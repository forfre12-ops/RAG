/* 화면을 진짜 DOM 으로 띄우고 사람이 하듯 조작하는 하니스.
 *
 * 기존 콘솔 시험이 못 잡던 것이 여기서 잡힌다. 종전 하니스는 document.getElementById 가
 * **어떤 id 든 가짜 요소를 만들어 돌려주는** 스텁이었다. 그래서 "화면에서 지운 요소를
 * 초기화 코드가 계속 만지다가 TypeError 로 뒤 코드를 통째로 막은" 부류(2026-08-23 실측)가
 * 시험을 그대로 통과했다. jsdom 은 없는 id 에 null 을 준다 — 그 순간 터진다.
 *
 * 잡는 것:  스크립트 실행 오류 · 없는 함수/요소 참조 · 버튼을 눌렀을 때 실제로 무슨 일이
 *           일어나는가 · 어떤 요청이 어떤 본문으로 나갔는가 · 화면에 무엇이 그려졌는가.
 * 못 잡는 것: 레이아웃·CSS 시각 결과·실제 브라우저 고유 동작(파일 대화상자 등).
 */

import fs from 'node:fs';
import path from 'node:path';
import { JSDOM, VirtualConsole } from 'jsdom';
import { STATIC_DIR } from './server.mjs';

/* jsdom 이 못 하는 것들 — 브라우저에는 있으나 jsdom 에 없어서 나는 잡음은 앱 결함이 아니다. */
const JSDOM_NOISE = [
  /Could not parse CSS stylesheet/i,
  /Not implemented: HTMLCanvasElement/i,
  /Not implemented: navigation/i,
  /Not implemented: window\.scrollTo/i,
  /Error: Not implemented: HTMLFormElement\.prototype\.(requestSubmit|submit)/i,
];
const isNoise = (msg) => JSDOM_NOISE.some((r) => r.test(String(msg)));

/* async 핸들러가 던진 것은 jsdom 창이 아니라 **node 프로세스**로 올라온다(네이티브 Promise).
 * 잡아두지 않으면 시나리오 하나가 실행기 전체를 죽인다. 지금 열려 있는 화면의 오류 목록으로
 * 흘려보내 "그 버튼이 죽었다"로 기록되게 한다. */
let ERROR_SINK = null;
let TRAP_INSTALLED = false;

/* 실서버 모드에서 쓸 API 키 등, 모든 화면에 공통으로 심어 둘 브라우저 저장소 값.
 * 콘솔은 localStorage 의 koipa_api_key 를 읽어 쓴다(주소에 ?key= 로 한 번 넣으면 저장되는
 * 바로 그 값) — 시험도 같은 경로를 쓴다. */
let DEFAULT_STORAGE = {};
export function setDefaultStorage(kv) {
  DEFAULT_STORAGE = { ...kv };
}
export function installGlobalErrorTrap() {
  if (TRAP_INSTALLED) return;
  TRAP_INSTALLED = true;
  const push = (where) => (e) => {
    const rec = { where, message: (e && e.message) || String(e), stack: (e && e.stack) || '' };
    if (ERROR_SINK) ERROR_SINK.push(rec);
    else console.error(`[${where}] ${rec.message}`);
  };
  process.on('unhandledRejection', push('unhandledrejection'));
  process.on('uncaughtException', push('uncaught'));
}

/* ── type="module" 우회 ────────────────────────────────────────────────────
 * jsdom 은 ES 모듈을 실행하지 않는다. index.html 의 본체(app.js)가 모듈이라 그대로 두면
 * 화면이 통째로 죽은 것과 구분이 안 된다. 로컬 모듈만 쓰므로(외부 CDN 0건) 의존 순서대로
 * 이어 붙이고 import/export 표기만 걷어내 고전 스크립트로 실행한다.
 * ⚠ 이것은 하니스의 임시 조치다 — 실제 브라우저의 모듈 스코프 격리를 재현하지 않는다.
 * -------------------------------------------------------------------------*/
function bundleModule(entryRel, seen = new Set()) {
  const full = path.join(STATIC_DIR, entryRel.replace(/^\.\//, ''));
  if (seen.has(full)) return '';
  seen.add(full);
  let src = fs.readFileSync(full, 'utf8');
  let out = '';
  src = src.replace(/^\s*import\s+[^;]*?from\s+["'](\.\/[^"']+)["'];?\s*$/gm, (_m, dep) => {
    out += bundleModule(dep, seen) + '\n';
    return '';
  });
  src = src.replace(/^\s*export\s+(const|let|var|function|class|async\s+function)/gm, '$1');
  src = src.replace(/^\s*export\s*\{[^}]*\};?\s*$/gm, '');
  return out + `\n/* ==== ${entryRel} ==== */\n` + src;
}

/** jsdom FormData/Blob → node(undici) 로 넘길 수 있는 형태로 옮겨 담는다. */
async function toNodeBody(win, body) {
  if (body == null) return undefined;
  if (typeof body === 'string') return body;
  if (win.FormData && body instanceof win.FormData) {
    const fd = new FormData();
    for (const [k, v] of body.entries()) {
      if (typeof v === 'string') fd.append(k, v);
      else {
        const buf = Buffer.from(await v.arrayBuffer());
        fd.append(k, new Blob([buf], { type: v.type || 'application/octet-stream' }), v.name || 'upload.bin');
      }
    }
    return fd;
  }
  if (win.Blob && body instanceof win.Blob) return Buffer.from(await body.arrayBuffer());
  return body;
}

export async function openPage(server, urlPath, opts = {}) {
  const {
    query = '',
    hash = '',
    storage = {},
    session = {},
    bundleModules = false,
    confirmAnswer = true,
    promptAnswer = '',
    /** 스크립트가 돌기 직전에 window 를 손보고 싶을 때(저장소 차단 흉내 등). */
    beforeBoot = null,
  } = opts;

  const pageUrl = `${server.url}${urlPath}${query}${hash}`;
  let html = await (await fetch(`${server.url}${urlPath}`)).text();

  if (bundleModules) {
    /* 모듈은 defer 처럼 문서 끝에서 돌고, 스코프는 IIFE 로 가둔다 — 브라우저의 모듈 의미
     * (지연 실행 + 전역 오염 없음)에 최대한 가깝게. 전역을 안 만드는 것이 핵심이라,
     * 인라인 onclick 이 모듈 안 함수를 부르면 여기서도 실제와 똑같이 실패한다. */
    const bundles = [];
    html = html.replace(/<script type="module" src="\.\/([^"]+)"><\/script>/g, (_m, rel) => {
      bundles.push(bundleModule('./' + rel));
      return `<!-- e2e: module ${rel} moved to end of body -->`;
    });
    if (bundles.length) {
      // ⚠ 치환문자열이 아니라 **함수**로 넣는다. 문자열로 넣으면 번들 안의 `$$`·`$&` 가
      //   치환 특수기호로 해석돼 소스가 조용히 망가진다(app.js 의 `const $$` 가 `const $`
      //   로 바뀌어 "Identifier '$' has already been declared" 가 났다).
      const injected = `<script>\n(function(){\n${bundles.join('\n')}\n})();\n</script>\n</body>`;
      html = html.replace(/<\/body>/i, () => injected);
    }
  }

  const errors = [];
  ERROR_SINK = errors;
  const consoleErrors = [];
  const dialogs = [];
  const opened = [];
  let inflight = 0;
  const vc = new VirtualConsole();
  vc.on('jsdomError', (e) => {
    if (isNoise(e.message)) return;
    errors.push({ where: 'jsdom', message: e.message, stack: (e.detail && e.detail.stack) || e.stack || '' });
  });
  vc.on('error', (...a) => consoleErrors.push(a.map(String).join(' ')));

  const dom = new JSDOM(html, {
    url: pageUrl,
    runScripts: 'dangerously',
    resources: 'usable',
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(win) {
      try {
        for (const [k, v] of Object.entries({ ...DEFAULT_STORAGE, ...storage })) win.localStorage.setItem(k, v);
        for (const [k, v] of Object.entries(session)) win.sessionStorage.setItem(k, v);
      } catch { /* 저장소 차단 흉내는 별도 시나리오에서 한다 */ }

      win.addEventListener('error', (ev) => {
        const m = ev.message || (ev.error && ev.error.message) || String(ev);
        if (!isNoise(m)) errors.push({ where: 'window.onerror', message: m, stack: (ev.error && ev.error.stack) || '' });
      });
      win.addEventListener('unhandledrejection', (ev) => {
        const r = ev.reason;
        errors.push({ where: 'unhandledrejection', message: (r && r.message) || String(r), stack: (r && r.stack) || '' });
      });

      win.alert = (m) => dialogs.push({ kind: 'alert', message: String(m) });
      win.confirm = (m) => { dialogs.push({ kind: 'confirm', message: String(m) }); return confirmAnswer; };
      win.prompt = (m) => { dialogs.push({ kind: 'prompt', message: String(m) }); return promptAnswer; };
      win.open = (u, t) => { opened.push({ url: String(u), target: t }); return { focus() {}, closed: false }; };
      win.print = () => {};

      // 브라우저에는 있고 jsdom 에는 없는 것들. 없으면 앱이 TypeError 로 죽는데
      // 그것은 실제 결함이 아니라 하니스의 결함이므로 채워 준다.
      if (!win.Element.prototype.scrollIntoView) win.Element.prototype.scrollIntoView = function () {};
      if (!win.HTMLElement.prototype.scrollTo) win.HTMLElement.prototype.scrollTo = function () {};
      if (!win.DataTransfer) {
        win.DataTransfer = class {
          constructor() { this.items = { _f: [], add: (f) => this.items._f.push(f) }; }
          get files() { const l = this.items._f.slice(); l.item = (i) => l[i]; return l; }
        };
      }
      /* jsdom 의 input.files 는 진짜 FileList 만 받는다. 브라우저에서는 DataTransfer 로
       * 만든 FileList 를 대입하는 것이 정상 경로(끌어다 놓기)이므로, 배열 유사 객체도
       * 받아들이도록 접근자를 덧댄다. 이것도 하니스의 보정이지 앱 동작 변경이 아니다. */
      const proto = win.HTMLInputElement.prototype;
      const orig = Object.getOwnPropertyDescriptor(proto, 'files');
      if (orig && orig.get) {
        Object.defineProperty(proto, 'files', {
          configurable: true,
          get() { return this._e2eFiles !== undefined ? this._e2eFiles : orig.get.call(this); },
          set(v) {
            try {
              if (orig.set) { orig.set.call(this, v); return; }
            } catch { /* 진짜 FileList 가 아니면 아래로 */ }
            const l = v ? Array.from(v) : [];
            l.item = (i) => l[i];
            this._e2eFiles = l;
          },
        });
      }

      if (typeof beforeBoot === 'function') beforeBoot(win);

      win.fetch = async (input, init = {}) => {
        const href = typeof input === 'string' ? input : (input && input.url) || String(input);
        const abs = new URL(href, win.location.href).toString();
        inflight += 1;
        try {
          const body = await toNodeBody(win, init.body);
          const res = await fetch(abs, { method: init.method || 'GET', headers: init.headers, body });
          return res;
        } finally {
          inflight -= 1;
        }
      };
    },
  });

  const win = dom.window;
  await new Promise((r) => {
    if (win.document.readyState === 'complete') r();
    else win.addEventListener('load', r, { once: true });
  });

  const page = {
    dom,
    win,
    doc: win.document,
    errors,
    consoleErrors,
    dialogs,
    opened,
    url: pageUrl,

    $(id) { return win.document.getElementById(id); },
    q(sel) { return win.document.querySelector(sel); },
    qa(sel) { return Array.from(win.document.querySelectorAll(sel)); },

    /** 요청이 다 끝날 때까지 기다린다(연쇄 호출 포함). */
    async settle(maxMs = 4000) {
      const t0 = Date.now();
      let quiet = 0;
      while (Date.now() - t0 < maxMs) {
        await new Promise((r) => setTimeout(r, 12));
        quiet = inflight === 0 ? quiet + 1 : 0;
        if (quiet >= 4) return true;
      }
      return false;
    },

    async wait(ms) { await new Promise((r) => setTimeout(r, ms)); },

    /** 조건이 참이 될 때까지. 안 되면 false — 시나리오가 사유와 함께 실패시킨다. */
    async until(fn, maxMs = 4000) {
      const t0 = Date.now();
      while (Date.now() - t0 < maxMs) {
        try { if (fn()) return true; } catch { /* 아직 안 그려짐 */ }
        await new Promise((r) => setTimeout(r, 20));
      }
      return false;
    },

    visible(elOrId) {
      const el = typeof elOrId === 'string' ? page.$(elOrId) : elOrId;
      if (!el) return false;
      for (let n = el; n && n.nodeType === 1; n = n.parentElement) {
        const st = win.getComputedStyle(n);
        if (st.display === 'none' || st.visibility === 'hidden') return false;
        if (n.hasAttribute && n.hasAttribute('hidden')) return false;
      }
      return true;
    },

    /** 사람이 누르듯 누른다 — 없거나 안 보이거나 비활성이면 그 자리에서 실패시킨다. */
    click(elOrId, { requireVisible = true } = {}) {
      const el = typeof elOrId === 'string' ? page.$(elOrId) : elOrId;
      if (!el) throw new Error(`누를 대상이 없다: ${elOrId}`);
      if (requireVisible && !page.visible(el)) throw new Error(`화면에 안 보이는 것을 눌렀다: ${elOrId}`);
      if (el.disabled) throw new Error(`비활성 버튼을 눌렀다: ${elOrId}`);
      el.click();
      return el;
    },

    /** onclick 속성에 적힌 코드를 그대로 실행 — 보이지 않는 탭의 버튼까지 훑을 때 쓴다. */
    fire(el) {
      const code = el.getAttribute('onclick');
      if (!code) return;
      win.eval(`(function(){ ${code} }).call(document.currentScript || document.body)`);
    },

    set(id, value) {
      const el = page.$(id);
      if (!el) throw new Error(`입력란이 없다: ${id}`);
      el.value = value;
      el.dispatchEvent(new win.Event('input', { bubbles: true }));
      el.dispatchEvent(new win.Event('change', { bubbles: true }));
      return el;
    },

    check(id, on = true) {
      const el = page.$(id);
      if (!el) throw new Error(`체크박스가 없다: ${id}`);
      el.checked = on;
      el.dispatchEvent(new win.Event('change', { bubbles: true }));
      return el;
    },

    /** 파일 선택 — input.files 는 읽기 전용이라 정의를 바꿔 끼운다. */
    attachFile(inputId, { name = 'sample.pdf', type = 'application/pdf', size = 2048, content = null } = {}) {
      const el = page.$(inputId);
      if (!el) throw new Error(`파일 입력란이 없다: ${inputId}`);
      const bytes = content ?? new Uint8Array(size).fill(65);
      const f = new win.File([bytes], name, { type });
      const list = [f];
      list.item = (i) => list[i];
      Object.defineProperty(el, 'files', { value: list, configurable: true });
      el.dispatchEvent(new win.Event('change', { bubbles: true }));
      return f;
    },

    text(idOrEl) {
      const el = typeof idOrEl === 'string' ? page.$(idOrEl) : idOrEl;
      return el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '';
    },
    html(idOrEl) {
      const el = typeof idOrEl === 'string' ? page.$(idOrEl) : idOrEl;
      return el ? el.innerHTML : '';
    },
    bodyText() { return (win.document.body.textContent || '').replace(/\s+/g, ' '); },

    /** 화면에 남은 오류 흔적 — 로그창의 err 줄 + alert */
    logLines(kind = null) {
      return page.qa('#logbody .logline' + (kind ? '.' + kind : '')).map((n) => page.text(n));
    },

    close() {
      if (ERROR_SINK === errors) ERROR_SINK = null;
      try { win.close(); } catch { /* 이미 닫힘 */ }
    },
  };

  return page;
}
