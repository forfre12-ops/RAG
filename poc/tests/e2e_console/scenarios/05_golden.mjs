/* 5. 검수 화면 — Product Shell V2의 정식 검수자 배정 흐름.
 *
 * 옛 G2 문서 묶음 등록/잡 목록 카드는 서버 호환용 DOM·API만 남기고 새 콘솔에서는
 * 노출하지 않는다. 같은 목적의 두 화면을 동시에 보이면 운영자가 어느 경로가 정본인지
 * 알 수 없기 때문이다. 20명×50건 정확 배정은 19_product_shell.mjs가 실제 요청까지
 * 별도로 검증한다.
 */

import { openPage, goToPane } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function expertsView(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  await goToPane(page, 'experts');
  await page.settle();
  return page;
}

export const scenarios = [
  {
    id: 'golden.build.ai-generation-is-not-on-screen',
    title: '검수자 배정 화면에 AI 후보 생성 기능이 없다',
    why: '판정 LLM으로 후보 등급을 만들지 않기로 한 운영 결정이 화면에서 되살아나면 안 된다',
    async run({ server, check }) {
      const page = await expertsView(server);

      for (const id of ['gold-go', 'gold-ai-block', 'gold-ai-blocked', 'gold-provider',
                        'gold-source', 'gold-n', 'gold-corpus-dir', 'gold-require-evidence']) {
        check.ok(!page.$(id), `${id} 이 화면에 없다`);
      }
      check.ok(!page.win.startGoldenBuild, 'startGoldenBuild 배선이 없다');
      check.ok(page.visible('product-experts'), '검수자 배정 화면이 실제로 열린다');
      check.excludes(page.text('product-experts'), '후보 생성', '검수자 배정 화면에 후보 생성 동작이 없다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'golden.legacy-registration-is-not-exposed-twice',
    title: '검수 계정별 진행 화면에는 배정 입력 폼을 노출하지 않는다',
    why: '계정별 진행을 보는 화면에 배정 설정을 섞으면 요구하지 않은 운영 절차와 권한이 생긴다',
    async run({ server, check }) {
      const page = await expertsView(server);

      check.ok(page.visible('product-experts'), '검수 계정별 진행 화면이 보인다');
      for (const id of ['gold-register-block', 'gold-reg', 'gold-build-path', 'gold-jobs-card']) {
        check.ok(!page.visible(id), `${id} 구형 제어는 새 콘솔에 중복 노출되지 않는다`);
      }
      for (const id of ['expert-reviewer-ids', 'expert-reviewer-roster', 'expert-plan-submit', 'expert-assignment-reason']) {
        check.ok(!page.$(id), `${id} 배정 입력 제어는 진행 화면에 없다`);
      }
      check.includes(page.text('product-experts'), '검수 계정별 진행 현황', '계정별 진행 상태를 설명한다');
      check.includes(page.text('product-experts'), '검수 계정', '진행 현황 요약을 보여준다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
