/* 13. 문서 업로드 진행 오버레이 — 세 화면이 같은 것을 쓴다.
 *
 * 이 오버레이는 시연 화면에만 있었고, 시험은 세 화면 통틀어 0건이었다. 큰 문서는 수 분이
 * 걸려서 표시가 없으면 "눌렀는데 아무 일도 안 일어난다"가 된다.
 *
 * 보는 것 셋: 요청이 도는 동안 떠 있는가 · 끝나면 닫히는가 · 실패해도 닫히는가.
 * 마지막이 핵심이다 — 안 닫히면 화면 전체가 잠긴다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

/** 오버레이가 지금 떠 있는가. window 것을 직접 묻는다(구현 세부가 아니라 공개 API). */
function isOpen(page) {
  return !!(page.win?.UploadProgress?.isOpen?.());
}

export const scenarios = [
  /* [2026-09-29] 'upload.progress.admin-stays-open-until-done' ·
     'upload.progress.admin-closes-on-failure' 를 뺐다 — 벡터(cl-file/btn-extract, 분류 실행
     카드)를 콘솔에서 뺐다. 관리자 콘솔에는 이제 업로드 표면이 없다 — 같은 UploadProgress
     컴포넌트는 아래 demo·manage 시나리오가 계속 검증한다. */

  {
    id: 'upload.progress.demo-stays-open-until-done',
    writes: true,
    needsMock: true,
    title: '등급 시연 — 업로드가 도는 동안 진행 팝업이 떠 있고, 끝나면 닫힌다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/index.html', { bundleModules: true });
      await page.settle();
      server.faults.push({ path: '/documents/analyze', delayMs: 600 });

      page.attachFile('doc-file', { name: '시연 문서.pdf' });
      check.eq(isOpen(page), true, '요청이 나가는 즉시 팝업이 뜬다');

      await page.settle();
      check.eq(isOpen(page), false, '끝나면 팝업이 닫힌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'upload.progress.manage-opens-over-closed-modal',
    writes: true,
    needsMock: true,
    title: '후보 관리 — 업로드 모달이 닫혀도 진행 팝업은 남는다',
    why: '모달을 먼저 닫으면 화면이 비어 "등록이 취소됐나" 로 읽힌다',
    async run({ server, check }) {
      const page = await openPage(server, '/api/v1/golden/candidates/manage.html');
      await page.settle();
      server.faults.push({ path: '/golden/candidates/upload', delayMs: 600 });

      page.click('openUpload');
      page.attachFile('file', { name: '운영절차서.pdf' });
      page.click('upload');
      check.eq(isOpen(page), true, '업로드가 나가는 즉시 팝업이 뜬다');

      await page.settle();
      check.eq(isOpen(page), false, '끝나면 팝업이 닫힌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'upload.progress.manage-list-reload-shows-progress',
    needsMock: true,
    title: '후보 관리 — 목록을 새로고침·필터할 때도 같은 진행 팝업이 뜨고, 끝나면 닫힌다',
    why: '2026-09-24: 후보가 3천 건대로 늘면서 목록 조회가 눈에 띄게 걸리는데, 화면에는 표시가 '
       + '없어 "멈췄나" 로 읽혔다(요청 사유: 업로드 팝업과 같은 것을 목록 조회에도 달아 달라).',
    async run({ server, check }) {
      const page = await openPage(server, '/api/v1/golden/candidates/manage.html');
      await page.settle();
      check.eq(isOpen(page), false, '최초 로딩이 끝난 뒤에는 팝업이 없다');

      server.faults.push({ path: /^\/golden\/candidates(\?|$)/, delayMs: 600 });
      page.click('refresh');
      check.eq(isOpen(page), true, '새로고침을 누르면 즉시 팝업이 뜬다');
      check.includes(page.text('ap-title'), '목록', '무엇을 하고 있는지 업로드와 다른 문구로 말한다');

      await page.settle();
      check.eq(isOpen(page), false, '끝나면 팝업이 닫힌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'upload.progress.manage-background-reload-does-not-show-progress',
    writes: true,
    needsMock: true,
    title: '후보 관리 — 결정 저장 뒤의 배경 목록 재조회는 진행 팝업을 띄우지 않는다',
    why: '결정 저장 직후 화면에는 "저장했습니다" 가 떠야 한다. 그 직후 목록을 배경에서 다시 '
       + '읽어 오는데, 이때도 전체화면 팝업을 띄우면 방금 뜬 저장 결과를 곧바로 덮어 가린다.',
    async run({ server, check }) {
      const page = await openPage(server, '/api/v1/golden/candidates/manage.html');
      await page.settle();
      page.click(page.q('#rows .candidate'));
      await page.settle();

      server.faults.push({ path: /^\/golden\/candidates(\?|$)/, delayMs: 600 });
      page.set('action', 'change');
      page.set('finalGrade', 'S1');
      page.set('reason', '재확인 완료');
      page.click('save');
      check.eq(isOpen(page), false, '결정 저장 뒤의 배경 재조회는 팝업을 띄우지 않는다');

      await page.settle();
      check.eq(isOpen(page), false, '배경 재조회가 끝난 뒤에도 여전히 팝업이 없다');
      check.includes(page.text('saveMsg'), '저장', '저장 결과 문구가 팝업에 가려지지 않고 끝까지 남는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
