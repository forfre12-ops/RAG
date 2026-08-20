/* 골든 잡 목록 패널 — GET /api/v1/golden/jobs
 *
 * 왜 필요한가: 콘솔은 마지막 golden_job_id 를 JS 변수(LAST_GOLDEN_JOB_ID)에만 들고 있다.
 * 새로고침하면 사라져서, 229건짜리 후보를 절반쯤 서명하다 F5 를 누르면 그 잡으로 돌아갈
 * 방법이 없었다(2026-08-02 실환경 점검).
 *
 * 왜 별도 파일인가: admin.html 은 다른 작업(합성 샘플 패널)이 동시에 편집 중이라, 같은
 * 파일에 패널을 직접 써넣으면 커밋 시 남의 미완성 변경을 함께 쓸어담게 된다. admin.html
 * 에는 <script> 한 줄만 추가하고 본체는 여기 둔다(충돌면적 최소화).
 *
 * admin.html 인라인 스크립트가 먼저 실행되므로 cfg()/api()/log()/esc()/LAST_GOLDEN_JOB_ID/
 * loadGoldenJob() 을 그대로 쓴다(클래식 스크립트는 전역 렉시컬 스코프를 공유).
 */
(function () {
  'use strict';

  var PANEL_HTML = [
    // data-pane/order — 이 카드는 런타임에 삽입되므로 admin.html 의 탭 전환 규칙을
    // 직접 달아 준다(없으면 어느 탭에서나 항상 떠서 탭 분리가 무의미해진다).
    '<section class="card col-span" id="gold-jobs-card" data-pane="review" style="order:3">',
    '  <div class="card-head">',
    '    <div class="ttl"><span class="step-no">3</span> 골든 잡 목록</div>',
    '    <span class="ep">GET /golden/jobs</span>',
    '  </div>',
    '  <div class="card-body">',
    '    <div class="btn-row">',
    '      <span class="sec-note" style="margin-right:auto;" id="gold-jobs-note">',
    '        생성·등록한 골든 잡을 골라 검수/서명으로 이동합니다. 새로고침 후에도 남습니다.',
    '      </span>',
    '      <button class="btn sec sm" onclick="loadGoldenJobList()">목록 새로고침</button>',
    '    </div>',
    '    <div id="gold-jobs-body" style="margin-top:10px;">',
    '      <span style="color:var(--text-faint);font-size:12px;">[목록 새로고침]을 눌러 조회하세요.</span>',
    '    </div>',
    '  </div>',
    '</section>'
  ].join('\n');

  function mount() {
    // 골든 빌더 카드 바로 뒤에 붙인다(검수 흐름상 인접).
    var summary = document.getElementById('gold-summary');
    var anchor = summary && summary.closest ? summary.closest('section.card') : null;
    var wrap = document.createElement('div');
    wrap.innerHTML = PANEL_HTML;
    var panel = wrap.firstElementChild;
    if (anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(panel, anchor.nextSibling);
    } else {
      var grid = document.querySelector('.wrap') || document.body;
      grid.appendChild(panel);
    }
    // 이 스크립트는 인라인 초기화(initPane) 이후에 로드된다. 방금 삽입한 카드에는
    // 탭 표시 규칙이 아직 적용되지 않았으므로 현재 탭 기준으로 한 번 다시 적용한다.
    if (typeof window.showPane === 'function') {
      var cur = 'review';
      try { cur = sessionStorage.getItem('koipa_admin_pane') || 'review'; } catch (e) {}
      window.showPane(cur);
    }
    // 이제야 #gold-jobs-card 가 문서에 있다 — 주소에 그 앵커가 실려 왔다면 지금 처리한다
    // (공용 메뉴의 「골든셋 검수」가 이 카드를 가리킨다).
    if (typeof window.gotoHash === 'function') window.gotoHash();
  }

  function fmtTime(iso) {
    if (!iso) return '—';
    // 서버가 UTC ISO 로 준다. 초 단위까지만 — 목록 식별에 충분하다.
    return String(iso).replace('T', ' ').replace(/\.\d+/, '').replace('Z', '');
  }

  function statusPill(status, error) {
    if (error) return '<span class="pill warn">실패</span>';
    if (status === 'done') return '<span class="pill ok">완료</span>';
    return '<span class="pill">' + esc(status || '-') + '</span>';
  }

  window.loadGoldenJobList = async function loadGoldenJobList() {
    var box = document.getElementById('gold-jobs-body');
    if (!box) return;
    box.innerHTML = '<span style="color:var(--text-faint);font-size:12px;">조회 중…</span>';
    try {
      var d = await api('GET', '/golden/jobs?limit=20');
      var jobs = (d && d.jobs) || [];
      // [실측 2026-08-08] 서버는 ordering:"best_effort" 로 정직하게 고지하지만(Redis SCAN 순서라
      // 최근순 보장 없음), 화면이 그 순서를 그대로 그리면 방금 만든 잡이 목록 중간에 박힌다.
      // 실서버 응답이 실제로 07:02 → 07:05 오름차순으로 왔다. 고지만으로는 부족하다 —
      // 감리 시연 중 "방금 만든 잡"을 눈으로 찾아야 하는 상황이 나온다.
      // submitted_at(ISO8601·UTC)은 문자열 비교로 시간순이 성립하므로 여기서 내림차순 정렬한다.
      // 값이 없는 행은 뒤로 보낸다(빈 문자열이 항상 작다).
      jobs = jobs.slice().sort(function (a, b) {
        return String(b.submitted_at || '').localeCompare(String(a.submitted_at || ''));
      });
      if (!jobs.length) {
        box.innerHTML = '<span style="color:var(--text-faint);font-size:12px;">'
          + '골든 잡이 없습니다 — 위에서 후보를 생성하거나 슬레이트를 등록하세요.</span>';
        return;
      }
      var rows = jobs.map(function (j) {
        var cur = (j.job_id === LAST_GOLDEN_JOB_ID) ? ' style="background:var(--accent-soft)"' : '';
        var gold = (j.gold_count == null) ? '—' : String(j.gold_count);
        var unc = (j.uncertain_count == null) ? '—' : String(j.uncertain_count);
        var kind = (j.kind === 'golden_register') ? '슬레이트 등록' : '후보 빌드';
        return '<tr' + cur + '>'
          + '<td><code>' + esc(j.job_id.slice(0, 8)) + '</code></td>'
          + '<td>' + esc(kind) + '</td>'
          + '<td>' + statusPill(j.status, j.error) + '</td>'
          + '<td style="text-align:right">' + esc(gold) + '</td>'
          + '<td style="text-align:right">' + esc(unc) + '</td>'
          + '<td style="font-size:11px;color:var(--text-dim)">' + esc(fmtTime(j.submitted_at)) + '</td>'
          + '<td><button class="btn sec sm" onclick="pickGoldenJob(\'' + esc(j.job_id) + '\')">선택</button></td>'
          + '</tr>';
      }).join('');
      box.innerHTML =
        '<div class="table-wrap"><table style="width:100%;font-size:12px;border-collapse:collapse">'
        + '<thead><tr>'
        + '<th style="text-align:left">job</th><th style="text-align:left">종류</th>'
        + '<th style="text-align:left">상태</th><th style="text-align:right">gold</th>'
        + '<th style="text-align:right">uncertain</th><th style="text-align:left">생성</th><th></th>'
        + '</tr></thead><tbody>' + rows + '</tbody></table></div>'
        // 서버 정렬은 best-effort(Redis SCAN 순서)라, 화면에서 생성 시각으로 다시 정렬해 놓았다.
        // 그 사실을 밝혀 둔다 — "서버가 준 순서"와 "화면 순서"가 다르다는 것을 감춰선 안 된다.
        + (d.ordering === 'best_effort'
            ? '<p class="sec-note" style="margin-top:6px;">서버 정렬은 잡 저장소 기준 '
              + '<b>best-effort</b>(최근순 보장 아님)라, <b>생성 시각으로 최신순 재정렬</b>해 표시합니다. '
              + '목록이 20건으로 잘리므로 그보다 오래된 잡은 여기 없을 수 있습니다.</p>'
            : '');
      log('골든 잡 목록 ' + jobs.length + '건 조회', 'ok');
    } catch (e) {
      box.innerHTML = '<div class="warnbox">골든 잡 목록 조회 실패: '
        + esc(String(e.status || '')) + ' ' + esc((e.data && e.data.detail) || e.message || '') + '</div>';
    }
  };

  window.pickGoldenJob = function pickGoldenJob(id) {
    LAST_GOLDEN_JOB_ID = id;
    log('골든 잡 선택: ' + id, 'ok');
    if (typeof loadGoldenJob === 'function') loadGoldenJob();   // 요약 카드·검수/서명 버튼 갱신
    loadGoldenJobList();                                         // 현재 선택 행 하이라이트
    var card = document.getElementById('gold-summary');
    if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
