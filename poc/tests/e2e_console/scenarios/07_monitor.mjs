/* 7. 운영 — 관제 대시보드 · 감사 로그 · 실시간 관제 · 합성 검수 큐.
 *
 * "현재 상태를 한 화면에서 본다"가 목적인 자리다. 조회 실패를 성공처럼 그리면 관제가
 * 통째로 거짓이 되므로, 부분 실패(degraded)가 그대로 표시되는지까지 본다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function opsTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  page.click(page.q('.tab[data-tab="ops"]'));
  return page;
}

export const scenarios = [
  {
    id: 'monitor.dashboard.renders-panels',
    title: '관제 종합을 누르면 패널들이 실제 수치로 그려진다',
    async run({ server, check }) {
      const page = await opsTab(server);
      page.click(page.q('button[onclick="loadDashboard()"]'));
      await page.settle();

      check.ok(server.lastCall('GET', '/admin/dashboard'), 'GET /admin/dashboard 를 불렀다');
      const grid = page.text('dash-grid');
      check.includes(grid, '이중검토 보류', '이중검토 패널이 있다');
      check.includes(grid, '배포 준비 상태', '배포 준비 패널이 있다');
      check.includes(page.text('dash-meta'), '생성', '언제 만든 수치인지 적는다');
      // ⚠ 문구·구분기호는 화면 손질로 자주 바뀐다(2026-08-23 에도 바뀌었다). 뜻이 유지되는지만 본다.
      check.data.matches(grid, /실\s*0\s*[·/]\s*합\s*3/, '등급별로 실문서/합성을 갈라 보여준다');
      check.matches(grid, /실문서\s*0/, '서명분이 전부 합성인 등급이 있으면 그 사실을 알린다');
      check.data.matches(grid, /부족[:\s].*TS.*S1.*S2.*S3/, '무엇이 부족한지 적는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'monitor.dashboard.degraded-visible',
    title: '일부 조회가 실패하면 그 항목이 실패했다고 표시한다',
    why: '실패한 항목을 0 으로 그리면 "문제 없음"으로 읽힌다',
    needsMock: true,
    async run({ server, check }) {
      const page = await opsTab(server);
      server.overrides['GET /admin/dashboard'] = {
        generated_at: '2026-08-23T02:30:00Z',
        escalation_held: {},
        locked_readiness: {},
        active_learning: {},
        kill_gate: {},
        drift: {},
        degraded: ['escalation_held', 'drift'],
      };
      page.click(page.q('button[onclick="loadDashboard()"]'));
      await page.settle();
      check.includes(page.text('dash-meta'), '조회 실패', '무엇을 못 읽었는지 상단에 적는다');
      check.includes(page.text('dash-meta'), 'escalation_held', '실패한 항목 이름이 보인다');
      return page;
    },
  },

  {
    id: 'monitor.dashboard.failure-visible',
    title: '대시보드 자체가 실패하면 빈 화면이 아니라 사유가 뜬다',
    needsMock: true,
    async run({ server, check }) {
      const page = await opsTab(server);
      server.faults.push({ path: '/admin/dashboard', status: 403, body: { detail: '권한 없음' } });
      page.click(page.q('button[onclick="loadDashboard()"]'));
      await page.settle();
      check.includes(page.text('dash-grid'), '조회 실패', '실패했다고 말한다');
      check.includes(page.text('dash-grid'), 'admin 역할 필요', '403 일 때 무엇이 필요한지 짚어 준다');
      return page;
    },
  },

  {
    id: 'monitor.audit.list-and-filter',
    title: '감사 로그를 조건으로 조회하면 행이 그려지고 신원 없음도 구분된다',
    why: '공유 API 키로 부르면 행위자 신원이 안 남는다 — 그 사실을 화면이 스스로 드러내야 한다',
    async run({ server, check }) {
      const page = await opsTab(server);
      page.set('au-action', 'model.activate');
      page.set('au-success', 'false');
      page.click(page.q('button[onclick="loadAuditLog()"]'));
      await page.settle();

      const call = server.lastCall('GET', '/admin/audit-log');
      check.includes(call?.path || '', 'action=model.activate', '동작 필터가 실렸다');
      check.includes(call?.path || '', 'success=false', '성공여부 필터가 실렸다');
      check.data.eq(page.qa('#au-body tr').length, 2, '두 건이 그려졌다');
      check.data.includes(page.html('au-body'), 'gate_blocked', '실패 사유코드가 보인다');
      check.data.includes(page.text('au-info'), '전체 2건', '전체 건수가 보인다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'monitor.audit.actorless-warning',
    title: '행위자 신원이 없는 기록이 섞이면 경고를 띄운다',
    needsMock: true,
    async run({ server, check }) {
      const page = await opsTab(server);
      server.overrides['GET /admin/audit-log'] = {
        items: [{ occurred_at: '2026-08-23T02:00:00Z', action: 'confirm', actor_id: null, actor_role: null, target_type: null, target_id: null, success: true, error_code: null, ip_address: null, request_id: null }],
        total: 1, limit: 50, offset: 0,
        actorless_ratio: 1.0,
        warnings: ['행위자 신원이 없는 기록이 100% 입니다 — 공유 API 키 모드에서는 감사 추적이 성립하지 않습니다'],
      };
      page.click(page.q('button[onclick="loadAuditLog()"]'));
      await page.settle();
      check.includes(page.html('au-body'), '신원 없음', '신원이 없는 행이 그렇게 표시된다');
      check.ok(page.visible('au-warn'), '경고가 보인다');
      check.includes(page.text('au-warn'), '감사 추적이 성립하지 않습니다', '경고 원문이 그대로 보인다');
      return page;
    },
  },

  {
    id: 'monitor.audit.empty-and-failure',
    title: '기록이 없을 때와 조회가 실패했을 때를 구분해 말한다',
    needsMock: true,
    async run({ server, check }) {
      const page = await opsTab(server);
      server.overrides['GET /admin/audit-log'] = { items: [], total: 0, limit: 50, offset: 0, actorless_ratio: 0, warnings: [] };
      page.click(page.q('button[onclick="loadAuditLog()"]'));
      await page.settle();
      check.includes(page.text('au-body'), '해당 기록이 없습니다', '없다고 말한다');

      delete server.overrides['GET /admin/audit-log'];
      server.faults.push({ path: '/admin/audit-log', status: 500, body: { detail: 'DB 오류' } });
      page.click(page.q('button[onclick="loadAuditLog()"]'));
      await page.settle();
      check.includes(page.text('au-body'), '조회 실패', '실패는 "없음"과 다르게 말한다');
      check.includes(page.text('au-body'), 'DB 오류', '서버 사유가 보인다');
      return page;
    },
  },

  {
    id: 'monitor.autorefresh.starts-and-stops',
    title: '실시간 관제를 켜면 주기적으로 다시 읽고, 끄면 멈춘다',
    async run({ server, check }) {
      const page = await opsTab(server);
      const sel = page.$('auto-interval');
      check.ok(sel, '주기 선택이 있다');
      check.includes(page.text('auto-status'), '정지', '처음에는 꺼져 있다');

      // 가장 짧은 주기를 골라 실제로 도는지 본다
      const shortest = Array.from(sel.options).map((o) => Number(o.value)).filter((v) => v > 0).sort((a, b) => a - b)[0];
      const before = server.calls.length;
      page.set('auto-interval', String(shortest));
      await page.settle();
      check.matches(page.text('auto-status'), /갱신|실시간|live/i, '켜졌다고 표시된다');
      check.gte(server.calls.length, before + 1, '켜자마자 한 번 읽는다');

      const afterFirst = server.calls.length;
      const ticked = await page.until(() => server.calls.length > afterFirst, Math.max(3000, shortest * 1000 + 1500));
      check.ok(ticked, `주기(${shortest}초)마다 다시 읽는다`);

      page.set('auto-interval', '0');
      await page.settle();
      const stopped = server.calls.length;
      await page.wait(Math.min(2000, shortest * 1000 + 500));
      check.eq(server.calls.length, stopped, '끄면 더 이상 읽지 않는다');
      // 끄는 순간 진행 중이던 tick 이 끝나면서 상태를 'live' 로 되돌려 쓰던 자리 —
      // 폴링은 멈췄는데 표시만 "실시간 갱신 중"으로 남으면 화면이 거짓말을 한다.
      check.includes(page.text('auto-status'), '정지', '꺼졌다고 표시된다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'monitor.synth.queue-and-review',
    writes: true,
    title: '합성 검수 큐를 읽어 승인/반려하면 목록에서 빠진다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      page.click(page.q('.tab[data-tab="train"]'));
      page.click(page.q('button[onclick="loadSynthQueue()"]'));
      await page.settle();

      check.eq(page.qa('#sy-queue .q-item').length, 2, '2건이 그려졌다');
      check.includes(page.html('sy-queue'), '품질 0.81', '품질 점수가 보인다');
      check.includes(page.text('sy-queue'), 'EUV 공정 레시피', '본문 미리보기가 보인다');

      page.set('sy-g-0', 'S1');   // 목표 TS 와 다른 등급으로 승인
      page.click(page.q('#sy-queue button[onclick="synthReview(0,\'approve\')"]'));
      await page.settle();

      const call = server.lastCall('POST', '/synth/');
      check.includes(call?.path || '', '/review', '검수 결과를 보냈다');
      check.eq(call?.body?.decision, 'approve', '승인으로 나갔다');
      check.eq(call?.body?.corrected_grade, 'S1', '바꾼 등급이 교정값으로 실렸다');
      check.eq(page.qa('#sy-queue .q-item').length, 1, '처리한 건이 목록에서 빠졌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'monitor.synth.generate',
    writes: true,
    title: '합성 생성 요청은 예상 건수·비용을 화면에 돌려준다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      page.click(page.q('.tab[data-tab="train"]'));
      page.set('sy-grade', 'TS');
      page.set('sy-count', '20');
      page.click('sy-gen');
      await page.settle();

      const call = server.lastCall('POST', '/synth/generate');
      check.eq(call?.body?.target_grade, 'TS', '목표 등급이 실렸다');
      check.eq(call?.body?.count, 20, '건수가 실렸다');
      check.includes(page.text('sy-info'), '예상 20건', '예상 건수가 보인다');
      check.includes(page.text('sy-info'), '$', '예상 비용이 보인다');
      check.eq(page.$('sy-gen')?.disabled, false, '버튼이 잠긴 채 남지 않는다');
      return page;
    },
  },
];
