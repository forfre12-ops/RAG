/* 2. 운영 — 문서를 분류한다.
 *
 * 본문 붙여넣기 · 파일 업로드 추출 · 대용량 폴백(413) · 빈 입력 · 업로드 실패.
 * 업로드 계열은 "눌렀는데 아무 일도 안 일어난다"가 반복해서 나온 자리다(2026-08-23 커밋).
 * 그래서 성공만 보지 않고, 실패했을 때 **화면에 사유가 남는가**까지 본다.
 */

import { openPage } from '../lib/page.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function admin(server, opts) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  return page;
}

export const scenarios = [
  {
    id: 'ops.classify.text',
    writes: true,
    title: '본문을 넣고 「분류하기」를 누르면 결과 카드와 검수 큐 적재까지 간다',
    async run({ server, check }) {
      const page = await admin(server);
      page.set('cl-docid', 'E2E-DOC-010');
      page.set('cl-title', '기술이전 계약초안');
      page.set('cl-body', '본 계약의 대상 기술은 영업비밀에 해당하며 대외 반출을 금한다.');
      page.click('btn-classify');
      await page.settle();

      const call = server.lastCall('POST', '/classify');
      check.ok(call, 'POST /classify 가 실제로 나갔다');
      check.eq(call?.body?.doc_id, 'E2E-DOC-010', '보낸 본문에 doc_id 가 실렸다');
      check.eq(call?.body?.return_evidence, true, '근거 요청 플래그가 실렸다');

      const res = page.html('cl-result');
      check.includes(res, 'S1', '결과 카드에 등급이 찍혔다');
      check.includes(res, '81.0%', '신뢰도가 찍혔다');
      check.includes(res, 'v-fe4b386b', '모델 버전이 찍혔다');
      check.includes(page.html('queue'), 'E2E-DOC-010', '검수 큐에 적재됐다');
      check.eq(page.$('btn-classify')?.disabled, false, '끝나고 버튼이 다시 눌린다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'ops.classify.failure-visible',
    writes: true,
    title: '분류가 실패하면 결과 자리에 사유가 남는다 (무반응 금지)',
    why: '실패가 로그창에만 남으면 사용자는 "눌렀는데 아무 일도 없다"로 읽는다',
    needsMock: true,
    async run({ server, check }) {
      const page = await admin(server);
      server.faults.push({ path: '/classify', status: 500, body: { detail: '분류기 로드 실패' } });
      page.set('cl-body', '아무 본문');
      page.click('btn-classify');
      await page.settle();

      const res = page.text('cl-result');
      check.ok(res.length > 0, '결과 자리가 비어 있지 않다', `실제=${res}`);
      check.includes(res, '분류 실패', '실패했다고 화면이 말한다');
      check.includes(res, '분류기 로드 실패', '서버가 준 사유가 그대로 보인다');
      check.eq(page.$('btn-classify')?.disabled, false, '실패해도 버튼이 잠긴 채 남지 않는다');
      return page;
    },
  },

  {
    id: 'ops.upload.extract-then-classify',
    writes: true,
    title: '파일을 올려 「추출 → 분류」하면 본문이 채워지고 이어서 분류된다',
    async run({ server, check }) {
      const page = await admin(server);
      page.attachFile('cl-file', { name: '기술이전 계약초안.pdf' });
      check.includes(page.text('cl-file-name'), '기술이전 계약초안.pdf', '고른 파일 이름이 화면에 뜬다');

      page.click('btn-extract-classify');
      await page.settle();

      const up = server.lastCall('POST', '/documents/analyze');
      check.ok(up, 'POST /documents/analyze 가 나갔다');
      check.gte(up?.bytes, 100, '파일이 multipart 본문에 실렸다');

      check.includes(page.$('cl-body')?.value || '', '기술이전 계약초안', '추출 본문이 분류 입력란에 들어갔다');
      check.eq(page.$('cl-title')?.value, '기술이전 계약초안.pdf', '제목이 파일명으로 채워졌다');
      check.includes(page.html('cl-file-info'), '추출됨', '추출 성공 배지가 떴다');
      check.includes(page.html('cl-file-info'), 'pdfminer', '추출기 이름이 보인다');
      check.ok(server.lastCall('POST', '/classify'), '이어서 분류까지 갔다');
      check.includes(page.html('cl-result'), 'S1', '분류 결과가 그려졌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'ops.upload.no-file-selected',
    title: '파일을 안 고르고 추출을 누르면 안내가 뜬다',
    async run({ server, check }) {
      const page = await admin(server);
      page.click('btn-extract');
      await page.settle();
      check.includes(page.text('cl-file-info'), '파일을 선택', '무엇을 하라는지 화면이 말한다');
      check.eq(server.countCalls('POST', '/documents/analyze'), 0, '헛된 요청을 보내지 않는다');
      return page;
    },
  },

  {
    id: 'ops.upload.large-413-fallback',
    writes: true,
    title: '대용량이라 413 이 오면 적재 → 비동기 분류로 자동 전환된다',
    why: '동기 경로 상한(약 9페이지)을 넘는 실문서가 흔하다 — 여기서 끊기면 업로드가 통째로 막힌다',
    needsMock: true,
    async run({ server, check }) {
      const page = await admin(server);
      server.faults.push({ path: '/documents/analyze', status: 413, body: { detail: '동기 분류 상한 초과' } });

      page.attachFile('cl-file', { name: '대용량_기술문서.pdf', size: 4096 });
      page.click('btn-extract');
      // 잡 폴링은 첫 조회 전에 2초를 쉰다 — 그 대기까지 기다려 준다.
      await page.until(() => server.lastCall('GET', '/classify/jobs/'), 8000);
      await page.settle();

      check.ok(server.lastCall('POST', '/documents'), '적재 경로(POST /documents)로 전환했다');
      const upload = server.lastCall('POST', '/documents');
      check.ok(server.lastCall('POST', '/classify/async'), '비동기 분류를 요청했다');
      check.ok(server.lastCall('GET', '/classify/jobs/'), '잡 상태를 폴링했다');
      check.includes(page.html('cl-file-info'), '분류 완료', '끝났다고 화면이 말한다');
      check.includes(page.$('cl-docid')?.value || '', '9999', '서버가 준 doc_id 로 바뀌었다');
      // 색인은 끄고 보내야 한다 — 켜면 대용량에서 워커가 타임아웃으로 죽는다(2026-08-08 실측)
      check.ok(upload && upload.bytes > 0, '적재 요청에 본문이 실렸다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'ops.upload.failure-visible',
    writes: true,
    title: '업로드가 실패하면 그 자리에 사유가 보인다',
    why: '2026-08-23: 오류가 화면 뒤에 찍혀 사용자에게는 무반응으로 보였다',
    needsMock: true,
    async run({ server, check }) {
      const page = await admin(server);
      server.faults.push({ path: '/documents/analyze', status: 415, body: { detail: '지원하지 않는 형식입니다(.hwp 확장 미설치)' } });

      page.attachFile('cl-file', { name: '공고문.hwp', type: 'application/x-hwp' });
      page.click('btn-extract');
      await page.settle();

      const info = page.text('cl-file-info');
      check.includes(info, '추출 실패', '실패 배지가 떴다');
      check.includes(info, '415', '상태코드가 보인다');
      check.ok(page.visible('cl-file-info'), '그 안내가 화면에서 실제로 보인다');
      check.eq(page.$('btn-extract')?.disabled, false, '버튼이 잠긴 채 남지 않는다');
      return page;
    },
  },

  {
    id: 'ops.upload.empty-text',
    writes: true,
    title: '추출은 됐는데 본문이 0자면 스캔본일 수 있다고 알려준다',
    needsMock: true,
    async run({ server, check }) {
      const page = await admin(server);
      const empty = JSON.parse(JSON.stringify(server.faults.length ? {} : {}));
      server.overrides['POST /documents/analyze'] = {
        filename: 'scan.pdf',
        file_size_bytes: 100,
        parse: { source_format: 'pdf', extraction_method: 'pdfminer', extraction_quality: 0.0, content_quality: 0.0, ocr_used: false, char_count: 0, chunk_count: 0, warnings: ['텍스트 레이어 없음'], pii_masked_count: 0 },
        gate: { requires_review: true, reasons: ['thin_text'] },
        classification: null,
        evidence: [],
        text_preview: '',
        text: '',
        stages: [],
      };
      page.attachFile('cl-file', { name: 'scan.pdf' });
      page.click('btn-extract');
      await page.settle();
      check.includes(page.text('cl-file-info'), '비어', '본문이 비었다고 말한다');
      check.includes(page.text('cl-file-info'), '스캔본', '왜 그런지까지 말한다');
      void empty;
      return page;
    },
  },

  {
    id: 'ops.classify.drag-and-drop',
    title: '끌어다 놓기로도 파일이 올라간다',
    async run({ server, check }) {
      const page = await admin(server);
      const zone = page.$('cl-drop');
      check.ok(zone, '드롭 영역이 있다');
      const f = new page.win.File([new Uint8Array(64)], 'dropped.docx', {
        type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      });
      const ev = new page.win.Event('drop', { bubbles: true, cancelable: true });
      ev.dataTransfer = { files: [f] };
      zone.dispatchEvent(ev);
      await page.settle();
      check.includes(page.text('cl-file-name'), 'dropped.docx', '놓은 파일이 선택 상태가 됐다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
