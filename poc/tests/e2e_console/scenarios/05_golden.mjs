/* 5. 검수 — 검증 기준문서(정답지) 준비 · 등록 · 검수/서명 화면 연결.
 *
 * 이 흐름은 "화면이 서버 상태에 따라 다르게 보여야" 하는 대표 자리다. 등급을 매길 LLM 이
 * 없는 서버에서 「후보 생성」을 눌러 봐야 전부 S3 로만 나온다(2026-08-23 실측). 그래서
 * 누르기 전에 잠그고 실제로 되는 경로를 위로 올리는지까지 본다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

const LLM_ON = (base) => JSON.parse(JSON.stringify({ ...base, llm_provider: 'local' }));

async function reviewTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  page.click(page.q('.tab[data-tab="review"]'));
  return page;
}

export const scenarios = [
  {
    id: 'golden.status.shows-real-vs-synthetic',
    title: '검증문서 현황이 등급별 충족도와 실문서/합성 구성을 함께 보여준다',
    why: '합성만으로 서명이 차도 ready=true 가 된다 — 그대로 읽으면 실세계 성능으로 오해한다',
    async run({ server, check }) {
      const page = await reviewTab(server);
      page.click(page.q('button[onclick="loadGoldenStatus()"]'));
      await page.settle();

      check.ok(server.lastCall('GET', '/golden/summary'), '정본 구성을 읽었다');
      check.ok(server.lastCall('GET', '/admin/locked-readiness'), '서명 확정 현황을 읽었다');
      const box = page.html('gs-body');
      check.includes(box, '정본 구성', '정본 구성 패널이 있다');
      check.data.includes(box, '777', '전체 건수가 보인다');
      check.includes(box, '미충족', '아직 배포 가능하지 않다고 말한다');
      check.data.includes(box, '25건 남음', '등급별로 몇 건 남았는지 보인다');
      check.includes(box, '부족 등급', '어느 등급이 부족한지 적는다');
      check.includes(page.text('gs-body'), '학습 시드', 'tier 이름을 업무 말로 옮겨 보여준다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'golden.build.blocked-without-llm',
    title: 'LLM 이 없는 서버에서는 「후보 생성」이 잠기고 대신 되는 경로를 알려준다',
    async run({ server, check }) {
      const page = await reviewTab(server);
      check.eq(page.$('gold-go')?.disabled, true, '후보 생성 버튼이 잠겼다');
      check.ok(page.visible('gold-ai-blocked'), '왜 잠겼는지 안내가 보인다');
      check.includes(page.text('gold-ai-blocked'), 'LLM', '등급을 매길 LLM 이 없다고 말한다');
      check.ok(page.visible('gold-build-path'), '대신 쓸 수 있는 문서 묶음 목록이 보인다');

      // 잠긴 버튼을 강제로 실행해도 요청이 나가면 안 된다
      page.win.startGoldenBuild();
      await page.settle();
      check.eq(server.countCalls('POST', '/golden/build'), 0, '잠긴 경로로는 요청이 나가지 않는다');
      check.includes(page.text('gold-progress'), 'LLM', '눌렀을 때도 같은 사유를 말한다');
      return page;
    },
  },

  {
    id: 'golden.build.runs-when-llm-available',
    writes: true,
    title: 'LLM 이 있으면 후보 생성 → 완료 폴링 → 검수/서명 버튼 활성화까지 간다',
    needsMock: true,
    async run({ server, check }) {
      const health = JSON.parse(JSON.stringify(server.overrides));
      void health;
      const { FIXTURES } = await import('../lib/server.mjs');
      server.overrides['GET /healthz'] = LLM_ON(FIXTURES['GET /healthz']);

      const page = await reviewTab(server);
      check.eq(page.$('gold-go')?.disabled, false, 'LLM 이 있으면 버튼이 열린다');

      page.set('gold-source', 'editor');
      page.set('cl-body', '당사 EUV 공정 레시피 원본 — 대외 반출 금지.');
      page.click('gold-go');
      await page.settle(6000);

      const call = server.lastCall('POST', '/golden/build');
      check.ok(call, 'POST /golden/build 가 나갔다');
      check.eq(call?.body?.source_type, 'inline', '분류 패널 본문을 후보로 보냈다');
      check.gte((call?.body?.docs || []).length, 1, '본문이 실제로 실렸다');
      check.ok(server.lastCall('GET', '/golden/jobs/'), '완료까지 상태를 폴링했다');
      check.includes(page.text('gold-progress'), '완료', '완료했다고 말한다');
      check.includes(page.text('gold-progress'), '120', '몇 건이 후보가 됐는지 말한다');
      check.eq(page.$('gold-btn-review')?.disabled, false, '검수 화면 버튼이 열렸다');
      check.eq(page.$('gold-btn-signoff')?.disabled, false, '서명 화면 버튼이 열렸다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'golden.build.empty-body-guarded',
    title: '보낼 본문이 비어 있으면 무엇을 해야 하는지 알려주고 요청은 안 보낸다',
    needsMock: true,
    async run({ server, check }) {
      const { FIXTURES } = await import('../lib/server.mjs');
      server.overrides['GET /healthz'] = LLM_ON(FIXTURES['GET /healthz']);
      const page = await reviewTab(server);
      page.set('gold-source', 'editor');
      page.set('cl-body', '');
      page.click('gold-go');
      await page.settle();
      check.includes(page.text('gold-progress'), '비어 있', '본문이 비었다고 말한다');
      check.includes(page.text('gold-progress'), '분류 실행', '어디에 넣으라고 짚어 준다');
      check.eq(server.countCalls('POST', '/golden/build'), 0, '요청이 나가지 않는다');
      return page;
    },
  },

  {
    id: 'golden.register.existing-bundle',
    writes: true,
    title: '이미 있는 문서 묶음을 골라 「검수 시작」을 누르면 잡으로 등록된다',
    why: '자유 입력이던 시절에는 서버에 무슨 파일이 있는지 몰라 경로를 외워 쳐야 했다',
    async run({ server, check }) {
      const page = await reviewTab(server);
      const sel = page.$('gold-build-path');
      check.data.gte(sel?.options.length, 3, '고를 수 있는 묶음이 목록으로 나온다');
      check.data.includes(page.html('gold-build-path'), '120건', '건수와 날짜가 함께 보인다');

      page.set('gold-build-path', 'datasets/proxy_gold/build_ff5a822c.jsonl');
      page.click('gold-reg');
      await page.settle();

      const call = server.lastCall('POST', '/golden/jobs/register');
      check.eq(call?.body?.build_path, 'datasets/proxy_gold/build_ff5a822c.jsonl', '고른 경로가 그대로 실렸다');
      check.includes(page.logLines('ok').join(' '), '문서 묶음 등록', '등록됐다고 로그가 말한다');
      check.ok(server.lastCall('GET', '/golden/jobs/'), '등록 직후 상태를 읽는다');
      check.includes(page.text('gold-progress'), 'done', '이어서 그 잡의 상태를 화면에 보여준다');
      check.includes(page.html('gold-summary'), '120', '후보 건수 요약이 그려졌다');
      check.eq(page.$('gold-reg')?.disabled, false, '버튼이 잠긴 채 남지 않는다');
      return page;
    },
  },

  {
    id: 'golden.register.nothing-selected',
    title: '아무것도 안 고르고 검수 시작을 누르면 고르라고 말한다',
    async run({ server, check }) {
      const page = await reviewTab(server);
      page.set('gold-build-path', '');
      page.click('gold-reg');
      await page.settle();
      check.includes(page.text('gold-progress'), '고르세요', '무엇을 하라는지 말한다');
      check.eq(server.countCalls('POST', '/golden/jobs/register'), 0, '요청이 나가지 않는다');
      return page;
    },
  },

  {
    id: 'golden.register.failure-visible',
    writes: true,
    title: '등록이 실패하면 상태코드와 사유가 화면에 남는다',
    needsMock: true,
    async run({ server, check }) {
      const page = await reviewTab(server);
      server.faults.push({ path: '/golden/jobs/register', status: 404, body: { detail: '경로를 찾을 수 없습니다' } });
      page.set('gold-build-path', 'datasets/proxy_gold/build_ff5a822c.jsonl');
      page.click('gold-reg');
      await page.settle();
      check.includes(page.text('gold-progress'), '등록 실패', '실패했다고 말한다');
      check.includes(page.text('gold-progress'), '404', '상태코드가 보인다');
      check.includes(page.text('gold-progress'), 'datasets/ 밖', '404 일 때 흔한 원인을 짚어 준다');
      return page;
    },
  },

  {
    id: 'golden.jobs.panel-lists-and-links',
    title: '검수 목록 패널이 잡을 나열하고 검수·서명으로 이어진다',
    why: 'job_id 를 JS 변수로만 들고 있으면 새로고침 한 번에 진행 중이던 검수로 못 돌아간다',
    async run({ server, check }) {
      const page = await reviewTab(server);
      page.click(page.q('button[onclick="loadGoldenJobList()"]'));
      await page.settle();
      check.ok(server.lastCall('GET', '/golden/jobs'), 'GET /golden/jobs 를 불렀다');
      const body = page.html('gold-jobs-body');
      check.data.includes(body, 'eeeeeeee', '완료된 잡이 보인다');
      check.data.includes(body, 'dddddddd', '진행 중인 잡도 보인다');
      check.matches(body, /signoff|서명/, '서명 화면으로 가는 길이 있다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'golden.open-review-html',
    writes: true,
    title: '검수/서명 화면 열기는 잡 id 가 있어야 하고, 서명 토큰을 붙여 연다',
    why: '?t= 토큰이 빠져 403 이 나던 것이 사람 검수가 시작되지 못한 진짜 원인이었다',
    needsMock: true,
    async run({ server, check }) {
      const page = await reviewTab(server);
      // 잡이 없을 때
      page.win.openGoldenReview();
      await page.settle();
      check.includes(page.text('gold-progress'), '열 잡이 없습니다', '먼저 무엇을 하라고 말한다');
      check.eq(page.opened.length, 0, '빈 주소로 창을 열지 않는다');

      // 잡을 만든 뒤
      page.set('gold-build-path', 'datasets/proxy_gold/build_ff5a822c.jsonl');
      page.click('gold-reg');
      await page.settle();

      server.overrides['GET /golden/jobs/{job_id}'] = {
        status: 'done', stats: null, gold_count: 120, uncertain_count: 8,
        gold_path: null, uncertain_path: null, error: null,
        review_url: '/api/v1/golden/jobs/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/review.html?t=abc123',
        signoff_url: '/api/v1/golden/jobs/eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee/signoff.html?t=def456',
      };
      page.click('gold-btn-signoff');
      await page.settle();
      check.eq(page.opened.length, 1, '새 창을 하나 열었다');
      check.includes(page.opened[0]?.url || '', 'signoff.html', '서명 화면 주소로 열었다');
      check.includes(page.opened[0]?.url || '', 't=def456', '서명 토큰이 붙었다');
      return page;
    },
  },

  {
    id: 'golden.signoff-return-prompt',
    writes: true,
    title: '서명 탭을 열었다가 돌아오면 다음 단계로 이어 준다',
    async run({ server, check }) {
      const page = await reviewTab(server);
      page.set('gold-build-path', 'datasets/proxy_gold/build_ff5a822c.jsonl');
      page.click('gold-reg');
      await page.settle();
      page.click('gold-btn-signoff');
      await page.settle();

      page.win.dispatchEvent(new page.win.Event('focus'));
      check.ok(page.visible('signoff-return'), '돌아왔을 때 확인 띠가 뜬다');

      page.click(page.q('button[onclick="confirmSignoffDone()"]'));
      await page.settle();
      check.eq(page.q('.tab.active')?.dataset.tab, 'train', '다음 단계인 학습·배포 탭으로 옮겨 준다');
      check.ok(!page.visible('signoff-return'), '확인 띠는 사라진다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
