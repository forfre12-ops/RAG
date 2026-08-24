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
    id: 'demo.surface.write-demo-visible-on-demo-server',
    needsMock: true,
    title: '시연 설정이면 그 버튼은 그대로 있다',
    why: '운영에서 감추는 것이지 기능을 없애는 것이 아니다',
    async run({ server, check }) {
      healthWith(server, true);
      const page = await demo(server);

      check.eq(page.visible('btn-reflect'), true, '시연 서버에서는 버튼이 보인다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
