/* 12. 검증문서 후보 관리(manage.html) — 서버가 파이썬으로 렌더해 내려 주는 화면.
 *
 * 콘솔 6면 중 이 한 면만 **실행 시험이 0건**이었다(공용 네비 시험이 링크 목록만 봤다).
 * 정적 파일이 아니라 node 하니스가 열 방법이 없었기 때문이다. 이제 렌더 결과를 떠 두고
 * (scripts/dump_console_html.py) 그것을 서빙해 실제로 띄운다. 떠 둔 판이 낡으면
 * tests/test_e2e_console_rendered_snapshot.py 가 깨진다.
 *
 * 이 화면은 포털 JWT 세션으로만 열리므로(require_role) 브라우저는 쿠키를 싣는다 —
 * 하니스는 화면 동작을 보는 것이 목적이라 인증은 서버 쪽 시험에 맡긴다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

const URL = '/api/v1/golden/candidates/manage.html';

async function manage(server, opts = {}) {
  const page = await openPage(server, URL, opts);
  await page.settle();
  return page;
}

export const scenarios = [
  {
    id: 'manage.boot.renders-everything',
    title: '후보 관리 화면이 오류 없이 뜨고 목록·요약·품질·원장이 모두 그려진다',
    why: '이 화면은 실행 시험이 하나도 없어, 통째로 죽어도 아무도 몰랐다',
    async run({ server, check }) {
      const page = await manage(server);

      assertNoScriptErrors(check, page);
      check.includes(page.text('session'), '지재원관리자', '인증된 신원을 서버에서 받아 표시한다');
      check.excludes(page.text('session'), '확인 중', '로그인 확인이 끝났다');

      check.data.eq(page.qa('#rows .candidate').length, 3, '후보 목록이 그려졌다');
      check.data.includes(page.text('rows'), 'PGC-0001', '문서 id 가 보인다');
      check.data.includes(page.text('rows'), 'EUV 공정 레시피', '제목이 보인다');
      check.includes(page.html('rows'), 'pill', '상태 배지가 붙었다');

      check.data.includes(page.text('kpis'), '전체 후보', 'KPI 카드가 그려졌다');
      check.data.eq(page.text('readiness'), '1/3', '확정 진행도가 표시된다');
      check.data.includes(page.text('flash'), '3건을 불러왔습니다', '몇 건을 읽었는지 알려준다');
      check.data.includes(page.text('flash'), '폐기 1건은 목록에서 뺐습니다', '목록에서 뺀 것을 숨기지 않고 밝힌다');

      check.includes(page.text('qualityBody'), '길이가 등급을 알려주는 정도', '품질 지표가 그려졌다');
      check.data.gte(page.qa('#ledgerRows .candidate').length, 1, '결정 원장이 그려졌다');
      return page;
    },
  },

  {
    id: 'manage.list.filters-go-to-server',
    title: '필터를 넣고 누르면 그 조건이 그대로 질의로 나간다',
    async run({ server, check }) {
      const page = await manage(server);
      page.set('status', 'deferred');
      page.set('grade', 'S3');
      page.set('origin', 'public_real');
      page.set('query', 'PGC-0003');
      page.set('review_batch', '2026-08-A');
      page.click('filter');
      await page.settle();

      // 하위 경로(/golden/candidates/decisions)가 따로 있어 정확 일치로 집는다.
      const call = server.exactCall('GET', '/golden/candidates');
      check.ok(call, 'GET /golden/candidates 를 다시 불렀다');
      for (const q of ['status=deferred', 'grade=S3', 'origin=public_real', 'query=PGC-0003', 'review_batch=2026-08-A']) {
        check.includes(call?.path || '', q, `질의에 ${q} 가 실렸다`);
      }
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.detail.opens-with-text-and-history',
    title: '후보를 클릭하면 상세가 열리고 본문·메타·결정 이력이 나온다',
    needsData: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click(page.q('#rows .candidate'));
      await page.settle();

      check.ok(server.lastCall('GET', '/golden/candidates/PGC-0001'), '상세를 조회했다');
      check.ok(page.q('#detail')?.classList.contains('show'), '상세 영역이 열렸다');
      check.includes(page.text('detailTitle'), 'PGC-0001', '문서 id 가 제목에 있다');
      check.includes(page.text('metas'), 'SHA-256', '문서 해시가 표시된다');
      check.includes(page.text('metas'), '제안: TS', '제안 등급이 보인다');
      check.includes(page.text('scope'), '접근 금지', '주장 범위가 보인다');
      check.includes(page.text('document'), 'EUV 노광 공정', '원문이 들어갔다');
      check.includes(page.html('documentRendered'), '<h1', '읽기 좋게 보기가 마크다운을 렌더했다');
      check.includes(page.text('history'), 'reopen', '결정 이력이 보인다');
      // 합성 후보에는 출처 기록 칸을 띄우지 않는다
      check.ok(!page.visible('provBox'), '합성 후보에는 출처 기록 칸이 안 뜬다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.detail.doc-view-toggle',
    title: '「읽기 좋게」와 「원문 그대로」가 서로 전환된다',
    why: '검수 판단은 원문 기준이라 두 보기가 실제로 갈려야 한다',
    needsData: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click(page.q('#rows .candidate'));
      await page.settle();

      check.ok(page.visible('documentRendered'), '처음에는 읽기 좋게 보기가 보인다');
      check.ok(!page.visible('document'), '원문 보기는 숨어 있다');

      page.click('viewRaw');
      check.ok(page.visible('document'), '원문 보기로 바뀐다');
      check.ok(!page.visible('documentRendered'), '읽기 좋게 보기는 숨는다');
      check.eq(page.$('viewRaw')?.getAttribute('aria-pressed'), 'true', '눌린 상태가 표시된다');

      page.click('viewRendered');
      check.ok(page.visible('documentRendered'), '다시 읽기 좋게 보기로 돌아온다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.decision.reason-required',
    title: '등급 변경·보류·폐기에는 사유가 없으면 저장되지 않는다',
    why: '사유 없는 결정은 원장에 남아도 감사에서 근거가 되지 못한다',
    needsData: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click(page.q('#rows .candidate'));
      await page.settle();

      page.set('action', 'change');
      check.ok(page.visible('gradeWrap'), '등급 변경을 고르면 등급 칸이 나타난다');
      page.set('reason', '   ');
      page.click('save');
      await page.settle();

      check.eq(server.countCalls('POST', '/golden/candidates/PGC-0001/decision'), 0, '사유가 없으면 보내지 않는다');
      check.includes(page.text('flash'), '사유가 필요', '무엇이 없어서 막혔는지 말한다');
      check.includes(page.$('flash')?.className || '', 'error', '오류 표시로 뜬다');
      return page;
    },
  },

  {
    id: 'manage.decision.saves-with-grade-and-reason',
    title: '등급과 사유를 넣고 저장하면 그대로 서버로 나가고 화면이 갱신된다',
    needsData: true,
    writes: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click(page.q('#rows .candidate'));
      await page.settle();

      page.set('action', 'change');
      page.set('finalGrade', 'S1');
      page.set('reason', '본문 기준 핵심 기술정보로 판단');
      page.click('save');
      await page.settle();

      const call = server.lastCall('POST', '/golden/candidates/PGC-0001/decision');
      check.ok(call, '결정이 서버로 나갔다');
      check.eq(call?.body?.action, 'change', '결정 종류가 실렸다');
      check.eq(call?.body?.grade, 'S1', '확정 등급이 실렸다');
      check.includes(call?.body?.reason || '', '핵심 기술정보', '사유가 실렸다');
      check.gte(server.countCalls('GET', '/golden/candidates/PGC-0001'), 2, '저장 뒤 상세를 다시 읽는다');
      // ⚠ '저장 완료' 안내는 곧바로 show() → load() 가 목록 문구로 덮어쓴다. 화면에 남는
      //   확인은 갱신된 상세뿐이라, 여기서는 그 갱신을 확인한다(문구는 잠그지 않는다).
      check.ok(page.q('#detail')?.classList.contains('show'), '상세가 열린 채로 갱신된다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.management.defaults-to-unknown',
    title: '비밀관리성(M) 칸이 상세에 있고, 기본이 「확인 안 됨」이다',
    why: '「확인 안 됨」과 「전 임직원 열람」은 M 을 정반대로 만든다 — 뭉치면 S1 이 사라지거나 미탐이 열린다',
    needsData: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click(page.q('#rows .candidate'));
      await page.settle();

      check.ok(page.visible('mgmtWrap'), '비밀관리성 칸이 상세에 있다');
      check.eq(page.$('secMarking')?.value, '', '보안표시 기본은 확인 안 됨이다');
      check.eq(page.$('accScope')?.value, '', '접근범위 기본은 확인 안 됨이다');
      check.includes(page.text('mgmtState'), '확인 안 됨', '지금 M 이 미확인임을 말한다');
      check.includes(page.text('mgmtState'), 'S1', '이 상태에서 무엇이 불가능한지 말한다');
      check.includes(page.text('mgmtWrap'), '모르면', '추측하지 말라고 말한다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.management.sends-marking-and-scope',
    title: '보안표시·접근범위를 고르면 결정과 함께 전송된다',
    why: 'M 은 등급 결정의 입력이라 사유와 같은 이벤트에 실려야 근거가 재구성된다',
    needsData: true,
    writes: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click(page.q('#rows .candidate'));
      await page.settle();

      page.set('action', 'defer');
      page.set('reason', '접근권한 확인 후 재검토');
      page.set('secMarking', 'confidential');
      page.set('accScope', 'department');
      page.click('save');
      await page.settle();

      const call = server.lastCall('POST', '/golden/candidates/PGC-0001/decision');
      check.ok(call, '결정이 서버로 나갔다');
      check.eq(call?.body?.security_marking, 'confidential', '보안표시가 실렸다');
      check.eq(call?.body?.access_scope, 'department', '접근범위가 실렸다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.management.unknown-is-not-sent',
    title: '「확인 안 됨」으로 두면 그 값을 보내지 않는다',
    why: '빈 값을 보내면 서버가 그것을 입력으로 읽어 M 을 덮어쓸 수 있다 — 모름은 침묵이어야 한다',
    needsData: true,
    writes: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click(page.q('#rows .candidate'));
      await page.settle();

      page.set('action', 'defer');
      page.set('reason', '판단 보류');
      page.click('save');
      await page.settle();

      const call = server.lastCall('POST', '/golden/candidates/PGC-0001/decision');
      check.ok(call, '결정이 서버로 나갔다');
      check.eq(call?.body?.security_marking, undefined, '보안표시는 안 실린다');
      check.eq(call?.body?.access_scope, undefined, '접근범위는 안 실린다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.decision.failure-visible',
    title: '결정 저장이 실패하면 사유가 화면에 남는다',
    needsMock: true,
    needsData: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click(page.q('#rows .candidate'));
      await page.settle();

      server.faults.push({ path: '/golden/candidates/PGC-0001/decision', status: 409, body: { detail: '이미 확정된 문서입니다' } });
      page.set('action', 'defer');
      page.set('reason', '보류 사유');
      page.click('save');
      await page.settle();

      check.includes(page.text('flash'), '저장 실패', '실패했다고 말한다');
      check.includes(page.text('flash'), '이미 확정된 문서', '서버가 준 사유가 그대로 보인다');
      check.includes(page.$('flash')?.className || '', 'error', '오류 표시로 뜬다');
      return page;
    },
  },

  {
    id: 'manage.provenance.partial-record',
    title: '실문서 후보에는 출처 기록 칸이 뜨고, 미완이면 그렇게 표시된다',
    why: '실문서 74건 중 62건이 사용 권한 근거가 비어 있었는데 화면에 채울 칸이 없었다',
    needsMock: true,
    needsData: true,
    writes: true,
    async run({ server, check }) {
      const { FIXTURES } = await import('../lib/server.mjs');
      // 상세 본보기는 한 벌뿐이라, 실문서 후보를 볼 때는 그 문서로 바꿔 준다.
      const real = FIXTURES['GET /golden/candidates'].candidates[2];
      server.overrides['GET /golden/candidates/{doc_id}'] = {
        ...real,
        text: '공개 보도자료 모음. 기관 게시판에 공개된 자료다.',
        decision_history: [{ action: 'defer', status: 'deferred', reason: '출처 권한 근거 확인 필요', actor_id: '지재원관리자', decided_at: '2026-08-23T01:20:00Z' }],
      };

      const page = await manage(server);
      page.click(page.qa('#rows .candidate')[2]);   // 3번째 = 공개 실문서(출처 미완)
      await page.settle();

      check.ok(page.visible('provBox'), '실문서에는 출처 기록 칸이 뜬다');
      check.includes(page.text('provStatus'), '미완', '사용 권한 근거가 없다고 표시된다');
      check.eq(page.$('provSrc')?.value, '기관 공개 게시판', '이미 적힌 원천 위치가 채워져 있다');
      check.eq(page.$('provBasis')?.value, '', '빈 것은 빈 채로 보인다');
      check.eq(page.$('provSave')?.disabled, false, '아직 미완이라 저장 버튼이 열려 있다');

      page.set('provBasis', '공공누리 등 공공저작물 이용허락');
      page.click('provSave');
      await page.settle();
      const call = server.lastCall('POST', '/golden/candidates/PGC-0003/provenance');
      check.ok(call, '출처 기록이 서버로 나갔다');
      check.includes(JSON.stringify(call?.body || {}), '공공누리', '채운 근거가 실려 나갔다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.upload.modal-and-validation',
    title: '업로드 모달이 열리고, 파일이 없으면 보내지 않는다',
    why: '파일이 없는데 요청을 보내면 서버가 422 로 되돌려 준다 — 화면에서 먼저 잡는다',
    async run({ server, check }) {
      const page = await manage(server);
      check.ok(!page.visible('modal'), '처음에는 모달이 닫혀 있다');
      page.click('openUpload');
      check.ok(page.visible('modal'), '업로드 모달이 열린다');

      // 파일 없이
      page.click('upload');
      await page.settle();
      check.eq(server.countCalls('POST', '/golden/candidates/upload'), 0, '파일이 없으면 안 보낸다');
      check.includes(page.text('upMsg') + page.text('flash'), '파일을 선택', '파일을 고르라고 말한다');

      // [2026-08-23] 출처·권한 근거는 더 이상 업로드에서 강제하지 않는다 — 게이트가
      // 등급 확정(decide)과 승격(promote_to_locked)으로 옮겨졌다. 화면이 그 사실을
      // 실제로 말하고 있는지 본다(안 그러면 사용자는 여전히 필수로 읽는다).
      const modalText = page.text('modal');
      check.includes(modalText, '(선택)', '두 칸이 선택 입력으로 표시된다');
      check.includes(modalText, '등급을 확정할 수 없습니다', '언제 막히는지를 모달이 말한다');

      page.click('cancelUpload');
      check.ok(!page.visible('modal'), '취소하면 모달이 닫힌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.upload.sends-without-provenance',
    title: '출처를 비워도 업로드는 나간다 — 막히는 자리는 등급 확정이다',
    why: '현관에서 막으니 평가셋이 보호된 게 아니라 등록 자체가 안 됐다(실측 2026-08-17 · 223: 실문서 74건 중 62건 미완)',
    writes: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click('openUpload');
      page.attachFile('file', { name: '운영절차서.docx' });
      page.set('upOrigin', 'organization_real');
      page.click('upload');
      await page.settle(6000);

      const call = server.lastCall('POST', '/golden/candidates/upload');
      check.ok(call, '출처가 비어 있어도 업로드가 나갔다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.upload.sends-origin-and-basis',
    title: '출처·근거를 채워 업로드하면 그 값이 함께 전송된다',
    writes: true,
    async run({ server, check }) {
      const page = await manage(server);
      page.click('openUpload');
      page.attachFile('file', { name: '운영절차서.docx' });
      page.set('upOrigin', 'organization_real');
      page.set('upSource', '품질관리/운영절차/2026');
      page.set('upBasisSel', '소유부서 검수용 사용 승인');
      page.click('upload');
      await page.settle(6000);

      const call = server.lastCall('POST', '/golden/candidates/upload');
      check.ok(call, '업로드가 나갔다');
      check.gte(call?.bytes, 100, '파일이 multipart 본문에 실렸다');
      check.eq(page.$('upload')?.disabled, false, '버튼이 잠긴 채 남지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.upload.custom-basis-toggle',
    title: '근거를 「직접 입력」으로 고르면 입력칸이 나타난다',
    async run({ server, check }) {
      const page = await manage(server);
      page.click('openUpload');
      check.ok(!page.visible('upBasis'), '처음에는 직접 입력칸이 숨어 있다');
      page.set('upBasisSel', '__custom__');
      check.ok(page.visible('upBasis'), '직접 입력을 고르면 칸이 나타난다');
      return page;
    },
  },

  {
    id: 'manage.ledger.filter',
    title: '결정 원장을 종류로 걸러 다시 읽는다',
    async run({ server, check }) {
      const page = await manage(server);
      page.set('ledgerFilter', 'defer');
      await page.settle();
      const call = server.lastCall('GET', '/golden/candidates/decisions');
      check.ok(call, '결정 원장을 조회했다');
      check.data.gte(page.qa('#ledgerRows .candidate').length, 1, '원장 행이 그려졌다');
      check.data.includes(page.text('ledgerRows'), '출처 권한 근거 확인 필요', '사유가 그대로 보인다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'manage.list.failure-visible',
    title: '목록 조회가 실패하면 그 사실이 화면에 보인다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, URL);
      server.faults.push({ path: '/golden/candidates', status: 403, body: { detail: '포털 로그인이 필요합니다' } });
      await page.settle();
      page.click('refresh');
      await page.settle();

      check.includes(page.text('flash'), '불러오기 실패', '실패했다고 말한다');
      check.includes(page.$('flash')?.className || '', 'error', '오류 표시로 뜬다');
      check.eq(page.errors.length, 0, '스크립트가 죽지 않았다', page.errors.map((e) => e.message).join(' | '));
      return page;
    },
  },
];
