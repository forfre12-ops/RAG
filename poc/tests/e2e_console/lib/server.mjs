/* 콘솔을 띄우기 위한 최소 서버.
 *
 *   /console/*, /demo/*   → src/koipa/api/static 의 실제 파일 (배포되는 그 파일 그대로)
 *   /api/v1/*             → lib/fixtures.json 의 본보기 응답 (+ 고장 주입)
 *
 * 왜 mock 인가. 실 서버를 띄우면 PG·모델·큐가 있어야 하고, 그러면 "화면이 눌리는가"를
 * 보려는 시험이 인프라 사정으로 건너뛰어진다. 여기서는 화면 쪽만 고정하고, 응답 본보기가
 * 실제 서버와 어긋나지 않는지는 파이썬 계약 시험(test_e2e_console_api_contract.py)이
 * response_model 로 따로 잠근다.
 *
 * 실 서버를 상대로 같은 시나리오를 돌리려면 run.mjs --base http://호스트:포트 를 쓴다.
 */

import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const STATIC_DIR = path.resolve(HERE, '../../../src/koipa/api/static');
export const FIXTURES = JSON.parse(fs.readFileSync(path.join(HERE, 'fixtures.json'), 'utf8'));

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.pdf': 'application/pdf',
  '.txt': 'text/plain; charset=utf-8',
};

/** 'GET /admin/keywords/{keyword_id}' → 정규식. 경로 변수는 한 조각만 먹는다. */
function toMatcher(key) {
  const [method, tmpl] = key.split(' ');
  const rx = new RegExp(
    '^' + tmpl.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\{[^}]+\\\}/g, '[^/]+') + '$',
  );
  return { method, tmpl, rx };
}

const MATCHERS = Object.keys(FIXTURES)
  .filter((k) => k !== '_note')
  .map(toMatcher)
  // 고정 경로가 변수 경로보다 먼저 잡히게 — /golden/jobs/register 가 /golden/jobs/{id} 로 새면 안 된다.
  .sort((a, b) => (a.tmpl.includes('{') ? 1 : 0) - (b.tmpl.includes('{') ? 1 : 0));

export function lookupFixture(method, apiPath) {
  const clean = apiPath.split('?')[0];
  for (const m of MATCHERS) {
    if (m.method === method && m.rx.test(clean)) return { key: `${m.method} ${m.tmpl}`, body: FIXTURES[`${m.method} ${m.tmpl}`] };
  }
  return null;
}

/* ── 고장 주입 ───────────────────────────────────────────────────────────────
 * 시나리오가 server.faults 배열에 규칙을 넣으면 그 요청만 비틀어 준다.
 *   {path:'/classify', status:500}                 서버 오류
 *   {path:/keywords/, status:401, body:{detail:…}} 인증 실패
 *   {path:'/healthz', raw:'<html>502</html>'}      JSON 자리에 HTML (파싱 실패 경로)
 *   {path:'/classify', abort:true}                 소켓 끊김 (네트워크 오류 경로)
 *   {path:'/classify', delayMs:400}                지연 (버튼 이중 클릭·진행 표시 확인)
 *   {path:'/review-queue', body:{items:[],…}}      빈 목록
 *   {once:true}                                    한 번만 적용하고 소진
 * ------------------------------------------------------------------------- */
function matchFault(faults, apiPath, method) {
  for (const f of faults) {
    if (f._spent) continue;
    if (f.method && f.method !== method) continue;
    const p = f.path;
    const hit = p instanceof RegExp ? p.test(apiPath) : apiPath.split('?')[0] === p || apiPath.startsWith(p);
    if (hit) return f;
  }
  return null;
}

function serveStatic(req, res, urlPath) {
  const rel = urlPath.replace(/^\/(console|demo)\/?/, '') || 'index.html';
  const full = path.join(STATIC_DIR, decodeURIComponent(rel));
  if (!full.startsWith(STATIC_DIR)) {
    res.writeHead(403).end('forbidden');
    return;
  }
  let target = full;
  try {
    if (fs.statSync(target).isDirectory()) target = path.join(target, 'index.html');
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }).end('not found: ' + rel);
    return;
  }
  const body = fs.readFileSync(target);
  res.writeHead(200, { 'Content-Type': MIME[path.extname(target)] || 'application/octet-stream' });
  res.end(body);
}

/** 실서버 모드 — 요청을 그대로 넘기되 **기록은 남긴다.**
 *  기록이 없으면 "어떤 요청이 어떤 본문으로 나갔나" 확인이 통째로 죽어, 실서버 시험이
 *  화면 그림 검사로만 쪼그라든다. 고장 주입·응답 덮어쓰기는 이 모드에서 하지 않는다. */
async function proxyTo(upstream, req, res, rawBody) {
  const target = upstream.replace(/\/$/, '') + (req.url || '/');
  const headers = { ...req.headers };
  delete headers.host;
  delete headers.connection;
  delete headers['content-length'];
  let up;
  try {
    up = await fetch(target, {
      method: req.method,
      headers,
      body: ['GET', 'HEAD'].includes(req.method) || !rawBody.length ? undefined : rawBody,
      redirect: 'manual',
    });
  } catch (e) {
    res.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ detail: `실서버에 닿지 못했다: ${e.message}` }));
    return;
  }
  const out = {};
  up.headers.forEach((v, k) => {
    if (!['content-encoding', 'transfer-encoding', 'content-length'].includes(k)) out[k] = v;
  });
  res.writeHead(up.status, out);
  if (up.body) {
    const reader = up.body.getReader();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
    }
  }
  res.end();
}

export async function startServer({ upstream = null } = {}) {
  const state = {
    faults: [],
    /** 콘솔이 실제로 보낸 요청 전부 — 시나리오가 "무엇을 어떤 본문으로 불렀나"를 본다. */
    calls: [],
    /** 경로별 응답 덮어쓰기: overrides['GET /review-queue'] = {...} */
    overrides: {},
  };

  const server = http.createServer(async (req, res) => {
    const urlPath = (req.url || '/').split('?')[0];

    if (urlPath.startsWith('/console') || urlPath.startsWith('/demo')) {
      // 실서버 모드에서는 **배포된 화면**을 받아야 한다 — 로컬 파일로 보면 그 서버를 시험한 것이 아니다.
      if (upstream) {
        await proxyTo(upstream, req, res, Buffer.alloc(0));
        return;
      }
      serveStatic(req, res, urlPath);
      return;
    }

    if (!urlPath.startsWith('/api/v1')) {
      if (upstream) {
        await proxyTo(upstream, req, res, Buffer.alloc(0));
        return;
      }
      res.writeHead(404, { 'Content-Type': 'application/json' }).end('{"detail":"no route"}');
      return;
    }

    const apiPath = (req.url || '').slice('/api/v1'.length) || '/';
    const chunks = [];
    for await (const c of req) chunks.push(c);
    const raw = Buffer.concat(chunks);
    let parsed = null;
    try {
      parsed = raw.length ? JSON.parse(raw.toString('utf8')) : null;
    } catch {
      parsed = raw.length ? '<non-json body>' : null;
    }
    const call = {
      method: req.method,
      path: apiPath,
      headers: req.headers,
      body: parsed,
      bytes: raw.length,
      at: state.calls.length,
    };
    state.calls.push(call);

    if (upstream) {                       // 기록만 하고 그대로 넘긴다
      await proxyTo(upstream, req, res, raw);
      return;
    }

    const fault = matchFault(state.faults, apiPath, req.method);
    if (fault) {
      if (fault.once) fault._spent = true;
      if (fault.abort) {
        req.destroy();
        res.destroy();
        return;
      }
      if (fault.delayMs) await new Promise((r) => setTimeout(r, fault.delayMs));
      if (fault.raw !== undefined) {
        res.writeHead(fault.status || 200, { 'Content-Type': fault.contentType || 'text/html; charset=utf-8' });
        res.end(fault.raw);
        return;
      }
      if (fault.status && fault.status >= 400) {
        res.writeHead(fault.status, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(fault.body ?? { detail: `주입된 오류 ${fault.status}` }));
        return;
      }
      if (fault.body !== undefined) {
        res.writeHead(fault.status || 200, { 'Content-Type': 'application/json; charset=utf-8' });
        res.end(JSON.stringify(fault.body));
        return;
      }
    }

    const ovKey = Object.keys(state.overrides).find((k) => {
      const m = toMatcher(k);
      return m.method === req.method && m.rx.test(apiPath.split('?')[0]);
    });
    if (ovKey) {
      res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify(state.overrides[ovKey]));
      return;
    }

    const hit = lookupFixture(req.method, apiPath);
    /* 스트리밍 응답(SSE) — 본보기가 {_sse:[{event,data},…]} 면 그대로 흘려보낸다.
     * 시연 화면의 단계 점등이 이 이벤트로 도므로, JSON 한 번으로는 그 경로를 못 본다. */
    if (hit && hit.body && Array.isArray(hit.body._sse)) {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache',
        Connection: 'keep-alive',
      });
      for (const ev of hit.body._sse) {
        res.write(`event: ${ev.event}\ndata: ${JSON.stringify(ev.data)}\n\n`);
        await new Promise((r) => setTimeout(r, ev.delayMs ?? 5));
      }
      res.end();
      return;
    }
    if (!hit) {
      // 본보기에 없는 경로 = 콘솔이 부르는데 우리가 모르는 경로. 조용히 넘기지 않는다.
      state.calls[state.calls.length - 1].unknown = true;
      res.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' });
      res.end(JSON.stringify({ detail: `본보기 없음: ${req.method} ${apiPath}` }));
      return;
    }
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify(hit.body));
  });

  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const { port } = server.address();
  return {
    ...state,
    port,
    live: !!upstream,
    upstream,
    url: `http://127.0.0.1:${port}`,
    reset() {
      state.faults.length = 0;
      state.calls.length = 0;
      for (const k of Object.keys(state.overrides)) delete state.overrides[k];
    },
    /** 특정 엔드포인트가 불린 횟수 */
    countCalls(method, pathPart) {
      return state.calls.filter((c) => c.method === method && c.path.startsWith(pathPart)).length;
    },
    lastCall(method, pathPart) {
      return [...state.calls].reverse().find((c) => c.method === method && c.path.startsWith(pathPart)) || null;
    },
    /** 조건에 맞는 요청이 하나라도 있었나 — 같은 경로를 여러 주체가 부를 때 쓴다
     *  (예: /healthz 는 콘솔 본체와 배포 배지가 각각 부르고, 배지는 키를 안 싣는다). */
    anyCall(method, pathPart, pred = () => true) {
      return state.calls.find((c) => c.method === method && c.path.startsWith(pathPart) && pred(c)) || null;
    },
    close: () => new Promise((r) => server.close(r)),
  };
}
