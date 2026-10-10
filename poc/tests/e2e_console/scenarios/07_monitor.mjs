/* 7. 운영 — 관제 대시보드 · 감사 로그 · 실시간 관제 · 합성 검수 큐.
 *
 * "현재 상태를 한 화면에서 본다"가 목적인 자리다. 조회 실패를 성공처럼 그리면 관제가
 * 통째로 거짓이 되므로, 부분 실패(degraded)가 그대로 표시되는지까지 본다.
 */

import { openPage, goToPane } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function opsTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  await goToPane(page, 'ops');
  return page;
}

// [2026-10-10] 감사 로그 카드(admin.html:1006)는 옛 "운영" 탭 하나에 같이 있었지만, Product
// Shell V2에서 "운영 개요"(overview)와 "감사·보고서"(audit)로 갈라졌다 — data-pane="ops" 는
// 그대로지만 data-product-view="audit" 다. opsTab()의 일반 pane→view 매핑(ops→overview)으로는
// 안 보인다 — 이 카드를 쓰는 시나리오는 전용 내비게이션으로 바로 간다.
async function auditTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  await goToPane(page, 'audit');
  return page;
}

async function synthesisTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  await goToPane(page, 'synthesis');
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
      check.includes(grid, '재학습 대기', '재학습 반영 대기 패널이 있다');
      check.ok(!/이중검토 보류|평가 정답지 준비도|안전 경보/.test(grid), '요구사항 밖 운영 패널을 그리지 않는다');
      check.includes(page.text('dash-meta'), '생성', '언제 만든 수치인지 적는다');
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
        degraded: ['active_learning', 'drift'],
      };
      page.click(page.q('button[onclick="loadDashboard()"]'));
      await page.settle();
      check.includes(page.text('dash-meta'), '조회 불가', '무엇을 못 읽었는지 상단에 적는다');
      check.includes(page.text('dash-meta'), '재학습 대기', '실패한 항목을 운영 용어로 보여준다');
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
      check.matches(page.text('dash-grid'), /admin 역할.*필요/, '403 일 때 무엇이 필요한지 짚어 준다');
      check.includes(page.text('dash-grid'), '403', '어떤 실패인지도 남긴다');
      return page;
    },
  },

  {
    id: 'monitor.audit.list-and-filter',
    title: '감사 로그를 조건으로 조회하면 행이 그려지고 신원 없음도 구분된다',
    why: '공유 API 키로 부르면 행위자 신원이 안 남는다 — 그 사실을 화면이 스스로 드러내야 한다',
    async run({ server, check }) {
      const page = await auditTab(server);
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
      const page = await auditTab(server);
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
    id: 'monitor.audit.actor-time-and-page-filter',
    title: '감사 로그를 행위자·기간으로 좁히고 다음 쪽으로 이동한다',
    why: '운영 사고를 특정 담당자·시간대에서 추적할 수 있어야 최신 50건만 보는 관제가 되지 않는다',
    needsMock: true,
    async run({ server, check }) {
      server.overrides['GET /admin/audit-log'] = {
        items: [{ occurred_at: '2026-10-05T01:00:00Z', action: 'model.activate', actor_id: 'admin-kim', actor_role: 'admin', target_type: 'model', target_id: 'v-test', success: true, error_code: null, ip_address: null, request_id: null }],
        total: 51, limit: 50, offset: 0, actorless_ratio: 0, warnings: [],
      };
      const page = await auditTab(server);
      page.set('au-actor', 'admin-kim');
      page.set('au-from', '2026-10-01T09:00');
      page.set('au-to', '2026-10-31T18:00');
      page.click(page.q('button[onclick="loadAuditLog()"]'));
      await page.settle();

      const first = new URL(server.lastCall('GET', '/admin/audit-log')?.path || '/', 'http://e2e');
      check.eq(first.searchParams.get('actor_id'), 'admin-kim', '행위자 필터가 실렸다');
      check.ok(first.searchParams.has('occurred_from') && first.searchParams.has('occurred_to'), '기간 필터가 UTC 질의로 실렸다');
      check.eq(first.searchParams.get('offset'), '0', '첫 쪽은 0부터 읽는다');
      check.eq(page.$('au-next')?.disabled, false, '다음 쪽이 있으면 버튼이 열린다');

      page.click(page.q('#au-next'));
      await page.settle();
      const next = new URL(server.lastCall('GET', '/admin/audit-log')?.path || '/', 'http://e2e');
      check.eq(next.searchParams.get('offset'), '50', '다음 쪽은 offset 50을 보낸다');
      check.includes(page.text('au-page'), '51', '현재 쪽 범위가 표시된다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'monitor.audit.empty-and-failure',
    title: '기록이 없을 때와 조회가 실패했을 때를 구분해 말한다',
    needsMock: true,
    async run({ server, check }) {
      const page = await auditTab(server);
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
      const page = await synthesisTab(server);
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

  // [2026-08-24] 승인분 조회에서 **검수자가 고친 등급**이 보이는가.
  // 서버는 corrected_grade 를 돌려주는데 화면이 target_grade 만 그리면, TS 로 고쳐 승인한
  // 건이 목록에는 생성 시 등급으로 보인다 — 데이터는 맞고 화면만 다른 말을 하는 상태다.
  // 이 시나리오가 그 회귀를 막는다.
  {
    id: 'monitor.synth.corrected-grade-is-shown',
    needsMock: true,   // server.overrides 로 합성 큐를 주입한다 — 실서버에는 안 먹는다
    title: '교정 승인된 합성 문서는 목록에 고친 등급으로 보인다',
    async run({ server, check }) {
      server.overrides['GET /synth/queue'] = {
        total: 1,
        items: [{
          synth_id: '14141414-1414-4141-8141-141414141414',
          target_grade: 'S3',          // 생성 시 요구한 등급
          corrected_grade: 'TS',       // 검수자가 고친 등급 = 학습 라벨
          domain: '반도체',
          llm_provider: 'local',
          llm_model: 'qwen3-14b',
          quality_score: 0.77,
          review_status: 'approved',
          preview: '공정 레시피 원본 — 검수자가 등급을 올렸다.',
          created_at: '2026-08-24T02:00:00Z',
        }],
      };
      const page = await synthesisTab(server);
      page.set('sy-status', 'approved');
      page.click(page.q('button[onclick="loadSynthQueue()"]'));
      await page.settle();

      const html = page.html('sy-queue');
      const text = page.text('sy-queue');
      check.eq(page.qa('#sy-queue .q-item').length, 1, '1건이 그려졌다');
      check.includes(html, 'TS', '고친 등급이 보인다');
      check.includes(text, '교정됨', '교정됐다는 표시가 있다');
      check.includes(text, '생성 시 S3', '원래 목표 등급도 함께 밝힌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'monitor.synth.generate',
    writes: true,
    title: '합성 생성 요청은 예상 건수·비용을 화면에 돌려준다',
    async run({ server, check }) {
      const page = await synthesisTab(server);
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
