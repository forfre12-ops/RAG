"""콘솔 화면의 공용 껍데기 — 레이아웃 CSS 한 곳.

왜(2026-08-18). 후보 관리(manage)와 검수·서명(signoff)이 **다른 골격**이었다.

    manage    header.top -> frame -> [side | main -> hero + summary + section + list]
    signoff   header.top -> signbar -> container (필터 + 카드)

머리만 같고 그 아래가 딴 화면이라, 검수자가 두 화면을 오갈 때 다른 시스템처럼 보였다.
색·글꼴을 맞춘 뒤에도 이질감이 남은 이유가 이 골격 차이다.

값의 출처는 manage.html(GET /api/v1/golden/candidates/manage.html)이다 — 골격을 그쪽에서
그대로 옮겨 왔고, 검수·서명 화면이 그 모양을 따라간다.

⚠ 선택자는 **manage 원문 그대로** 쓴다. 2026-08-19 실측에서 이 모듈이 manage 의 정확한
  사본이 아니었다 — `.glabel`/`.status`/`.btn.sm` 을 bare 로 적어 놓았고,
  `.summaryIntro,.metric` 결합 규칙을 둘로 쪼개면서 `.summaryIntro` 에 `grid-column:1/-1`
  을 상시로 줘 버렸다(manage 는 @media(1050px) 안에서만). 그대로 합쳤으면 요약 카드가
  padding·border·min-height 를 잃고 전폭으로 퍼졌다. 값을 바꿀 때 **manage 화면 렌더를
  대조**하는 이유가 이것이다.

  ⛔ 종전 주석의 "manage.html 이 감리 정본 화면설계서 UI-01/UI-04 에 실린 화면" 은 사실이
  아니었다(2026-08-19 확인). 화면설계서에 manage.html 은 없고, UI-01=/demo/index.html ·
  UI-04·UI-05=골든 검수·서명 화면이다.
"""

SHELL_CSS = """
/* [2026-08-21] 본문 글꼴을 다섯 화면 한 벌로. 종전에는 이 두 곳만 Arial 계열이라
   같은 한글 문서를 보는데 화면마다 글자 모양이 달랐다(관리자 콘솔·등급 시연은
   Pretendard 계열). 시스템 폰트를 앞세우고 한글 폴백을 뒤에 둔다. */
body{margin:0;background:var(--bg-surface);color:var(--text);font-family:var(--font-sans);line-height:1.6}
.top{height:var(--shell-header-height);border-bottom:1px solid var(--border-mid);display:flex;align-items:center;padding:0 34px;gap:18px;background:var(--bg)}
.mark{width:38px;height:38px;display:flex;align-items:center;justify-content:center;color:#1b4ea8}
.brand{font-size:17px;font-weight:900;letter-spacing:1px}
.divider{height:23px;border-left:1px solid #cfcfcb}
.product{font-size:14px;font-weight:800;letter-spacing:.8px;color:#9da0a1}
.topmid{margin:auto;color:#777;font-size:14px}
.revision{margin-left:14px;padding:7px 11px;border:1px solid var(--line);border-radius:6px;font:700 12px ui-monospace,monospace;color:#555}
.dot{display:inline-block;width:10px;height:10px;background:#bf6c00;border-radius:50%;margin-right:8px;box-shadow:0 0 0 4px #fff4df}
.workspace{font-size:11px;font-weight:800;color:#949494;letter-spacing:.5px}
.frame{display:grid;grid-template-columns:var(--shell-sidebar-width) minmax(0,1fr);gap:var(--shell-gap);align-items:start;max-width:var(--shell-content-max);min-height:calc(100vh - var(--shell-header-height));margin:0 auto;padding:24px var(--shell-page-gutter) 112px;box-sizing:border-box}
.side{position:sticky;top:calc(var(--shell-header-height) + 22px);border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--bg);box-shadow:var(--shell-panel-shadow);padding:15px 12px;min-height:438px;display:flex;flex-direction:column;box-sizing:border-box}
.main{min-width:0;padding:4px 0 0;max-width:none}
.cap{font:800 10px var(--font-mono);letter-spacing:.09em;color:var(--text-dim)}
.eyebrow{font:800 11px var(--font-mono);letter-spacing:.08em;color:var(--text-dim)}
.hero{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:var(--shell-gap);padding:4px 0 28px;border-bottom:1px solid var(--border)}
  .gate{border:1px solid var(--border);border-left:3px solid var(--text);border-radius:var(--radius-lg);background:var(--bg);align-self:start;padding:20px;min-height:0;box-shadow:0 2px 8px rgba(9,9,11,.025)}
.gate .glabel{font:12px ui-monospace,monospace;color:#8a949a}
.gate .status{display:inline-block;border-radius:20px;background:var(--green);color:#087341;padding:5px 10px;font-size:11px;font-weight:800}
.actions{display:flex;gap:9px;margin-top:20px}
.section{margin-top:32px}
.sectionTop{display:flex;align-items:end;justify-content:space-between;gap:20px;border-bottom:1px solid var(--border);padding-bottom:16px}
.secNum{display:inline-block;border:1px solid var(--border-strong);padding:4px 7px;border-radius:var(--radius-sm);color:var(--text-dim);font:11px var(--font-mono);margin-right:10px;vertical-align:5px}
.list{width:100%}
.rowhead{padding:13px 12px;background:#f7f7f5;color:#899199;font:11px ui-monospace,monospace}
.btn{border:1px solid #d8d8d5;background:#fff;padding:11px 14px;font-weight:800;font-size:13px;cursor:pointer}
.btn.black{background:#111;color:#fff;border-color:#111}
/* [2026-08-21] `.btn` 한 이름이 화면마다 정반대를 뜻했다 —
   여기(골든 2면)는 흰 테두리=보조인데 admin.html:75·styles.css:423 은 검정 채움=주요다.
   그래서 이 두 화면만 주요동작을 `btn black` 으로 따로 써 왔다.
   다수 규약(주요=검정)에 맞추되 **CSS 기본값은 뒤집지 않는다** — 이 파일의
   `class="btn"` 5개가 한꺼번에 검정이 되어 버린다. 대신 보조동작 별칭을 추가해
   세 화면이 같은 이름을 쓸 수 있게 한다: 주요=`btn black`, 보조=`btn sec`/`btn ghost`. */
.btn.sec,.btn.ghost{background:#fff;color:#111;border-color:#d8d8d5}
.btn.primary{background:#111;color:#fff;border-color:#111}
.btn:focus-visible{outline:2px solid #0070f3;outline-offset:2px}
.viewbar .btn.sm{padding:6px 11px;font-size:12px}
.summary{margin-top:24px;border:1px solid var(--border);border-top:3px solid var(--text);border-radius:var(--radius-lg);background:var(--bg);display:grid;grid-template-columns:1.18fr repeat(4,1fr);overflow:hidden;box-shadow:0 2px 8px rgba(9,9,11,.025)}
.summaryIntro,.metric{padding:20px;border-right:1px solid var(--border);min-height:132px}
.metric:last-child{border-right:0}
.metric .mcap{font-size:12px;color:#8a9299}
.flash{min-height:20px;color:#087341;font-size:13px;margin:14px 0}
.flash.error{color:#bf2337}
.empty{padding:45px 14px;color:#8a9299}
.pill{display:inline-block;border-radius:18px;padding:5px 9px;font-size:11px;font-weight:800}

/* 후보 관리·서명 화면의 개별 CSS보다 우선하는 공용 작업공간 규격.
   각 화면의 업무 정보는 달라도, 좌측 작업공간·제목·카드 밀도는 관리자 콘솔과 같아야 한다. */
.frame .side .workname{margin:10px 8px 4px;font-size:16px;font-weight:800;letter-spacing:-.02em;color:var(--text)}
.frame .side .workdesc{margin:0 8px;font-size:12px;line-height:1.55;color:var(--text-dim)}
.frame .side .branch{margin:8px 8px 0;font:700 11px var(--font-mono);color:var(--text-dim)}
.frame .side .nav,.frame .side .sidenav{margin:18px 0 0;padding:0;border:0;display:flex;flex-direction:column;gap:3px}
.frame .side .nav a,.frame .side .sidenav a{display:flex;align-items:center;gap:10px;margin:0;padding:8px 10px;border:1px solid transparent;border-left:1px solid transparent;border-radius:var(--radius);color:var(--text-dim);font-size:13px;text-decoration:none}
.frame .side .nav a span,.frame .side .sidenav a span{font:700 10px var(--font-mono);color:inherit;padding:0}
.frame .side .nav a.active,.frame .side .sidenav a.active{background:var(--bg-inverse);border-color:var(--bg-inverse);color:#fff;font-weight:800;padding-left:10px}
.frame .side .ledger{margin:18px 5px 2px;padding:14px 4px 1px;border-top:1px solid var(--border);font-size:11px;color:var(--text-dim)}
.frame .side .ledger b{color:var(--text)}
.frame .hero h1{font-size:clamp(28px,3vw,36px);line-height:1.18;letter-spacing:-.045em;margin:12px 0 10px;font-weight:850;color:var(--text)}
.frame .hero h1 em{color:var(--text)}
.frame .hero p{max-width:720px;margin:0;color:var(--text-soft);font-size:14px;line-height:1.65}
.frame .hero .gate strong{font-size:28px;letter-spacing:-.04em;margin:8px 0;color:var(--text)}
.frame .section h2{font-size:24px;letter-spacing:-.035em;color:var(--text)}
.frame .sectionTop p{margin:6px 0 0;color:var(--text-soft);font-size:13px}
.frame .metric b{font-size:26px;letter-spacing:-.04em;color:var(--text)}
.frame .summaryIntro h2{font-size:18px;color:var(--text)}
"""


# 반응형 규칙은 **스타일시트 맨 뒤**에 붙여야 한다.
# 앞쪽(SHELL_CSS 자리)에 두면 뒤에 오는 기본 규칙이 덮어 버린다 — 실측으로 확인한 두 곳:
#   .rowhead{display:none}      <- 뒤의 `.rowhead,.candidate{display:grid}` 가 이긴다
#   .filters input{width:100%}  <- 뒤의 `.filters input{width:170px}` 가 이긴다
# 그래서 SHELL_CSS 와 분리해 두고, 각 화면이 자기 스타일 끝에 이어 붙인다.
# 화면 고유 반응형(manage 의 .detail·.candidate 등)은 각자 파일에 남긴다.
SHELL_MEDIA_CSS = """
@media(max-width:1050px){.frame{grid-template-columns:1fr;padding:18px var(--shell-page-gutter) 96px}.side{display:none}.main{padding:0}.hero{grid-template-columns:1fr}.summary{grid-template-columns:repeat(3,1fr)}.summaryIntro{grid-column:1/-1}}
@media(max-width:700px){.top{padding:0 16px}.topmid,.workspace{display:none}.frame{padding:14px 12px 84px}.main{padding:0}.hero h1{letter-spacing:-2.5px}.summary{grid-template-columns:repeat(2,1fr)}.rowhead{display:none}.section h2{font-size:31px}.sectionTop{align-items:start;flex-direction:column}.sectionTop p{margin-left:0}.filters{width:100%}.filters input{width:100%}}
"""
