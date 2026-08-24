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
      // [2026-08-24] 종전에는 '81.0%' 가 찍히는지 봤다. 화면에서 신뢰도 수치를 빼기로 했으므로
      // 이제 **숫자가 없다는 것**과 그 자리에 결정이 있다는 것을 잠근다.
      check.ok(!/\d+\.\d%/.test(res), '결과 카드에 신뢰도 수치가 없다');
      check.ok(/자동 확정|검수 필요/.test(res), '숫자 대신 결정(자동 확정·검수 필요)이 찍혔다');
      check.includes(res, 'v-fe4b386b', '모델 버전이 찍혔다');
      check.includes(page.html('queue'), 'E2E-DOC-010', '검수 큐에 적재됐다');
      // 붙여넣은 본문은 문서로 적재하지 않는다 — 올린 파일만 적재한다(아래 upload 시나리오).
      check.ok(!server.exactCall('POST', '/documents'), '붙여넣은 본문을 문서로 적재하지는 않는다');
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
    id: 'ops.upload.ingest-before-classify',
    writes: true,
    title: '올린 파일은 분류 **전에** 적재된다 — 그래야 확정·재라벨이 남는다',
    why: '2026-08-24 실측: doc_id 를 upload-<ts>(비-UUID)로 만들어 분류해서 서버가 영속화를 '
       + '건너뛰었고, 확정·재라벨은 전부 "classification not found in DB" 로만 끝났다',
    async run({ server, check }) {
      const page = await admin(server);
      page.attachFile('cl-file', { name: '공사지명원.xls' });
      page.click('btn-extract-classify');
      await page.settle();

      check.ok(server.exactCall('POST', '/documents'), '분류 전에 적재 요청이 나갔다');
      const cl = server.lastCall('POST', '/classify');
      check.eq(cl?.body?.doc_id, '99999999-9999-4999-8999-999999999999',
               '서버가 준 실제 doc_id(UUID)로 분류했다');
      check.ok(!/^upload-/.test(cl?.body?.doc_id || ''), '임시 doc_id 로 분류하지 않는다');
      check.eq(page.$('cl-docid')?.value, '99999999-9999-4999-8999-999999999999',
               '화면 doc_id 도 실제 문서로 바뀌었다');
      check.ok(page.q('#queue button[onclick="doConfirm(0)"]'), '큐 항목에 확정 버튼이 있다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'ops.upload.readonly-does-not-ingest',
    // 하니스가 만드는 파일은 'A' 로 채운 껍데기다. 실서버는 그것을 못 읽어 본문 0자를 주고
    // (실측 2026-08-24 223: extract_error "Expected BOF record; found b'AAAAAAAA'"),
    // 본문이 없으면 분류로 넘어가지 않아 이 시나리오가 보려는 로그가 아예 안 남는다.
    needsMock: true,
    title: '읽기 전용이면 파일을 적재하지 않고, 저장되지 않는다고 말한다',
    why: '쓰기 잠금은 지켜야 하고, 대신 왜 확정이 안 되는지는 그 자리에서 알려야 한다',
    async run({ server, check }) {
      const page = await admin(server);
      const gate = page.$('cfg-write-enable');
      check.ok(gate, '쓰기 작업 허용 스위치가 있다');
      gate.checked = false;

      page.attachFile('cl-file', { name: '공사지명원.xls' });
      page.click('btn-extract-classify');
      await page.settle();

      check.ok(!server.exactCall('POST', '/documents'), '읽기 전용에서는 적재하지 않는다');
      const cl = server.lastCall('POST', '/classify');
      check.ok(/^upload-/.test(cl?.body?.doc_id || ''), '적재 없이 임시 doc_id 로 분류만 한다');
      check.includes(page.logLines('info').join(' '), '저장되지 않아',
                     '이 분류로는 확정·재라벨을 남길 수 없다고 로그가 말한다');
      return page;
    },
  },

  {
    id: 'ops.classify.not-persisted-shows-reason',
    writes: true,
    title: '서버가 저장을 건너뛴 분류에는 확정 버튼 대신 사유가 뜬다',
    why: '누르면 반드시 실패하는 버튼을 두지 않는다 — 종전에는 눌러야 "not found in DB" 를 봤다',
    needsMock: true,
    async run({ server, check }) {
      const page = await admin(server);
      server.overrides['POST /classify'] = {
        inference_id: '44444444-4444-4444-8444-444444444444',
        doc_id: 'E2E-DOC-011',
        label: 'S1', confidence: 0.81, scores: { TS: 0.05, S1: 0.81, S2: 0.1, S3: 0.04 },
        evaluation_factors: { secrecy: 2, value: 2, management: 1 },
        factors_source: 'rule_evidenced', evidence: [], rag_context_used: [],
        model_version: 'v-fe4b386b', elapsed_ms: 120, status: 'staging',
        warnings: ["persistence skipped: doc_id='E2E-DOC-011' is not a UUID"],
      };
      page.set('cl-docid', 'E2E-DOC-011');
      page.set('cl-body', '본 계약의 대상 기술은 영업비밀에 해당한다.');
      page.click('btn-classify');
      await page.settle();

      check.ok(!page.q('#queue button[onclick="doConfirm(0)"]'), '확정 버튼을 내보내지 않는다');
      check.ok(!page.q('#queue button[onclick="doRelabel(0)"]'), '재라벨 버튼도 내보내지 않는다');
      check.includes(page.text('queue'), '저장되지 않은 분류', '왜 안 되는지 그 자리에 적힌다');
      check.includes(page.text('queue'), '확정·재라벨을 기록할 수 없습니다', '무엇이 안 되는지 말한다');
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
