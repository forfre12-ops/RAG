#!/usr/bin/env node
/* 시나리오 실행기.
 *
 *   node run.mjs                     전부 실행 (사람이 읽는 출력)
 *   node run.mjs --json              결과를 JSON 으로 (pytest 가 이 형식을 읽는다)
 *   node run.mjs --only golden       id/제목에 'golden' 이 든 것만
 *   node run.mjs --list              목록만
 *   node run.mjs --base http://…     실서버를 상대로 (mock 대신). 아래 주의 참조
 *   node run.mjs --base … --allow-writes   상태를 바꾸는 시나리오까지
 *
 * --base 실서버 모드: 화면도 API 도 그 서버에서 받는다(자체 서버는 기록만 하는 프록시로 선다).
 * 고장 주입은 불가능하므로 needsMock 시나리오는 건너뛴다. 상태를 바꾸는 시나리오(writes)는
 * **기본으로 건너뛴다** — 실서버에 분류·확정·학습 요청을 무심코 날리지 않기 위해서다.
 * 돌리려면 --allow-writes 를 명시하고, 시험 서버에만 쓸 것.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { startServer } from './lib/server.mjs';
import { makeCheck } from './lib/expect.mjs';
import { installGlobalErrorTrap, setDefaultStorage } from './lib/page.mjs';

installGlobalErrorTrap();

const HERE = path.dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const flag = (n) => argv.includes(n);
const opt = (n, d = null) => {
  const i = argv.indexOf(n);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : d;
};

const AS_JSON = flag('--json');
const ONLY = opt('--only');
const LIVE_BASE = opt('--base');
const ALLOW_WRITES = flag('--allow-writes');
// 실서버는 API 키가 다르다. 없으면 콘솔 기본값(demo-secret-key)으로 전부 401 이 난다.
const API_KEY = opt('--key') || process.env.KOIPA_E2E_API_KEY || null;
if (API_KEY) setDefaultStorage({ koipa_api_key: API_KEY });

async function loadScenarios() {
  const dir = path.join(HERE, 'scenarios');
  const files = fs.readdirSync(dir).filter((f) => f.endsWith('.mjs')).sort();
  const out = [];
  for (const f of files) {
    const mod = await import(pathToFileURL(path.join(dir, f)).href);
    for (const s of mod.scenarios || []) out.push({ ...s, file: f });
  }
  return out;
}

function selected(list) {
  if (!ONLY) return list;
  const k = ONLY.toLowerCase();
  return list.filter((s) => s.id.toLowerCase().includes(k) || s.title.toLowerCase().includes(k) || s.file.includes(k));
}

const all = await loadScenarios();
const list = selected(all);

if (flag('--list')) {
  for (const s of list) console.log(`${s.id}\t${s.title}`);
  process.exit(0);
}

/* 실서버 모드도 **자체 서버를 띄우고 그리로 넘긴다**(기록 프록시). 그래야 "어떤 요청이
 * 어떤 본문으로 나갔나"를 실서버 상대로도 그대로 확인할 수 있다. */
const server = await startServer(LIVE_BASE ? { upstream: LIVE_BASE.replace(/\/$/, '') } : {});

if (server.live && !AS_JSON) {
  console.log(`실서버 모드: ${server.upstream}`);
  console.log(ALLOW_WRITES
    ? '⚠ --allow-writes — 상태를 바꾸는 시나리오도 돈다. 실제로 분류·확정·학습 요청이 나간다.'
    : '읽기 전용 — 상태를 바꾸는 시나리오는 건너뛴다(돌리려면 --allow-writes).');
}

const results = [];
for (const s of list) {
  if (server.live && s.needsMock) {
    results.push({ id: s.id, title: s.title, file: s.file, status: 'skipped', reason: '고장 주입이 필요해 실서버 모드에서는 건너뛴다', failures: [], passed: [], ms: 0 });
    continue;
  }
  if (server.live && s.needsData) {
    results.push({ id: s.id, title: s.title, file: s.file, status: 'skipped', reason: '본보기 데이터(문서·목록)가 있어야 성립 — 실서버 DB 내용은 시험이 정할 수 없다', failures: [], passed: [], ms: 0 });
    continue;
  }
  if (server.live && s.writes && !ALLOW_WRITES) {
    results.push({ id: s.id, title: s.title, file: s.file, status: 'skipped', reason: '서버 상태를 바꾸는 시나리오 — --allow-writes 로만 실행', failures: [], passed: [], ms: 0 });
    continue;
  }
  server.reset();
  const check = makeCheck({ live: !!server.live });
  const t0 = Date.now();
  let crash = null;
  let page = null;
  try {
    page = await s.run({ server, check, live: !!server.live });
  } catch (e) {
    crash = `${e && e.constructor ? e.constructor.name : 'Error'}: ${e && e.message}\n${(e && e.stack) || ''}`.slice(0, 1500);
  } finally {
    if (page && page.close) page.close();
  }
  results.push({
    id: s.id,
    title: s.title,
    why: s.why || '',
    file: s.file,
    status: crash ? 'crash' : check.failures.length ? 'fail' : 'pass',
    crash,
    failures: check.failures,
    passed: check.passed,
    ms: Date.now() - t0,
  });
}

await server.close();

if (AS_JSON) {
  process.stdout.write(JSON.stringify({ base: server.url, live: !!server.live, results }, null, 2));
} else {
  const mark = { pass: 'PASS', fail: 'FAIL', crash: 'CRASH', skipped: 'SKIP' };
  let bad = 0;
  for (const r of results) {
    if (r.status === 'fail' || r.status === 'crash') bad += 1;
    console.log(`${mark[r.status].padEnd(5)} ${r.id.padEnd(34)} ${r.title}  (${r.ms}ms, 확인 ${r.passed.length}건)`);
    for (const f of r.failures) console.log(`        · ${f.label}${f.detail ? '  — ' + f.detail : ''}`);
    if (r.crash) console.log(`        · 시나리오가 중단됐다: ${r.crash.split('\n')[0]}`);
  }
  const npass = results.filter((r) => r.status === 'pass').length;
  console.log(`\n${results.length}개 시나리오 · 통과 ${npass} · 실패 ${bad} · 건너뜀 ${results.filter((r) => r.status === 'skipped').length}`);
  process.exit(bad ? 1 : 0);
}
