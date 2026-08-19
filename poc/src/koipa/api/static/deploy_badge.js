/* 배포 주체·화면 종류 배지 — 지재원/고객사 이중배포에서 "지금 보는 콘솔이 누구 것이고
 * 어떤 화면인지"를 nav 와 브라우저 탭 제목에 박아 혼동을 없앤다.
 *
 * 왜 필요한가: 지재원(:8000)과 고객사(:8001)는 동일 이미지·동일 정적 콘솔을 서빙한다.
 * 화면이 픽셀 단위로 같아서 포트를 놓치면 어느 쪽을 조작 중인지 알 수 없다(2026-08-02 실사고:
 * 로컬 도커가 포트를 선점해 테스트서버 대신 로컬 콘솔을 열고도 눈치채지 못함).
 *
 * 데이터 출처: GET /api/v1/healthz 의 deploy_profile — 서버가 스스로 보고하는 값이라
 * 화면이 임의로 단정하지 않는다. 미상/실패 시 추측하지 않고 "확인 실패"로 표기한다.
 *
 * 사용: <script src="./deploy_badge.js" data-screen="관리자"></script>
 */
(function () {
  'use strict';

  var SELF = document.currentScript;
  var SCREEN = (SELF && SELF.getAttribute('data-screen')) || '';

  // deploy_profile → 배포 주체. 프로파일은 "역량 tier"라 소속을 직접 담지 않지만,
  // 본 사업 토폴로지(지재원=학습 공장 / 고객사=폐쇄망 운영)에서 1:1로 대응한다.
  var PROFILES = {
    'full-train':   { site: '지재원',      role: '모델 공장',   cls: 'jjw' },
    'onprem-local': { site: '고객사',      role: '폐쇄망 운영', cls: 'cust' },
    'lite-cloud':   { site: '오픈망 파일럿', role: '경량',       cls: 'pilot' },
    'lite-noapi':   { site: '로컬·개발',   role: 'dryrun',     cls: 'dev' }
  };

  var CSS = [
    '.site-badge{display:inline-flex;align-items:center;gap:0;margin-left:10px;',
    '  font-size:11.5px;font-weight:600;line-height:1;white-space:nowrap;vertical-align:middle;}',
    '.site-badge>span{padding:4px 8px;border:1px solid transparent;}',
    '.site-badge .site-name{color:#fff;letter-spacing:-.01em;}',
    '.site-badge .site-role{background:#fff;color:#525252;border-color:rgba(0,0,0,.16);border-left:0;font-weight:500;}',
    '.site-badge .site-kind{background:#f4f4f4;color:#0a0a0a;border-color:rgba(0,0,0,.16);border-left:0;}',
    '.site-badge.jjw   .site-name{background:#0a0a0a;}',
    '.site-badge.cust  .site-name{background:#0070f3;}',
    '.site-badge.pilot .site-name{background:#d97706;}',
    '.site-badge.dev   .site-name{background:#737373;}',
    '.site-badge.unknown .site-name{background:#fff;color:#737373;border-color:rgba(0,0,0,.16);}',
    '@media (max-width:720px){.site-badge .site-role{display:none;}}'
  ].join('\n');

  function injectStyle() {
    var st = document.createElement('style');
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function buildBadge(info, profile) {
    var b = document.createElement('span');
    b.className = 'site-badge ' + info.cls;
    b.title = 'deploy_profile=' + profile + ' — 이 콘솔이 붙어 있는 배포 주체(서버 /healthz 보고값)';

    var name = document.createElement('span');
    name.className = 'site-name';
    name.textContent = info.site;
    b.appendChild(name);

    if (info.role) {
      var role = document.createElement('span');
      role.className = 'site-role';
      role.textContent = info.role;
      b.appendChild(role);
    }
    if (SCREEN) {
      var kind = document.createElement('span');
      kind.className = 'site-kind';
      kind.textContent = SCREEN;
      b.appendChild(kind);
    }
    return b;
  }

  function mount(badge) {
    // [2026-08-20] 상단이 header.top 으로 통일되면서 종전 셀렉터(.nav-inner 계열)가 전부
    // 빗나갔다. 그러면 마지막 폴백이 걸려 배지가 **헤더 밖 body 맨 위**에 떨어진다.
    // header.top 을 먼저 본다 — 서버 렌더 화면(golden_review_html)은 같은 자리에 서버가
    // 넣으므로, 여기 순서는 정적 2면(admin.html·index.html)을 위한 것이다.
    var top = document.querySelector('header.top');
    if (top) { top.appendChild(badge); return; }
    // 옛 골격이 남아 있는 화면을 위한 경로. 브랜드가 <a> 이거나 <span> 이라 셋을 본다.
    var anchor = document.querySelector('.nav-inner .brand, .nav-inner > a, .nav-inner > span');
    if (anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(badge, anchor.nextSibling);
      return;
    }
    var inner = document.querySelector('.nav-inner');
    if (inner) { inner.insertBefore(badge, inner.firstChild); return; }
    document.body.insertBefore(badge, document.body.firstChild);
  }

  function applyTitle(info) {
    var tag = SCREEN ? info.site + '·' + SCREEN : info.site;
    document.title = '[' + tag + '] ' + document.title;
  }

  function render(profile, ok) {
    var info = ok
      ? (PROFILES[profile] || { site: profile || '프로파일 미상', role: '', cls: 'unknown' })
      : { site: '서버 확인 실패', role: '', cls: 'unknown' };
    injectStyle();
    mount(buildBadge(info, ok ? profile : 'n/a'));
    applyTitle(info);
  }

  function start() {
    fetch('/api/v1/healthz', { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function (d) { render(String(d.deploy_profile || ''), true); })
      .catch(function () { render('', false); });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
