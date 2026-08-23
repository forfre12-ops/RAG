/* 11. 등급 시연 화면(index.html) — 관리자 콘솔과 같은 서버를 쓰는 두 번째 화면.
 *
 * 여기 본체(app.js)는 ES 모듈이라 하니스가 묶어서 실행한다(브라우저의 모듈 스코프를
 * 흉내내 IIFE 로 가둔다 — 그래서 인라인 onclick 이 모듈 함수를 부르면 실제와 똑같이 실패한다).
 *
 * ⚠ 실 브라우저와 다른 점: 모듈 실행 방식이 하니스 재현이라는 것. 나머지(요청·DOM·이벤트)는
 *   실제와 같다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function demo(server, opts = {}) {
  const page = await openPage(server, '/console/index.html', { bundleModules: true, ...opts });
  await page.settle();
  return page;
}

export const scenarios = [
  {
    id: 'demo.classify.escalation-explained',
    writes: true,
    title: '안전 규칙이 올려 잡은 등급이면 화면이 그 사실을 말한다',
    why: '실측 2026-08-24: 「룰·모델 모두 TS 로 일치」라고 떠 있는데 검수로 간 카드가 있었다. '
       + '실제로는 모델 최고점이 S1(0.52)인데 escalation 이 TS(0.46)를 채택했고 그 확률이 임계에 '
       + '못 미쳐 검수로 간 것이다. 화면이 그걸 말해 주지 않으면 검수자는 이유를 찾을 수 없다.',
    async run({ server, check }) {
      // 실제로 났던 판정을 그대로 심는다 — argmax 는 S1 인데 안전 규칙이 TS 를 채택했고,
      // 채택 등급의 확률이 임계에 못 미쳐 검수로 간 경우.
      const result = {
        inference_id: '44444444-4444-4444-8444-444444444444',
        doc_id: 'demo-input', label: 'TS', confidence: 0.46,
        scores: { TS: 0.46, S1: 0.52, S2: 0.01, S3: 0.01 },
        rule_grade: 'TS', model_grade: 'TS', decision_path: '룰·모델 모두 TS 로 일치',
        status: 'needs_review', model_version: 'v-fe4b386b', elapsed_ms: 12,
        warnings: ['low-confidence: confidence=0.46 < 0.50 — review recommended'],
        evidence: [], evaluation_factors: { secrecy: 2, value: 2, management: 2 },
        factors_source: 'rule_evidenced', rag_context_used: [],
      };
      server.overrides['POST /classify/stream'] = {
        _sse: [
          { event: 'progress', data: { stage: 'extract', elapsed_ms: 12 } },
          { event: 'progress', data: { stage: 'finalize', elapsed_ms: 150 } },
          { event: 'result', data: result },
        ],
      };
      server.overrides['POST /classify'] = result;
      const page = await demo(server);
      page.set('doc-body', '본 문서는 당사의 영업비밀에 해당하며 대외 반출을 금한다.');
      page.click('btn-classify');
      await page.until(() => page.text('result-head').includes('TS'), 8000);
      await page.settle();

      const all = page.text('result-head') + page.text('result-summary') + page.text('result-dual');
      check.includes(all, '검수 필요', '검수 결정이 표시된다');
      check.matches(all, /안전 규칙이 더 높은 TS/, '올려 잡았다는 사실이 설명된다', all);
      check.matches(all, /가장 높게 본 등급은 S1/, '모델 최고점 등급이 무엇인지 밝힌다', all);
      // 화면에서 신뢰도 수치를 뺀 결정은 여기서도 지켜져야 한다.
      check.ok(!/0\.46|46(\.0)?%|0\.52|52(\.0)?%/.test(all), '확률 수치는 뜨지 않는다', all);
      assertNoScriptErrors(check, page);
      return page;
    },
  },
  {
    id: 'demo.classify.sse-stages',
    writes: true,
    title: '본문을 넣고 분류하면 SSE 로 단계가 점등되고 최종 등급이 나온다',
    why: '단계 표시는 스트리밍 이벤트로만 도는 경로다 — JSON 한 번짜리 시험으로는 안 지나간다',
    async run({ server, check }) {
      const page = await demo(server);
      page.set('doc-body', '본 문서는 당사의 영업비밀에 해당하며 대외 반출을 금한다.');
      page.click('btn-classify');
      // SSE 는 헤더만 오면 fetch 가 끝나므로 settle() 로는 부족하다 — 결과가 그려질 때까지 기다린다.
      const drawn = await page.until(() => page.text('result-head').includes('S1'), 8000);
      check.ok(drawn, '스트림이 끝나고 결과가 그려졌다', page.text('result-head'));
      await page.settle();

      const call = server.lastCall('POST', '/classify/stream');
      check.ok(call, 'POST /classify/stream 이 나갔다');
      check.includes(call?.body?.content || '', '영업비밀', '본문이 실려 나갔다');

      const seq = page.html('stage-seq');
      check.ok(seq.length > 0, '단계 표시가 그려졌다');
      check.matches(page.text('stage-seq'), /본문 추출|정규화|임베딩/, '단계 이름이 보인다');
      check.includes(page.text('result-head'), 'S1', '최종 등급이 표시된다');
      // [2026-08-24] 계약이 바뀌었다. 화면 머리에 서는 것은 신뢰도 숫자가 아니라 **결정**이다.
      // 종전 시험은 /81|0.81/ 로 숫자가 뜨는지를 봤는데, 그 숫자를 크게 띄우던 것이 화면이
      // 설명하는 판정 논리와 실제 판정 논리를 어긋나게 하던 원인이었다(자동확정은 conf 단독이
      // 아니라 다단 게이트로 난다). 이제는 반대로 **숫자가 안 뜨는 것**까지 시험이 지킨다.
      check.matches(page.text('result-head'), /자동 확정|검수 필요/, '자동확정·검수 결정이 표시된다');
      check.ok(
        !/(^|[^\d])0\.81([^\d]|$)/.test(page.text('result-head') + page.text('result-summary'))
        && !/81(\.0)?%/.test(page.text('result-head') + page.text('result-summary')),
        '신뢰도 수치는 화면에 뜨지 않는다',
        page.text('result-head') + ' | ' + page.text('result-summary'),
      );
      check.includes(page.html('result-dual'), 'S1', '룰·모델 두 판정이 나란히 보인다');
      check.ok(page.html('result-factors').length > 0, 'S·V·M 3요소가 그려졌다');
      check.eq(page.$('btn-classify')?.disabled, false, '끝나고 버튼이 다시 눌린다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'demo.classify.empty-input-guard',
    title: '본문 없이 분류를 누르면 안내가 뜨고 요청은 안 나간다',
    async run({ server, check }) {
      const page = await demo(server);
      page.set('doc-body', '   ');
      page.click('btn-classify');
      await page.settle();
      check.eq(server.countCalls('POST', '/classify/stream'), 0, '빈 본문으로는 보내지 않는다');
      const told = page.bodyText().includes('본문') || page.dialogs.length > 0;
      check.ok(told, '무엇이 필요한지 알려준다');
      return page;
    },
  },

  {
    id: 'demo.upload.analyze',
    needsData: true,
    title: '파일을 올리면 파싱 결과와 등급이 함께 나온다',
    async run({ server, check }) {
      const page = await demo(server);
      page.attachFile('doc-file', { name: '기술이전 계약초안.pdf' });
      const drawn = await page.until(() => page.text('result-head').includes('S1'), 8000);
      await page.settle();

      check.ok(server.lastCall('POST', '/documents/analyze'), 'POST /documents/analyze 가 나갔다');
      check.ok(drawn, '분류 결과가 그려졌다', page.text('result-head'));
      check.includes(page.text('doc-file-name'), '기술이전 계약초안.pdf', '올린 파일 이름이 보인다');
      check.includes(page.$('doc-body')?.value || '', '기술이전', '추출 본문이 입력란에 들어갔다');
      check.eq(page.$('doc-title')?.value, '기술이전 계약초안.pdf', '제목이 파일명으로 채워졌다');
      // 업로드 경로의 단계 표시는 서버가 준 stages 를 그대로 쓴다(이름·소요시간).
      check.includes(page.text('stage-seq'), 'extract', '서버가 준 파싱 단계가 표시된다');
      check.includes(page.text('stage-seq'), '120', '단계별 실측 시간이 함께 보인다');
      check.includes(page.html('result-dual'), 'S1', '룰·모델 판정이 보인다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'demo.upload.failure-visible',
    title: '업로드가 실패하면 사유가 화면에 보인다',
    needsMock: true,
    async run({ server, check }) {
      const page = await demo(server);
      server.faults.push({ path: '/documents/analyze', status: 500, body: { detail: '추출기 오류' } });
      page.attachFile('doc-file', { name: 'broken.pdf' });
      await page.settle(6000);

      const shown = page.bodyText();
      check.matches(shown, /실패|오류|500/, '실패 사실이 화면에 보인다');
      check.ok(page.errors.length === 0, '스크립트가 죽지 않았다', page.errors.map((e) => e.message).join(' | '));
      return page;
    },
  },

  {
    id: 'demo.ops.dashboard-refresh',
    title: '운영 현황 타일이 서버 수치로 채워진다',
    async run({ server, check }) {
      const page = await demo(server);
      const btn = page.$('btn-dash-refresh');
      check.ok(btn, '대시보드 새로고침 버튼이 있다');
      btn.click();
      await page.settle();
      check.ok(server.lastCall('GET', '/dashboard/summary'), 'GET /dashboard/summary 를 불렀다');
      check.ok(page.html('ops-tiles').length > 20, '타일이 그려졌다');
      const tiles = page.text('ops-tiles');
      check.data.includes(tiles, '120', '총 분류수가 보인다');
      check.includes(tiles, '검수 대기', '검수 대기 타일이 있다');
      check.data.includes(tiles, '74', '검수 반영 수가 보인다');
      check.data.matches(page.html('ops-tiles'), /g-TS[^>]*>TS 9/, '등급 분포가 서버 수치로 채워졌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'demo.reflect.correction-flows-into-next-classify',
    writes: true,
    title: '「실시간 반영 시연」이 등록→분류→교정·승급→재분류 4단계를 실제로 돈다',
    why: '검수 교정이 다음 분류에 반영된다는 주장을 화면이 서버 실호출로 보여 주는 자리다 '
       + '(POST /promotions/promote 는 콘솔에서 여기서만 나간다)',
    async run({ server, check }) {
      const page = await demo(server);
      const btn = page.$('btn-reflect');
      check.ok(btn, '시연 버튼이 있다');
      btn.click();
      const done = await page.until(() => page.text('reflect-busy').includes('완료')
        || page.text('reflect-busy').includes('오류'), 12000);
      await page.settle();

      check.ok(done, '끝까지 돌았다', page.text('reflect-busy'));
      check.excludes(page.text('reflect-busy'), '오류', '중간에 끊기지 않았다');

      // 네 단계가 실제 요청으로 나갔는가
      check.ok(server.lastCall('POST', '/documents'), '① 문서를 등록했다');
      check.gte(server.countCalls('POST', '/classify'), 2, '②·④ 분류를 두 번 했다(최초·재분류)');
      check.ok(server.lastCall('POST', '/relabel'), '③ 교정을 보냈다');
      const promote = server.lastCall('POST', '/promotions/promote');
      check.ok(promote, '③ 승급을 보냈다');
      check.eq(promote?.body?.actor?.role, 'reviewer', '승급 요청에 검수자 신원이 실렸다');
      check.ok(promote?.body?.expected_label, '어느 등급으로 승급하는지 실렸다');

      // 화면에 4단계가 남았는가
      const steps = page.text('reflect-steps');
      for (const t of ['등록', '최초 분류', '검수 교정', '재분류']) {
        check.includes(steps, t, `${t} 단계가 화면에 남았다`);
      }
      check.gte(server.countCalls('GET', '/dashboard/summary'), 2, '단계마다 운영 현황을 다시 읽는다');
      check.eq(page.$('btn-reflect')?.disabled, false, '끝나고 버튼이 다시 눌린다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'demo.reflect.failure-visible',
    title: '시연 도중 실패하면 어디서 멈췄는지 화면에 남는다',
    needsMock: true,
    async run({ server, check }) {
      const page = await demo(server);
      server.faults.push({ path: '/documents', status: 500, body: { detail: '스토리지 오류' } });
      page.$('btn-reflect').click();
      const shown = await page.until(() => page.text('reflect-busy').includes('오류'), 8000);
      await page.settle();

      check.ok(shown, '오류라고 화면이 말한다', page.text('reflect-busy'));
      check.eq(server.countCalls('POST', '/promotions/promote'), 0, '앞 단계가 깨지면 뒤로 진행하지 않는다');
      check.eq(page.$('btn-reflect')?.disabled, false, '버튼이 잠긴 채 남지 않는다');
      return page;
    },
  },

  {
    id: 'demo.health.pill',
    title: '연결 확인이 서버 프로파일을 표시한다',
    async run({ server, check }) {
      const page = await demo(server);
      const pill = page.$('health');
      check.ok(pill, '헬스 표시가 있다');
      pill.click();
      await page.settle();
      check.gte(server.countCalls('GET', '/healthz'), 1, 'healthz 를 불렀다');
      check.includes(page.text('health-txt'), 'full-train', '서버 프로파일이 표시된다');
      return page;
    },
  },

  {
    id: 'demo.legal.cards',
    title: '법령 근거 카드가 4등급 모두 그려진다',
    async run({ server, check }) {
      const page = await demo(server);
      const html = page.html('legal-grid');
      for (const g of ['TS', 'S1', 'S2', 'S3']) {
        check.includes(page.text('legal-grid') + html, g, `${g} 카드가 있다`);
      }
      check.includes(page.bodyText(), '부정경쟁방지', '근거 법률이 화면에 있다');
      return page;
    },
  },
];
