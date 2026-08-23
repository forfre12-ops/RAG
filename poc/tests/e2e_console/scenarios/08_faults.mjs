/* 8. 고장 — 서버가 정상이 아닐 때 화면이 어떻게 되는가.
 *
 * 인증 실패 · 권한 부족 · 서버 오류 · 연결 끊김 · JSON 자리에 HTML · 느린 응답 · 빈 응답.
 * 이 묶음의 기준은 하나다: **사용자가 무슨 일이 일어났는지 알 수 있는가.**
 * 조용히 아무 일도 안 일어난 것처럼 보이는 것이 가장 나쁜 결과다.
 */

import { openPage } from '../lib/page.mjs';

const CLICKS = [
  ['검수 큐', 'button[onclick="loadReviewQueue()"]', 'ops'],
  ['관제 종합', 'button[onclick="loadDashboard()"]', 'ops'],
  ['감사 로그', 'button[onclick="loadAuditLog()"]', 'ops'],
  ['검증문서 현황', 'button[onclick="loadGoldenStatus()"]', 'review'],
  ['작업 목록', 'button[onclick="loadJobs()"]', 'train'],
  ['최신 메트릭', 'button[onclick="loadMetrics()"]', 'train'],
  ['등급체계 조회', 'button[onclick="loadGradeEditor()"]', 'config'],
  ['키워드 조회', 'button[onclick="loadKeywords()"]', 'config'],
];

async function pressAll(page) {
  for (const [, sel, pane] of CLICKS) {
    page.click(page.q(`.tab[data-tab="${pane}"]`));
    const btn = page.q(sel);
    if (btn) btn.click();
    await page.settle();
  }
}

export const scenarios = [
  {
    id: 'fault.auth.401-everywhere-visible',
    title: '키가 틀리면(401) 어느 카드를 눌러도 실패 사실이 화면에 남는다',
    why: '실배포 서버는 키가 달라 열자마자 전부 401 이 된다 — 이때 화면이 조용하면 원인을 못 찾는다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/', status: 401, body: { detail: 'invalid api key' } });

      await pressAll(page);

      const errs = page.logLines('err');
      check.gte(errs.length, CLICKS.length, `누른 만큼 실패가 로그에 남는다 (${errs.length}건)`);
      check.includes(errs.join(' '), '401', '상태코드가 로그에 보인다');

      // 로그창 밖(각 카드 자리)에도 남는가 — 접힌 로그만으로는 사용자가 못 본다
      const inCard = {
        '검수 큐': page.text('rq-info') + page.text('queue'),
        '관제 종합': page.text('dash-grid'),
        '감사 로그': page.text('au-body'),
        '검증문서 현황': page.text('gs-body'),
        '작업 목록': page.text('jobs-body'),
        '최신 메트릭': page.text('metrics-extra'),
        '등급체계 조회': page.text('grade-info'),
        '키워드 조회': page.text('kw-info'),
      };
      for (const [name, txt] of Object.entries(inCard)) {
        check.matches(txt, /실패|오류|401/, `${name}: 카드 자리에도 실패가 보인다`, txt.slice(0, 160));
      }
      return page;
    },
  },

  {
    id: 'fault.auth.403-tells-what-role',
    title: '권한이 없으면(403) 어떤 역할이 필요한지 알려준다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      page.set('cfg-role', 'reviewer');   // 관리자 콘솔의 역할 목록은 admin·reviewer·kl_backend·system
      server.faults.push({ path: '/admin/', status: 403, body: { detail: 'admin role required' } });

      page.click(page.q('.tab[data-tab="ops"]'));
      page.click(page.q('button[onclick="loadDashboard()"]'));
      await page.settle();
      check.includes(page.text('dash-grid'), 'admin 역할 필요', '무슨 역할이 필요한지 말한다');

      page.click(page.q('button[onclick="loadAuditLog()"]'));
      await page.settle();
      check.includes(page.text('au-body'), 'admin 역할 필요', '감사 로그에서도 같은 안내를 준다');
      check.eq(server.lastCall('GET', '/admin/')?.headers['x-actor-role'], 'reviewer', '고른 역할이 실제로 헤더에 실린다');
      return page;
    },
  },

  {
    id: 'fault.server.500-shows-detail',
    title: '서버 오류(500)의 사유가 그대로 보인다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/admin/dashboard', status: 500, body: { detail: 'psycopg.OperationalError: connection refused' } });
      page.click(page.q('.tab[data-tab="ops"]'));
      page.click(page.q('button[onclick="loadDashboard()"]'));
      await page.settle();
      check.includes(page.text('dash-grid'), 'connection refused', '서버가 준 원문이 보인다');
      check.includes(page.text('dash-grid'), '500', '상태코드가 보인다');
      return page;
    },
  },

  {
    id: 'fault.network.disconnect',
    title: '연결이 끊기면 네트워크 오류라고 말하고 화면이 죽지 않는다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/classify', abort: true });

      page.set('cl-body', '본문');
      page.click('btn-classify');
      await page.settle();

      check.includes(page.logLines('err').join(' '), '네트워크 오류', '네트워크 오류라고 말한다');
      check.ok(page.text('cl-result').length > 0, '결과 자리에도 실패가 남는다', page.text('cl-result'));
      check.eq(page.$('btn-classify')?.disabled, false, '버튼이 잠긴 채 남지 않는다');

      // 끊긴 뒤에도 다음 조작이 된다
      server.faults.length = 0;
      page.click('btn-classify');
      await page.settle();
      check.includes(page.html('cl-result'), 'S1', '연결이 돌아오면 다시 동작한다');
      return page;
    },
  },

  {
    id: 'fault.html-instead-of-json',
    title: 'JSON 자리에 HTML 오류 페이지가 와도 화면이 무너지지 않는다',
    why: '역프록시가 502 를 HTML 로 돌려주는 흔한 상황이다 — JSON.parse 가 던지면 그 뒤가 다 멈춘다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/review-queue', status: 502, raw: '<html><head><title>502 Bad Gateway</title></head><body><h1>502</h1></body></html>' });

      page.click(page.q('.tab[data-tab="ops"]'));
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();

      check.eq(page.errors.length, 0, '스크립트가 죽지 않았다', page.errors.map((e) => e.message).join(' | '));
      check.includes(page.logLines('err').join(' '), '502', '502 라고 로그에 남는다');
      // 화면이 계속 쓸 수 있는 상태여야 한다
      server.faults.length = 0;
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();
      check.eq(page.qa('#queue .q-item').length, 3, '그 다음 조회는 정상으로 그려진다');
      return page;
    },
  },

  {
    id: 'fault.slow.button-locked-while-running',
    title: '응답이 느린 동안 같은 버튼이 두 번 눌리지 않는다',
    why: '중복 제출은 학습·활성화 같은 비가역 동작에서 실제 사고가 된다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/classify', delayMs: 600 });

      page.set('cl-body', '본문');
      page.click('btn-classify');
      check.eq(page.$('btn-classify')?.disabled, true, '요청 중에는 버튼이 잠긴다');
      check.includes(page.text('btn-classify'), '분류 중', '진행 중이라고 문구가 바뀐다');

      let second = null;
      try { page.click('btn-classify'); } catch (e) { second = e.message; }
      check.ok(second && second.includes('비활성'), '두 번째 클릭이 막힌다');

      await page.settle(3000);
      check.eq(server.countCalls('POST', '/classify'), 1, '요청은 한 번만 나갔다');
      check.eq(page.$('btn-classify')?.disabled, false, '끝나면 다시 눌린다');
      return page;
    },
  },

  {
    id: 'fault.autorefresh.stop-during-inflight',
    title: '느린 tick 도중에 자동 갱신을 끄면 표시도 「정지」로 남는다',
    why: '폴링은 멈췄는데 표시만 "실시간 갱신 중"이면 화면이 거짓을 말한다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/admin/dashboard', delayMs: 700 });

      page.set('auto-interval', '3');       // 즉시 1회 tick 시작(느린 응답)
      await page.wait(120);                  // tick 진행 중
      page.set('auto-interval', '0');        // 진행 중에 끈다
      check.includes(page.text('auto-status'), '정지', '끈 직후에는 정지로 표시된다');

      await page.settle(4000);               // 진행 중이던 tick 이 끝난 뒤
      check.includes(page.text('auto-status'), '정지', '늦게 끝난 tick 이 표시를 되돌리지 않는다');
      return page;
    },
  },

  {
    id: 'fault.healthz.down-keeps-console-usable',
    title: 'healthz 가 실패해도 나머지 화면은 그대로 쓸 수 있다',
    why: '초기화 한 곳이 죽어 화면 절반이 비는 사고가 실제로 있었다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      // 정상으로 뜬 뒤 healthz 를 죽이고 다시 눌러 본다
      server.faults.push({ path: '/healthz', status: 503, body: { detail: 'model not loaded' } });
      page.click(page.q('button[onclick="checkHealth()"]'));
      await page.settle();

      check.includes(page.text('health-txt'), '실패', '헬스 표시가 실패로 바뀐다');
      check.eq(page.$('health')?.className, 'health-pill bad', '빨간 상태로 바뀐다');

      // 헬스가 죽어도 다른 조회는 된다
      page.click(page.q('.tab[data-tab="ops"]'));
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();
      check.eq(page.qa('#queue .q-item').length, 3, '검수 큐는 정상으로 그려진다');
      check.eq(page.errors.length, 0, '스크립트 오류가 나지 않는다', page.errors.map((e) => e.message).join(' | '));
      return page;
    },
  },

  {
    id: 'fault.storage.blocked',
    title: '브라우저 저장소가 막혀 있어도 화면은 뜬다',
    why: '사생활 보호 모드·기업 정책에서 localStorage 접근이 예외를 던진다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html', {
        beforeBoot(win) {
          const boom = () => { throw new Error('SecurityError: storage disabled'); };
          Object.defineProperty(win, 'localStorage', { get: boom, configurable: true });
          Object.defineProperty(win, 'sessionStorage', { get: boom, configurable: true });
        },
      });
      await page.settle();
      check.eq(page.errors.length, 0, '저장소가 막혀도 스크립트가 죽지 않는다', page.errors.map((e) => e.message).join(' | '));
      check.ok(page.q('.tab.active'), '탭이 정상으로 표시된다');
      check.gte(server.countCalls('GET', '/healthz'), 1, '초기화가 끝까지 돌았다');
      return page;
    },
  },
];
