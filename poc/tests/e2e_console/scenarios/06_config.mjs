/* 6. 설정 — 등급체계(FUN-005-①)와 태깅 키워드(FUN-023-④).
 *
 * 도입 시 한 번 정하고 거의 안 바꾸는 화면이라, 정작 바꿔야 할 때 안 되는 것이 늦게 발견된다.
 * 편집 → 저장 → 다른 화면(합성·키워드 필터)의 등급 목록까지 따라 바뀌는지 함께 본다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function configTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  page.click(page.q('.tab[data-tab="config"]'));
  return page;
}

export const scenarios = [
  {
    id: 'config.grades.load-edit-save',
    writes: true,
    title: '등급체계를 불러와 행을 추가하고 저장하면 재학습 필요 안내가 뜬다',
    async run({ server, check }) {
      const page = await configTab(server);
      page.click(page.q('button[onclick="loadGradeEditor()"]'));
      await page.settle();

      check.includes(page.text('grade-info'), '활성 4개', '현재 등급 수가 보인다');
      check.eq(page.qa('#grade-body tr').length, 4, '4행이 편집표에 그려졌다');
      check.eq(page.$('ge-code-0')?.value, 'TS', '코드가 입력란에 들어갔다');
      check.eq(page.$('ge-name-0')?.value, '특급기밀', '명칭이 입력란에 들어갔다');

      page.click(page.q('button[onclick="addGradeRow()"]'));
      check.eq(page.qa('#grade-body tr').length, 5, '행이 하나 늘었다');
      page.set('ge-code-4', 'S4');
      page.set('ge-name-4', '4급 참고');

      page.click('grade-save');
      await page.settle();

      const msgs = page.dialogs.map((d) => d.message).join(' ');
      check.includes(msgs, '추가될 등급: S4', '무엇이 추가되는지 되묻는다');
      const call = server.lastCall('PUT', '/schema/grades');
      check.ok(call, 'PUT /schema/grades 가 나갔다');
      check.eq((call?.body?.grades || []).length, 5, '5개 등급이 실려 나갔다');
      check.eq(call?.body?.grades?.[4]?.code, 'S4', '새 등급 코드가 실렸다');
      check.includes(page.html('grade-result'), '재학습 필요', '재학습이 필요하다고 알린다');
      check.includes(page.html('grade-result'), 'version v2', '새 버전이 보인다');
      check.includes(page.text('grade-info'), '저장 완료', '저장됐다고 말한다');
      // 저장한 등급이 다른 카드의 선택지에도 반영돼야 한다
      check.includes(page.html('sy-grade'), 'S4', '합성 생성의 등급 목록에도 반영된다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'config.grades.validation',
    title: '코드 중복·빈 값·0개 저장은 서버에 가기 전에 막는다',
    async run({ server, check }) {
      const page = await configTab(server);
      page.click(page.q('button[onclick="loadGradeEditor()"]'));
      await page.settle();

      page.set('ge-code-1', 'TS');            // 0번과 중복
      page.click('grade-save');
      await page.settle();
      check.ok(page.dialogs.some((d) => d.message.includes('중복')), '중복이라고 알린다');
      check.eq(server.countCalls('PUT', '/schema/grades'), 0, '중복이면 보내지 않는다');

      page.set('ge-code-1', '');              // 필수 누락
      page.click('grade-save');
      await page.settle();
      check.ok(page.dialogs.some((d) => d.message.includes('필수')), '필수 항목이라고 알린다');
      check.eq(server.countCalls('PUT', '/schema/grades'), 0, '빈 값이면 보내지 않는다');
      return page;
    },
  },

  {
    id: 'config.grades.remove-warns-soft-delete',
    writes: true,
    title: '행을 지우면 하드 삭제가 아니라 비활성이라고 미리 알린다',
    async run({ server, check }) {
      const page = await configTab(server);
      page.click(page.q('button[onclick="loadGradeEditor()"]'));
      await page.settle();
      page.click(page.q('#grade-body button[onclick="removeGradeRow(3)"]'));
      check.includes(page.text('grade-info'), '비활성', '비활성(소프트 삭제)이라고 알린다');
      page.click('grade-save');
      await page.settle();
      check.includes(page.dialogs.map((d) => d.message).join(' '), '비활성(소프트 삭제)될 등급: S3', '무엇이 사라지는지 되묻는다');
      check.eq((server.lastCall('PUT', '/schema/grades')?.body?.grades || []).length, 3, '3개만 저장된다');
      return page;
    },
  },

  {
    id: 'config.keywords.list-and-filter',
    title: '키워드를 조회하면 표가 그려지고 필터가 요청에 실린다',
    async run({ server, check }) {
      const page = await configTab(server);
      page.set('kw-f-grade', 'TS');
      page.set('kw-f-active', 'false');
      page.click(page.q('button[onclick="loadKeywords()"]'));
      await page.settle();

      const call = server.lastCall('GET', '/admin/keywords');
      check.includes(call?.path || '', 'grade=TS', '등급 필터가 실렸다');
      check.includes(call?.path || '', 'active_only=false', '활성 필터가 실렸다');
      check.data.eq(page.qa('#kw-body tr').length, 3, '3건이 표에 그려졌다');
      check.data.includes(page.text('kw-info'), '3건', '건수가 보인다');
      check.data.includes(page.html('kw-body'), '대외 반출 금지', '키워드가 보인다');
      check.data.includes(page.html('kw-body'), '비활성', '비활성 키워드가 구분돼 보인다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'config.keywords.create',
    writes: true,
    title: '키워드를 추가하면 서버로 나가고 목록을 다시 읽으며 반영 여부를 알린다',
    why: '핫리로드가 안 되면 저장은 됐어도 서빙에는 아직 안 먹는다 — 그 차이를 화면이 말해야 한다',
    async run({ server, check }) {
      const page = await configTab(server);
      page.set('kw-n-grade', 'S2');
      page.set('kw-n-keyword', '대외비');
      page.set('kw-n-weight', '1.2');
      page.click(page.q('button[onclick="createKeyword()"]'));
      await page.settle();

      const call = server.lastCall('POST', '/admin/keywords');
      check.eq(call?.body?.keyword, '대외비', '키워드가 실렸다');
      check.eq(call?.body?.grade, 'S2', '등급이 실렸다');
      check.eq(call?.body?.weight, 1.2, '가중치가 숫자로 실렸다');
      check.ok(call?.body?.actor?.user_id, '행위자가 실렸다');
      // ⚠ kw-info 에 찍힌 반영 안내는 곧바로 loadKeywords() 가 건수로 덮어쓴다.
      //   현재 남는 곳은 작업 로그다 — 그 사실을 그대로 잠근다.
      check.includes(page.logLines().join(' '), '핫리로드', '서빙 반영 여부가 작업 로그에 남는다');
      check.eq(page.$('kw-n-keyword')?.value, '', '입력란이 비워졌다');
      check.gte(server.countCalls('GET', '/admin/keywords'), 1, '목록을 다시 읽었다');
      return page;
    },
  },

  {
    id: 'config.keywords.empty-input-guard',
    title: '빈 키워드는 보내지 않는다',
    async run({ server, check }) {
      const page = await configTab(server);
      page.set('kw-n-keyword', '   ');
      page.click(page.q('button[onclick="createKeyword()"]'));
      await page.settle();
      check.ok(page.dialogs.some((d) => d.message.includes('키워드를 입력')), '입력하라고 알린다');
      check.eq(server.countCalls('POST', '/admin/keywords'), 0, '요청이 나가지 않는다');
      return page;
    },
  },

  {
    id: 'config.keywords.patch-and-toggle',
    writes: true,
    title: '수정·비활성이 PATCH 로 나가고, 비활성은 되묻는다',
    async run({ server, check }) {
      const page = await configTab(server);
      page.click(page.q('button[onclick="loadKeywords()"]'));
      await page.settle();

      page.set('kw-w-0', '3.5');
      page.click(page.q('#kw-body button[onclick="patchKeyword(0)"]'));
      await page.settle();
      let call = server.lastCall('PATCH', '/admin/keywords/');
      check.includes(call?.path || '', '/admin/keywords/1', '해당 키워드 id 로 나갔다');
      check.eq(call?.body?.weight, 3.5, '바뀐 가중치가 실렸다');

      page.click(page.q('#kw-body button[onclick="toggleKeyword(0)"]'));
      await page.settle();
      check.ok(page.dialogs.some((d) => d.kind === 'confirm' && d.message.includes('소프트 삭제')), '비활성은 되묻고 하드 삭제가 아님을 밝힌다');
      call = server.lastCall('PATCH', '/admin/keywords/');
      check.eq(call?.body?.is_active, false, '비활성으로 나갔다');
      return page;
    },
  },

  {
    id: 'config.keywords.warning-banner',
    title: '서버 경고(DB 미시드 등)는 접지 않고 배너로 띄운다',
    why: '0건을 "규칙이 없다"로 오독하면 룰엔진이 죽은 것을 못 알아챈다',
    needsMock: true,
    async run({ server, check }) {
      const page = await configTab(server);
      server.overrides['GET /admin/keywords'] = {
        keywords: [], count: 0,
        warnings: ['DB 에 시드 키워드가 없습니다 — 룰엔진이 파일 시드로 동작 중입니다'],
      };
      page.click(page.q('button[onclick="loadKeywords()"]'));
      await page.settle();
      check.ok(page.visible('kw-warn'), '경고 배너가 실제로 보인다');
      check.includes(page.text('kw-warn'), '시드 키워드가 없습니다', '경고 원문이 그대로 보인다');
      check.includes(page.text('kw-body'), '키워드가 없습니다', '빈 표 안내도 함께 뜬다');
      return page;
    },
  },
];
