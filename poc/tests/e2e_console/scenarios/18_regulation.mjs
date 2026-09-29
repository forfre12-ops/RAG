/* 18. 사내 규정(참고 표시) — 설정 탭 카드와 검수 화면의 「관련 규정(참고)」 블록 (FUN-004).
 *
 * 왜 만들었나(2026-09-25). 올려 둔 규정에서 검수 중인 문서와 관련된 원문 문장을 참고로 보여 주는 기능이다.
 * 서버 쪽은 파이썬 시험이 잠그고, 여기서는 화면이 **실제로 눌리고 그려지는지**를 본다 — 특히
 *   ① 기능이 꺼진 서버(라우트 없음)에서 카드도 검수 블록도 나오지 않고, 오류 줄도 남지 않는가
 *   ② 규정 원문·규정명은 사용자 입력이라 HTML 로 해석되지 않는가
 *   ③ 검수 화면에 점수·유사도·신뢰도 수치가 나오지 않는가(2026-08-24 지시)
 *   ④ 결과가 비면 블록을 그리지 않는가 — 「관련 규정 없음」은 규정에 그런 내용이 없다는 뜻으로 읽힌다
 *   ⑤ 쓰기(올리기·활성화·보관·삭제·조항 표시)가 확인창·적용 대상 확인·Safe Mode 를 지키는가
 *
 * 하니스 사용법. 기본 본보기(fixtures.json)는 규정 3건(사용 중·사용 전·보관)을 돌려주고, 검수 큐 본보기의
 * doc_id 는 UUID 가 아니라서(E2E-DOC-001) 관련 규정 조회가 나가지 않는다 — 조회를 보려면
 * `withQueue` 로 UUID 문서를 심는다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';
import { FORBIDDEN, visibleText } from './16_forbidden_strings.mjs';

const REG1 = 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa'; // 사용 중
const REG2 = 'bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb'; // 사용 전(분석 끝)
const REG3 = 'cccccccc-3333-4333-8333-cccccccccccc'; // 보관
const C1 = 'c1c1c1c1-0000-4000-8000-000000000001';
const C3 = 'c1c1c1c1-0000-4000-8000-000000000003';
const DOC1 = 'd0d0d0d0-0000-4000-8000-000000000001';
const DOC2 = 'd0d0d0d0-0000-4000-8000-000000000002';

const SENT1 = '① 극비 문서는 사전 승인 없이 사외로 반출할 수 없다.';

const SUMMARY = (id, name, ver, status, over = {}) => ({
  reg_id: id, name, version_label: ver, status, clause_count: 3, sentence_count: 9,
  effective_date: null, created_at: '2026-09-20T01:00:00+00:00', activated_at: null, ...over,
});
const DETAIL = (id, name, ver, status, over = {}) => ({
  ...SUMMARY(id, name, ver, status), filename: 'privacy.md', source_format: 'md', split_mode: 'article',
  embed_model: 'e2e-embedder', embed_target_count: 6, embedded_count: 6, display_clause_count: 1,
  scope_note: null, scope_confirmed: false, warnings: [], error_message: null, ...over,
});
const CLAUSE = (id, no, title, kind, display, text, over = {}) => ({
  clause_id: id, seq: 0, article_no: no, title, chapter: '제1장', kind, kind_source: 'auto', display, text, ...over,
});
const LIST = (items) => ({ items, total: items.length });

const EVID = (doc, sentences = [SENT1], over = {}) => ({
  doc_id: doc, indexed: true, reason: null,
  items: [{
    regulation: { reg_id: REG1, name: '문서보안 규정', version_label: 'v3.1' },
    clause: { clause_id: C3, article_no: '제12조', title: '극비 문서의 취급' },
    sentences, is_grade_list: false,
  }],
  ...over,
});
const EMPTY = (doc, reason = 'below_floor') => ({ doc_id: doc, indexed: true, reason, items: [] });
// [2026-09-29] evFault·QITEM·withQueue(검수 큐 조회 벡터)를 뺐다 — 그 UI(검수 큐·확정 대기의
// 「왜 이 등급인가?」 패널)를 콘솔에서 뺐다(위 두 시나리오 제거 자리 참고).

const calls = (server, method, prefix) => server.calls.filter((c) => c.method === method && c.path.startsWith(prefix));
const selectBtn = (page, id) => page.q(`button[onclick="selectRegulation('${id}')"]`);
const visText = (page, id) => {
  const el = page.$(id).cloneNode(true);
  el.querySelectorAll('[data-tech], script, style').forEach((n) => n.remove());
  return (el.textContent || '').replace(/\s+/g, ' ').trim();
};
const NUMBERISH = /\d+(\.\d+)?\s*%|유사도|신뢰도|점수|confidence|score|similarity/i;

async function openConfig(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  page.click(page.q('.tab[data-tab="config"]'));
  return page;
}
async function choose(page, id) {
  page.click(selectBtn(page, id));
  await page.settle();
}

export const scenarios = [
  {
    id: 'regulation.card.wiring-any-server',
    title: '화면을 열면 규정 기능이 있는지 한 번 묻고, 있으면 카드를 보이고 없으면 아무것도 바꾸지 않는다',
    why: '기능은 기본 꺼짐이라 대부분의 서버에는 라우트가 없다. 카드가 항상 보이면 눌러도 404 인 카드가 되고, '
       + '항상 숨겨 두면 켠 서버에서 쓸 수 없다. 이 시나리오는 본보기에 기대지 않아 실서버 모드에서도 같은 판정을 한다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      const probe = server.anyCall('GET', '/regulations', (c) => c.path.split('?')[0] === '/regulations');
      check.ok(probe, '열자마자 GET /regulations 로 규정 기능이 있는지 물었다', server.calls.map((c) => c.path).join(' | '));
      check.ok(!probe?.headers['x-api-key'] || page.win.localStorage.getItem('koipa_api_key'),
        '키·토큰을 화면에서 받지 않는다(예전에 저장된 값이 있을 때만 헤더에 실린다)');

      const card = page.$('reg-card');
      check.ok(card, '카드 마크업은 항상 있다(숨김 여부만 서버 응답이 정한다)');
      const on = !card.classList.contains('reg-off');
      page.click(page.q('.tab[data-tab="config"]'));
      check.eq(page.visible('reg-card'), on, on ? '기능이 있는 서버 — 설정 탭에서 카드가 보인다' : '기능이 없는 서버 — 카드가 보이지 않는다');
      page.click(page.q('.tab[data-tab="ops"]'));
      check.ok(!page.visible('reg-card'), '운영 탭에서는 카드가 보이지 않는다(설정 탭 카드다)');
      if (!on) {
        check.eq(calls(server, 'GET', '/regulations').length, 1, '꺼진 서버에는 그 뒤로 규정 요청을 더 보내지 않는다');
      }
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'regulation.card.hidden-when-server-has-no-feature',
    needsMock: true,
    title: '규정 기능이 없는 서버(404)·볼 권한이 없는 사용자(403)에게는 카드도 검수 블록도 없고 오류 줄도 남지 않는다',
    why: '꺼진 서버에서 정상인 404 가 요청 로그에 오류로 쌓이면 운영자는 매번 고장으로 읽는다. '
       + '검수 화면은 문서를 펼칠 때마다 조회를 보내지도 않아야 한다',
    // [2026-09-29] "펼쳐도 규정을 조회하지 않는다" 절을 뺐다 — 그 펼침 패널(검수 큐의 toggleWhy)을
    // 콘솔에서 뺐다. 실제 문서 검수 중 규정참고 표시는 이제 KL 포털이 GET .../regulation-evidence 를
    // 직접 불러 그린다(우리 콘솔 몫이 아니다) — 카드 숨김 자체는 여전히 우리 설정 탭의 몫이라 남긴다.
    async run({ server, check }) {
      for (const [status, detail] of [[404, 'Not Found'], [403, 'Forbidden']]) {
        server.reset();
        server.faults.push({ path: '/regulations', method: 'GET', status, body: { detail } });
        const page = await openPage(server, '/console/admin.html');
        await page.settle();

        check.ok(page.$('reg-card').classList.contains('reg-off'), `${status}: 카드가 숨겨져 있다`);
        page.click(page.q('.tab[data-tab="config"]'));
        check.ok(!page.visible('reg-card'), `${status}: 설정 탭을 열어도 카드가 보이지 않는다`);
        check.includes(page.text('pane-note'), '등급 체계와 태깅 규칙입니다', `${status}: 탭 안내문은 그대로다`);
        check.excludes(page.text('pane-note'), '사내 규정', `${status}: 없는 기능을 안내문에 적지 않는다`);
        check.eq(page.logLines('err').filter((l) => l.includes('/regulations')).length, 0, `${status}: 요청 로그에 규정 오류 줄이 없다`);
        assertNoScriptErrors(check, page);
        page.close();
      }
      return null;
    },
  },

  {
    id: 'regulation.card.lists-in-plain-korean',
    needsMock: true,
    title: '규정 목록이 한국어 상태(사용 중·사용 전·보관)로 그려지고, 내부 값·구현 정보·쓰이지 않는 입력칸이 없다',
    why: '상태 영문값(active·ready)이나 필드 이름이 화면에 새면 감리·사용자가 그대로 읽는다. 입력칸은 전부 요청에 실려야 한다',
    async run({ server, check }) {
      const page = await openConfig(server);

      check.ok(page.visible('reg-card'), '설정 탭에 카드가 보인다');
      check.includes(page.text('pane-note'), '사내 규정', '기능이 있는 서버에서는 탭 안내문이 카드를 말한다');
      const rows = page.qa('#reg-body tr');
      check.eq(rows.length, 3, '규정 3건이 그려졌다');
      const body = page.text('reg-body');
      for (const s of ['사용 중', '사용 전', '보관']) check.includes(body, s, `상태 ${s}`);
      check.includes(body, '문서보안 규정', '규정명이 보인다');
      check.includes(body, 'v3.1', '판 표기가 보인다');
      check.includes(page.text('reg-info'), '규정 3건', '건수가 보인다');
      check.includes(page.text('reg-info'), '사용 중 1건', '사용 중인 규정 수가 보인다');

      const txt = visText(page, 'reg-card');
      check.ok(!/\b(active|ready|archived|indexing|failed)\b/.test(txt), '상태의 영문 내부값이 화면에 없다', txt.slice(0, 200));
      check.ok(!/reg_id|version_label|clause_id|scope_confirmed/.test(txt), '필드 이름이 화면에 없다');
      check.includes(txt, '등급을 바꾸지 않습니다', '카드 안내문이 등급을 바꾸지 않는다고 밝힌다');
      check.ok(!NUMBERISH.test(txt), '점수·유사도·퍼센트 수치가 카드에 없다', txt.slice(0, 200));

      // 버튼 — 상태에 맞는 것만: 조항 보기(3) · 보관(사용 중 1) · 삭제(사용 전·보관 2)
      check.eq(page.qa('#reg-body button[onclick^="selectRegulation("]').length, 3, '「조항 보기」가 규정마다 있다');
      check.eq(page.qa('#reg-body button[onclick^="archiveRegulation("]').length, 1, '「보관」은 사용 중인 규정에만 있다');
      check.eq(page.qa('#reg-body button[onclick^="deleteRegulation("]').length, 2, '「삭제」는 사용 중이 아닌 규정에만 있다');
      check.ok(page.q(`button[onclick="archiveRegulation('${REG1}')"]`), '보관 버튼이 사용 중인 규정(REG1)에 붙었다');
      check.ok(!page.q(`button[onclick="deleteRegulation('${REG1}')"]`), '사용 중인 규정에는 삭제 버튼이 없다');

      // 입력칸 — 전부 요청에 쓰인다(다른 시나리오가 실제 전송을 본다)
      // [2026-09-29 정정] reg-llm-toggle·reg-runtime-toggle(런타임 스위치 체크박스 2개, 208d202f)이
      // 빠져 있었다 — 이 시험이 그 커밋 이후로 갱신된 적이 없었다(오늘 콘솔 분리 작업과는 무관).
      const ids = page.qa('#reg-card input').map((e) => e.id).sort();
      check.eq(ids.join(','), 'reg-date,reg-file,reg-llm-toggle,reg-name,reg-runtime-toggle,reg-scope-note,reg-scope-ok,reg-ver', '입력칸은 규정명·판·시행일·파일·활성화 확인·런타임 스위치 2개뿐이다');
      check.eq(page.dialogs.filter((d) => d.kind === 'prompt').length, 0, '프롬프트 창이 뜨지 않는다');

      const vis = visibleText(page);
      const hits = FORBIDDEN.filter(([s]) => vis.includes(s));
      check.ok(hits.length === 0, '구현 정보 금지 문자열이 화면에 없다', hits.map(([s]) => s).join(' · '));
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'regulation.select.shows-clause-table-and-toggles-display',
    writes: true,
    needsMock: true,
    title: '「조항 보기」로 조항 표(종류·표시)가 열리고, 「표시」를 바꾸면 PATCH 로 나가며, 「원문」이 펼쳐진다',
    why: '어떤 조항이 검수 화면에 나갈지는 관리자가 정한다 — 총칙·절차·등급 정의는 처음부터 꺼져 있고, 잘못 나뉘었으면 고칠 수 있어야 한다',
    async run({ server, check }) {
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, '개인정보 처리 지침', 'v1.0', 'ready');
      server.overrides['GET /regulations/{reg_id}/clauses'] = LIST([
        CLAUSE(C1, '제1조', '목적', 'general', false, '제1조(목적)\n이 지침은 개인정보의 처리에 관한 기준을 정한다.'),
        CLAUSE('c1c1c1c1-0000-4000-8000-000000000002', '제10조', '등급의 구분', 'grade_def', false, '제10조(등급의 구분)\n문서는 극비·기밀·대외비·공개로 구분한다.'),
        CLAUSE(C3, '제12조', '극비 문서의 취급', 'handling', true, `제12조(극비 문서의 취급)\n${SENT1}`),
      ]);
      const page = await openConfig(server);
      check.eq(page.$('reg-detail').style.display, 'none', '고르기 전에는 선택한 규정 자리가 닫혀 있다');

      await choose(page, REG2);
      check.ok(server.anyCall('GET', `/regulations/${REG2}`, (c) => c.path.split('?')[0] === `/regulations/${REG2}`), '규정 상세를 읽었다');
      const cl = server.anyCall('GET', `/regulations/${REG2}/clauses`);
      check.ok(cl, '조항 표를 읽었다');
      check.ok(/limit=500/.test(cl?.path || ''), '한 번에 500건까지 요청한다');

      check.ok(page.visible('reg-detail'), '선택한 규정 자리가 열렸다');
      check.includes(page.text('reg-detail-head'), '개인정보 처리 지침 v1.0', '규정명·판이 보인다');
      check.includes(page.text('reg-detail-head'), '사용 전', '상태가 한국어다');
      check.includes(page.text('reg-detail-head'), '표시 대상 1개', '표시 대상 조항 수가 보인다');
      const main = page.qa('#reg-clauses tr:not([id])');
      check.eq(main.length, 3, '조항 3개가 표에 그려졌다');
      const kinds = main.map((r) => r.children[2].textContent.trim());
      check.eq(kinds.join('|'), '일반|등급 정의|취급 기준', '종류가 한국어로 나온다');
      const boxes = page.qa('#reg-clauses input[type="checkbox"]');
      check.eq(boxes.map((b) => b.checked).join(','), 'false,false,true', '취급 기준 조항만 표시 대상이다');
      check.ok(page.visible('reg-activate-box'), '사용 전 규정에는 활성화 자리가 있다');

      const t1 = page.$(`reg-ct-${C1}`);
      check.ok(!page.visible(t1), '원문은 처음에 접혀 있다');
      page.click(page.q(`button[onclick="toggleRegClauseText('${C1}')"]`));
      check.ok(page.visible(t1), '「원문」을 누르면 펼쳐진다');
      check.includes(page.text(t1), '이 지침은 개인정보의 처리에 관한 기준을 정한다.', '조항 원문이 그대로 나온다');
      page.click(page.q(`button[onclick="toggleRegClauseText('${C1}')"]`));
      check.ok(!page.visible(t1), '다시 누르면 접힌다');

      // 표시 끄기 — 서버가 돌려준 조항으로 행을 바꾼다
      server.overrides['PATCH /regulations/{reg_id}/clauses/{clause_id}'] = CLAUSE(C3, '제12조', '극비 문서의 취급', 'handling', false, `제12조(극비 문서의 취급)\n${SENT1}`, { kind_source: 'admin' });
      const cb = page.qa('#reg-clauses input[type="checkbox"]')[2];
      cb.click();
      await page.settle();
      const p = server.lastCall('PATCH', '/regulations');
      check.eq(p?.path, `/regulations/${REG2}/clauses/${C3}`, 'PATCH 가 그 규정의 그 조항으로 나갔다');
      check.eq(JSON.stringify(p?.body), '{"display":false}', '본문은 표시 여부 하나뿐이다(행위자는 인증이 정한다)');
      const after = page.qa('#reg-clauses input[type="checkbox"]');
      check.eq(after[2].checked, false, '서버 응답대로 체크가 꺼져 있다');
      check.includes(page.text('reg-clauses'), '수정됨', '관리자가 고친 조항은 표시가 붙는다');
      check.eq(page.dialogs.length, 0, '성공 경로에서 경고창이 뜨지 않는다');

      // 실패 — 체크를 서버 값으로 되돌리고 사유를 알린다
      server.faults.push({ path: `/regulations/${REG2}/clauses/${C3}`, method: 'PATCH', status: 409, body: { detail: '수정할 수 없는 상태입니다: 분석 중' } });
      page.qa('#reg-clauses input[type="checkbox"]')[2].click();
      await page.settle();
      check.includes(page.text('reg-notice'), '조항 표시를 바꾸지 못했습니다', '실패를 카드 안에 알린다');
      check.includes(page.text('reg-notice'), '분석 중', '서버가 준 사유가 한국어로 그대로 나온다');
      check.eq(page.qa('#reg-clauses input[type="checkbox"]')[2].checked, false, '체크가 서버가 아는 값으로 되돌아왔다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'regulation.upload.sends-every-field-and-follows-progress',
    writes: true,
    needsMock: true,
    title: '규정 올리기가 파일·규정명·판·시행일을 실어 보내고, 분석 진행을 조용히 따라가다 끝나면 멈춘다',
    why: '올리는 데 몇 분 걸리는 규정이 목록에서 안 바뀌면 관리자는 올라간 건지 모른다. 진행을 갱신하는 타이머가 끝난 뒤에도 돌면 서버를 계속 두드린다',
    async run({ server, check }) {
      server.overrides['GET /regulations'] = LIST([SUMMARY(REG1, '문서보안 규정', 'v3.1', 'active')]);
      const page = await openConfig(server);
      page.win.eval('REG_POLL_MS=40');
      const before = server.calls.length;

      // 빠진 것이 있으면 요청이 나가지 않는다
      page.click('reg-upload');
      await page.settle();
      check.includes(page.text('reg-notice'), '올릴 규정 파일을 고르십시오', '파일이 없으면 이유를 알린다');
      page.attachFile('reg-file', { name: 'privacy_2026.md', type: 'text/markdown', content: new TextEncoder().encode('제1조(목적)\n이 지침은 개인정보의 처리에 관한 기준을 정한다.\n') });
      check.eq(page.$('reg-name').value, 'privacy_2026', '파일을 고르면 규정명 초안이 파일 이름으로 채워진다');
      page.set('reg-name', '');
      page.click('reg-upload');
      await page.settle();
      check.includes(page.text('reg-notice'), '규정명을 입력하십시오', '규정명이 없으면 이유를 알린다');
      page.set('reg-name', '개인정보 처리 지침');
      page.click('reg-upload');
      await page.settle();
      check.includes(page.text('reg-notice'), '판 표기를 입력하십시오', '판이 없으면 이유를 알린다');
      check.eq(calls(server, 'POST', '/regulations').length, 0, '빠진 것이 있는 동안 서버 요청은 나가지 않았다');

      // 올리기 — 분석 중으로 온다
      page.set('reg-ver', 'v1.0');
      page.set('reg-date', '2026-09-01');
      server.overrides['GET /regulations'] = LIST([
        SUMMARY(REG1, '문서보안 규정', 'v3.1', 'active'), SUMMARY(REG2, '개인정보 처리 지침', 'v1.0', 'indexing', { clause_count: 0, sentence_count: 0 })]);
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, '개인정보 처리 지침', 'v1.0', 'indexing', { embedded_count: 2, embed_target_count: 6, clause_count: 0, sentence_count: 0 });
      page.click('reg-upload');
      await page.settle();

      const up = server.lastCall('POST', '/regulations');
      check.eq(up?.path, '/regulations', 'POST /regulations 가 나갔다');
      const raw = up?.raw || '';
      check.includes(raw, 'name="file"', '파일이 실렸다');
      check.includes(raw, 'filename="privacy_2026.md"', '파일 이름이 실렸다');
      check.includes(raw, 'name="name"', '규정명이 실렸다');
      check.includes(raw, '개인정보 처리 지침', '규정명 값이 실렸다');
      check.includes(raw, 'name="version_label"', '판이 실렸다');
      check.includes(raw, 'v1.0', '판 값이 실렸다');
      check.includes(raw, 'name="effective_date"', '시행일이 실렸다');
      check.includes(raw, '2026-09-01', '시행일 값이 실렸다');
      check.excludes(raw, 'name="actor"', '행위자를 본문에 싣지 않는다(감사 신원은 인증이 정한다)');
      check.eq(page.$('reg-name').value + page.$('reg-ver').value + page.$('reg-date').value, '', '보낸 뒤 입력칸이 비워졌다');
      check.includes(page.text('reg-notice'), '규정을 올렸습니다', '올렸다고 알린다');
      check.eq(page.qa('#reg-body tr').length, 2, '목록에 새 규정이 생겼다');
      check.includes(page.text('reg-body'), '분석 중', '새 규정의 상태가 분석 중이다');
      check.includes(page.text('reg-detail-note'), '규정을 분석하는 중…', '선택한 규정 자리에 분석 진행이 보인다');
      check.includes(page.text('reg-detail-note'), '2/6', '진행 건수가 보인다');
      check.ok(!page.visible('reg-clause-wrap'), '분석 중에는 조항 표를 그리지 않는다');
      check.ok(!page.visible('reg-activate-box'), '분석 중에는 활성화할 수 없다');

      // 진행 갱신 — 요청 로그·원본 응답 칸을 건드리지 않는 조용한 읽기
      const logBefore = page.qa('#logbody .logline').length;
      const polled = await page.until(() => calls(server, 'GET', '/regulations').length > 3, 3000);
      check.ok(polled, '분석 중인 동안 상태를 다시 읽는다');
      check.eq(page.qa('#logbody .logline').length, logBefore, '갱신은 요청 로그를 채우지 않는다');

      // 끝남
      server.overrides['GET /regulations'] = LIST([
        SUMMARY(REG1, '문서보안 규정', 'v3.1', 'active'), SUMMARY(REG2, '개인정보 처리 지침', 'v1.0', 'ready')]);
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, '개인정보 처리 지침', 'v1.0', 'ready');
      const done = await page.until(() => page.text('reg-body').includes('사용 전') && page.visible('reg-clause-wrap'), 4000);
      check.ok(done, '끝나면 목록·선택한 규정·조항 표가 저절로 바뀐다', page.text('reg-body'));
      check.includes(page.text('reg-notice'), '규정 분석이 끝났습니다', '끝났다고 알린다');
      check.ok(page.visible('reg-activate-box'), '끝나면 활성화 자리가 열린다');
      check.excludes(page.text('reg-detail-note'), '규정을 분석하는 중…', '분석 중 문구가 사라졌다');
      await page.settle();
      const n = calls(server, 'GET', '/regulations').length;
      await page.wait(300);                       // 갱신 간격(40ms)의 7배 — 멈추지 않았다면 여러 번 더 나간다
      check.eq(calls(server, 'GET', '/regulations').length, n, '끝난 뒤에는 갱신 타이머가 멈춘다');

      // 같은 파일 — 새로 만들지 않고 기존 규정을 연다
      server.overrides['POST /regulations'] = { reg_id: REG2, status: 'ready', duplicate: true };
      page.attachFile('reg-file', { name: 'privacy_2026.md', type: 'text/markdown', content: new TextEncoder().encode('같은 내용') });
      page.set('reg-name', '개인정보 처리 지침'); page.set('reg-ver', 'v1.0');
      page.click('reg-upload');
      await page.settle();
      check.includes(page.text('reg-notice'), '같은 파일이 이미 올라 있습니다', '같은 파일이면 그렇게 알린다');

      // 실패 — 사유를 한국어로
      for (const [status, detail, want] of [
        [422, '지원하지 않는 형식입니다: .exe (지원: txt, md, docx, pdf, hwp, hwpx)', '지원하지 않는 형식입니다'],
        [413, 'file too large', '한 번에 처리할 수 있는 크기를 넘습니다'],
        [503, '색인 작업 큐를 사용할 수 없습니다', '색인 작업 큐를 사용할 수 없습니다'],
      ]) {
        server.faults.length = 0;
        server.faults.push({ path: '/regulations', method: 'POST', status, body: { detail } });
        page.attachFile('reg-file', { name: 'x.exe', type: 'application/octet-stream', size: 64 });
        page.set('reg-name', '오류 시험'); page.set('reg-ver', 'v0');
        page.click('reg-upload');
        await page.settle();
        check.includes(page.text('reg-notice'), '규정을 올리지 못했습니다', `${status}: 실패했다고 알린다`);
        check.includes(page.text('reg-notice'), want, `${status}: 사유가 사람이 읽는 말이다`);
      }
      check.ok(!page.$('reg-upload').disabled, '실패 뒤에도 올리기 버튼이 다시 눌린다');
      check.ok(server.calls.length > before, '요청이 실제로 나갔다(자기 점검)');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'regulation.upload.failed-and-warning-notes',
    needsMock: true,
    title: '분석에 실패한 규정은 사유를 보이고 조항 표·활성화가 없으며, 분할 경고는 그대로 알린다',
    why: '실패한 규정에 활성화 버튼이 남으면 누르고 나서야 못 쓴다는 것을 안다. 경고("조 단위 구분이 없어 …")는 정확도가 낮다는 유일한 신호다',
    async run({ server, check }) {
      server.overrides['GET /regulations'] = LIST([SUMMARY(REG3, '낡은 규정', 'v0', 'failed', { clause_count: 0 })]);
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG3, '낡은 규정', 'v0', 'failed', { error_message: '규정에서 글자를 읽지 못했습니다' });
      const page = await openConfig(server);
      await choose(page, REG3);
      check.includes(page.text('reg-detail-note'), '규정 분석에 실패했습니다', '실패를 알린다');
      check.includes(page.text('reg-detail-note'), '규정에서 글자를 읽지 못했습니다', '서버가 준 사유를 보인다');
      check.ok(!page.visible('reg-clause-wrap'), '조항 표가 없다');
      check.ok(!page.visible('reg-activate-box'), '활성화 자리가 없다');
      check.ok(page.q(`button[onclick="deleteRegulation('${REG3}')"]`), '실패한 규정은 지울 수 있다');
      check.includes(page.text('reg-body'), '분석 실패', '목록 상태가 한국어다');

      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, '개인정보 처리 지침', 'v1.0', 'ready', { split_mode: 'paragraph', warnings: ['조 단위 구분이 없어 정확도가 낮을 수 있습니다.'] });
      server.overrides['GET /regulations'] = LIST([SUMMARY(REG2, '개인정보 처리 지침', 'v1.0', 'ready')]);
      page.click(page.q('button[onclick="loadRegulations()"]'));
      await page.settle();
      await choose(page, REG2);
      check.includes(page.text('reg-detail-note'), '조 단위 구분이 없어 정확도가 낮을 수 있습니다.', '분할 경고가 그대로 보인다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'regulation.activate.needs-scope-confirmation-and-confirm-dialog',
    writes: true,
    needsMock: true,
    title: '활성화는 「검수 대상 문서의 취급 기준」 확인 체크와 확인창을 거쳐야 나가고, 확인 없이는 요청이 없다',
    why: '규정이 문서에 적용되는지는 점수로 가를 수 없다 — 사람이 확인하는 것이 유일한 장치다. 체크 없이 활성화가 되면 그 장치가 없다',
    async run({ server, check }) {
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, '개인정보 처리 지침', 'v1.0', 'ready');
      let page = await openConfig(server);
      await choose(page, REG2);
      check.includes(page.text('reg-activate-box'), '이 규정은 검수 대상 문서의 취급 기준입니다.', '확인 문구가 화면에 있다');

      page.click('reg-activate');
      await page.settle();
      check.eq(calls(server, 'POST', `/regulations/${REG2}/activate`).length, 0, '체크하지 않으면 요청이 나가지 않는다');
      check.includes(page.text('reg-notice'), '체크', '왜 안 되는지 알린다');
      check.eq(page.dialogs.filter((d) => d.kind === 'confirm').length, 0, '체크 전에는 확인창도 뜨지 않는다');

      page.check('reg-scope-ok', true);
      page.set('reg-scope-note', '사내 업무 문서 전체');
      page.click('reg-activate');
      await page.settle();
      const conf = page.dialogs.filter((d) => d.kind === 'confirm');
      check.eq(conf.length, 1, '확인창이 한 번 뜬다');
      check.includes(conf[0]?.message || '', '관련 규정(참고)', '확인창이 무엇이 바뀌는지 말한다');
      check.includes(conf[0]?.message || '', '등급 판정에는 쓰이지 않습니다', '등급을 바꾸지 않는다고 밝힌다');
      const a = server.lastCall('POST', `/regulations/${REG2}/activate`);
      check.ok(a, 'POST /regulations/{id}/activate 가 나갔다');
      check.eq(JSON.stringify(a?.body), '{"scope_confirmed":true,"scope_note":"사내 업무 문서 전체"}', '적용 대상 확인과 설명이 실렸다');
      check.includes(page.text('reg-notice'), '활성화했습니다', '활성화했다고 알린다');
      check.ok(calls(server, 'GET', '/regulations').length >= 2, '목록을 다시 읽었다');

      // 설명을 비우면 null 이다
      page.check('reg-scope-ok', true);
      page.set('reg-scope-note', '');
      page.click('reg-activate');
      await page.settle();
      check.eq(JSON.stringify(server.lastCall('POST', `/regulations/${REG2}/activate`)?.body), '{"scope_confirmed":true,"scope_note":null}', '설명이 없으면 null 로 나간다');

      // 서버가 거절하면 사유를 알린다
      server.faults.push({ path: `/regulations/${REG2}/activate`, method: 'POST', status: 409, body: { detail: '표시할 수 있는 조항이 없습니다' } });
      page.check('reg-scope-ok', true);
      page.click('reg-activate');
      await page.settle();
      check.includes(page.text('reg-notice'), '활성화하지 못했습니다', '거절을 알린다');
      check.includes(page.text('reg-notice'), '표시할 수 있는 조항이 없습니다', '서버가 준 사유가 나온다');
      page.close();

      // 확인창에서 취소
      server.reset();
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, '개인정보 처리 지침', 'v1.0', 'ready');
      page = await openConfig(server, { confirmAnswer: false });
      await choose(page, REG2);
      page.check('reg-scope-ok', true);
      page.click('reg-activate');
      await page.settle();
      check.eq(page.dialogs.filter((d) => d.kind === 'confirm').length, 1, '확인창이 떴다');
      check.eq(calls(server, 'POST', `/regulations/${REG2}/activate`).length, 0, '취소하면 요청이 나가지 않는다');
      page.close();

      // 이미 사용 중인 규정 — 활성화 자리가 없다
      server.reset();
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG1, '문서보안 규정', 'v3.1', 'active', { scope_note: '사내 업무 문서 전체', scope_confirmed: true });
      page = await openConfig(server);
      await choose(page, REG1);
      check.ok(!page.visible('reg-activate-box'), '사용 중인 규정에는 활성화 자리가 없다');
      check.includes(page.text('reg-detail-note'), '참고로 표시되고 있습니다', '표시되고 있다고 알린다');
      check.includes(page.text('reg-detail-note'), '사내 업무 문서 전체', '적용 대상 설명이 보인다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'regulation.archive-and-delete.ask-first-and-refresh',
    writes: true,
    needsMock: true,
    title: '「보관」·「삭제」는 확인창을 거쳐 각자의 요청으로 나가고 목록이 다시 읽힌다 — 취소하면 요청이 없다',
    why: '삭제는 조항·문장·원본이 함께 지워져 되돌릴 수 없다. 확인 없이 나가면 잘못 누른 한 번으로 규정이 사라진다',
    async run({ server, check }) {
      let page = await openConfig(server, { confirmAnswer: false });
      page.click(page.q(`button[onclick="archiveRegulation('${REG1}')"]`));
      page.click(page.q(`button[onclick="deleteRegulation('${REG3}')"]`));
      await page.settle();
      check.eq(page.dialogs.filter((d) => d.kind === 'confirm').length, 2, '둘 다 확인창이 떴다');
      check.eq(calls(server, 'POST', `/regulations/${REG1}/archive`).length + calls(server, 'DELETE', '/regulations/').length, 0, '취소하면 요청이 나가지 않는다');
      page.close();

      server.reset();
      page = await openConfig(server);
      const listBefore = calls(server, 'GET', '/regulations').length;
      page.click(page.q(`button[onclick="archiveRegulation('${REG1}')"]`));
      await page.settle();
      const msg = page.dialogs.filter((d) => d.kind === 'confirm')[0]?.message || '';
      check.includes(msg, '문서보안 규정 v3.1', '확인창이 어느 규정인지 말한다');
      check.includes(msg, '다시 활성화할 수 있습니다', '보관은 되돌릴 수 있다고 말한다');
      check.ok(server.lastCall('POST', `/regulations/${REG1}/archive`), 'POST /regulations/{id}/archive 가 나갔다');
      check.includes(page.text('reg-notice'), '보관했습니다', '보관했다고 알린다');
      check.gte(calls(server, 'GET', '/regulations').length, listBefore + 1, '목록을 다시 읽었다');

      await choose(page, REG3);
      check.ok(page.visible('reg-detail'), '지우기 전에 규정 하나를 열어 두었다');
      page.click(page.q(`button[onclick="deleteRegulation('${REG3}')"]`));
      await page.settle();
      const del = page.dialogs.filter((d) => d.kind === 'confirm')[1]?.message || '';
      check.includes(del, '되돌릴 수 없습니다', '삭제는 되돌릴 수 없다고 말한다');
      const d = server.lastCall('DELETE', '/regulations/');
      check.eq(d?.path, `/regulations/${REG3}`, 'DELETE 가 그 규정으로 나갔다');
      check.ok(!page.visible('reg-detail'), '지운 규정이 열려 있었으면 그 자리를 닫는다');
      check.includes(page.text('reg-notice'), '삭제했습니다', '삭제했다고 알린다');

      // 서버가 막으면 사유를 알린다(사용 중인 판은 지울 수 없다 등)
      server.faults.push({ path: `/regulations/${REG2}`, method: 'DELETE', status: 409, body: { detail: '삭제할 수 없는 상태입니다: 사용 중' } });
      page.click(page.q(`button[onclick="deleteRegulation('${REG2}')"]`));
      await page.settle();
      check.includes(page.text('reg-notice'), '삭제하지 못했습니다', '거절을 알린다');
      check.includes(page.text('reg-notice'), '사용 중', '서버가 준 사유가 나온다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'regulation.preview.uses-the-documents-on-screen',
    needsMock: true,
    title: '「미리보기」는 화면에 올라온 저장된 문서만(중복 없이) 보내고, 문서마다 규정 문장이나 표시하지 않은 이유를 보인다',
    why: '규정이 문서에 적용되는지는 점수로 가를 수 없어 활성화 전에 사람이 결과를 보고 정한다. 붙여넣은 본문처럼 저장되지 않은 문서는 조회할 수 없다',
    // [2026-09-29] 이 목록의 공급원이던 검수 큐 카드를 콘솔에서 뺐다 — QUEUE 는 이제 항상 빈
    // 배열이라 loadReviewQueue() 로 채울 수 없다(admin.html regPreviewDocs 주석 참고). 알려진
    // 한계로 남긴 수동 경로(QUEUE.push)를 이 시험도 그대로 쓴다 — 렌더링 자체는 아직 살아 있다.
    async run({ server, check }) {
      server.overrides['POST /regulations/{reg_id}/preview'] = { reg_id: REG2, results: [
        EVID(DOC1), EMPTY(DOC2, 'below_floor')] };
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, '개인정보 처리 지침', 'v1.0', 'ready');
      const page = await openConfig(server);
      // regPreviewDocs() 는 QUEUE 항목의 title 만 읽는다(loadReviewQueue() 가 하던 매핑을 손으로 흉내).
      // QUEUE 는 top-level let 이라 page.win.QUEUE 로는 안 닿는다(window 프로퍼티가 아니다) —
      // win.eval 로 같은 전역 어휘 범위(script 들이 공유하는 영역)에서 직접 push 한다.
      page.win.eval(`QUEUE.push(
        { doc_id: '${DOC1}', title: '검수 문서 1.docx' },
        { doc_id: '${DOC2}', title: '검수 문서 2.docx' },
        { doc_id: '${DOC1}', title: '검수 문서 1.docx' },
        { doc_id: 'NOT-A-UUID', title: 'x' }
      )`);
      await choose(page, REG2);
      page.click(page.q('button[onclick="previewRegulation()"]'));
      await page.settle();

      const pv = server.lastCall('POST', `/regulations/${REG2}/preview`);
      check.ok(pv, 'POST /regulations/{id}/preview 가 나갔다');
      check.eq(JSON.stringify(pv?.body), JSON.stringify({ doc_ids: [DOC1, DOC2] }), '저장된 문서만, 같은 문서는 한 번만 실렸다');
      const t = page.text('reg-preview');
      check.includes(t, '문서 2건 중 1건에 규정 문장이 표시됩니다', '몇 건에 표시되는지 말한다');
      check.includes(t, '검수 문서 1.docx', '문서 이름이 보인다');
      check.includes(t, '문서보안 규정 v3.1 · 제12조(극비 문서의 취급)', '어느 조항인지 보인다');
      check.includes(t, SENT1, '규정 원문 문장이 그대로 나온다');
      check.includes(t, '관련도가 기준에 못 미쳐 표시하지 않았습니다', '표시하지 않은 문서는 이유를 말한다');
      check.includes(t, '등급을 바꾸지 않습니다', '등급을 바꾸지 않는다고 밝힌다');
      check.ok(!NUMBERISH.test(t), '점수·유사도·퍼센트 수치가 없다', t.slice(0, 200));
      check.eq(calls(server, 'POST', '/regulations/').filter((c) => !c.path.endsWith('/preview')).length, 0, '미리보기는 상태를 바꾸는 요청을 보내지 않는다');
      page.close();

      // 화면에 저장된 문서가 없으면 요청 없이 안내한다(기본 본보기의 doc_id 는 UUID 가 아니다)
      server.reset();
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, '개인정보 처리 지침', 'v1.0', 'ready');
      const p2 = await openConfig(server);
      await choose(p2, REG2);
      p2.click(p2.q('button[onclick="previewRegulation()"]'));
      await p2.settle();
      check.eq(calls(server, 'POST', `/regulations/${REG2}/preview`).length, 0, '미리볼 문서가 없으면 요청이 나가지 않는다');
      check.includes(p2.text('reg-preview'), '미리볼 문서가 없습니다', '이유와 다음에 할 일을 말한다');
      check.includes(p2.text('reg-preview'), '더 이상 자동으로 채워지지 않습니다', '자동으로 안 채워지는 이유를 말한다');
      return p2;
    },
  },

  /* [2026-09-29] 'regulation.review.block-under-why-in-both-lists' ·
     'regulation.review.empty-and-failure-draw-nothing-or-a-muted-line' 을 뺐다 — 둘 다
     검수 큐·확정 대기의 「왜 이 등급인가?」 펼침 패널(toggleWhy·toggleWhyStaging) 안에서
     관련 규정 블록이 그려지는지를 봤는데, 그 패널 자체를 콘솔에서 뺐다(사용자 지시 — 실고객
     문서 검수는 KL 포털로). 그 표시는 이제 KL 포털이 GET /documents/{doc_id}/regulation-evidence
     를 직접 불러 자기 화면에 그리는 몫이다(그 엔드포인트는 이미 kl_backend 를 받는다) — 우리
     e2e 로 검증할 UI 표면이 아니다. 서버 쪽 계약(빈 결과·404·503 처리)은 여전히
     poc/tests/test_regulation_api.py 가 지킨다. */

  {
    id: 'regulation.xss.names-and-text-are-never-html',
    needsMock: true,
    title: '규정명·조항 원문·관련 문장에 HTML·스크립트가 섞여도 실행되지 않고 글자로만 보인다',
    why: '규정 파일은 회원사 담당자가 올리는 사용자 입력이다. 규정명이나 본문에 태그가 들어 있으면 관리자·검수자 화면에서 실행될 수 있다(저장형 XSS)',
    async run({ server, check }) {
      const evil = '<img src=x onerror="window.__pwn=1">';
      const evil2 = '<script>window.__pwn=2</script>';
      server.overrides['GET /regulations'] = LIST([SUMMARY(REG2, `${evil}규정`, 'v1<b>x</b>', 'ready', { effective_date: evil })]);
      server.overrides['GET /regulations/{reg_id}'] = DETAIL(REG2, `${evil}규정`, 'v1<b>x</b>', 'ready', { warnings: [evil], scope_note: evil });
      server.overrides['GET /regulations/{reg_id}/clauses'] = LIST([
        CLAUSE(C3, evil, evil, 'handling', true, `${evil2}\n${evil}`)]);

      const page = await openConfig(server);
      await choose(page, REG2);
      page.click(page.q(`button[onclick="toggleRegClauseText('${C3}')"]`));
      await page.settle();

      check.eq(page.win.__pwn, undefined, '어떤 문자열도 실행되지 않았다');
      check.eq(page.qa('#reg-card img, #reg-card script').length, 0, '카드 안에 img·script 태그가 만들어지지 않았다');
      check.eq(page.qa('#reg-card b').filter((b) => b.textContent === 'x').length, 0, '판 표기에 섞인 <b> 태그가 굵은 글씨로 해석되지 않았다(카드가 쓰는 <b> 는 제외)');
      check.includes(page.text('reg-body'), '<img src=x onerror="window.__pwn=1">규정', '규정명이 글자 그대로 보인다');
      check.includes(page.text('reg-clauses'), '<script>window.__pwn=2</script>', '조항 원문이 글자 그대로 있다');
      // [2026-09-29] 관련 문장(검수 화면 블록) XSS 검증은 뺐다 — 그 표시 화면(#why-0)을 콘솔에서
      // 뺐다. 그 렌더링은 이제 KL 포털의 몫이라 우리 e2e 로 검증할 대상이 아니다.
      check.eq(page.errors.length, 0, '스크립트 오류가 없다', page.errors.map((e) => e.message).join(' | '));
      return page;
    },
  },

  {
    id: 'regulation.pane.checkbox-and-buttons-survive-tab-switches',
    needsMock: true,
    title: '탭을 오가도 카드가 설정 탭에만 보이고, 기능이 꺼진 카드는 되살아나지 않는다',
    why: 'showPane 이 [data-pane] 카드의 표시를 다시 쓴다 — 인라인 스타일로 숨기면 탭을 한 번 누르면 숨긴 카드가 되살아난다(프로파일 카드에서 실측)',
    async run({ server, check }) {
      server.faults.push({ path: '/regulations', method: 'GET', status: 404, body: { detail: 'Not Found' } });
      const off = await openPage(server, '/console/admin.html');
      await off.settle();
      for (const tab of ['review', 'train', 'config', 'ops', 'config']) {
        off.click(off.q(`.tab[data-tab="${tab}"]`));
        check.ok(!off.visible('reg-card'), `기능이 없는 서버 — ${tab} 탭에서도 카드가 숨겨져 있다`);
      }
      off.close();

      server.reset();
      const on = await openPage(server, '/console/admin.html');
      await on.settle();
      const seen = {};
      for (const tab of ['review', 'train', 'config', 'ops', 'config']) {
        on.click(on.q(`.tab[data-tab="${tab}"]`));
        seen[tab] = on.visible('reg-card');
      }
      check.eq(JSON.stringify(seen), JSON.stringify({ review: false, train: false, config: true, ops: false }), '기능이 있는 서버 — 설정 탭에서만 보인다');
      assertNoScriptErrors(check, on);
      return on;
    },
  },
];
