/* 14. 시연 화면의 쓰기 표면 — 운영 서버에서는 사라져야 한다.
 *
 * 「실시간 반영 시연」은 이름과 달리 실 DB 에 쓴다(등록 → 분류 → 교정 → 승급, 행위자
 * reviewer-demo 고정). 프로덕션(onprem-local·full-train)은 demo_console_enabled=false 라
 * 이 버튼이 있으면 운영 원장에 시연 행이 섞인다.
 */

import { openPage } from '../lib/page.mjs';
import { FIXTURES } from '../lib/server.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function demo(server, opts = {}) {
  const page = await openPage(server, '/console/index.html', { bundleModules: true, ...opts });
  await page.settle();
  return page;
}

/** healthz 응답에서 operational_config.demo_console_enabled 만 바꿔 끼운다. */
function healthWith(server, enabled) {
  const base = JSON.parse(JSON.stringify(FIXTURES['GET /healthz']));
  base.operational_config.demo_console_enabled = enabled;
  server.faults.push({ path: '/healthz', body: base });
}

export const scenarios = [
  {
    id: 'demo.surface.write-demo-hidden-on-production',
    needsMock: true,
    title: '운영 설정이면 「실시간 반영 시연」 버튼이 사라지고 왜 없는지 적힌다',
    why: '요건 근거 없는 쓰기(/promotions/promote)가 운영 원장에 시연 행을 남겼다',
    async run({ server, check }) {
      healthWith(server, false);
      const page = await demo(server);

      check.eq(page.visible('btn-reflect'), false, '시연 쓰기 버튼이 화면에서 사라진다');
      check.includes(page.text('reflect-busy'), '시연용 쓰기', '왜 없는지 그 자리에 적힌다');
      check.eq(server.countCalls('POST', '/promotions/promote'), 0, '승급 요청이 나가지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    /* [2026-08-24 2차] 「실적재」는 이 화면에서 **운영 검수 큐**로 문서를 넣는 경로다
       (POST /documents → /classify). 1차에서 btn-reflect 만 막았더니 223(full-train ·
       demo_console_enabled=false)에서 버튼은 사라졌는데 이 체크박스는 그대로 있었다.
       같은 선언을 두 경로가 다르게 따르지 않게 잠근다. */
    id: 'demo.surface.persist-hidden-on-production',
    needsMock: true,
    title: '운영 설정이면 「실적재」 체크박스와 그 설명도 사라진다',
    why: '서버가 시연용 쓰기를 하지 않는다고 선언했는데 검수 큐 적재 경로만 남아 있었다',
    async run({ server, check }) {
      healthWith(server, false);
      const page = await demo(server);

      check.eq(page.visible('persist-demo'), false, '실적재 체크박스가 화면에서 사라진다');
      check.eq(page.$('persist-demo').checked, false, '감추기 전에 꺼진다');
      check.includes(page.text('persist-hint'), '적재하지 않습니다', '없는 기능을 안내하지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    /* 감춘 요소는 콘솔에서 다시 켤 수 있다. 화면이 아니라 **쓰기 직전**에도 막는지 본다. */
    id: 'demo.surface.persist-blocked-even-if-checked',
    needsMock: true,
    title: '운영 설정에서는 실적재를 강제로 켜도 적재 요청이 나가지 않는다',
    why: '표시로만 막으면 스크립트 한 줄로 운영 원장에 데모 행이 들어간다',
    async run({ server, check }) {
      healthWith(server, false);
      const page = await demo(server);

      // 검수 대상(needs_review)으로 응답을 바꾼다 — 원래는 이때 적재된다(11_demo 참조).
      server.overrides['POST /documents/analyze'] = {
        filename: '설계 초안.docx',
        file_size_bytes: 4096,
        parse: { source_format: 'docx', extraction_method: 'python-docx', extraction_quality: 0.72, content_quality: 0.7, ocr_used: false, char_count: 900, chunk_count: 2, warnings: [], pii_masked_count: 0, extract_error: null },
        gate: { requires_review: true, reasons: ['low_extraction_quality'] },
        classification: {
          label: 'S1', confidence: 0.42, scores: { TS: 0.1, S1: 0.42, S2: 0.3, S3: 0.18 },
          status: 'needs_review', model_version: 'v-fe4b386b',
          factors: { secrecy: 2, value: 1, management: 1 }, factors_source: 'rule_evidenced',
          rule_factors: null, warnings: ['low_confidence'], elapsed_ms: 180,
          rule_grade: 'S1', model_grade: 'S2', decision_path: 'disagreement',
        },
        evidence: [], text_preview: '설계 초안 본문', text: '설계 초안 본문', stages: [],
      };
      page.$('persist-demo').checked = true;          // 감춰진 것을 강제로 켠다
      page.attachFile('doc-file', { name: '설계 초안.docx' });
      await page.settle(8000);

      check.ok(!server.exactCall('POST', '/documents'), '적재 요청이 나가지 않는다');
      check.ok(server.lastCall('POST', '/documents/analyze'), '분석 자체는 그대로 된다');
      // 이 화면의 로그창은 #live-log 다(관리자 콘솔의 #logbody 가 아니다 — page.logLines 는 후자를 본다).
      check.includes(page.text('live-log'), '실적재 건너뜀', '왜 안 했는지 로그에 남는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'demo.surface.write-demo-visible-on-demo-server',
    needsMock: true,
    title: '시연 설정이면 그 버튼은 그대로 있다',
    why: '운영에서 감추는 것이지 기능을 없애는 것이 아니다',
    async run({ server, check }) {
      healthWith(server, true);
      const page = await demo(server);

      check.eq(page.visible('btn-reflect'), true, '시연 서버에서는 버튼이 보인다');
      check.eq(page.visible('persist-demo'), true, '실적재도 시연 서버에서는 그대로다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
