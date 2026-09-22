/* 17. 확정 대기(자동 확정 임시저장) 목록 — FUN-005 「임시저장 → 관리자 최종확정」.
 *
 * 왜 만들었나(2026-09-21). 서버는 2026-08-28 부터 GET /review-queue?include_staging=true 로
 * 확정 대기(status='staging') 목록을 돌려주는데, 관리자 콘솔에서 그 값을 쓰는 곳이 0건이었다.
 * 자동 확정된 분류가 관리자가 볼 수 없는 목록에 쌓이고 있었다는 뜻이다.
 * 여기서는 그 카드를 **실제로 눌러서** 본다: 목록 표시 · 최종 확정 후 행이 빠지는가 · 빈 목록 ·
 * 오류(401·403·500) · 서버가 include_staging 을 무시하는 경우.
 *
 * 하니스 사용법. 기본 본보기(GET /review-queue)는 쿼리를 구분하지 않고 같은 3건을 돌려준다. 그래서
 * 확정 대기 조회만 걸리도록 **정규식 고장 규칙**(path: /include_staging=true/)으로 응답을 심는다 —
 * 검수 큐 조회에는 그 값이 없으므로 그쪽은 본보기 3건 그대로다(두 목록이 서로 안 섞이는지도 함께 본다).
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

/** 확정 대기 조회에만 걸린다(주소에 include_staging=true 가 있는 요청). */
const STAGING_URL = /include_staging=true/;

const ITEM = (n, over = {}) => ({
  classification_id: `aaaaaaaa-0000-4000-8000-00000000000${n}`,
  doc_id: `STG-DOC-00${n}`,
  filename: `자동확정 문서 ${n}.docx`,
  grade: 'S3',
  confidence: 0.97,           // 서버는 준다 — 화면에 나오면 안 된다(2026-08-24 지시)
  model_version: 'v-fe4b386b',
  status: 'staging',
  classified_at: '2026-09-20T01:00:00Z',
  text_preview: `확정 대기 본문 미리보기 ${n} — 신제품 출시 보도자료 공개본.`,
  review_reason: null,
  score_margin: 0.51,
  ...over,
});
const LIST = (items, over = {}) => ({ items, total: items.length, limit: 50, offset: 0, warnings: [], ...over });

async function withStaging(server, items, opts = {}, over = {}) {
  server.faults.push({ path: STAGING_URL, body: LIST(items, over) });
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  return page;
}

const rows = (page) => page.qa('#stg-queue .q-item');
const confirmBtn = (page, n = 0) => page.qa('#stg-queue button[onclick^="doConfirmStaging("]')[n];
const relabelBtn = (page, n = 0) => page.qa('#stg-queue button[onclick^="doRelabelStaging("]')[n];
const keyOf = (rowEl) => rowEl.getAttribute('data-stg-key');
const posts = (server) => server.calls.filter((c) => c.method === 'POST');
/** 기본 화면에 **보이는** 글자. [data-tech](기술 상세 — ?tech=1 일 때만 보인다)·script·style 은 뺀다. */
const visText = (page, id) => {
  const el = page.$(id).cloneNode(true);
  el.querySelectorAll('[data-tech], script, style').forEach((n) => n.remove());
  return (el.textContent || '').replace(/\s+/g, ' ').trim();
};

export const scenarios = [
  {
    id: 'staging.list.wiring-any-server',
    title: '화면을 열면 확정 대기 조회(include_staging=true)가 나가고, 목록·빈 상태·오류 중 하나가 그려진다',
    why: '서버 경로는 있었으나 화면이 그 값을 쓰지 않아 관리자가 확정 대기 문서를 볼 수 없었다. '
       + '이 시나리오는 본보기 데이터에 기대지 않아 실서버 모드에서도 같은 판정을 한다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      const call = server.anyCall('GET', '/review-queue', (c) => /include_staging=true/.test(c.path));
      check.ok(call, '열자마자 include_staging=true 로 확정 대기를 조회했다', server.calls.map((c) => c.path).join(' | '));
      check.ok(/status=staging/.test(call?.path || ''), '확정 대기만 받도록 status=staging 도 준다(검수 대기와 섞이지 않는다)');
      check.ok(!/key=|token=|apikey/i.test(call?.path || ''), '주소에 키·토큰 파라미터가 없다');
      // 실서버 모드에서 --key 를 주면 콘솔이 (예전에 저장된 값이 있을 때만 쓰는 호환 경로로) 헤더를 붙인다 — 그때는 보지 않는다.
      check.data.ok(!call?.headers['x-api-key'], '요청에 X-API-Key 를 붙이지 않는다(인증은 서버가 붙인 쿠키)');

      check.ok(page.$('staging-card'), '확정 대기 카드가 있다');
      check.ok(page.$('btn-staging-queue'), '다시 불러오는 버튼이 있다');
      check.ok(!page.$('queue').contains(page.$('stg-queue')), '검수 큐 목록과 다른 자리다');
      check.ok(page.visible('staging-card'), '운영 탭에서 바로 보인다');

      const shown = page.text('stg-queue') + ' ' + page.text('stg-info');
      check.ok(!/불러오는 중/.test(page.text('stg-queue')), '「불러오는 중」에 멈춰 있지 않다', shown);
      check.matches(shown, /확정 대기|불러오지 못했습니다|조회 실패/, '목록·빈 상태·오류 문구 중 하나가 그려졌다', shown);
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.list.renders-and-is-separate-from-review-queue',
    needsMock: true,
    title: '확정 대기 문서가 검수 대기 목록과 따로 그려지고, 신뢰도 수치·내부 상태값·입력칸이 없다',
    why: '두 목록은 성격이 다르다(AI 가 확신하지 못한 문서 / AI 가 기준을 통과시킨 문서). 섞이면 자동 확정분이 '
       + '검수 대기처럼 읽힌다. 서버는 confidence 를 주지만 화면에 수치를 띄우지 않는다는 지시가 있다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1), ITEM(2, { grade: 'S2' })]);

      check.eq(rows(page).length, 2, '확정 대기 2건이 그려졌다');
      check.eq(page.qa('#queue .q-item').length, 3, '검수 대기 목록은 본보기 3건 그대로다(섞이지 않는다)');
      for (const id of ['STG-DOC-001', 'STG-DOC-002']) {
        check.includes(page.html('stg-queue'), id, `확정 대기에 ${id} 가 보인다`);
        check.excludes(page.html('queue'), id, `${id} 는 검수 대기 목록에 없다`);
      }
      check.includes(page.html('stg-queue'), '자동확정 문서 1.docx', '파일명이 보인다');
      check.includes(page.text('stg-queue'), '확정 대기 본문 미리보기 1', '본문 미리보기가 보인다');
      check.includes(page.text('stg-queue'), '자동 확정', '자동 확정된 건이라고 행마다 말한다');
      check.includes(page.html('stg-queue'), 'g-S2', '등급 배지가 서버 등급을 따른다');
      check.includes(page.text('stg-info'), '확정 대기 2건', '건수가 표시된다');
      check.includes(visText(page, 'staging-card'), '문서 검토 대기', '카드 안내문이 검수 대기 목록과의 차이를 말한다');

      const q = server.anyCall('GET', '/review-queue', (c) => STAGING_URL.test(c.path));
      check.ok(/limit=50/.test(q?.path || ''), '한 번에 50건까지만 요청한다(검수 큐와 같은 한도)');

      // 지시(2026-08-24): 화면에 신뢰도 수치를 띄우지 않는다 — 서버가 준 0.97 이 어디에도 없어야 한다.
      const txt = visText(page, 'staging-card');
      check.ok(!/\d+(\.\d+)?\s*%/.test(txt), '카드에 퍼센트 수치가 없다', txt.slice(0, 200));
      check.ok(!txt.includes('0.97') && !txt.includes('97'), '서버가 준 신뢰도 0.97 이 화면에 없다');
      check.ok(!/staging|needs_review|confidence/i.test(txt), '내부 상태값·필드명이 화면에 새지 않는다', txt.slice(0, 200));

      // 지시: 쓰이지 않는 입력칸을 만들지 않는다 · 키·토큰 입력칸 금지.
      check.eq(page.qa('#staging-card input, #staging-card textarea').length, 0, '카드에 입력칸이 없다(키·토큰 칸 포함)');
      check.eq(page.qa('#staging-card select').length, 2, '선택칸은 행마다 재라벨 등급 하나뿐이다');
      check.eq(page.dialogs.filter((d) => d.kind === 'prompt').length, 0, '프롬프트 창이 뜨지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.confirm.removes-row-with-existing-confirm-api',
    writes: true,
    needsMock: true,
    title: '「최종 확정」을 누르면 기존 POST /confirm 으로 나가고, 그 행이 목록에서 빠지고 건수가 줄어든다',
    why: '요건은 새 API 없이 기존 최종 확정 동작을 재사용하는 것이다. 확정했는데 행이 남으면 두 번 누르게 된다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1), ITEM(2)]);

      page.click(confirmBtn(page, 0));
      await page.settle();

      const call = server.lastCall('POST', '/confirm');
      check.ok(call, 'POST /confirm 이 나갔다');
      check.eq(call?.path, '/confirm', '새 API 가 아니라 기존 /confirm 이다');
      check.eq(call?.body?.doc_id, 'STG-DOC-001', '어느 문서인지 실렸다');
      check.eq(call?.body?.confirmed_label, 'S3', '확정 등급이 서버가 제시한 등급이다');
      check.eq(call?.body?.inference_id, 'aaaaaaaa-0000-4000-8000-000000000001', '어느 분류인지 실렸다');
      check.eq(call?.body?.model_version, 'v-fe4b386b', '모델 버전이 실렸다');
      check.ok(call?.body?.actor?.user_id, '행위자가 실렸다 — 감사 추적의 전제');
      check.eq(call?.body?.note, 'console confirm', '검수 큐의 확정과 같은 본문이다');
      check.eq(posts(server).filter((c) => c.path !== '/confirm').length, 0, '확정 말고 다른 쓰기 요청은 없었다');

      check.eq(rows(page).length, 1, '확정한 행이 목록에서 빠졌다');
      check.excludes(page.html('stg-queue'), 'STG-DOC-001', '확정한 문서는 더 보이지 않는다');
      check.includes(page.html('stg-queue'), 'STG-DOC-002', '나머지는 그대로 있다');
      check.includes(page.text('stg-info'), '확정 대기 1건', '건수가 하나 줄었다');
      check.includes(page.text('stg-notice'), '최종 확정', '무엇이 처리됐는지 목록 위에 남는다');
      check.eq(page.qa('#queue .q-item').length, 3, '검수 대기 목록은 건드리지 않는다');
      check.eq(page.dialogs.length, 0, '성공 경로에서 경고창이 뜨지 않는다');

      page.click(confirmBtn(page, 0));
      await page.settle();
      check.eq(rows(page).length, 0, '마지막 행도 빠졌다');
      check.includes(page.text('stg-queue'), '확정 대기 중인 문서가 없습니다', '다 처리하면 빈 상태로 말한다');
      check.includes(page.text('stg-info'), '확정 대기 0건', '건수가 0 이다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.confirm.remaining-beyond-page-is-not-called-empty',
    writes: true,
    needsMock: true,
    title: '서버에 더 남아 있는데 불러온 건을 다 처리하면 「없습니다」가 아니라 남은 건수를 말한다',
    why: '한 번에 50건만 불러온다. 다 처리한 화면이 「확정 대기 없음」이라고 하면 남은 문서를 아무도 보지 않는다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1), ITEM(2)], {}, { total: 120 });
      check.includes(page.text('stg-info'), '확정 대기 120건 (표시 2)', '전체 건수와 표시 건수를 나눠 말한다');
      check.includes(page.text('stg-info'), '일부만 표시', '일부만 보이고 있다고 말한다');

      page.click(confirmBtn(page, 0));
      await page.settle();
      page.click(confirmBtn(page, 0));
      await page.settle();

      const q = page.text('stg-queue');
      check.excludes(q, '확정 대기 중인 문서가 없습니다', '남아 있는데 없다고 하지 않는다');
      check.includes(q, '남은 118건', '남은 건수를 말한다');
      check.includes(q, '확정 대기 보기', '다음에 할 일을 말한다');
      return page;
    },
  },

  {
    id: 'staging.confirm.not-persisted-keeps-row',
    writes: true,
    needsMock: true,
    title: 'persisted=false 면 행을 지우지 않고 기록되지 않았다고 알린다',
    why: 'DB 가 없을 때 조용히 성공처럼 보이면 확정한 줄 알고 넘어간다 — 확정 대기는 그 문서의 마지막 관문이다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1)]);
      server.overrides['POST /confirm'] = {
        confirmation_id: '77777777-7777-4777-8777-777777777777',
        confirmed_at: '2026-09-21T02:00:00Z',
        persisted: false,
        warnings: ['DB 미가용 — 감사 로그만 남았습니다'],
        second_review_required: false,
      };
      page.click(confirmBtn(page, 0));
      await page.settle();

      check.eq(rows(page).length, 1, '행이 목록에 남아 있다');
      check.includes(page.text('stg-notice'), '기록되지 않았습니다', '기록되지 않았다고 목록 위에 알린다');
      check.excludes(page.text('stg-notice'), '최종 확정했습니다', '성공이라고 말하지 않는다');
      check.includes(page.logLines('err').join(' '), '미영속', '로그에도 남는다');
      check.ok(!confirmBtn(page, 0).disabled, '다시 시도할 수 있게 버튼이 살아 있다');
      check.includes(page.text('stg-info'), '확정 대기 1건', '건수가 줄지 않는다');
      return page;
    },
  },

  {
    id: 'staging.confirm.second-review-leaves-list-with-notice',
    writes: true,
    needsMock: true,
    title: '고등급 이중검토 대상이면 완료라 하지 않고 2차 검수가 필요하다고 알린다',
    why: '이 확정은 아직 끝나지 않았다 — 서버에서는 확정 대기가 아니라 2차 검수 대기로 옮겨진다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1, { grade: 'TS' })]);
      server.overrides['POST /confirm'] = {
        confirmation_id: '77777777-7777-4777-8777-777777777777',
        confirmed_at: '2026-09-21T02:00:00Z',
        persisted: true, warnings: [], second_review_required: true,
      };
      page.click(confirmBtn(page, 0));
      await page.settle();

      check.eq(rows(page).length, 0, '확정 대기에서는 빠진다(더는 확정 대기가 아니다)');
      check.includes(page.text('stg-notice'), '2차 검수', '2차 검수가 필요하다고 알린다');
      check.excludes(page.text('stg-notice'), '최종 확정했습니다', '최종 확정했다고 말하지 않는다');
      return page;
    },
  },

  {
    id: 'staging.confirm.forbidden-keeps-row-and-tells-role',
    writes: true,
    needsMock: true,
    title: '확정이 403 으로 거절되면 행을 남기고 어떤 권한이 필요한지 알려준다',
    why: '권한 없는 사용자의 클릭이 조용히 무시되거나 행이 사라지면 확정된 줄 안다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1)]);
      server.faults.push({ path: '/confirm', method: 'POST', status: 403, body: { detail: 'admin role required' } });
      page.click(confirmBtn(page, 0));
      await page.settle();

      check.eq(rows(page).length, 1, '행이 남아 있다');
      check.includes(page.text('stg-notice'), '권한이 없습니다', '권한이 없다고 알린다');
      check.includes(page.text('stg-notice'), 'admin', '필요한 역할을 말한다');
      check.excludes(page.text('stg-notice'), 'API 키', '키를 확인하라는 말은 하지 않는다');
      check.ok(!confirmBtn(page, 0).disabled, '버튼이 잠긴 채 남지 않는다');
      check.eq(page.dialogs.length, 0, '경고창 없이 목록 위에 알린다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.confirm.non-json-success-keeps-row',
    writes: true,
    needsMock: true,
    title: '확정 응답이 JSON 이 아니면(200 으로 온 HTML) 성공으로 치지 않고 행을 남긴다',
    why: '역프록시가 오류 페이지를 200 으로 주면 응답 본문에 confirmation_id 가 없다 — 그것을 성공으로 읽으면 확정하지 않은 문서가 목록에서 사라진다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1)]);
      server.faults.push({ path: '/confirm', method: 'POST', status: 200, raw: '<html><body>gateway page</body></html>' });
      page.click(confirmBtn(page, 0));
      await page.settle();

      check.eq(rows(page).length, 1, '행이 남아 있다');
      check.includes(page.text('stg-notice'), '응답을 읽지 못했습니다', '응답을 못 읽었다고 알린다');
      check.excludes(page.text('stg-notice'), '최종 확정했습니다', '성공이라고 말하지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.confirm.double-click-sends-once',
    writes: true,
    needsMock: true,
    title: '응답을 기다리는 동안 같은 행의 버튼이 잠겨 요청이 한 번만 나간다',
    why: '확정은 감사 기록에 남는 동작이다 — 느린 응답에 두 번 눌러도 한 번이어야 한다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1)]);
      server.faults.push({ path: '/confirm', method: 'POST', delayMs: 300 });
      const btn = confirmBtn(page, 0);
      page.click(btn);
      check.ok(btn.disabled, '누르는 즉시 버튼이 잠긴다');
      check.ok(relabelBtn(page, 0).disabled, '같은 행의 재라벨 버튼도 잠긴다');
      btn.click();   // 잠긴 버튼을 또 눌러도 아무 일도 없어야 한다
      await page.settle();
      check.eq(server.countCalls('POST', '/confirm'), 1, '확정 요청이 한 번만 나갔다');
      check.eq(rows(page).length, 0, '처리가 끝나면 행이 빠진다');
      return page;
    },
  },

  {
    id: 'staging.relabel.sends-chosen-grade',
    writes: true,
    needsMock: true,
    title: '서버 제시 등급이 틀리면 등급을 골라 「재라벨 전송」 — 기존 POST /relabel 로 나가고 행이 빠진다',
    why: '확정 대기의 목적은 관리자가 잘못된 자동 확정을 잡는 것이다 — 고칠 길이 이 목록에 없으면 잡아도 소용이 없다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1)]);
      const key = keyOf(rows(page)[0]);
      page.set(`stg-rl-${key}`, 'S1');
      page.click(relabelBtn(page, 0));
      await page.settle();

      const call = server.lastCall('POST', '/relabel');
      check.ok(call, 'POST /relabel 이 나갔다');
      check.eq(call?.path, '/relabel', '새 API 가 아니라 기존 /relabel 이다');
      check.eq(call?.body?.original_label, 'S3', '원 등급이 실렸다');
      check.eq(call?.body?.corrected_label, 'S1', '교정 등급이 실렸다');
      check.eq(call?.body?.inference_id, 'aaaaaaaa-0000-4000-8000-000000000001', '어느 분류인지 실렸다');
      check.ok(call?.body?.actor?.user_id, '행위자가 실렸다');
      check.eq(server.countCalls('POST', '/confirm'), 0, '확정 요청은 나가지 않았다');
      check.eq(rows(page).length, 0, '재라벨한 행이 목록에서 빠졌다');
      check.includes(page.text('stg-notice'), '재라벨', '무엇이 처리됐는지 목록 위에 남는다');
      check.includes(page.logLines('ok').join(' '), '재라벨 완료', '완료 로그가 남았다');
      return page;
    },
  },

  {
    id: 'staging.relabel.same-grade-asks-first',
    needsMock: true,
    title: '등급을 안 바꾸고 재라벨을 누르면 한 번 되묻고, 취소하면 보내지 않는다',
    why: '같은 등급이면 해야 할 일은 「최종 확정」이다 — 검수 큐의 재라벨과 같은 규칙',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1)], { confirmAnswer: false });
      page.click(relabelBtn(page, 0));
      await page.settle();
      check.ok(page.dialogs.some((d) => d.kind === 'confirm' && d.message.includes('동일')), '동일 등급이라고 되묻는다');
      check.eq(server.countCalls('POST', '/relabel'), 0, '취소하면 보내지 않는다');
      check.eq(rows(page).length, 1, '행은 그대로다');
      return page;
    },
  },

  {
    id: 'staging.safe-mode.blocks-confirm',
    writes: true,
    needsMock: true,
    title: '쓰기 허용을 끄면 확정 대기의 확정·재라벨도 나가지 않는다',
    why: '읽기 전용 시연 중 실수로 누르는 것을 막는 장치는 새 카드에도 걸려 있어야 한다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1)]);
      page.check('cfg-write-enable', false);
      page.click(confirmBtn(page, 0));
      page.click(relabelBtn(page, 0));
      await page.settle();

      check.eq(posts(server).length, 0, '쓰기 요청이 하나도 나가지 않았다');
      check.ok(page.dialogs.filter((d) => d.kind === 'alert' && d.message.includes('Safe Mode')).length >= 2, '차단 이유를 알려준다');
      check.eq(rows(page).length, 1, '행은 그대로다');
      // 조회는 막지 않는다
      page.click('btn-staging-queue');
      await page.settle();
      check.eq(rows(page).length, 1, '읽기 전용에서도 다시 불러오기는 된다');
      return page;
    },
  },

  {
    id: 'staging.empty.says-so',
    needsMock: true,
    title: '확정 대기가 0건이면 그렇게 말하고, 다음에 무슨 일이 일어나는지 적는다',
    async run({ server, check }) {
      const page = await withStaging(server, []);
      check.eq(rows(page).length, 0, '행이 없다');
      check.includes(page.text('stg-queue'), '확정 대기 중인 문서가 없습니다', '없다고 말한다');
      check.includes(page.text('stg-queue'), '자동 확정되면', '무엇이 여기 쌓이는지 적는다');
      check.includes(page.text('stg-info'), '확정 대기 0건', '건수 0 을 표시한다');
      check.ok(!/불러오지 못했습니다|조회 실패/.test(page.text('staging-card')), '오류로 읽히지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.empty.server-warning-is-not-zero',
    needsMock: true,
    title: '서버가 빈 목록과 함께 경고를 주면(DB 미가용) 「0건」이 아니라 조회 실패일 수 있다고 말한다',
    why: '서버는 DB 를 못 쓰면 items=[] + warnings 로 답한다 — 이것을 「없습니다」로 그리면 장애가 정상으로 읽힌다',
    async run({ server, check }) {
      const page = await withStaging(server, [], {}, { warnings: ['db unavailable: OperationalError'] });
      check.includes(page.text('stg-queue'), '0건이 아니라 조회 실패일 수 있습니다', '조회 실패 가능성을 말한다');
      check.includes(page.text('stg-queue'), 'db unavailable', '서버가 준 사유를 그대로 보여준다');
      check.excludes(page.text('stg-queue'), '확정 대기 중인 문서가 없습니다', '「없습니다」라고 하지 않는다');
      return page;
    },
  },

  {
    id: 'staging.error.500-visible-and-recoverable',
    needsMock: true,
    title: '서버 오류(500)면 그 사실이 카드에 보이고, 검수 큐는 영향이 없고, 다시 누르면 회복된다',
    why: '실패가 접힌 로그에만 남으면 「확정 대기 0건」과 구분되지 않는다',
    async run({ server, check }) {
      server.faults.push(
        { path: STAGING_URL, status: 500, once: true, body: { detail: 'DB 연결 실패' } },
        { path: STAGING_URL, body: LIST([ITEM(1), ITEM(2)]) },
      );
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      check.includes(page.text('stg-queue'), '불러오지 못했습니다', '목록 자리에 실패가 적힌다');
      check.includes(page.text('stg-info'), '조회 실패', '상단에도 실패라고 적힌다');
      check.includes(page.text('stg-info'), '500', '상태코드가 보인다');
      check.includes(page.logLines('err').join(' '), '확정 대기 로드 실패', '로그에도 남는다');
      check.eq(page.qa('#queue .q-item').length, 3, '검수 큐는 정상으로 그려진다(서로 영향이 없다)');

      page.click('btn-staging-queue');
      await page.settle();
      check.eq(rows(page).length, 2, '다시 누르면 회복된다');
      check.ok(!/조회 실패/.test(page.text('stg-info')), '실패 표시가 사라진다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.error.transient-failure-keeps-list',
    needsMock: true,
    title: '이미 그려 둔 목록은 한 번 실패했다고 지우지 않는다',
    why: '검수 큐와 같은 규칙 — 일시 오류로 화면이 비면 정보가 오히려 준다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1), ITEM(2)]);
      check.eq(rows(page).length, 2, '먼저 목록이 그려져 있다');

      server.faults.unshift({ path: STAGING_URL, status: 503, body: { detail: '일시적 오류' } });
      page.click('btn-staging-queue');
      await page.settle();

      check.eq(rows(page).length, 2, '있던 목록은 그대로 남는다');
      check.includes(page.text('stg-info'), '조회 실패', '실패는 상단에 알린다');
      check.includes(page.text('stg-info'), '503', '상태코드가 보인다');
      return page;
    },
  },

  {
    id: 'staging.error.401-goes-to-login-not-a-prompt',
    needsMock: true,
    title: '인증이 없으면(401) 프롬프트나 키 입력칸 없이 로그인 화면으로 한 번 보내고, 사유만 적는다',
    why: '사용자 지시(2026-08-24): 키·토큰을 화면에서 받지 않는다. 인증은 서버가 붙인 쿠키이고 401 은 로그인 화면으로 보낸다',
    async run({ server, check }) {
      server.faults.push({ path: STAGING_URL, status: 401, body: { detail: 'missing authorization' } });
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      check.eq(page.win.sessionStorage.getItem('koipa_login_bounced'), '1', '로그인 화면으로 한 번 보냈다는 표식이 남는다');
      check.includes(page.text('stg-queue'), '로그인이 필요합니다', '무엇이 필요한지 카드에 적는다');
      check.excludes(visText(page, 'staging-card'), 'API 키', '키를 확인하라는 말을 하지 않는다');
      check.eq(page.dialogs.filter((d) => d.kind === 'prompt').length, 0, '프롬프트 창이 뜨지 않는다');
      check.eq(page.qa('#staging-card input').length, 0, '카드에 입력칸이 없다');
      check.ok(!server.calls.some((c) => c.headers['x-api-key']), '어떤 요청에도 X-API-Key 가 붙지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.error.403-tells-role-no-login-bounce',
    needsMock: true,
    title: '권한이 없으면(403) 어떤 역할이 필요한지 알려주고, 로그인 화면으로 보내지는 않는다',
    why: '로그인은 돼 있는데 역할이 모자란 사용자를 로그인 화면으로 돌려보내면 같은 자리를 맴돈다',
    async run({ server, check }) {
      server.faults.push({ path: STAGING_URL, status: 403, body: { detail: 'admin role required' } });
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      check.includes(page.text('stg-queue'), '권한이 없습니다', '권한이 없다고 적는다');
      check.matches(page.text('stg-queue'), /admin.*reviewer/, '필요한 역할을 말한다');
      check.ok(!page.win.sessionStorage.getItem('koipa_login_bounced'), '로그인 화면으로 보내지 않았다');
      check.eq(page.dialogs.length, 0, '경고창이 뜨지 않는다');
      return page;
    },
  },

  {
    id: 'staging.error.non-json-200-is-not-empty-list',
    needsMock: true,
    title: '조회 응답이 JSON 이 아니면(200 으로 온 HTML) 「대기 0건」이 아니라 읽지 못했다고 말한다',
    why: 'api() 는 JSON 이 아니면 문자열을 돌려준다 — 그대로 읽으면 items 가 없어 빈 목록으로 그려진다',
    async run({ server, check }) {
      server.faults.push({ path: STAGING_URL, status: 200, raw: '<html><body>gateway page</body></html>' });
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      check.includes(page.text('stg-queue'), '응답을 읽지 못했습니다', '읽지 못했다고 말한다');
      check.excludes(page.text('stg-queue'), '확정 대기 중인 문서가 없습니다', '「없습니다」라고 하지 않는다');
      check.ok(!/확정 대기 0건/.test(page.text('stg-info')), '건수 0 을 표시하지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.list.only-staging-status-is-shown',
    needsMock: true,
    title: '응답에 확정 대기가 아닌 항목이 섞여 오면(서버가 include_staging 을 무시) 그것은 그리지 않는다',
    why: '검수 대기 문서가 「자동 확정」 딱지와 확정 버튼을 달고 나오면 확신 없는 문서를 자동 확정으로 오인해 확정하게 된다',
    async run({ server, check }) {
      const mixed = [ITEM(1), ITEM(2, { status: 'needs_review', doc_id: 'REVIEW-DOC-9' })];
      const page = await withStaging(server, mixed);

      check.eq(rows(page).length, 1, '확정 대기 1건만 그려졌다');
      check.excludes(page.html('stg-queue'), 'REVIEW-DOC-9', '검수 대기 문서는 확정 대기 목록에 없다');
      check.includes(page.text('stg-info'), '확정 대기 1건 (표시 1)', '건수는 그려진 만큼만 말한다(서버 total 은 믿지 않는다)');
      check.includes(page.text('stg-info'), '확정 대기가 아닌 1건은 제외', '제외했다는 사실을 말한다');
      return page;
    },
  },

  {
    id: 'staging.evidence.lazy-and-cached',
    needsMock: true,
    title: '「왜 이 등급인가?」는 눌렀을 때 그 행의 근거를 한 번만 조회해 보여준다',
    why: '확정 대기 문서를 확정하기 전에 AI 가 왜 그 등급을 골랐는지 봐야 한다 — 기존 근거 조회를 그대로 쓴다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1), ITEM(2)]);
      check.eq(server.countCalls('GET', '/review-queue/'), 0, '목록만으로는 근거를 미리 안 가져온다');

      const key = keyOf(rows(page)[1]);
      page.click(`stg-why-btn-${key}`);
      await page.settle();
      const call = server.lastCall('GET', '/review-queue/');
      check.eq(call?.path, '/review-queue/aaaaaaaa-0000-4000-8000-000000000002/evidence', '눌린 행의 분류 id 로 조회한다');
      check.includes(page.html(`stg-why-${key}`), '판정', '근거 상자에 판정 줄이 있다');
      check.eq(page.text(`stg-why-btn-${key}`), '근거 접기', '버튼 문구가 접기로 바뀐다');

      page.click(`stg-why-btn-${key}`);
      page.click(`stg-why-btn-${key}`);
      await page.settle();
      check.eq(server.countCalls('GET', '/review-queue/'), 1, '이미 본 근거는 다시 요청하지 않는다');

      // 다른 행을 확정해도 열어 둔 근거가 접히지 않는다(나머지 행을 다시 그리지 않는다)
      check.ok(page.visible(`stg-why-${key}`), '근거 상자가 열려 있다');
      page.click(confirmBtn(page, 0));
      await page.settle();
      check.eq(rows(page).length, 1, '첫 행이 빠졌다');
      check.ok(page.visible(`stg-why-${key}`), '다른 행을 처리해도 열어 둔 근거가 접히지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.same-classification-in-review-list-is-settled',
    writes: true,
    needsMock: true,
    title: '방금 분류한 자동 확정 건이 두 목록에 함께 있을 때, 확정 대기에서 확정하면 검수 큐 쪽 항목도 완료로 바뀐다',
    why: '분류 직후 그 건은 「문서 검토 대기」에 세션 항목으로 남고 같은 건이 확정 대기에도 온다. 한쪽만 처리되면 이미 확정한 건에 확정 버튼이 남는다',
    async run({ server, check }) {
      // 본보기 POST /classify 는 inference_id 44444444-… · status=staging 을 돌려준다.
      const page = await withStaging(server, [ITEM(1, { classification_id: '44444444-4444-4444-8444-444444444444', doc_id: 'E2E-DOC-010' })]);
      page.set('cl-docid', 'E2E-DOC-010');
      page.set('cl-body', '본 계약의 대상 기술은 영업비밀에 해당한다.');
      page.click('btn-classify');
      await page.settle();
      check.ok(page.q('#queue button[onclick="doConfirm(0)"]'), '분류 직후 검수 큐 쪽에 세션 항목과 확정 버튼이 있다');

      page.click(confirmBtn(page, 0));
      await page.settle();

      check.eq(rows(page).length, 0, '확정 대기에서 빠졌다');
      check.ok(!page.q('#queue button[onclick="doConfirm(0)"]'), '검수 큐 쪽 같은 항목의 확정 버튼이 사라졌다');
      check.includes(page.qa('#queue .q-item')[0].textContent, '완료', '그 항목이 완료로 바뀌었다');
      check.eq(server.countCalls('POST', '/confirm'), 1, '확정 요청은 한 번뿐이다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.confirm-in-review-list-removes-it-from-staging',
    writes: true,
    needsMock: true,
    title: '반대로 검수 큐 쪽에서 그 건을 확정하면 확정 대기 목록에서도 빠진다',
    why: '같은 건을 확정 대기에서 또 누르는 일을 막는다(서버는 멱등이지만 화면이 이미 끝난 건을 남겨 두면 혼란스럽다)',
    async run({ server, check }) {
      const page = await withStaging(server, [
        ITEM(1, { classification_id: '44444444-4444-4444-8444-444444444444', doc_id: 'E2E-DOC-010' }),
        ITEM(2),
      ]);
      page.set('cl-docid', 'E2E-DOC-010');
      page.set('cl-body', '본 계약의 대상 기술은 영업비밀에 해당한다.');
      page.click('btn-classify');
      await page.settle();

      page.click(page.q('#queue button[onclick="doConfirm(0)"]'));
      await page.settle();

      check.eq(rows(page).length, 1, '확정 대기에서 그 건이 빠졌다');
      check.excludes(page.html('stg-queue'), 'E2E-DOC-010', '확정한 문서가 확정 대기에 남지 않는다');
      check.includes(page.html('stg-queue'), 'STG-DOC-002', '다른 건은 그대로다');
      check.includes(page.text('stg-info'), '확정 대기 1건', '건수도 줄었다');
      return page;
    },
  },

  {
    id: 'staging.xss.no-script-execution',
    needsMock: true,
    title: '응답에 <script>·<img onerror> 가 섞여도 실행되지 않고 글자로만 보인다',
    why: '실문서 제목·본문이 그대로 화면에 들어가는 자리다',
    async run({ server, check }) {
      const page = await withStaging(server, [ITEM(1, {
        doc_id: '<img src=x onerror="window.__pwned=1">',
        filename: '<script>window.__pwned=1</script>보고서.docx',
        text_preview: '<script>window.__pwned=1</script>',
      })]);

      check.eq(page.win.__pwned, undefined, '주입된 스크립트가 실행되지 않았다');
      check.eq(page.qa('#stg-queue script').length, 0, '스크립트 요소로 파싱되지 않았다');
      check.eq(page.qa('#stg-queue img').length, 0, '이미지 요소로 파싱되지 않았다');
      check.includes(page.text('stg-queue'), '<script>', '글자 그대로 보인다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'staging.notices.review-list-points-to-staging',
    needsMock: true,
    title: '「자동 확정은 오지 않는다」는 안내가 자동 확정분을 볼 곳(확정 대기 목록)을 함께 알려준다',
    why: '종전 안내는 검수 대기 목록에 자동 확정분이 안 온다고만 적어 자동 확정분을 볼 곳이 없는 것처럼 읽혔다. '
       + '이제 확정 대기 목록이 있으므로 문구가 그 사실과 맞아야 한다',
    async run({ server, check }) {
      server.overrides['GET /review-queue'] = { items: [], total: 0, limit: 50, offset: 0, warnings: [] };
      const page = await withStaging(server, [ITEM(1)]);

      const empty = page.text('queue');
      check.includes(empty, '자동 확정된 분류는 오지 않습니다', '이 목록에 무엇이 안 오는지는 그대로 말한다');
      check.includes(empty, '「확정 대기」', '자동 확정분을 볼 곳을 알려준다');
      check.includes(empty, '사람 판정을 기다리는', '이 목록에 무엇이 쌓이는지도 말한다');

      // 분류 직후 「검토할 문서 보기」 — 세션 항목을 남기며 하는 안내
      page.set('cl-docid', 'AUTO-1');
      page.set('cl-body', '다음 주 회의 일정과 점심 메뉴를 안내합니다.');
      page.click('btn-classify');
      await page.settle();
      page.click('btn-review-queue');
      await page.settle();
      check.includes(page.text('rq-info'), '「확정 대기」', '세션 항목 안내도 확정 대기 목록을 가리킨다');

      check.includes(page.text('pane-note'), '최종 확정', '운영 탭 설명이 자동 확정분의 최종 확정을 말한다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
