/* 9. 안전장치 — 쓰기 차단(Safe Mode) · 프로파일별 화면 구성 · 서버 설정 반영.
 *
 * "읽기 전용으로 두고 보여만 준다"가 실제로 지켜지는지, 그리고 회원사 서버에서 지재원
 * 전용 기능이 그대로 보이지 않는지를 본다. 둘 다 화면에만 있는 규칙이라 서버 시험으로는
 * 확인되지 않는다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

/* 쓰기 동작 전수 — 이름 · 누를 것 · 사전 준비 · 나가면 안 되는 요청 */
const WRITES = [
  {
    name: '확정', pane: 'ops', endpoint: 'POST /confirm',
    async setup(page) {
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();
    },
    press: (page) => page.q('#queue button[onclick="doConfirm(0)"]'),
  },
  {
    name: '재라벨', pane: 'ops', endpoint: 'POST /relabel',
    async setup(page) {
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();
    },
    press: (page) => page.q('#queue button[onclick="doRelabel(0)"]'),
  },
  { name: '재학습 제출', pane: 'train', endpoint: 'POST /train', press: (p) => p.q('button[onclick="submitTrain()"]') },
  { name: '모델 핫리로드', pane: 'train', endpoint: 'POST /admin/model/reload', press: (p) => p.q('button[onclick="reloadModel()"]') },
  {
    name: '모델 활성화', pane: 'train', endpoint: 'POST /admin/model/activate',
    async setup(page) { page.set('act-ver', 'v-cccccccc'); },
    press: (p) => p.q('button[onclick="activateModel()"]'),
  },
  { name: '모델 롤백', pane: 'train', endpoint: 'POST /admin/model/rollback', press: (p) => p.q('button[onclick="rollbackModel()"]') },
  { name: '합성 생성', pane: 'train', endpoint: 'POST /synth/generate', press: (p) => p.$('sy-gen') },
  {
    name: '등급체계 저장', pane: 'config', endpoint: 'PUT /schema/grades',
    async setup(page) {
      page.click(page.q('button[onclick="loadGradeEditor()"]'));
      await page.settle();
    },
    press: (p) => p.$('grade-save'),
  },
  {
    name: '키워드 추가', pane: 'config', endpoint: 'POST /admin/keywords',
    async setup(page) { page.set('kw-n-keyword', '테스트'); },
    press: (p) => p.q('button[onclick="createKeyword()"]'),
  },
  {
    name: '검증 기준문서 등록', pane: 'review', endpoint: 'POST /golden/jobs/register',
    async setup(page) { page.set('gold-build-path', 'datasets/proxy_gold/build_ff5a822c.jsonl'); },
    press: (p) => p.$('gold-reg'),
  },
];

export const scenarios = [
  {
    id: 'safety.safe-mode.blocks-every-write',
    writes: true,
    title: '쓰기 허용을 끄면 상태를 바꾸는 동작이 하나도 나가지 않는다',
    why: '시연·감리 중 실수로 누르는 것을 막는 장치다 — 한 곳이라도 새면 장치가 아니다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      page.check('cfg-write-enable', false);
      check.includes(page.text('write-gate-msg'), '읽기 전용', '읽기 전용이라고 화면이 말한다');

      for (const w of WRITES) {
        page.click(page.q(`.tab[data-tab="${w.pane}"]`));
        if (w.setup) await w.setup(page);
        const before = server.calls.length;
        const btn = w.press(page);
        if (!btn) { check.ok(false, `${w.name}: 누를 버튼을 찾지 못했다`); continue; }
        btn.click();
        await page.settle();
        const [method, path] = w.endpoint.split(' ');
        const sent = server.calls.slice(before).some((c) => c.method === method && c.path.startsWith(path));
        check.ok(!sent, `${w.name}: 차단됐다 (${w.endpoint} 미발생)`);
      }
      const alerts = page.dialogs.filter((d) => d.kind === 'alert' && d.message.includes('Safe Mode'));
      check.gte(alerts.length, WRITES.length - 2, '차단될 때마다 이유를 알려준다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'safety.safe-mode.reads-still-work',
    title: '쓰기를 막아도 조회는 그대로 된다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      page.check('cfg-write-enable', false);

      page.click(page.q('.tab[data-tab="ops"]'));
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      page.click(page.q('button[onclick="loadDashboard()"]'));
      await page.settle();
      check.ok(server.lastCall('GET', '/review-queue'), '검수 큐 조회는 된다');
      check.ok(server.lastCall('GET', '/admin/dashboard'), '관제 조회는 된다');
      check.data.eq(page.qa('#queue .q-item').length, 3, '목록이 정상으로 그려진다');
      return page;
    },
  },

  {
    id: 'safety.profile.customer-hides-factory-cards',
    title: '고객사 프로파일에서는 모델 공장 전용 카드가 사라진다',
    why: '한 벌의 콘솔을 두 곳에 서빙한다 — 회원사 관리자에게 지재원 전용 기능이 보이면 눌러도 0건이 나온다',
    needsMock: true,
    async run({ server, check }) {
      const { FIXTURES } = await import('../lib/server.mjs');
      server.overrides['GET /healthz'] = { ...FIXTURES['GET /healthz'], deploy_profile: 'onprem-local' };

      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      const factory = page.qa('[data-profile="full-train"]');
      check.gte(factory.length, 2, '모델 공장 전용 카드가 존재는 한다');
      for (const el of factory) check.ok(!page.visible(el), `숨겨졌다: ${el.querySelector('.ttl')?.textContent?.trim() || el.id}`);
      check.ok(page.visible('profile-banner'), '어떤 서버인지 배너가 뜬다');
      check.includes(page.text('profile-banner'), '고객사', '고객사 시스템이라고 말한다');

      // 탭을 눌러도 숨김이 되살아나면 안 된다(인라인 style 로 숨기던 시절의 회귀)
      page.click(page.q('.tab[data-tab="train"]'));
      page.click(page.q('.tab[data-tab="review"]'));
      for (const el of factory) check.ok(!page.visible(el), `탭 전환 후에도 숨겨져 있다: ${el.id || 'card'}`);
      return page;
    },
  },

  {
    id: 'safety.profile.factory-shows-all',
    title: '지재원 프로파일에서는 전 구간 카드가 보이고 배너가 그렇게 말한다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      check.includes(page.text('profile-banner'), '모델 공장', '지재원 모델 공장이라고 말한다');
      check.includes(page.text('profile-banner'), 'noop', 'LLM 이 noop 이면 그 결과가 0건이라고 미리 알린다');
      // 프로파일 숨김과 탭 숨김을 섞지 않으려고, 각 카드를 자기 탭에서 확인한다.
      for (const el of page.qa('[data-profile="full-train"]')) {
        page.click(page.q(`.tab[data-tab="${el.dataset.pane}"]`));
        check.ok(page.visible(el), `${el.dataset.pane} 탭에서 모델 공장 전용 카드가 보인다`);
        check.ok(!el.classList.contains('pf-hidden'), '프로파일 숨김 클래스가 붙지 않았다');
      }
      return page;
    },
  },

  {
    id: 'safety.server-config.no-client-side-threshold',
    title: '검수 여부는 서버 판정만 쓴다 — 화면에 임계 입력칸이 없다',
    why: [
      '종전 화면에는 「목록 강조 기준」 입력칸이 있었고 「색칠만, 판정 무관」이라 적혀 있었지만,',
      '실제로는 confidence < 그 값 으로 검수/자동 배지를 계산했다. 서버 라우팅 게이트는 14개인데',
      '(services/review_reasons.py) 그중 신뢰도 게이트 하나만 흉내 낸 것이라, agreement-gate 로',
      '검수에 간 문서가 화면에는 「자동」으로 떴다. 칸을 다시 넣으면 그 거짓말이 돌아온다.',
    ].join(' '),
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      check.ok(!page.$('gate'), '화면 임계 입력칸이 없다');
      check.ok(!page.$('srv-gate-v'), '읽기 전용 서버 임계 표시도 없다');

      // 신뢰도가 높아도 서버가 needs_review 라고 하면 화면은 「검수」로 말해야 한다.
      server.overrides['POST /classify'] = {
        inference_id: '00000000-0000-4000-8000-0000000000aa',
        doc_id: 'gate-test-1', label: 'S2', confidence: 0.98, scores: {},
        model_version: 'v-test', elapsed_ms: 12,
        status: 'needs_review',
        warnings: ['agreement-gate: rule S1 vs model S2'],
      };
      page.click(page.q('.tab[data-tab="ops"]'));
      page.set('cl-docid', 'gate-test-1');
      page.set('cl-body', '판정 대상 본문');
      page.click(page.$('btn-classify'));
      await page.settle();

      check.includes(page.text('cl-result'), '검수 필요',
        '신뢰도 98% 여도 서버가 needs_review 면 「검수 필요」로 말한다');
      check.includes(page.text('queue'), '검수', '큐 배지도 서버 판정을 따른다');
      return page;
    },
  },

  {
    id: 'safety.actor.identity-on-every-write',
    writes: true,
    title: '상태를 바꾸는 요청에는 행위자가 반드시 실린다',
    why: '행위자 없이 나가면 감사 로그에 신원이 안 남는다 — 감리의 핵심 항목이다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      page.set('cfg-user', '검수자홍길동');
      page.set('cfg-role', 'reviewer');

      page.click(page.q('.tab[data-tab="ops"]'));
      page.click(page.q('button[onclick="loadReviewQueue()"]'));
      await page.settle();
      page.click(page.q('#queue button[onclick="doConfirm(0)"]'));
      await page.settle();

      page.click(page.q('.tab[data-tab="config"]'));
      page.set('kw-n-keyword', '신규키워드');
      page.click(page.q('button[onclick="createKeyword()"]'));
      await page.settle();

      for (const [m, p] of [['POST', '/confirm'], ['POST', '/admin/keywords']]) {
        const c = server.lastCall(m, p);
        check.eq(c?.body?.actor?.user_id, '검수자홍길동', `${p}: 행위자 이름이 실렸다`);
        check.eq(c?.body?.actor?.role, 'reviewer', `${p}: 행위자 역할이 실렸다`);
        check.eq(c?.headers['x-actor-role'], 'reviewer', `${p}: 헤더에도 역할이 실렸다`);
      }
      return page;
    },
  },
];
