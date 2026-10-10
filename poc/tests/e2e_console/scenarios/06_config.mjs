/* 6. 설정 — 등급체계(FUN-005-①)와 태깅 키워드(FUN-023-④).
 *
 * 도입 시 한 번 정하고 거의 안 바꾸는 화면이라, 정작 바꿔야 할 때 안 되는 것이 늦게 발견된다.
 * 편집 → 저장 → 다른 화면(합성·키워드 필터)의 등급 목록까지 따라 바뀌는지 함께 본다.
 */

import { openPage, goToPane } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function configTab(server, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  await goToPane(page, 'config');
  return page;
}

export const scenarios = [
  {
    id: 'config.grades.fixed-four-levels-and-metadata-save',
    writes: true,
    title: '등급은 네 개로 고정되고 표시 정보만 저장한다',
    async run({ server, check }) {
      const page = await configTab(server);
      page.click(page.q('button[onclick="loadGradeEditor()"]'));
      await page.settle();

      check.includes(page.text('grade-info'), '활성 4개', '현재 등급 수가 보인다');
      check.eq(page.qa('#grade-body tr').length, 4, '4행이 편집표에 그려졌다');
      check.eq(page.qa('#grade-body .mono').map((el) => el.textContent).join(','), 'TS,S1,S2,S3', '고정 등급 코드가 보인다');
      check.eq(page.$('ge-name-0')?.value, '특급기밀', '명칭이 입력란에 들어갔다');
      check.eq(page.qa('#grade-body button').length, 0, '등급 추가·삭제 버튼이 없다');

      page.set('ge-name-0', '특급 기밀');
      page.click('grade-save');
      await page.settle();

      const confirm = page.dialogs.find((d) => d.kind === 'confirm');
      check.includes(confirm?.message || '', '4등급 라벨 공간은 변경되지 않습니다', '고정 정책을 확인한다');
      const call = server.lastCall('PUT', '/schema/grades');
      check.ok(call, 'PUT /schema/grades 가 나갔다');
      check.eq((call?.body?.grades || []).length, 4, '4개 등급만 실려 나갔다');
      check.eq((call?.body?.grades || []).map((g) => g.code).join(','), 'TS,S1,S2,S3', '서버에도 고정 코드만 전송한다');
      check.ok(!Object.hasOwn(call?.body || {}, 'force'), 'force 우회값은 전송하지 않는다');
      check.includes(page.html('grade-result'), 'version v2', '새 버전이 보인다');
      check.includes(page.text('grade-info'), '저장 완료', '저장됐다고 말한다');
      check.includes(page.html('sy-grade'), 'S3', '합성 생성의 고정 등급도 유지된다');
      assertNoScriptErrors(check, page);
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
    why: '저장과 반영 상태가 다를 수 있으므로, 결과를 짧고 명확하게 남긴다',
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
      // kw-info 는 목록 재조회가 덮어쓰므로 작업 로그의 간결한 상태를 확인한다.
      check.includes(page.logLines().join(' '), '반영 완료', '반영 상태가 작업 로그에 남는다');
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
      // [2026-08-24] 「소프트 삭제」·「하드 삭제」는 우리가 만든 말이라 화면에서 뺐다.
      // 시험은 뜻을 본다 — 되묻는가, 그리고 지워지는 게 아니라는 것을 밝히는가.
      check.ok(page.dialogs.some((d) => d.kind === 'confirm' && /완전 삭제가 아니라/.test(d.message)),
               '비활성은 되묻고 완전 삭제가 아님을 밝힌다');
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
