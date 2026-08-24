/* 5. 검수 — 검증문서(모델 평가 정답지) 준비 · 등록 · 검수/서명 화면 연결.
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
    /* [2026-08-24] AI 후보 생성 시나리오 3건(blocked-without-llm · runs-when-llm-available ·
       empty-body-guarded)을 지웠다. 그 기능이 화면에서 빠졌기 때문이다 —
       요건이 아니고(제출본에 "골든"·"검증문서" 0회 · 이 카드에 secmark 없음), 판정 LLM 을
       붙이지 않기로 했고, 필요한 검수 묶음은 이미 준비돼 있다.
       API(POST /golden/build)는 그대로 살아 있고 서버측 시험이 따로 지킨다.
       여기서는 **화면에 되살아나지 않는지**만 잠근다 — 되살리려면 이 시나리오를 먼저 지워야
       하므로, 무심코 돌아오는 것을 막는다. */
    id: 'golden.build.ai-generation-is-not-on-screen',
    title: '화면에 AI 후보 생성이 없다',
    why: '요건 아님 · 판정 LLM 미사용 결정. 되살아나면 이 시험이 먼저 걸린다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      for (const id of ['gold-go', 'gold-ai-block', 'gold-ai-blocked', 'gold-provider',
                        'gold-source', 'gold-n', 'gold-corpus-dir', 'gold-require-evidence']) {
        check.ok(!page.$(id), `${id} 이 화면에 없다`);
      }
      check.ok(!page.win.startGoldenBuild, 'startGoldenBuild 배선이 없다');
      // 「후보 생성」이라는 말 자체는 다른 카드(학습 후보 생성·합성, FUN-003 §N)에 남아 있다.
      // 이 카드 안에만 없으면 된다.
      const card = page.$('gold-reg').closest('section.card');
      check.ok(!/후보 생성/.test(card.textContent), '이 카드에는 「후보 생성」이 없다');
      // 대신 실제로 쓰는 경로는 그대로 있어야 한다.
      check.ok(page.$('gold-reg'), '「검수 시작」은 그대로 있다');
      check.ok(page.$('gold-build-path'), '문서 묶음 선택칸은 그대로 있다');
      assertNoScriptErrors(check, page);
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

      page.set('gold-build-path', 'datasets/golden_review/ff5a822c/candidates.jsonl');
      page.click('gold-reg');
      await page.settle();

      const call = server.lastCall('POST', '/golden/jobs/register');
      check.eq(call?.body?.build_path, 'datasets/golden_review/ff5a822c/candidates.jsonl', '고른 경로가 그대로 실렸다');
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
      page.set('gold-build-path', 'datasets/golden_review/ff5a822c/candidates.jsonl');
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
    needsData: true,   // 「후보 생성」 잡이 원장에 있어야 한다(223 에는 등록 잡만 있다)
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

      /* [2026-08-24] 여기까지가 종전 단언이었다 — id 두 개가 보이는지만 봤다.
         그래서 본보기의 kind 가 실서버(golden_register)와 달라 등록 잡이 "후보 생성" 으로
         잘못 그려지는데도 통과했다(실측: 두 행 다 "후보 생성"). 칸을 직접 읽는다. */
      const rows = [...page.$('gold-jobs-body').querySelectorAll('tbody tr')]
        .map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent.trim()));
      const reg = rows.filter((r) => r[1] === '문서 묶음 등록');
      check.data.gte(reg.length, 2, '등록한 묶음이 「문서 묶음 등록」으로 적힌다');
      check.ok(rows.some((r) => r[1] === '후보 생성'), 'AI 가 만든 잡은 「후보 생성」으로 적힌다');

      /* 같은 건수의 등록 잡이 둘 있어도 서로 구분돼야 한다 — 223 에서 같은 파일을 두 번씩
         등록한 여섯 행이 전부 같아 보였다. 원본 파일 열이 그 답이다. */
      // 위 단언이 깨진 뒤에도 읽을 수 있는 실패를 남긴다(빈 배열 인덱싱으로 죽지 않게).
      check.eq(reg[0]?.[3], reg[1]?.[3], '건수만으로는 두 행이 같다(구분 근거가 못 된다)');
      check.ok(!!reg[0] && !!reg[1] && reg[0][2] !== reg[1][2], '원본 파일이 달라 두 행을 구분할 수 있다');
      check.includes(body, 'candidates.jsonl', '어느 파일에서 온 묶음인지 보인다');
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
      page.win.openGoldenSignoff();
      await page.settle();
      check.includes(page.text('gold-progress'), '열 작업이 없습니다', '먼저 무엇을 하라고 말한다');
      check.eq(page.opened.length, 0, '빈 주소로 창을 열지 않는다');

      // 잡을 만든 뒤
      page.set('gold-build-path', 'datasets/golden_review/ff5a822c/candidates.jsonl');
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
      page.set('gold-build-path', 'datasets/golden_review/ff5a822c/candidates.jsonl');
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
