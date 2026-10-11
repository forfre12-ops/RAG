/* 9. 안전장치 — 프로파일별 화면 구성 · 서버 설정 반영.
 *
 * 쓰기 권한은 UI 토글이 아니라 서버의 인증·RBAC가 강제한다. 여기서는 화면 구성과
 * 서버 설정 반영처럼 브라우저에서만 확인할 수 있는 규칙을 검증한다.
 */

import { openPage, goToPane } from '../lib/page.mjs';

export const scenarios = [
  {
    id: 'safety.profile.customer-hides-factory-cards',
    title: '고객사 프로파일에서는 모델 공장 전용 카드가 사라진다',
    why: '한 벌의 콘솔을 두 곳에 서빙한다 — 회원사 관리자에게 지재원 전용 기능이 보이면 눌러도 0건이 나온다',
    needsMock: true,
    async run({ server, check }) {
      const { FIXTURES } = await import('../lib/server.mjs');
      server.overrides['GET /healthz'] = {
        ...FIXTURES['GET /healthz'],
        deploy_profile: 'onprem-local',
        operational_config: { ...FIXTURES['GET /healthz'].operational_config, synthesis_console_enabled: false },
      };

      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      // [2026-08-24] 2 → 1. 검증문서 카드가 게이트에서 빠졌다 — 그 카드에서 지재원 전용이던
      // AI 후보 생성이 화면에서 제거됐고, 남은 검수 시작·서명 열기는 고객사에도 필요하다
      // (onprem-local 은 활성화에 사람 서명 평가셋을 요구한다). 남은 대상은 합성 생성 카드다.
      const factory = page.qa('[data-capability="synthesis-console"]');
      check.gte(factory.length, 1, '모델 공장 전용 카드가 존재는 한다');
      for (const el of factory) check.ok(!page.visible(el), `숨겨졌다: ${el.querySelector('.ttl')?.textContent?.trim() || el.id}`);
      check.ok(!page.q('#profile-banner'), '공통 환경 안내 배너가 없다');

      // Product Shell 메뉴와 카드 모두 capability를 따라 숨는다.
      check.ok(page.q('[data-product-nav="synthesis"]')?.classList.contains('pf-hidden'), '합성 메뉴도 숨겨졌다');
      for (const el of factory) check.ok(!page.visible(el), `탭 전환 후에도 숨겨져 있다: ${el.id || 'card'}`);
      return page;
    },
  },

  {
    id: 'safety.profile.factory-shows-all',
    title: '지재원 프로파일에서는 전 구간 카드가 보인다',
    needsMock: true,
    async run({ server, check }) {
      const { FIXTURES } = await import('../lib/server.mjs');
      server.overrides['GET /healthz'] = {
        ...FIXTURES['GET /healthz'],
        operational_config: { ...FIXTURES['GET /healthz'].operational_config, synthesis_console_enabled: true },
      };

      const page = await openPage(server, '/console/admin.html');
      await page.settle();
      check.ok(!page.q('#profile-banner'), '지재원 화면에도 공통 환경 안내 배너가 없다');
      // 프로파일 숨김과 다른 Product Shell 화면의 비가시 상태를 섞지 않는다.
      const factoryCards = page.qa('[data-capability="synthesis-console"][data-product-view="synthesis"]');
      check.gte(factoryCards.length, 1, '합성 기능 카드는 존재한다');
      await goToPane(page, 'synthesis');
      for (const el of factoryCards) {
        check.ok(page.visible(el), '모의 문서 생성·검토 화면에서 합성 기능 카드가 보인다');
        check.ok(!el.classList.contains('pf-hidden'), '프로파일 숨김 클래스가 붙지 않았다');
      }
      return page;
    },
  },

  {
    id: 'safety.server-config.no-client-side-threshold',
    title: '검수 여부는 서버 판정만 쓴다 — 화면에 임계 입력칸이 없다',
    why: [
      '종전 화면에는 「목록 강조 기준」 입력칸이 있었고 「색칠만, 판정 무관」이라 적혀 있었지만,',
      '실제로는 confidence < 그 값 으로 검수/자동 배지를 계산했다. 서버 라우팅 게이트는 14개인데',
      '(services/review_reasons.py) 그중 신뢰도 게이트 하나만 흉내 낸 것이라, agreement-gate 로',
      '검수에 간 문서가 화면에는 「자동」으로 떴다. 칸을 다시 넣으면 그 거짓말이 돌아온다.',
    ].join(' '),
    needsMock: true,
    async run({ server, check }) {
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      check.ok(!page.$('gate'), '화면 임계 입력칸이 없다');
      check.ok(!page.$('srv-gate-v'), '읽기 전용 서버 임계 표시도 없다');
      // [2026-09-29] "신뢰도가 높아도 서버가 needs_review 면 화면이 「검수」로 말한다"는
      // 재검증은 뺐다 — 그 카드(cl-*)를 콘솔에서 뺐다. 같은 성격의 검증은 이제
      // 11_demo.mjs(demo.classify.escalation-explained 등, index.html)가 맡는다.
      return page;
    },
  },

  {
    id: 'safety.actor.identity-on-every-write',
    writes: true,
    title: '상태를 바꾸는 요청에는 행위자가 반드시 실린다',
    why: '행위자 없이 나가면 감사 로그에 신원이 안 남는다 — 감리의 핵심 항목이다. '
       + '[2026-09-29] 종전에는 POST /confirm 도 같이 봤으나, 그 UI(검수 큐 카드)를 콘솔에서 뺐다 — '
       + '남은 예시(키워드 추가) 하나로 로그인 세션 기반 행위자 계약을 확인한다.',
    async run({ server, check }) {
      server.overrides['GET /golden/candidates/session'] = { actor_id: 'admin-01', actor_role: 'admin' };
      const page = await openPage(server, '/console/admin.html');
      await page.settle();

      await goToPane(page, 'config');
      page.set('kw-n-keyword', '신규키워드');
      page.click(page.q('button[onclick="createKeyword()"]'));
      await page.settle();

      for (const [m, p] of [['POST', '/admin/keywords']]) {
        const c = server.lastCall(m, p);
        check.eq(c?.body?.actor?.user_id, 'admin-01', `${p}: 로그인 행위자 이름이 실렸다`);
        check.eq(c?.body?.actor?.role, 'admin', `${p}: 로그인 행위자 역할이 실렸다`);
        check.eq(c?.headers['x-actor-role'], 'admin', `${p}: 헤더에도 로그인 역할이 실렸다`);
      }
      return page;
    },
  },
];
