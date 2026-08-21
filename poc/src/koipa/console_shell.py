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
body{margin:0;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;line-height:1.45}
.top{height:84px;border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 34px;gap:18px}
.mark{width:38px;height:38px;display:flex;align-items:center;justify-content:center;color:#1b4ea8}
.brand{font-size:17px;font-weight:900;letter-spacing:1px}
.divider{height:23px;border-left:1px solid #cfcfcb}
.product{font-size:14px;font-weight:800;letter-spacing:.8px;color:#9da0a1}
.topmid{margin:auto;color:#777;font-size:14px}
.revision{margin-left:14px;padding:7px 11px;border:1px solid var(--line);border-radius:6px;font:700 12px ui-monospace,monospace;color:#555}
.dot{display:inline-block;width:10px;height:10px;background:#bf6c00;border-radius:50%;margin-right:8px;box-shadow:0 0 0 4px #fff4df}
.workspace{font-size:11px;font-weight:800;color:#949494;letter-spacing:.5px}
.frame{display:grid;grid-template-columns:326px minmax(0,1fr);min-height:calc(100vh - 84px)}
.side{border-right:1px solid var(--line);padding:42px 28px;display:flex;flex-direction:column}
.main{padding:68px min(6.5vw,110px) 90px;max-width:1500px}
.cap{font:800 12px ui-monospace,monospace;letter-spacing:.7px;color:#969da3}
.eyebrow{font:800 12px ui-monospace,monospace;letter-spacing:.8px;color:#8a9298}
.hero{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:35px;padding-bottom:56px;border-bottom:1px solid var(--line)}
.gate{border:1px solid var(--line);border-left:5px solid var(--red);align-self:center;padding:25px 24px;min-height:218px}
.gate .glabel{font:12px ui-monospace,monospace;color:#8a949a}
.gate .status{display:inline-block;border-radius:20px;background:var(--green);color:#087341;padding:5px 10px;font-size:11px;font-weight:800}
.actions{display:flex;gap:9px;margin-top:20px}
.section{margin-top:76px}
.sectionTop{display:flex;align-items:end;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding-bottom:23px}
.secNum{display:inline-block;border:1px solid #dadad7;padding:5px 8px;border-radius:4px;color:#92999f;font:12px ui-monospace,monospace;margin-right:13px;vertical-align:8px}
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
.summary{margin-top:40px;border-top:3px solid #111;background:#fafaf8;display:grid;grid-template-columns:1.18fr repeat(4,1fr)}
.summaryIntro,.metric{padding:29px;border-right:1px solid var(--line);min-height:154px}
.metric:last-child{border-right:0}
.metric .mcap{font-size:12px;color:#8a9299}
.flash{min-height:20px;color:#087341;font-size:13px;margin:14px 0}
.flash.error{color:#bf2337}
.empty{padding:45px 14px;color:#8a9299}
.pill{display:inline-block;border-radius:18px;padding:5px 9px;font-size:11px;font-weight:800}
"""


# 반응형 규칙은 **스타일시트 맨 뒤**에 붙여야 한다.
# 앞쪽(SHELL_CSS 자리)에 두면 뒤에 오는 기본 규칙이 덮어 버린다 — 실측으로 확인한 두 곳:
#   .rowhead{display:none}      <- 뒤의 `.rowhead,.candidate{display:grid}` 가 이긴다
#   .filters input{width:100%}  <- 뒤의 `.filters input{width:170px}` 가 이긴다
# 그래서 SHELL_CSS 와 분리해 두고, 각 화면이 자기 스타일 끝에 이어 붙인다.
# 화면 고유 반응형(manage 의 .detail·.candidate 등)은 각자 파일에 남긴다.
SHELL_MEDIA_CSS = """
@media(max-width:1050px){.frame{grid-template-columns:1fr}.side{display:none}.main{padding:45px 30px}.hero{grid-template-columns:1fr}.summary{grid-template-columns:repeat(3,1fr)}.summaryIntro{grid-column:1/-1}}
@media(max-width:700px){.top{padding:0 16px}.topmid,.workspace{display:none}.main{padding:35px 18px}.hero h1{letter-spacing:-2.5px}.summary{grid-template-columns:repeat(2,1fr)}.rowhead{display:none}.section h2{font-size:31px}.sectionTop{align-items:start;flex-direction:column}.sectionTop p{margin-left:0}.filters{width:100%}.filters input{width:100%}}
"""
