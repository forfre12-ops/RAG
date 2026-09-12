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
  {
    id: 'upload.progress.admin-stays-open-until-done',
    writes: true,
    needsMock: true,
    title: '관리자 콘솔 — 업로드가 도는 동안 진행 팝업이 떠 있고, 끝나면 닫힌다',
    why: '종전에는 인라인 글씨 한 줄뿐이라 멈춘 것과 구분이 안 됐다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/documents/analyze', delayMs: 600 });

      page.attachFile('cl-file', { name: '대용량 기술자료.pdf' });
      page.click('btn-extract');
      check.eq(isOpen(page), true, '요청이 나가는 즉시 팝업이 뜬다');
      check.includes(page.text('ap-file'), '대용량 기술자료.pdf', '어느 파일인지 팝업에 적힌다');

      await page.settle();
      check.eq(isOpen(page), false, '끝나면 팝업이 닫힌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'upload.progress.admin-closes-on-failure',
    writes: true,
    needsMock: true,
    title: '관리자 콘솔 — 업로드가 실패해도 진행 팝업은 닫힌다',
    why: '오버레이가 남으면 화면 전체가 잠겨 아무것도 누를 수 없게 된다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      server.faults.push({ path: '/documents/analyze', status: 500, body: { detail: 'boom' } });

      page.attachFile('cl-file', { name: '깨진문서.pdf' });
      page.click('btn-extract');
      await page.settle();

      check.eq(isOpen(page), false, '실패해도 팝업이 닫힌다');
      check.includes(page.html('cl-file-info'), '추출 실패', '실패 사유는 화면에 남는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

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
];
