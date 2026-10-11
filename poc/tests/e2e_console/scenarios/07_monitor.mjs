/* 7. 운영 — 감사 로그와 모의 문서 검토 흐름. */

import { openPage, goToPane } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

// [2026-10-10] 감사 로그 카드(admin.html:1006)는 옛 "운영" 탭 하나에 같이 있었지만, Product
// Shell V2에서 감사·보고서(audit)는 전용 내비게이션으로 바로 연다.
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
    why: '운영 사고를 특정 담당자·시간대에서 추적할 수 있어야 최신 50건만 보는 조회가 되지 않는다',
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
    id: 'monitor.synth.queue-and-review',
    writes: true,
    needsMock: true,
    title: '합성 검수 큐를 읽어 승인/반려하면 목록에서 빠진다',
    why: '[2026-10-11] 기본 fixture의 둘째 건이 label_source:"llm_nonjson"이라 화면이 이제 큐에서 걸러낸다(승인해도 학습 편입 불가한 더미라서) — 이 시나리오가 보려는 건 그 필터링이 아니라 "승인하면 목록에서 빠진다"이므로, 전용 override로 둘 다 걸러지지 않는 2건을 둔다.',
    async run({ server, check }) {
      server.overrides['GET /synth/queue'] = {
        total: 2,
        items: [
          {
            synth_id: '11111111-1111-4111-8111-111111111111',
            target_grade: 'TS', domain: '반도체',
            llm_provider: 'local', llm_model: 'qwen3-14b',
            quality_score: 0.81, review_status: 'pending',
            preview: '당사 EUV 공정 레시피 원본 — 대외 반출 금지.',
            created_at: '2026-08-23T01:40:00Z',
          },
          {
            synth_id: '22222222-2222-4222-8222-222222222222',
            target_grade: 'S2', domain: 'hr',
            llm_provider: 'local', llm_model: 'qwen3-14b',
            quality_score: 0.72, review_status: 'pending',
            preview: '조직 개편 계획 초안 — 대외비.',
            created_at: '2026-08-23T01:42:00Z',
          },
        ],
      };
      const page = await synthesisTab(server);
      page.click(page.q('button[onclick="loadSynthQueue()"]'));
      await page.settle();

      check.eq(page.qa('#sy-queue .q-item').length, 2, '2건이 그려졌다');
      check.excludes(page.html('sy-queue'), '품질 0.81', '[2026-10-11] 품질 점수 배지는 제거됐다 — 단건 생성에서는 코퍼스 지표가 항상 미판정으로 찍혀 오해를 줬다');
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
      page.set('sy-context', '연구기획팀 기술 검토와 협력사 일정 조율');
      page.set('sy-document-type', '기술 검토 회의록');
      page.set('sy-count', '20');
      page.click('sy-gen');
      await page.settle();

      const call = server.lastCall('POST', '/synth/generate');
      check.eq(call?.body?.target_grade, 'TS', '목표 등급이 실렸다');
      check.eq(call?.body?.scenario_context, '연구기획팀 기술 검토와 협력사 일정 조율', '업무 맥락이 실렸다');
      check.eq(call?.body?.document_type_hint, '기술 검토 회의록', '문서 유형 힌트가 실렸다');
      check.eq(call?.body?.count, 20, '건수가 실렸다');
      check.includes(page.text('sy-info'), '예상 20건', '예상 건수가 보인다');
      check.includes(page.text('sy-info'), '$', '예상 비용이 보인다');
      check.eq(page.$('sy-gen')?.disabled, false, '버튼이 잠긴 채 남지 않는다');
      return page;
    },
  },

  {
    id: 'monitor.synth.playbook-manage',
    writes: true,
    title: '생성 규칙 팝업에서 조직별 규칙을 저장하면 다음 생성 적용본을 받는다',
    async run({ server, check }) {
      const page = await synthesisTab(server);
      page.click(page.q('button[onclick="openSynthPlaybookDialog()"]'));
      await page.settle();

      const dialog = page.$('sy-playbook-dialog');
      check.ok(dialog?.hasAttribute('open'), '생성 규칙 팝업이 열린다');
      check.includes(page.text('sy-playbook-baseline'), '기밀·보안 수준', '기본 안전 규칙을 볼 수 있다');

      page.set('sy-playbook-custom-rules', '회의록에는 결정 사항과 담당 부서를 함께 적는다.');
      page.set('sy-playbook-custom-avoid', '당사');
      page.click('sy-playbook-save');
      await page.settle();

      const call = server.lastCall('PUT', '/synth/playbook');
      check.data.includes(call?.body?.custom_rules || [], '결정 사항', '추가 규칙을 저장했다');
      check.data.includes(call?.body?.custom_avoid_phrases || [], '당사', '반복 금지 표현을 저장했다');
      check.includes(page.text('sy-info'), '다음 생성 요청부터 적용', '적용 시점을 분명히 말한다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
