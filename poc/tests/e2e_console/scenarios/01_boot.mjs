/* 1. 화면이 뜨는가 — 초기화가 끝까지 도는가.
 *
 * 이 묶음이 잡는 실제 사고(2026-08-23): 화면에서 지운 요소를 초기화 코드가 계속 만지다가
 * TypeError 를 냈고, 그 줄 뒤의 checkHealth() 가 아예 실행되지 않았다. 서버 임계는 '—' 로
 * 남고 프로파일 배너도 안 떴다. 문자열 검사·node --check 는 둘 다 통과했다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

export const scenarios = [
  {
    id: 'boot.admin.init-completes',
    title: '관리자 콘솔이 오류 없이 뜨고 초기화 7단계가 전부 실행된다',
    why: '초기화는 한 줄이라도 던지면 그 뒤가 통째로 안 돈다 — 화면은 멀쩡해 보인다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      assertNoScriptErrors(check, page);

      // ① initPane — 기본 탭은 운영
      check.eq(page.q('.tab.active')?.dataset.tab, 'ops', 'initPane: 기본 탭이 운영');
      check.ok(page.text('pane-note').length > 10, 'initPane: 탭 설명이 채워졌다', page.text('pane-note'));

      // ② loadGoldenBuilds — 서버 목록으로 셀렉트가 채워졌다(자유 입력 아님)
      check.gte(page.$('gold-build-path')?.options.length, 3, 'loadGoldenBuilds: 묶음 목록이 채워졌다');

      // ③ onTrainPresetChange — preset 설명
      check.ok(page.$('tr-preset'), 'onTrainPresetChange: 학습 preset 셀렉트가 있다');

      // ④ syncWriteGate
      check.ok(page.$('write-gate-msg') && page.text('write-gate-msg').length > 0, 'syncWriteGate: 안내문이 채워졌다');

      // ⑤ syncActiveGrades — 등급 셀렉트 3곳
      for (const id of ['sy-grade', 'kw-f-grade', 'kw-n-grade']) {
        if (page.$(id)) check.gte(page.$(id).options.length, 4, `syncActiveGrades: ${id} 가 등급으로 채워졌다`);
      }

      // ⑥ checkHealth — **초기화 마지막 줄에 가까운 것**. 여기까지 왔으면 앞이 안 죽은 것이다.
      check.gte(server.countCalls('GET', '/healthz'), 1, 'checkHealth: /healthz 를 실제로 불렀다');
      check.includes(page.text('health-txt'), '정상', 'checkHealth: 헬스 표시가 정상으로 바뀌었다');
      check.eq(page.$('health')?.className, 'health-pill ok', 'checkHealth: 헬스 pill 이 ok');

      // ⑦ applyServerConfig — **끝까지 돌았다**(2026-08-23 회귀 지점: 앞 함수 하나가 던지면
      // 뒤 초기화가 통째로 멈춘다). 종전에는 검수 임계 표시로 이걸 확인했는데, 2026-08-24 에
      // 그 칸을 화면에서 뺐다. 마지막 호출인 applySynthAvailability 의 결과로 바꾼다 —
      // 잠그는 대상은 숫자가 아니라 "뒤 코드가 살아 있다" 는 사실이다.
      check.includes(page.text('sy-provider-note'), '서버 설정',
        'applyServerConfig: 마지막 초기화(applySynthAvailability)까지 실행됐다');

      check.includes(page.bodyText(), '거버넌스 콘솔 준비 완료', '초기화 완료 로그가 남았다');
      return page;
    },
  },

  {
    id: 'boot.admin.external-scripts',
    title: '외부 스크립트 2개(배포 배지·검수 목록 패널)가 실제로 붙는다',
    why: '<script src> 가 404 여도 화면은 그냥 뜬다 — 기능만 조용히 사라진다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      // golden_jobs.js 는 카드를 런타임에 삽입한다
      check.ok(page.$('gold-jobs-card'), 'golden_jobs.js: 검수 목록 카드가 삽입됐다');
      check.ok(typeof page.win.loadGoldenJobList === 'function', 'golden_jobs.js: loadGoldenJobList 전역이 생겼다');

      // deploy_badge.js 는 healthz 를 직접 부른다
      check.gte(server.countCalls('GET', '/healthz'), 1, 'deploy_badge.js: 빌드 배지가 healthz 를 읽었다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'boot.admin.saved-tab-restored',
    title: '세션에 저장된 탭으로 복원되고, 없어진 탭 이름이면 기본 탭으로 떨어진다',
    why: '탭 이름을 개편했을 때 옛 이름이 sessionStorage 에 남아 화면이 빈 채로 열린 적이 있다',
    async run({ server, check }) {
      const p1 = await openPage(server, '/console/admin.html', { session: { koipa_admin_pane: 'config' } });
      await p1.settle();
      check.eq(p1.q('.tab.active')?.dataset.tab, 'config', '저장된 탭(설정)으로 복원된다');
      p1.close();

      // 2026-08-20 개편으로 없어진 이름
      const p2 = await openPage(server, '/console/admin.html', { session: { koipa_admin_pane: 'model' } });
      await p2.settle();
      check.eq(p2.q('.tab.active')?.dataset.tab, 'review', '없어진 탭 이름이면 검수 탭으로 떨어진다');
      check.ok(p2.qa('[data-pane]').some((s) => p2.visible(s)), '어느 탭이든 보이는 카드가 있다');
      return p2;
    },
  },

  {
    id: 'boot.admin.hash-opens-tab',
    title: '#앵커로 열면 그 카드가 든 탭까지 열린다',
    why: '대상 카드가 display:none 인 탭에 있으면 링크를 눌러도 아무 일도 안 일어난 것처럼 보인다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html', {
        hash: '#gold-jobs-card',
        session: { koipa_admin_pane: 'config' },
      });
      await page.settle();
      const ok = await page.until(() => page.$('gold-jobs-card') && page.visible('gold-jobs-card'), 3000);
      check.ok(ok, '검수 목록 카드가 실제로 보인다', `탭=${page.q('.tab.active')?.dataset.tab}`);
      check.eq(page.q('.tab.active')?.dataset.tab, 'review', '검수 탭으로 전환됐다');
      return page;
    },
  },

  {
    id: 'boot.admin.api-key-from-query',
    title: '?key= 로 열면 키가 입력란에 들어가고 저장되며 주소에서 지워진다',
    why: '실배포 서버는 키가 달라 기본값 그대로면 모든 버튼이 401 로 죽는다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html', { query: '?key=e2e-real-key' });
      await page.settle();
      check.eq(page.$('cfg-key')?.value, 'e2e-real-key', '키가 입력란에 들어갔다');
      check.eq(page.win.localStorage.getItem('koipa_api_key'), 'e2e-real-key', '다음 방문을 위해 저장됐다');
      check.excludes(page.win.location.search, 'key=', '주소창에서 키가 지워졌다');
      check.ok(
        server.anyCall('GET', '/healthz', (c) => c.headers['x-api-key'] === 'e2e-real-key'),
        '그 키로 실제 요청이 나갔다',
        JSON.stringify(server.calls.map((c) => [c.path, c.headers['x-api-key']])),
      );
      return page;
    },
  },

  {
    id: 'boot.admin.saved-key-reused',
    title: '저장된 키가 있으면 주소 없이도 그 키로 요청한다',
    why: '한 번 넣은 키를 매번 다시 넣게 하면 현장에서 안 쓴다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html', { storage: { koipa_api_key: 'saved-key-1' } });
      await page.settle();
      check.eq(page.$('cfg-key')?.value, 'saved-key-1', '저장된 키가 입력란에 들어갔다');
      check.ok(
        server.anyCall('GET', '/healthz', (c) => c.headers['x-api-key'] === 'saved-key-1'),
        '그 키로 요청했다',
      );
      return page;
    },
  },

  {
    id: 'boot.demo.index',
    title: '등급 시연 화면이 오류 없이 뜬다',
    why: 'app.js 는 ES 모듈이라 하니스가 묶어서 실행한다 — 모듈 안 함수가 인라인 핸들러에서 안 보이는 부류는 별도 시험이 본다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/index.html', { bundleModules: true });
      await page.settle();
      assertNoScriptErrors(check, page);
      check.ok(page.$('sample-select')?.options.length > 0, '예시 문서 목록이 채워졌다');
      check.ok(page.$('legal-grid')?.innerHTML.length > 50, '법령 근거 카드가 그려졌다');
      check.ok(page.$('toggle-row')?.innerHTML.length > 0, '옵션 토글이 그려졌다');
      return page;
    },
  },
];
