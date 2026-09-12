/* 15. 골든셋 검수 · 서명 화면 — 사람이 실제로 서명하는 자리.
 *
 * 왜 여기에 붙였나. 이 화면은 서버가 파이썬으로 그려 내려 주는 것이라 하니스가 못 띄웠고,
 * 대신 파이썬 시험(test_signoff_screen_runs.py)이 **없는 id 에도 가짜 요소를 만들어 주는
 * DOM 스텁**으로 스크립트를 돌리고 있었다 — 이 하니스가 있는 이유가 바로 그 방식이
 * 2026-08-23 결함(지운 요소를 만지다가 초기화가 통째로 멈춤)을 통과시켰기 때문이다.
 * 이제 같은 화면을 jsdom 으로 띄운다: 없는 id 는 null 이고, 그 순간 터진다.
 *
 * 주소는 `/api/v1/golden/jobs/{id}/signoff.html` 과 `.../review.html` 둘이지만 화면은
 * 하나다(2026-08-18 통합). 같음은 파이썬 test_review_signoff_cross_link 가 잠근다 —
 * 하니스는 한 판을 두 주소로 서빙하므로 여기서 같음을 확인해 봐야 순환이다.
 *
 * 화면이 서버에 묻는 것은 셋뿐이다.
 *   GET /golden/candidates/session          누가 서명하는가(로그인 쿠키의 sub)
 *   GET {POST_URL}/preflight                제출하면 무엇이 막히는가
 *   POST {POST_URL}                          서명 제출
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

// scripts/dump_console_html.py 가 이 값으로 판을 뜬다. 바뀌면 여기도 같이 바뀌어야 한다.
const JOB = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
const SIGNOFF_PATH = `/api/v1/golden/jobs/${JOB}/signoff.html`;
const POST_URL = `/api/v1/golden/jobs/${JOB}/signoff`;
const DEC_KEY = `koipa.signoff.${POST_URL}`;

const IDS = {
  ts: 'E2E-SIGN-TS-1',
  s1: 'E2E-SIGN-S1-1',
  s2: 'E2E-SIGN-S2-1',
  s3: 'E2E-SIGN-S3-1',
  pending: 'E2E-SIGN-PEND-1',
};

async function signoff(server, opts = {}) {
  const page = await openPage(server, SIGNOFF_PATH, { query: '?t=e2e-token', ...opts });
  await page.settle();
  return page;
}

/** 후보 카드의 결정 라디오를 사람이 누르듯 누른다. */
function decide(page, docId, value) {
  const el = page.q(`input[name="dec-${docId}"][value=${value}]`);
  if (!el) throw new Error(`결정 라디오가 없다: ${docId} ${value}`);
  el.click();
  return el;
}

/** 등급변경 드롭다운을 고른다 — 라디오를 누르지 않고 이것만 건드리는 경로. */
function pickGrade(page, docId, grade) {
  const sel = page.q(`.gsel[data-id="${docId}"]`);
  if (!sel) throw new Error(`등급 드롭다운이 없다: ${docId}`);
  sel.value = grade;
  sel.dispatchEvent(new page.win.Event('change', { bubbles: true }));
  return sel;
}

const card = (page, docId) => page.q(`.scard[data-id="${docId}"]`);

export const scenarios = [
  {
    id: 'signoff.boot.candidates-are-drawn',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '서명 화면이 뜨고 후보 카드가 실제로 그려진다',
    why: '2026-08-18 에 mdInline 이 빠져 화면이 한 건도 못 그렸는데 콘솔 시험 423건이 통과했다',
    async run({ server, check }) {
      const page = await signoff(server);

      assertNoScriptErrors(check, page);
      const cards = page.qa('#grid .scard');
      check.eq(cards.length, 6, '후보 6건(서명 대상 5 · 보기 전용 1)이 모두 카드로 그려졌다');
      for (const [key, docId] of Object.entries(IDS)) {
        check.ok(card(page, docId), `${key} 카드가 있다 (${docId})`);
      }
      // 본문이 비면 검수자는 읽을 것이 없다 — 카드 껍데기만 그려진 것과 구분해야 한다.
      const body = page.text(page.q(`.scard[data-id="${IDS.ts}"] .docbody`));
      check.includes(body, '배합비', '문서 본문이 카드 안에 들어 있다');
      check.includes(page.text('deccount'), '0 / 5', '진행 숫자는 서명 대상 5건 기준이다');
      return page;
    },
  },

  {
    id: 'signoff.pending.is-visible-but-has-no-form',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '합의 미달 후보는 보이되 결정 폼이 없다',
    why: '폼이 있으면 "왜 눌러도 안 되나" 가 되고, 빼 버리면 "왜 안 보이나" 가 된다',
    async run({ server, check }) {
      const page = await signoff(server);

      const pend = card(page, IDS.pending);
      check.ok(pend, '보기 전용 후보도 목록에 있다');
      check.includes(pend?.className || '', 'pending', '보기 전용으로 표시된다');
      check.eq(page.q(`input[name="dec-${IDS.pending}"]`), null, '결정 라디오가 붙지 않았다');
      check.ok(page.q(`input[name="dec-${IDS.ts}"]`), '서명 대상에는 결정 라디오가 있다');
      check.includes(page.text('decbreak'), '합의 미달 1건', '몇 건이 보기 전용인지 적혀 있다');
      return page;
    },
  },

  {
    id: 'signoff.identity.comes-from-session',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '서명자 신원은 세션에서 온다 — 화면이 이름을 받지 않는다',
    why: '서명자가 자칭이 될 수 있던 경로를 2026-08-17 에 막았다. 화면에 이름 입력칸이 있으면 안 된다',
    async run({ server, check }) {
      const page = await signoff(server);

      check.ok(server.exactCall('GET', '/golden/candidates/session'), '세션에 신원을 물었다');
      check.data.includes(page.text('who'), '지재원관리자', '응답의 actor_id 를 그대로 보여준다');
      check.eq(page.$('submit').disabled, false, '신원이 있으면 제출을 막지 않는다');
      // 이름·키를 손으로 넣는 칸이 있으면 자칭 서명이 다시 열린다.
      const typed = page.qa('input[type=text], input[type=password]')
        .filter((el) => /reviewer|name|이름|key|키/i.test(`${el.id} ${el.placeholder || ''}`));
      check.eq(typed.length, 0, '검수자 이름·API 키를 입력받는 칸이 없다');
      return page;
    },
  },

  {
    id: 'signoff.identity.401-blocks-signing',
    needsMock: true,
    title: '로그인이 없으면 제출을 막고 어디로 가야 하는지 말해 준다',
    why: '링크의 ?t= 는 화면을 여는 열쇠일 뿐 신원이 아니다 — 열리는데 못 누르는 자리에서 멈춘다',
    async run({ server, check }) {
      server.faults.push({ path: '/golden/candidates/session', status: 401, body: { detail: 'no session' } });
      const page = await signoff(server);

      check.includes(page.text('who'), '로그인 필요', '왜 못 하는지 그 자리에 적힌다');
      check.includes(page.html('who'), '/api/v1/golden/candidates/login.html', '로그인 화면 주소를 준다');
      check.eq(page.$('submit').disabled, true, '제출 버튼이 잠긴다');
      check.eq(server.countCalls('POST', '/golden/jobs'), 0, '서명이 나가지 않는다');
      return page;
    },
  },

  {
    id: 'signoff.preflight.summary-comes-from-server',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '서명 전 점검이 서버 기준 수치를 보여준다',
    why: '종전에는 무엇이 막을지가 제출한 뒤에야 드러났다',
    async run({ server, check }) {
      const page = await signoff(server);

      const pf = server.exactCall('GET', `/golden/jobs/${JOB}/signoff/preflight`);
      check.ok(pf, '제출 전에 점검을 물었다');
      check.ok(page.visible('preflight'), '점검 결과 띠가 보인다');
      check.data.includes(page.text('preflight'), '남은 4건', '남은 건수를 서버 응답에서 가져온다');
      check.eq(page.$('submit').disabled, false, '막을 것이 없으면 제출은 열려 있다');
      return page;
    },
  },

  {
    id: 'signoff.preflight.blocking-locks-submit',
    needsMock: true,
    title: '점검에서 막힌 항목이 있으면 제출이 잠기고 사유가 뜬다',
    why: '223 실측 — 저장 폴더 권한 때문에 120건을 다 고르고 제출한 뒤에야 500 을 봤다',
    async run({ server, check }) {
      server.overrides[`GET /golden/jobs/{job_id}/signoff/preflight`] = {
        job_id: JOB,
        ok: false,
        blocking: [{
          code: 'signoff_store_unwritable',
          message: '서명을 저장할 수 없습니다(승격 기록).',
          detail: 'datasets/golden_review/ff5a822c 에 쓸 수 없습니다(uid 불일치)',
        }],
        warnings: [],
        candidates: { total: 5, already_locked: 0, already_rejected: 0, remaining: 5 },
        reviewer: { reviewer_id: 'kl-admin-test', will_be_rejected: false, reason: '' },
        publish: { live_path: '', configured: false },
        readiness: { ready: false, per_grade: {}, missing: [], reason: '' },
      };
      const page = await signoff(server);

      check.includes(page.text('preflight'), '제출할 수 없습니다', '막혔다고 말한다');
      check.includes(page.text('preflight'), 'uid 불일치', '무엇 때문인지 사유를 그대로 보여준다');
      check.eq(page.$('submit').disabled, true, '제출 버튼이 잠긴다');
      return page;
    },
  },

  {
    id: 'signoff.decision.grade-dropdown-updates-screen',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '등급 드롭다운만 건드려도 카운터와 카드가 함께 움직인다',
    why: '2026-08-21 — 결정은 저장되는데 화면이 하나도 안 움직여 검수자가 안 눌린 줄 알고 다시 눌렀다',
    async run({ server, check }) {
      const page = await signoff(server);

      pickGrade(page, IDS.s1, 'TS');
      check.includes(page.text('deccount'), '1 / 5', '진행 숫자가 올라간다');
      check.includes(card(page, IDS.s1)?.className || '', 'decided', '카드가 결정됨으로 강조된다');
      const radio = page.q(`input[name="dec-${IDS.s1}"][value=change]`);
      check.eq(radio?.checked, true, '등급변경 라디오가 함께 켜진다');
      check.includes(page.text('decbreak'), 'TS 1', '바꾼 등급으로 승격 예정에 잡힌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'signoff.decision.reject-is-not-counted-as-promotion',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '거부는 결정으로는 세되 승격 예정에서는 빠진다',
    why: '화면 숫자와 서버의 locked_by_grade 가 다른 규칙으로 세면 결과가 어긋난다',
    async run({ server, check }) {
      const page = await signoff(server);

      decide(page, IDS.ts, 'approve');
      decide(page, IDS.s2, 'reject');
      check.includes(page.text('deccount'), '2 / 5', '결정 건수는 둘 다 센다');
      check.includes(page.text('decbreak'), 'TS 1', '승인한 TS 는 승격 예정이다');
      check.includes(page.text('decbreak'), 'S2 0', '거부한 S2 는 승격 예정에서 빠진다');
      check.includes(card(page, IDS.s2)?.className || '', 'rejected', '거부 카드로 표시된다');
      return page;
    },
  },

  {
    id: 'signoff.filter.todo-narrows-to-undecided',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '「미결정」 필터가 남은 것만 남기고, 보기 전용은 거기 끼지 않는다',
    why: '결정될 수 없는 후보를 미결정에 넣으면 아무리 눌러도 줄지 않는 잔여가 생긴다',
    async run({ server, check }) {
      const page = await signoff(server);

      decide(page, IDS.ts, 'approve');
      page.click(page.q('.filter-btn[data-f="state"][data-v="todo"]'));
      const shown = page.qa('#grid .scard').map((el) => el.getAttribute('data-id'));
      check.eq(shown.includes(IDS.ts), false, '결정한 것은 빠진다');
      check.eq(shown.includes(IDS.pending), false, '보기 전용 후보는 미결정에 끼지 않는다');
      check.eq(shown.length, 4, '남은 서명 대상 4건만 보인다');
      check.includes(page.text('fTodo'), '미결정 4', '버튼에 잔여를 적어 둔다');
      return page;
    },
  },

  {
    id: 'signoff.submit.sends-decided-only-with-session-identity',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    writes: true,
    title: '제출하면 결정한 것만, 세션 신원과 함께 나간다',
    why: '미결정까지 실려 나가면 고르지 않은 후보가 서명된다',
    async run({ server, check }) {
      const page = await signoff(server);

      decide(page, IDS.ts, 'approve');
      pickGrade(page, IDS.s1, 'TS');
      decide(page, IDS.s2, 'reject');
      page.click('submit');
      await page.settle();

      const call = server.exactCall('POST', `/golden/jobs/${JOB}/signoff`);
      check.ok(call, '서명이 서버로 나갔다');
      const body = call?.body || {};
      const sent = (body.decisions || []).map((d) => d.doc_id).sort();
      check.eq(sent.length, 3, '결정한 3건만 실렸다');
      check.eq(sent.includes(IDS.s3), false, '고르지 않은 후보는 빠졌다');
      check.eq(sent.includes(IDS.pending), false, '보기 전용 후보는 빠졌다');
      const changed = (body.decisions || []).find((d) => d.doc_id === IDS.s1);
      check.eq(changed?.decision, 'change', '등급변경으로 실렸다');
      check.eq(changed?.grade, 'TS', '바꾼 등급이 함께 실렸다');
      check.data.eq(body.actor?.user_id, '지재원관리자', '서명자는 세션 신원이다');
      check.eq(body.publish, false, '라이브 반영은 체크하지 않으면 false 다');
      return page;
    },
  },

  {
    id: 'signoff.submit.preview-says-live-is-unchanged',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    writes: true,
    title: '라이브 반영을 안 켜고 낸 제출은 미리보기라고 말하고, 결정은 남는다',
    why: '조용히 미리보기로 끝나면 검수자는 반영된 줄 알거나 처음부터 다시 한다',
    async run({ server, check }) {
      const page = await signoff(server);

      decide(page, IDS.ts, 'approve');
      page.click('submit');
      await page.settle();

      check.includes(page.text('result'), '미리보기', '라이브가 안 바뀌었다고 적는다');
      check.includes(page.text('result'), '결정은 그대로 남아 있습니다', '다시 하지 않아도 된다고 말한다');
      check.data.includes(page.text('result'), '서명자: kl-admin-test', '실제 기록된 서명자를 보여준다');
      check.ok(page.q(`input[name="dec-${IDS.ts}"][value=approve]`)?.checked, '고른 결정이 화면에 남아 있다');
      check.eq(page.$('submit').disabled, false, '제출 버튼이 다시 열린다');
      return page;
    },
  },

  {
    id: 'signoff.submit.publish-checkbox-goes-out',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    writes: true,
    title: '라이브 반영을 켜면 그 값이 함께 나간다',
    why: '체크는 했는데 요청에 안 실리면 아무도 모르게 미리보기로 끝난다',
    async run({ server, check }) {
      const page = await signoff(server);

      decide(page, IDS.ts, 'approve');
      page.check('publish', true);
      page.click('submit');
      await page.settle();

      const call = server.exactCall('POST', `/golden/jobs/${JOB}/signoff`);
      check.eq(call?.body?.publish, true, 'publish=true 로 나갔다');
      check.eq(server.countCalls('GET', `/golden/jobs/${JOB}/signoff/preflight`) >= 2, true,
        '제출 뒤 점검을 다시 읽는다');
      return page;
    },
  },

  {
    id: 'signoff.submit.nothing-decided-is-refused-here',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '아무것도 고르지 않고 제출하면 화면에서 막는다',
    why: '빈 서명이 서버까지 가면 왕복 한 번을 버리고 사유도 흐려진다',
    async run({ server, check }) {
      const page = await signoff(server);

      page.click('submit');
      await page.settle();
      check.includes(page.text('result'), '결정한 후보가 없습니다', '왜 안 되는지 말해 준다');
      check.eq(server.countCalls('POST', `/golden/jobs/${JOB}/signoff`), 0, '요청이 나가지 않는다');
      return page;
    },
  },

  {
    id: 'signoff.submit.failure-is-visible',
    needsMock: true,
    writes: true,
    title: '서명 제출이 실패하면 사유가 화면에 남는다',
    why: '실패가 조용하면 검수자는 서명된 줄 안다',
    async run({ server, check }) {
      server.faults.push({ path: `/golden/jobs/${JOB}/signoff`, method: 'POST', status: 403,
        body: { detail: '이 계정으로는 서명할 수 없습니다' } });
      const page = await signoff(server);

      decide(page, IDS.ts, 'approve');
      page.click('submit');
      await page.settle();

      check.includes(page.text('result'), '실패(403)', '실패와 상태코드를 적는다');
      check.includes(page.text('result'), '서명할 수 없습니다', '서버가 준 사유를 그대로 보여준다');
      check.eq(page.$('submit').disabled, false, '버튼이 잠긴 채로 남지 않는다');
      return page;
    },
  },

  {
    id: 'signoff.restore.decisions-survive-a-reload',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '하던 결정은 창을 닫았다 열어도 복원되고, 지울 수 있다',
    why: '120건짜리 회차에서 60건 하다 창을 닫으면 60건을 다시 눌러야 했다',
    async run({ server, check }) {
      const page = await signoff(server, {
        storage: { [DEC_KEY]: JSON.stringify({ [IDS.ts]: { decision: 'approve' }, [IDS.s2]: { decision: 'reject' } }) },
      });

      check.ok(page.visible('restored'), '복원했다고 알려 준다');
      check.includes(page.text('restored'), '결정 2건', '몇 건을 복원했는지 적는다');
      check.includes(page.text('deccount'), '2 / 5', '복원한 결정이 진행 숫자에 반영된다');
      check.eq(page.q(`input[name="dec-${IDS.ts}"][value=approve]`)?.checked, true, '카드에도 그대로 켜져 있다');

      page.click('decclear');
      check.eq(page.visible('restored'), false, '지우면 알림이 사라진다');
      check.includes(page.text('deccount'), '0 / 5', '결정이 비워진다');
      check.eq(page.win.localStorage.getItem(DEC_KEY), null, '브라우저에 남은 것도 지운다');
      return page;
    },
  },

  {
    id: 'signoff.xss.embedded-document-does-not-execute',
    needsData: true,  // 실서버 모드 건너뜀 — 본보기 잡·평가 이력이 있어야 성립
    title: '후보 본문·문서번호에 든 HTML 은 글자로만 나온다',
    why: '<script id="data"> 블록이 문서 내용으로 조기 종료되면 저장형 XSS 가 된다',
    async run({ server, check }) {
      const page = await signoff(server);

      check.eq(page.win.__pwned, undefined, 'doc_id 의 스크립트가 실행되지 않았다');
      check.eq(page.win.__pwned2, undefined, '본문의 스크립트가 실행되지 않았다');
      check.includes(page.bodyText(), 'onerror', '문서번호는 글자로 그려진다');
      check.eq(page.qa('#grid img').length, 0, '본문이 태그로 살아나지 않았다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
