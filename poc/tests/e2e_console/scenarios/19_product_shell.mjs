/* 19. Product Shell V2 — 운영 개요·문서 작업대·검수 계정별 진행.
 *
 * 2026-10-10 작업분. admin.html 의 이 영역(productGoldenReviewAvailable 부터
 * initProductShell 까지, 함수 43개)은 옛 탭바를 대신하는 새 내비게이션인데,
 * 직접 이름이 등장하는 시나리오가 기존 파일 전체에 2개(productShowView 경로만)뿐이었다 —
 * 실제 동작(운영 개요 집계·문서 작업대 열람/결정·검수 계정별 진행)은 한 번도 화면으로
 * 눌러본 적이 없었다. 이 파일이 그 격차를 채운다.
 *
 * ⚠ 검수 배치 선택(product-review-batch)의 기본값은 **서버가 표시한 active 배치**다
 * (후보 API 의 review_batch_active → available_batches[].active). 이름순 마지막 배치를
 * 고르면 날짜 없는 과거 전달본을 현재 대상으로 오인한다 — 그 사고를 막는 배선이 핵심이라
 * 전용 시나리오(product.review-batch...)로 따로 잠근다.
 */

import { openPage, goToPane } from '../lib/page.mjs';
import { FIXTURES } from '../lib/server.mjs';
import { assertNoScriptErrors } from '../lib/expect.mjs';

async function productTab(server, view, opts = {}) {
  const page = await openPage(server, '/console/admin.html', opts);
  await page.settle();
  if (view !== 'overview') {
    await goToPane(page, view);
    await page.settle();
  }
  return page;
}

/** healthz 응답에서 operational_config 몇 개 키만 바꿔 끼운다(14_demo_surface.mjs 와 같은 꼴). */
function healthWith(server, patch) {
  const base = JSON.parse(JSON.stringify(FIXTURES['GET /healthz']));
  Object.assign(base.operational_config, patch);
  server.faults.push({ path: '/healthz', body: base });
}

/** GET /golden/candidates 전체를 통째로 바꿔 끼운다 — 검수 배치·문서유형 분포를 시나리오가 정한다. */
function candidatesWith(server, body) {
  server.overrides['GET /golden/candidates'] = body;
}

export const scenarios = [
  {
    id: 'product.nav-hidden-and-overview-explains-when-golden-review-missing',
    title: '검수 후보 API가 없는 이미지에서는 검증 문서·검수자 탭이 숨고 운영 개요가 이유를 말한다',
    why: '고객사 분리 이미지에는 candidate 원장이 물리적으로 없다 — 숨기지 않으면 버튼마다 404',
    needsMock: true,
    async run({ server, check }) {
      healthWith(server, { golden_review_console_enabled: false });
      const page = await productTab(server, 'overview');

      check.ok(page.q('[data-product-nav="documents"]')?.classList.contains('pf-hidden'), '검증 문서 탭이 숨는다');
      check.ok(page.q('[data-product-nav="experts"]')?.classList.contains('pf-hidden'), '검수자 배정 탭이 숨는다');
      check.includes(page.text('overview-summary-body'), '제공하지 않습니다', '숨긴 이유를 운영 개요에 적는다');
      check.ok(!page.$('gold-jobs-card'), 'golden_jobs.js 카드도 뜨지 않는다(같은 플래그를 읽는다)');
      check.eq(server.countCalls('GET', '/golden/candidates'), 0, '후보 API를 애초에 부르지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.legacy-product-views-remain-navigable',
    title: '레거시 카드로 구성된 화면도 새 제품 메뉴에서 열 수 있다',
    why: '제품 메뉴만 추가하고 대응 패널이 없는 화면을 그대로 두면 모델·정책·감사 메뉴가 클릭 즉시 예외로 끝난다',
    async run({ server, check }) {
      const page = await productTab(server, 'overview');
      for (const view of ['synthesis', 'policy', 'models', 'audit', 'settings']) {
        const nav = page.q(`[data-product-nav="${view}"]`);
        check.ok(nav, `${view} 메뉴가 있다`);
        page.click(nav);
        await page.settle();
        const cards = page.qa(`.grid > [data-product-view="${view}"]`);
        check.gte(cards.length, 1, `${view}에는 대응 카드가 있다`);
        check.ok(cards.some((card) => page.visible(card)), `${view}의 대응 카드가 보인다`);
      }
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.overview-renders-metrics-grades-and-category-table',
    title: '운영 개요가 집계 카드·등급별 분포·문서유형 표를 실제 응답으로 그린다',
    why: '운영 개요는 수치를 손으로 세지 않고 /golden/candidates 응답 하나로 전부 만든다',
    needsMock: true,
    async run({ server, check }) {
      candidatesWith(server, {
        total: 3,
        summary: { total: 3, fixed: 1, unfixed: 1, deferred: 1, discarded: 0,
          by_proposed_grade: { TS: 1 }, by_proposed_grade_unfixed: { TS: 1 }, by_final_grade: { S1: 1 } },
        candidates: [
          { doc_id: 'X-1', doc_format: '회의록' },
          { doc_id: 'X-2', doc_format: '회의록' },
          { doc_id: 'X-3', doc_format: '품의서' },
        ],
        available_batches: [{ review_batch: '2026-10-A', total: 3, active: true }],
        available_doc_formats: [{ doc_format: '회의록' }, { doc_format: '품의서' }],
      });
      const page = await productTab(server, 'overview');

      check.ok(server.lastCall('GET', '/golden/candidates'), '운영 개요가 후보 API를 불렀다');
      check.includes(page.text('overview-summary-body'), '3', '전체 문서 수가 그려진다', page.text('overview-summary-body'));
      const rows = page.qa('#product-category-body tbody tr');
      check.eq(rows.length, 2, '문서유형 두 종류가 각각 한 행이다');
      const types = rows.map((tr) => tr.querySelector('td').textContent.trim());
      check.ok(types.includes('회의록') && types.includes('품의서'), '문서유형 이름이 그대로 보인다', types.join(','));
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.overview-category-row-opens-matching-documents',
    title: '문서유형 행을 누르면 문서 작업대로 넘어가 그 유형으로 필터링된다',
    why: '집계만 보여주고 거기서 문서로 못 내려가면 운영 개요가 막다른 화면이 된다',
    needsMock: true,
    async run({ server, check }) {
      candidatesWith(server, {
        total: 2,
        summary: { total: 2, fixed: 0, unfixed: 2, deferred: 0, discarded: 0 },
        candidates: [
          { doc_id: 'X-1', doc_format: '인수인계서' },
          { doc_id: 'X-2', doc_format: '내부 공지' },
        ],
        available_batches: [{ review_batch: '2026-10-A', total: 2, active: true }],
        available_doc_formats: [{ doc_format: '인수인계서' }, { doc_format: '내부 공지' }],
      });
      const page = await productTab(server, 'overview');
      const row = page.qa('#product-category-body tbody tr').find((tr) => tr.textContent.includes('내부 공지'));
      check.ok(row, '「내부 공지」 행이 있다');
      page.click(row);
      await page.settle();

      check.eq(page.q('#product-documents')?.classList.contains('is-visible'), true, '문서 작업대 화면으로 전환됐다');
      check.eq(page.$('wb-format')?.value, '내부 공지', '유형 필터가 그 행의 유형으로 맞춰졌다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.review-batch-selector-defaults-to-the-active-batch',
    title: '검수 배치 선택은 서버가 표시한 active 배치를 기본값으로 삼는다(이름순 마지막이 아니다)',
    why: '2026-10-10 proxy_gold_candidate_service.py 수정의 화면 쪽 짝 — active 표식이 없으면 날짜 없는 과거 전달본을 현재 대상으로 오인한다',
    needsMock: true,
    async run({ server, check }) {
      candidatesWith(server, {
        total: 1050,
        summary: { total: 1050, fixed: 0, unfixed: 1050, deferred: 0, discarded: 0 },
        candidates: [],
        // 이름만 보면 2026-10-B 가 "마지막"이지만 active 는 2026-10-A 다 — 화면은 active 를 따라야 한다.
        available_batches: [
          { review_batch: '2026-07-OLD', total: 50 },
          { review_batch: '2026-10-A', total: 1000, active: true },
          { review_batch: '2026-10-B-draft', total: 1 },
        ],
        available_doc_formats: [],
      });
      const page = await productTab(server, 'overview');

      const select = page.$('product-review-batch');
      check.eq(select?.value, '2026-10-A', 'active 배치가 기본 선택된다', select?.value);
      const activeOption = [...select.options].find((o) => o.value === '2026-10-A');
      check.includes(activeOption?.textContent || '', '현재 검수', 'active 배치 옆에 표시가 붙는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.review-batch-change-reloads-overview-and-workbench',
    title: '검수 배치를 바꾸면 운영 개요와(문서 작업대를 보고 있었다면) 문서 목록을 그 배치로 다시 부른다',
    why: '배치를 바꿨는데 화면이 이전 배치 집계를 계속 보이면 검수자가 엉뚱한 범위를 검수한다',
    needsMock: true,
    async run({ server, check }) {
      candidatesWith(server, {
        total: 2, summary: { total: 2, fixed: 0, unfixed: 2, deferred: 0, discarded: 0 },
        candidates: [{ doc_id: 'X-1' }],
        available_batches: [
          { review_batch: '2026-07-OLD', total: 1 },
          { review_batch: '2026-10-A', total: 1, active: true },
        ],
        available_doc_formats: [],
      });
      const page = await productTab(server, 'documents');
      const before = server.countCalls('GET', '/golden/candidates');

      page.set('product-review-batch', '2026-07-OLD');
      await page.settle();

      // lastCall 은 접두사로 집어 '/golden/candidates/{doc_id}' 상세 호출과 섞인다(server.mjs 의
      // exactCall 설명 그대로) — 목록 호출만 보려면 쿼리를 뺀 경로가 정확히 같은 exactCall 을 쓴다.
      check.ok(server.countCalls('GET', '/golden/candidates') > before, '배치를 바꾸면 다시 불러온다');
      const call = server.exactCall('GET', '/golden/candidates');
      check.includes(call?.path || '', 'review_batch=2026-07-OLD', '바뀐 배치로 질의한다', call?.path);
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.workbench-lists-and-auto-selects-first-document',
    needsData: true,   // 기본 본보기의 doc_id(PGC-0001)로 단언한다 — 실서버 목록의 첫 문서는 다르다
    title: '문서 작업대를 열면 목록이 채워지고 첫 문서가 자동으로 선택돼 원문이 뜬다',
    why: '목록만 있고 아무것도 안 열려 있으면 "다음은 뭘 해야 하나" 화면이 된다',
    async run({ server, check }) {
      const page = await productTab(server, 'documents');
      const first = FIXTURES['GET /golden/candidates'].candidates[0];

      check.ok(server.lastCall('GET', '/golden/candidates/' + first.doc_id), '첫 문서 상세를 불러왔다');
      check.includes(page.html('wb-list'), 'active', '목록에서 한 항목이 선택 표시된다');
      check.includes(page.text('wb-document'), first.doc_id, '본문 화면에 그 문서 id가 보인다');
      check.includes(page.text('wb-inspector-body'), first.doc_id, '정보 패널에도 등록 정보가 채워진다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.workbench-renders-headings-and-tables-from-document-text',
    title: '문서 본문의 제목(#)과 표(|…|) 줄을 각각 제목·표로 그린다',
    why: '검수자가 긴 원문을 그냥 받아보면 표가 줄바꿈투성이 문단으로 뭉개진다',
    needsMock: true,
    async run({ server, check }) {
      server.overrides['GET /golden/candidates/X-9'] = {
        doc_id: 'X-9', title: '점검 기록', status: 'proposed', document_origin: 'organization_real',
        text: '# 점검 기록\n\n## 결과\n\n| 항목 | 값 |\n|---|---|\n| 압력 | 32 |\n| 온도 | 18 |\n\n확인자는 현장에서 서명했다.',
      };
      candidatesWith(server, {
        total: 1, summary: { total: 1, fixed: 0, unfixed: 1, deferred: 0, discarded: 0 },
        candidates: [{ doc_id: 'X-9', title: '점검 기록' }],
        available_batches: [], available_doc_formats: [],
      });
      const page = await productTab(server, 'documents');
      await page.until(() => page.text('wb-document').includes('점검 기록'));

      check.ok(page.q('#wb-document h3'), '본문 안의 # 제목이 h3로 그려진다');
      const table = page.q('#wb-document table.doc-table');
      check.ok(table, '표 줄이 table로 그려진다');
      const headers = [...(table?.querySelectorAll('thead th') || [])].map((th) => th.textContent.trim());
      check.ok(headers.includes('항목') && headers.includes('값'), '표 헤더가 그대로 들어간다', headers.join(','));
      check.eq(table?.querySelectorAll('tbody tr').length, 2, '구분선 다음 줄부터 본문 행이다');
      check.excludes(page.text('wb-document'), '점검 기록 점검 기록', '제목과 같은 첫 줄을 본문에서 중복해 넣지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.workbench-decision-requires-reason-then-saves',
    needsData: true,   // 기본 본보기의 doc_id로 결정을 저장한다 — 실서버엔 그 id가 없다
    title: '등급 지정 결정은 사유 없이는 막히고, 채우면 저장돼 서버로 나간다',
    why: '사유 없는 등급 변경은 나중에 왜 그랬는지 아무도 설명할 수 없다',
    writes: true,
    async run({ server, check }) {
      const page = await productTab(server, 'documents');
      const first = FIXTURES['GET /golden/candidates'].candidates[0];
      await page.until(() => !!page.$('wb-decision-action'));

      page.click(page.q('.wb-decision button'));
      await page.settle();
      check.includes(page.text('wb-decision-message'), '사유', '사유 없이 저장하면 사유를 적으라고 말한다');
      check.eq(server.countCalls('POST', '/golden/candidates/' + first.doc_id + '/decision'), 0, '사유가 없으면 요청이 안 나간다');

      page.set('wb-decision-grade', 'S1');
      page.set('wb-decision-reason', '현장 확인 결과 접근범위가 넓어 상향');
      page.click(page.q('.wb-decision button'));
      await page.settle();

      const call = server.lastCall('POST', '/golden/candidates/' + first.doc_id + '/decision');
      check.ok(call, '사유를 채우면 결정이 서버로 나간다');
      check.eq(call?.body?.action, 'change', '결정 행위가 실린다');
      check.eq(call?.body?.grade, 'S1', '고른 등급이 실린다');
      check.eq(call?.body?.reason, '현장 확인 결과 접근범위가 넓어 상향', '적은 사유가 그대로 실린다');
      // 저장 뒤 saveWorkbenchDecision 은 운영 개요·목록·이 문서 상세를 연달아 다시 불러
      // 결정 폼을 처음부터 다시 그린다 — "저장했습니다" 메시지는 그 재조회가 끝나기 전의
      // 중간 상태라 settle() 이후에는 이미 새 폼으로 덮인다(검증 대상이 아니다).
      // 폼이 깨지지 않고 새로 그려졌는지(사유 칸이 빈 새 폼인지)로 재조회 완주를 확인한다.
      check.eq(page.$('wb-decision-reason')?.value || '', '', '재조회로 결정 폼이 새로 그려진다(이전 입력이 남지 않는다)');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.workbench-inspector-history-tab-shows-past-decisions',
    title: '정보 패널의 「이력」 탭은 이 문서의 지난 결정을 보여준다',
    why: '지금 보이는 상태만 알면 왜 그 상태가 됐는지 검수자가 추측해야 한다',
    async run({ server, check }) {
      const page = await productTab(server, 'documents');
      const firstId = FIXTURES['GET /golden/candidates'].candidates[0].doc_id;
      await page.until(() => !!page.$('wb-inspector-body'));

      page.click(page.q('[data-inspector-tab="history"]'));
      await page.settle();

      check.eq(page.q('[data-inspector-tab="history"]')?.classList.contains('active'), true, '이력 탭이 활성 표시된다');
      // decision_history 는 목록 응답이 아니라 상세 응답(GET /golden/candidates/{doc_id})에만 있다.
      const detailFixture = FIXTURES['GET /golden/candidates/{doc_id}'];
      const history = (detailFixture?.doc_id === firstId && detailFixture.decision_history) || [];
      if (history.length) {
        check.includes(page.text('wb-inspector-body'), String(history[0].action), '지난 결정의 행위가 보인다');
      } else {
        check.includes(page.text('wb-inspector-body'), '없습니다', '이력이 없으면 없다고 말한다');
      }
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.workbench-tablet-inspector-drawer-keeps-focus-and-panel-state',
    title: '태블릿에서 결정 정보는 드로어로 열리고 ESC·Tab·키보드 리사이즈가 동작한다',
    why: '중간 폭에서 Inspector를 숨기면 근거와 최종 결정을 확인할 수 없고, 마우스 전용 리사이즈는 접근성을 깨뜨린다',
    async run({ server, check }) {
      const page = await productTab(server, 'documents', {
        beforeBoot(win) {
          Object.defineProperty(win, 'innerWidth', { configurable: true, value: 900 });
          win.matchMedia = (query) => ({
            matches: String(query).includes('min-width: 761px'),
            media: query,
            addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
          });
        },
      });
      await page.until(() => !!page.$('wb-inspector-body'));

      const trigger = page.$('wb-inspector-open');
      // jsdom does not evaluate this page's width media query consistently;
      // force the two tablet-only controls visible while exercising their
      // keyboard behavior. CSS breakpoint existence is locked by pytest.
      trigger.style.display = 'inline-flex';
      page.$('wb-inspector').style.display = 'block';
      page.$('wb-inspector-close').style.display = 'inline-flex';
      trigger.focus();
      page.win.toggleWorkbenchInspector(true);
      await page.wait(250);
      check.ok(page.$('document-workbench')?.classList.contains('inspector-open'), '태블릿에서 정보 패널이 드로어로 열린다');
      check.eq(trigger.getAttribute('aria-expanded'), 'true', '열림 상태를 보조기술에도 알린다');
      check.gte(page.win.inspectorFocusableElements().length, 1, '드로어 안에 포커스 가능한 조작 요소가 있다');
      check.eq(page.$('product-documents').hasAttribute('inert'), false, '열린 문서 화면은 inert 상태가 아니다');

      const focusables = page.qa('#wb-inspector button, #wb-inspector select, #wb-inspector textarea').filter((el) => !el.disabled);
      const first = focusables[0];
      // jsdom does not make CSS-hidden controls focusable.  Spy on the
      // boundary call itself; actual breakpoint visibility is separately
      // locked in the CSS/source test.
      let trappedFocuses = 0;
      const originalFirstFocus = first.focus.bind(first);
      first.focus = (...args) => { trappedFocuses += 1; return originalFirstFocus(...args); };
      trigger.focus();
      page.doc.dispatchEvent(new page.win.KeyboardEvent('keydown', { key: 'Tab', bubbles: true }));
      check.gte(trappedFocuses, 1, '드로어 밖으로 나가려는 Tab을 첫 조작 요소로 되돌린다');

      const leftHandle = page.q('[data-resize="left"]');
      leftHandle.dispatchEvent(new page.win.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
      check.ok(page.$('document-workbench').style.getPropertyValue('--wb-left').endsWith('px'), '화살표 키로 문서함 폭을 조절한다');
      check.ok(page.win.localStorage.getItem('koipa_admin_workbench_layout_v1'), '패널 폭을 이 브라우저에만 저장한다');

      let restoredFocuses = 0;
      const originalTriggerFocus = trigger.focus.bind(trigger);
      trigger.focus = (...args) => { restoredFocuses += 1; return originalTriggerFocus(...args); };
      page.doc.dispatchEvent(new page.win.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      check.ok(!page.$('document-workbench')?.classList.contains('inspector-open'), 'ESC로 드로어를 닫는다');
      check.eq(trigger.getAttribute('aria-expanded'), 'false', '닫힘 상태를 보조기술에도 알린다');
      check.gte(restoredFocuses, 1, '닫은 뒤 열기 버튼으로 포커스를 되돌린다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.experts-is-progress-only-not-assignment-setup',
    title: '검수 계정별 진행 화면은 배정 설정을 요구하거나 실행하지 않는다',
    why: '진행 현황은 이미 배정된 업무의 상태를 읽는 화면이며, 수동 배정 절차가 섞이면 역할과 흐름이 흐려진다',
    async run({ server, check }) {
      const page = await productTab(server, 'experts');
      await page.until(() => page.text('expert-assignment-summary').length > 0);

      for (const id of ['expert-reviewer-ids', 'expert-reviewer-roster', 'expert-plan-submit', 'expert-assignment-reason']) {
        check.ok(!page.$(id), `${id} 배정 설정 제어가 없다`);
      }
      check.ok(server.lastCall('GET', '/golden/assignments'), '진행 현황 API만 불러온다');
      check.eq(server.countCalls('POST', '/golden/assignments/expert-review-plan'), 0, '진행 화면은 배정 요청을 보내지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.experts-renders-detailed-per-account-progress',
    title: '검수 계정별 진행은 계정별 배정·결정·보류·미처리·최근 활동을 보여준다',
    why: '전체 숫자만 보면 어느 계정에 후속 조치가 필요한지 알 수 없다',
    needsMock: true,
    async run({ server, check }) {
      server.overrides['GET /golden/assignments'] = {
        total_candidates: 1000, assigned_candidate_count: 50, unassigned_candidate_count: 950,
        reviewers: [{
          reviewer_id: 'reviewer-01', visible_doc_count: 50, reviewed_doc_count: 18,
          fixed_doc_count: 12, deferred_doc_count: 3, excluded_doc_count: 1,
          in_progress_doc_count: 2, not_started_doc_count: 32, remaining_doc_count: 34,
          progress_percent: 36, last_action: 'defer', last_decided_at: '2026-10-11T09:30:00Z',
        }], assignments: [], ledger_events_total: 18, by_event: {},
        enforced: { assignment: true, blind: true },
        enforcement: { assignment_enforced: true, blind_enforced: true },
        progress_summary: {
          reviewer_count: 1, assigned_doc_count: 50, reviewed_doc_count: 18,
          fixed_doc_count: 12, deferred_doc_count: 3, excluded_doc_count: 1,
          in_progress_doc_count: 2, not_started_doc_count: 32, remaining_doc_count: 34, progress_percent: 36,
        },
      };
      const page = await productTab(server, 'experts');
      await page.until(() => page.text('expert-assignment-list').includes('reviewer-01'));

      const row = page.text('expert-assignment-list');
      for (const label of ['배정 50건', '판정 완료', '보류', '제외·폐기', '재검토', '미시작', '미처리', '최근 활동']) {
        check.includes(row, label, `${label} 지표가 계정 행에 있다`);
      }
      check.includes(row, '36.0%', '계정 처리율을 보여준다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'product.experts-shows-an-empty-state-without-inventing-assignment-controls',
    title: '배정된 검수 계정이 없으면 빈 상태를 보여주고 배정 입력 폼을 만들지 않는다',
    why: '상태가 비었을 때 임시 배정 폼으로 바꾸면 운영자는 현재 현황과 새 작업을 구분할 수 없다',
    needsMock: true,
    async run({ server, check }) {
      server.overrides['GET /golden/assignments'] = {
        total_candidates: 1000, assigned_candidate_count: 0, unassigned_candidate_count: 1000,
        reviewers: [], assignments: [], ledger_events_total: 0, by_event: {},
        enforced: { assignment: true, blind: true },
        enforcement: { assignment_enforced: true, blind_enforced: true },
        progress_summary: {
          reviewer_count: 0, assigned_doc_count: 0, reviewed_doc_count: 0,
          fixed_doc_count: 0, deferred_doc_count: 0, excluded_doc_count: 0,
          in_progress_doc_count: 0, not_started_doc_count: 0, remaining_doc_count: 0, progress_percent: 0,
        },
      };
      const page = await productTab(server, 'experts');
      await page.until(() => page.text('expert-assignment-list').includes('진행 중인 검수 계정이 없습니다.'));

      check.includes(page.text('expert-assignment-list'), '진행 중인 검수 계정이 없습니다.', '빈 진행 현황을 설명한다');
      check.eq(server.countCalls('POST', '/golden/assignments/expert-review-plan'), 0, '빈 상태에서도 배정 요청을 보내지 않는다');
      assertNoScriptErrors(check, page);
      return page;
    },
  },

  {
    id: 'golden-jobs.mount-does-not-duplicate-on-repeated-profile-ready',
    title: '서버 상태를 다시 확인해도(koipa:profile-ready 재발행) 검수 목록 카드가 중복되지 않는다',
    why: '2026-10-10 golden_jobs.js 수정 — 종전에는 DOMContentLoaded 로 한 번만 붙었지만, 이제는 '
       + 'profile-ready 를 들을 때마다 mount() 가 돌아 재확인을 누르면 카드가 쌓일 위험이 있었다',
    async run({ server, check }) {
      const page = await productTab(server, 'overview');
      check.eq(page.qa('#gold-jobs-card').length, 1, '첫 로드에서 카드가 하나다');

      // Product Shell V2에서는 서버 상태 확인이 「연결·운영 도구」 화면에 있다.
      await goToPane(page, 'settings');
      page.click(page.q('button[onclick="checkHealth()"]'));
      await page.settle();
      check.eq(page.qa('#gold-jobs-card').length, 1, '다시 확인해도 카드가 여전히 하나다');
      check.gte(server.countCalls('GET', '/healthz'), 2, '실제로 healthz 를 두 번 이상 불렀다(재마운트 기회가 있었다)');
      assertNoScriptErrors(check, page);
      return page;
    },
  },
];
