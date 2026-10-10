#!/usr/bin/env node
/* 시나리오 실행기.
 *
 *   node run.mjs                     전부 실행 (사람이 읽는 출력)
 *   node run.mjs --json              결과를 JSON 으로 (pytest 가 이 형식을 읽는다)
 *   node run.mjs --only golden       id/제목에 'golden' 이 든 것만
 *   node run.mjs --list              목록만
 *   node run.mjs --scenario-timeout-ms 15000  시나리오 하나의 최대 실행 시간
 *   node run.mjs --suite-timeout-ms 120000    전체 mock 실행의 최대 시간
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
import { closeOpenPages, installGlobalErrorTrap, setDefaultStorage, setDefaultTimerScale } from './lib/page.mjs';

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
const parseTimeout = (name, fallback, minimum) => {
  const raw = opt(name);
  if (raw === null) return fallback;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < minimum) {
    throw new Error(`${name} 은(는) ${minimum} 이상의 밀리초여야 합니다 (받은 값: ${raw})`);
  }
  return Math.floor(value);
};
// E2E는 mock-only DOM 배선 시험이다. 실제 서버 모드의 네트워크 지연을 묶어 죽이지 않도록
// 기본 전체 한도는 mock에만 적용한다. 어느 한 시나리오가 무한 대기하면 결과를 CRASH로 남기고
// 다음 실행을 막지 않는다.
const SCENARIO_TIMEOUT_MS = parseTimeout('--scenario-timeout-ms', 15_000, 100);
const SUITE_TIMEOUT_MS = parseTimeout('--suite-timeout-ms', LIVE_BASE ? 0 : 120_000, 0);
// mock은 브라우저 타이머만 10배 빠르게 돌린다. 폴링/진행 UI의 순서는 그대로 보되,
// 166개 시나리오가 수십 초의 실제 interval을 기다리지는 않게 한다.
if (!LIVE_BASE) setDefaultTimerScale(0.1);
// 실서버는 API 키가 다르다. 없으면 콘솔 기본값(demo-secret-key)으로 전부 401 이 난다.
const API_KEY = opt('--key') || process.env.KOIPA_E2E_API_KEY || null;
if (API_KEY) setDefaultStorage({ koipa_api_key: API_KEY });

// [2026-09-09] 배포 서버의 콘솔 인증은 **공유 API 키가 아니라 포털 JWT 쿠키**다
// (golden.py 가 공유 키를 거부한다: "shared API keys are not allowed"). --key 만 있던 동안
// 인증이 필요한 화면은 실서버 모드에서 전부 401 로 실패했고, 그것이 서버 결함처럼 보였다.
// 값은 쿠키 문자열 그대로다:  --cookie 'koipa_access_token=eyJ...'
// ⚠ 토큰을 명령줄에 그대로 적으면 셸 기록에 남는다. 파일에서 읽는 편이 낫다:
//     KOIPA_E2E_COOKIE="koipa_access_token=$(cat token.txt)" node run.mjs --base ...
const COOKIE = opt('--cookie') || process.env.KOIPA_E2E_COOKIE || null;

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
const server = await startServer(
  LIVE_BASE ? { upstream: LIVE_BASE.replace(/\/$/, ''), cookie: COOKIE } : {},
);

if (server.live && !AS_JSON) {
  console.log(`실서버 모드: ${server.upstream}`);
  console.log(ALLOW_WRITES
    ? '⚠ --allow-writes — 상태를 바꾸는 시나리오도 돈다. 실제로 분류·확정·학습 요청이 나간다.'
    : '읽기 전용 — 상태를 바꾸는 시나리오는 건너뛴다(돌리려면 --allow-writes).');
}

const results = [];
const suiteStartedAt = Date.now();
let suiteTimedOut = false;

function elapsedMs() { return Date.now() - suiteStartedAt; }

function progress(index, scenario, message) {
  // JSON은 stdout만 기계가 읽는다. 긴 실행의 관측은 stderr로 내보내 JSON 계약을 깨지 않는다.
  process.stderr.write(`[e2e ${index}/${list.length} · ${scenario.id}] ${message}\n`);
}

async function withinTimeout(work, timeoutMs, label) {
  if (!timeoutMs) return work();
  let timer = null;
  try {
    return await Promise.race([
      work(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label}: ${timeoutMs}ms 제한을 넘겼습니다`)), timeoutMs);
      }),
    ]);
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

function skipped(s, reason) {
  return { id: s.id, title: s.title, file: s.file, status: 'skipped', reason, failures: [], passed: [], ms: 0 };
}

for (const [index, s] of list.entries()) {
  if (SUITE_TIMEOUT_MS && elapsedMs() >= SUITE_TIMEOUT_MS) {
    suiteTimedOut = true;
    results.push(skipped(s, `전체 mock 실행 한도 ${SUITE_TIMEOUT_MS}ms를 넘겨 실행하지 않음`));
    continue;
  }
  if (server.live && s.needsMock) {
    results.push(skipped(s, '고장 주입이 필요해 실서버 모드에서는 건너뛴다'));
    continue;
  }
  if (server.live && s.needsData) {
    results.push(skipped(s, '본보기 데이터(문서·목록)가 있어야 성립 — 실서버 DB 내용은 시험이 정할 수 없다'));
    continue;
  }
  if (server.live && s.writes && !ALLOW_WRITES) {
    results.push(skipped(s, '서버 상태를 바꾸는 시나리오 — --allow-writes 로만 실행'));
    continue;
  }
  progress(index + 1, s, '시작');
  server.reset();
  const check = makeCheck({ live: !!server.live });
  const t0 = Date.now();
  let crash = null;
  let page = null;
  try {
    const remaining = SUITE_TIMEOUT_MS ? Math.max(1, SUITE_TIMEOUT_MS - elapsedMs()) : 0;
    const timeoutMs = remaining ? Math.min(SCENARIO_TIMEOUT_MS, remaining) : SCENARIO_TIMEOUT_MS;
    page = await withinTimeout(
      () => s.run({ server, check, live: !!server.live }),
      timeoutMs,
      `시나리오 ${s.id}`,
    );
  } catch (e) {
    crash = `${e && e.constructor ? e.constructor.name : 'Error'}: ${e && e.message}\n${(e && e.stack) || ''}`.slice(0, 1500);
    if (crash.includes('제한을 넘겼습니다')) closeOpenPages();
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
  progress(index + 1, s, crash ? '중단' : check.failures.length ? '실패' : '통과');
}

await server.close();

if (AS_JSON) {
  const bad = results.some((r) => r.status === 'fail' || r.status === 'crash') || suiteTimedOut;
  const payload = {
    base: server.url,
    live: !!server.live,
    suite: {
      elapsed_ms: elapsedMs(),
      scenario_timeout_ms: SCENARIO_TIMEOUT_MS,
      suite_timeout_ms: SUITE_TIMEOUT_MS,
      timed_out: suiteTimedOut,
    },
    results,
  };
  // stdout flush 뒤 명시적으로 끝낸다. jsdom/undici가 남긴 핸들이 있어도 JSON을
  // 기다리다 멈추는 일이 없고, 실패/타임아웃은 shell에서도 실패 코드로 드러난다.
  process.stdout.write(JSON.stringify(payload, null, 2), () => process.exit(bad ? 1 : 0));
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
  if (suiteTimedOut) {
    console.log(`전체 mock 실행 한도 ${SUITE_TIMEOUT_MS}ms를 넘어, 남은 시나리오는 실행하지 않았습니다.`);
    bad += 1;
  }
  process.exit(bad ? 1 : 0);
}
