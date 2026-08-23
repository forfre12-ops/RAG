/* 3. 운영 — 검수 큐에서 확정·재라벨한다.
 *
 * 현장 관리자가 매일 하는 일이다. 목록 → 근거 확인 → 확정/재라벨, 그리고 "확정했다는데
 * 사실은 저장이 안 된 경우"(persisted=false)와 "2차 검수가 필요한 경우"를 구분해 보여
 * 주는지까지 본다. 조용히 성공처럼 보이면 감사에서 그대로 문제가 된다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function withQueue(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  page.click(page.q('button[onclick="loadReviewQueue()"]'));
  await page.settle();
  return page;
}

export const scenarios = [
  {
    id: 'review.queue.list-and-why',
    needsData: true,
    title: '「검토할 문서 보기」 → 목록이 그려지고 「왜 이 등급인가」로 근거가 열린다',
    async run({ server, check }) {
      const page = await withQueue(server);

      check.ok(server.lastCall('GET', '/review-queue'), 'GET /review-queue 를 불렀다');
      check.includes(page.text('rq-info'), '대기 3건', '대기 건수가 표시된다');
      check.eq(page.qa('#queue .q-item').length, 3, '큐 항목 3건이 그려졌다');
      check.includes(page.html('queue'), 'E2E-DOC-001', '문서 id 가 보인다');
      check.includes(page.html('queue'), '차세대 공정 레시피.docx', '파일명이 보인다');
      check.includes(page.text('queue'), '당사 양자컴퓨팅연구소', '본문 미리보기가 보인다');

      // 근거 열기 — 지연 조회(lazy)라 여기서 처음 요청이 나가야 한다
      check.eq(server.countCalls('GET', '/review-queue/'), 0, '목록만으로는 근거를 미리 안 가져온다');
      page.click('why-btn-0');
      await page.settle();
      check.ok(server.lastCall('GET', '/review-queue/'), '근거 조회 요청이 나갔다');

      const why = page.html('why-0');
      check.includes(why, '판정', '근거 상자에 판정 줄이 있다');
      check.includes(why, '대외 반출을 금한다', '근거 인용문이 보인다');
      check.includes(why, 'secrecy', '근거 유형이 보인다');
      check.includes(why, '차순위', '차순위 등급이 보인다');
      check.includes(why, '자동확정 관측', '자동확정 관측치가 보인다');
      check.eq(page.text('why-btn-0'), '근거 접기', '버튼 문구가 접기로 바뀌었다');

      // 두 번 눌러도 다시 요청하지 않는다(캐시)
      page.click('why-btn-0');
      page.click('why-btn-0');
      await page.settle();
      check.eq(server.countCalls('GET', '/review-queue/'), 1, '이미 본 근거는 다시 요청하지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'review.confirm.success',
    writes: true,
    title: '확정을 누르면 서버로 나가고 그 항목이 완료로 바뀐다',
    async run({ server, check }) {
      const page = await withQueue(server);
      page.click(page.q('#queue .q-item button[onclick="doConfirm(0)"]'));
      await page.settle();

      const call = server.lastCall('POST', '/confirm');
      check.ok(call, 'POST /confirm 이 나갔다');
      check.eq(call?.body?.doc_id, 'E2E-DOC-001', '어느 문서인지 실렸다');
      check.eq(call?.body?.confirmed_label, 'TS', '확정 등급이 실렸다');
      check.eq(call?.body?.inference_id, '11111111-1111-4111-8111-111111111111', '어느 분류인지 실렸다');
      check.ok(call?.body?.actor?.user_id, '행위자가 실렸다 — 감사 추적의 전제');
      check.includes(page.html('queue'), '완료', '그 항목이 완료 표시로 바뀌었다');
      check.eq(page.dialogs.length, 0, '성공 경로에서 경고창이 뜨지 않는다');
      return page;
    },
  },

  {
    id: 'review.confirm.not-persisted',
    writes: true,
    title: 'persisted=false 면 완료로 바꾸지 않고 미영속이라고 알린다',
    why: 'DB 가 없을 때 조용히 성공처럼 보이면 검수 결과가 사라진 줄 모른다',
    needsMock: true,
    async run({ server, check }) {
      const page = await withQueue(server);
      server.overrides['POST /confirm'] = {
        confirmation_id: '77777777-7777-4777-8777-777777777777',
        confirmed_at: '2026-08-23T02:00:00Z',
        persisted: false,
        warnings: ['DB 미가용 — 감사 로그만 남았습니다'],
        second_review_required: false,
      };
      page.click(page.q('#queue button[onclick="doConfirm(0)"]'));
      await page.settle();

      check.excludes(page.html('queue'), '완료', '완료로 바꾸지 않는다');
      const errs = page.logLines('err').join(' ');
      check.includes(errs, '미영속', '미영속이라고 로그에 남는다');
      check.includes(errs, 'DB 미가용', '서버가 준 사유가 그대로 보인다');
      return page;
    },
  },

  {
    id: 'review.confirm.second-review',
    writes: true,
    title: '고등급 이중검토 대상이면 완료가 아니라 「2차 검수 필요」로 남는다',
    needsMock: true,
    async run({ server, check }) {
      const page = await withQueue(server);
      server.overrides['POST /confirm'] = {
        confirmation_id: '77777777-7777-4777-8777-777777777777',
        confirmed_at: '2026-08-23T02:00:00Z',
        persisted: true,
        warnings: [],
        second_review_required: true,
      };
      page.click(page.q('#queue button[onclick="doConfirm(0)"]'));
      await page.settle();
      check.includes(page.html('queue'), '2차 검수 필요', '2차 검수 필요 배지가 붙었다');
      check.ok(!page.q('#queue button[onclick="doConfirm(0)"]'), '그 항목의 확정 버튼은 사라진다');
      return page;
    },
  },

  {
    id: 'review.relabel.changes-grade',
    writes: true,
    title: '등급을 바꿔 재라벨하면 바뀐 등급으로 전송되고 목록에 반영된다',
    async run({ server, check }) {
      const page = await withQueue(server);
      page.set('rl-0', 'S1');
      page.click(page.q('#queue button[onclick="doRelabel(0)"]'));
      await page.settle();

      const call = server.lastCall('POST', '/relabel');
      check.ok(call, 'POST /relabel 이 나갔다');
      check.eq(call?.body?.original_label, 'TS', '원 등급이 실렸다');
      check.eq(call?.body?.corrected_label, 'S1', '교정 등급이 실렸다');
      check.includes(page.html('queue'), 'g-S1', '목록의 등급이 바뀌었다');
      check.includes(page.logLines('ok').join(' '), '재라벨 완료', '완료 로그가 남았다');
      check.includes(page.logLines().join(' '), '12/50', '재학습 임계 진행도가 보인다');
      return page;
    },
  },

  {
    id: 'review.relabel.same-grade-confirms',
    needsData: true,
    title: '같은 등급으로 재라벨하려 하면 한 번 되묻는다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html', { confirmAnswer: false });
      await page.settle();
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();
      page.click(page.q('#queue button[onclick="doRelabel(0)"]'));
      await page.settle();
      check.ok(page.dialogs.some((d) => d.kind === 'confirm' && d.message.includes('동일')), '동일 등급이라고 되묻는다');
      check.eq(server.countCalls('POST', '/relabel'), 0, '취소하면 보내지 않는다');
      return page;
    },
  },

  {
    id: 'review.queue.empty-state',
    title: '검수 대기가 0건이면 그렇게 말한다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.overrides['GET /review-queue'] = { items: [], total: 0, limit: 50, offset: 0, warnings: [] };
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();
      check.includes(page.text('rq-info'), '대기 0건', '0건이라고 알려준다');
      check.includes(page.text('queue'), '검수 큐에 적재됩니다', '빈 목록 안내가 뜬다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'review.queue.load-failure-visible',
    title: '검수 큐 조회가 실패하면 사용자가 그 사실을 알 수 있다',
    why: '실패가 접힌 로그창에만 남으면 "대기 0건"과 구분되지 않는다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/review-queue', status: 503, body: { detail: 'DB 연결 실패' } });
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();

      const shown = page.text('rq-info') + ' ' + page.text('queue');
      check.matches(shown, /실패|오류|503/, '큐 영역에 실패 사실이 보인다', shown);
      check.includes(page.logLines('err').join(' '), '검수 큐 로드 실패', '로그에도 남는다');
      return page;
    },
  },

  {
    id: 'review.queue.transient-failure-keeps-list',
    title: '한 번 실패했다고 이미 그려 둔 목록을 지우지는 않는다',
    why: '실시간 관제가 주기로 다시 읽는다 — tick 한 번 실패에 화면이 비면 정보가 오히려 준다',
    needsMock: true,
    async run({ server, check }) {
      const page = await withQueue(server);
      check.eq(page.qa('#queue .q-item').length, 3, '먼저 목록이 그려져 있다');

      server.faults.push({ path: '/review-queue', status: 503, body: { detail: '일시적 오류' } });
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();

      check.eq(page.qa('#queue .q-item').length, 3, '있던 목록은 그대로 남는다');
      check.includes(page.text('rq-info'), '조회 실패', '실패는 상단에 알린다');
      check.includes(page.text('rq-info'), '503', '상태코드가 보인다');
      return page;
    },
  },

  {
    id: 'review.xss.no-script-execution',
    title: '응답에 <script> 가 섞여도 실행되지 않고 글자로만 보인다',
    why: '실문서 제목·본문이 그대로 innerHTML 에 들어가는 자리다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.overrides['GET /review-queue'] = {
        items: [{
          classification_id: '11111111-1111-4111-8111-111111111111',
          doc_id: '<img src=x onerror="window.__pwned=1">',
          filename: '<script>window.__pwned=1</script>보고서.docx',
          grade: 'TS', confidence: 0.4, model_version: 'v-1', status: 'needs_review',
          classified_at: null, text_preview: '<script>window.__pwned=1</script>', review_reason: null, score_margin: null,
        }],
        total: 1, limit: 50, offset: 0, warnings: [],
      };
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();

      check.eq(page.win.__pwned, undefined, '주입된 스크립트가 실행되지 않았다');
      check.eq(page.qa('#queue script').length, 0, '스크립트 요소로 파싱되지 않았다');
      check.eq(page.qa('#queue img').length, 0, '이미지 요소로 파싱되지 않았다');
      check.includes(page.text('queue'), '<script>', '글자 그대로 보인다');
      return page;
    },
  },
];
