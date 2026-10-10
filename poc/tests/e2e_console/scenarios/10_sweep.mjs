/* 10. 전수 훑기 — 화면에 있는 버튼을 **하나도 빼지 않고** 눌러 본다.
 *
 * 왜 필요한가. 시나리오는 사람이 생각한 경로만 지난다. 실제로 깨지는 것은 아무도 안 보는
 * 버튼이다. 2026-08-18·08-23 사고 둘 다 "특정 버튼/초기화가 없는 함수·없는 요소를 만져서"
 * 났고, 문자열 검사 423건은 전부 통과했다.
 *
 * 여기서는 onclick 이 달린 요소를 모두 모아 차례로 실행하고 ReferenceError·TypeError 가
 * 하나라도 나면 그 버튼 이름과 함께 실패시킨다. 되돌릴 수 없는 동작은 확인창에서 '아니오'로
 * 답해 막는다(그래도 핸들러 본문은 실행된다 — 죽는지 보는 것이 목적이다).
 */

import { openPage } from '../lib/page.mjs';

const SKIP = new Set(['showPane']); // 탭 전환은 별도 시나리오가 본다(여기선 매번 되돌려야 해서 제외)

function handlers(page) {
  return page.qa('[onclick]').map((el) => ({
    el,
    code: el.getAttribute('onclick'),
    label: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 28) || el.id || el.tagName,
  }));
}

export const scenarios = [
  {
    id: 'sweep.admin.every-button-runs',
    writes: true,
    title: '관리자 콘솔의 모든 버튼을 눌러도 스크립트 오류가 나지 않는다',
    why: '없는 함수·없는 요소를 만지는 버튼은 눌러 봐야만 드러난다',
    async run({ server, check }) {
      // 확인창은 전부 '아니오' — 비가역 동작을 실제로 실행하지 않으면서 핸들러는 돌린다.
      const page = await openPage(server, '/console/admin.html', { confirmAnswer: false });
      await page.settle();

      // 목록·표를 먼저 채워 둔다 — 행 안의 버튼(확정·재라벨·수정 등)까지 훑기 위해서.
      for (const sel of [
        'button[onclick="loadGradeEditor()"]',
        'button[onclick="loadKeywords()"]',
        'button[onclick="loadSynthQueue()"]',
        'button[onclick="loadJobs()"]',
        'button[onclick="loadMetrics()"]',
      ]) {
        const b = page.q(sel);
        if (b) b.click();
        await page.settle();
      }

      const codes = [...new Set(handlers(page).map((h) => h.code))].filter((c) => !SKIP.has(c.replace(/\(.*/, '')));
      check.gte(codes.length, 30, `누를 것을 충분히 모았다 (${codes.length}개)`);

      const broken = [];
      let pressed = 0;
      for (const code of codes) {
        // 앞선 조작으로 목록이 다시 그려지면 옛 요소는 화면에서 떨어진다. 브라우저에서는
        // 그것을 누를 수 없으므로, 지금 화면에 붙어 있는 것만 누른다.
        const el = page.qa('[onclick]').find((n) => n.getAttribute('onclick') === code && n.isConnected);
        if (!el) continue;
        const label = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 28) || el.id || code;
        const before = page.errors.length;
        pressed += 1;
        try {
          el.click();
        } catch (e) {
          broken.push(`${label} [${code}] → ${e.message}`);
          continue;
        }
        await page.settle(2500);
        for (const err of page.errors.slice(before)) broken.push(`${label} [${code}] → ${err.message}`);
      }
      check.gte(pressed, 25, `실제로 누른 버튼 수 (${pressed}/${codes.length})`);
      check.ok(broken.length === 0, `누른 버튼 ${pressed}개 전부 오류 없이 실행됐다`, broken.join(' || '));
      return page;
    },
  },

  {
    id: 'sweep.admin.no-missing-globals',
    title: 'onclick 이 부르는 함수가 전부 실제로 정의돼 있다',
    why: '없는 함수를 부르면 버튼은 멀쩡해 보이고 눌러도 ReferenceError 만 난다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      const missing = [];
      for (const el of page.qa('[onclick]')) {
        const code = el.getAttribute('onclick');
        for (const name of new Set((code.match(/(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(/g) || []).map((m) => m.replace(/\s*\($/, '')))) {
          if (typeof page.win[name] === 'undefined' && !['if', 'return', 'typeof', 'function', 'new'].includes(name)) {
            missing.push(`${name}()  ← ${(el.textContent || el.id || '').trim().slice(0, 24)}`);
          }
        }
      }
      check.ok(missing.length === 0, '없는 함수를 부르는 버튼이 0개다', missing.join(' | '));
      return page;
    },
  },

  {
    id: 'sweep.admin.no-dangling-element-ids',
    title: '스크립트가 찾는 id 가 화면에 실재한다',
    why: '화면에서 지운 요소를 초기화가 계속 만지다가 그 뒤 초기화를 통째로 막은 적이 있다(2026-08-23)',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      const src = page.qa('script').map((s) => s.textContent || '').join('\n');
      // $('xxx') · getElementById('xxx') 형태의 **고정 문자열** id 만 본다(템플릿 id 는 런타임 생성).
      const ids = new Set();
      for (const m of src.matchAll(/\$\(\s*'([a-z0-9-]+)'\s*\)/g)) ids.add(m[1]);
      for (const m of src.matchAll(/getElementById\(\s*'([a-z0-9-]+)'\s*\)/g)) ids.add(m[1]);
      check.gte(ids.size, 40, `검사한 id 수 (${ids.size})`);

      // 런타임에 삽입되는 카드까지 그린 뒤에 확인한다.
      for (const sel of ['button[onclick="loadGradeEditor()"]', 'button[onclick="loadKeywords()"]']) {
        const b = page.q(sel);
        if (b) b.click();
      }
      await page.settle();

      // 문서 작업대의 결정 폼은 선택 문서를 그릴 때 동적으로 생긴다. 정적 HTML에 없다는
      // 이유로 미아 id로 오인하지 않도록 정식 Product Shell 경로로 한 번 연다.
      const documents = page.q('[data-product-nav="documents"]');
      if (documents && !documents.classList.contains('pf-hidden')) {
        documents.click();
        await page.settle();
        await page.until(() => !!page.$('wb-decision-action'), 3000);
      }

      /* 화면에서 뺐지만 코드가 **없을 때를 방어하며** 남겨 둔 것들.
       * 새로 생긴 미아 id 는 여기 없으니 그대로 실패한다 — 그것이 이 시험의 목적이다.
       *   sample-select : 2026-08-23 분류 카드에서 제거. populateSamples 가 `if(!sel) return`.
       *   btn-batch     : 2026-08-23 일괄 분류 버튼 제거. runBatch 가 `$('btn-batch')||{}`.
       * (이 두 개를 되살리거나 방어를 없애면 이 목록도 함께 손볼 것) */
      const GUARDED = new Set(['sample-select', 'btn-batch']);
      for (const id of GUARDED) {
        check.ok(!page.$(id), `방어 목록의 ${id} 는 실제로 화면에 없다 — 되살아났으면 목록에서 뺄 것`);
      }
      const dangling = [...ids].filter((id) => !page.$(id) && !GUARDED.has(id));
      check.ok(dangling.length === 0, '스크립트가 찾는 id 가 전부 화면에 있다(방어 목록 제외)', dangling.join(', '));
      return page;
    },
  },

  {
    id: 'sweep.nav.links-resolve',
    title: '공용 메뉴의 링크가 전부 실재하는 주소를 가리킨다',
    why: '화면 사이를 오갈 수 없으면 주소를 외워 쳐야 한다 — 실제로 그런 시기가 있었다',
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      const links = page.qa('.cnav-link');
      // [2026-08-24] 4 → 3. 「검증문서 검수 목록」이 빠졌다(console_nav.py) — 그 항목만
      // 다른 화면이 아니라 관리자 콘솔 자신의 내부 앵커를 가리켰다. 이 시험이 잠그는 것은
      // 개수가 아니라 "링크가 전부 실재하는 주소를 가리킨다"는 아래 반복문이다.
      // [2026-08-24] 3 → 2. 「검증문서 후보 관리」를 메뉴에서 뺐다(console_nav 주석 참조).
      check.gte(links.length, 2, `공용 메뉴 링크 수 (${links.length})`);

      for (const a of links) {
        const href = a.getAttribute('href');
        if (!href) {
          check.ok(a.getAttribute('aria-current') === 'page', `현재 화면 표시가 링크 대신 있다: ${a.textContent.trim()}`);
          continue;
        }
        // 정적 콘솔 주소만 이 하니스가 실제로 서빙한다. API 화면(golden/*.html)은
        // 서버 렌더라 mock 에 없으므로 형식만 본다.
        if (href.startsWith('/console/')) {
          const r = await fetch(server.url + href.split('#')[0]);
          check.eq(r.status, 200, `${a.textContent.trim()} → ${href} 가 열린다`);
        } else {
          check.matches(href, /^\/api\/v1\/golden\/.+\.html/, `${a.textContent.trim()} → API 화면 주소 형식이 맞다: ${href}`);
        }
      }
      return page;
    },
  },

  {
    id: 'sweep.static.assets-load',
    title: '화면이 참조하는 정적 자원이 전부 200 으로 온다',
    why: '<script src> 가 404 여도 화면은 그냥 뜨고 기능만 조용히 사라진다',
    needsMock: true,
    async run({ server, check }) {
      for (const pageUrl of ['/console/admin.html', '/console/index.html']) {
        const html = await (await fetch(server.url + pageUrl)).text();
        const refs = [
          ...[...html.matchAll(/<script[^>]+src="([^"]+)"/g)].map((m) => m[1]),
          ...[...html.matchAll(/<link[^>]+href="([^"]+)"/g)].map((m) => m[1]),
          ...[...html.matchAll(/<img[^>]+src="(\.[^"]+)"/g)].map((m) => m[1]),
        ].filter((u) => !u.startsWith('data:') && !u.startsWith('http'));
        check.gte(refs.length, 2, `${pageUrl}: 참조 자원 수 (${refs.length})`);
        for (const ref of refs) {
          const abs = new URL(ref, server.url + pageUrl).toString();
          const r = await fetch(abs);
          check.eq(r.status, 200, `${pageUrl} → ${ref}`);
        }
      }
      return null;
    },
  },

  {
    id: 'sweep.navigation.every-view-has-visible-content',
    title: '어느 Product Shell 메뉴를 열어도 빈 화면이 되지 않는다',
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      for (const nav of page.qa('[data-product-nav]').filter((n) => !n.classList.contains('pf-hidden'))) {
        nav.click();
        await page.settle();
        const view = nav.dataset.productNav;
        const productPanel = page.$(`product-${view}`);
        const navigationStatus = page.$('product-view-status');
        const viewLabel = {
          overview: '운영 개요', documents: '검증 문서 검토·결정', experts: '검수자 배정·진행',
          policy: '분류 정책·참고 규정', models: '학습·평가·릴리스', audit: '감사 기록', settings: '연결·운영 도구',
        }[view];
        check.includes(
          navigationStatus?.textContent || '',
          viewLabel,
          `${view} 메뉴 전환이 보조기술 상태 영역에 안내된다`,
          navigationStatus?.textContent || '',
        );
        if (productPanel) {
          check.ok(page.visible(productPanel), `${view} 메뉴의 본문이 보인다`);
          check.gte(productPanel.querySelectorAll('section,article,div').length, 1, `${view} 메뉴에 내용 골격이 있다`);
        } else {
          // policy/models/audit/settings는 검증된 기존 카드 묶음을 쓰되, 이제는 모든 카드가
          // 명시적인 Product View 소유권을 가진다. 구 data-pane으로 화면을 고르면 두 메뉴
          // 체계가 다시 섞이는 회귀가 된다.
          const selector = `.grid > [data-product-view="${view}"]:not([data-product-legacy])`;
          const shown = page.qa(selector).filter((card) => page.visible(card));
          check.gte(shown.length, 1, `${view} 메뉴의 소유 카드 본문이 보인다`);
        }
        const connectionSettings = page.qa('[data-product-view="settings"]').filter((s) => page.visible(s));
        if (view === 'settings') {
          check.gte(connectionSettings.length, 1, '연결·운영 도구 메뉴에서만 운영 연결 상태를 연다');
          const settings = connectionSettings[0];
          check.ok(settings.classList.contains('settings-card'), '설정 화면이 공용 설정 카드 표현을 사용한다');
          check.ok(settings.textContent.includes('운영 연결'), '설정 화면이 운영 연결 상태를 표시한다');
          check.ok(settings.querySelector('.settings-stack')?.hasAttribute('data-tech'), '기술 연결 설정과 시연 정리는 기본 화면에서 숨긴다');
        } else {
          check.eq(connectionSettings.length, 0, `${view} 메뉴에 연결·인증 상세가 중복 노출되지 않는다`);
        }
      }
      const unownedLegacyCards = page.qa('.grid > [data-pane]').filter((card) => !card.dataset.productView);
      check.eq(unownedLegacyCards.length, 0, '모든 기존 카드는 Product View 소유권을 명시한다', unownedLegacyCards.map((card) => card.id || card.textContent.trim().slice(0, 24)).join(' | '));
      check.eq(page.errors.length, 0, '메뉴를 다 눌러도 오류가 없다', page.errors.map((e) => e.message).join(' | '));
      return page;
    },
  },
];
