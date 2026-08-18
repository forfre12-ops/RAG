"""콘솔 화면의 공용 껍데기 — 레이아웃 CSS 한 곳.

왜(2026-08-18). 후보 관리(manage)와 검수·서명(signoff)이 **다른 골격**이었다.

    manage    header.top -> frame -> [side | main -> hero + summary + section + list]
    signoff   header.top -> signbar -> container (필터 + 카드)

머리만 같고 그 아래가 딴 화면이라, 검수자가 두 화면을 오갈 때 다른 시스템처럼 보였다.
색·글꼴을 맞춘 뒤에도 이질감이 남은 이유가 이 골격 차이다.

값의 출처는 manage.html(GET /api/v1/golden/candidates/manage.html)이다 — 골격을 그쪽에서
그대로 옮겨 왔고, 검수·서명 화면이 그 모양을 따라간다.

⚠ 지금 이 모듈을 쓰는 곳은 **검수·서명 화면 하나뿐이다**(golden_review_html.py).
  manage 화면은 golden.py 안에 같은 값의 사본을 그대로 갖고 있어, 여기서 값을 바꿔도
  그쪽은 바뀌지 않는다. 두 벌을 한 벌로 합치는 일은 아직 남아 있다(golden.py 가 이 모듈을
  import 하도록). 그때까지는 **두 곳을 같이 고쳐야 한다.**

⛔ 종전 주석의 "manage.html 이 감리 정본 화면설계서 UI-01/UI-04 에 실린 화면" 은 사실이
  아니었다(2026-08-19 확인). 화면설계서에 manage.html 은 없고, UI-01=/demo/index.html ·
  UI-04·UI-05=골든 검수·서명 화면이다.
"""

SHELL_CSS = """
body{margin:0;background:var(--paper);color:var(--ink);font-family:Arial,"Noto Sans KR",sans-serif;line-height:1.45}
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
.glabel{font:12px ui-monospace,monospace;color:#8a949a}
.status{display:inline-block;border-radius:20px;background:var(--green);color:#087341;padding:5px 10px;font-size:11px;font-weight:800}
.actions{display:flex;gap:9px;margin-top:20px}
.section{margin-top:76px}
.sectionTop{display:flex;align-items:end;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding-bottom:23px}
.secNum{display:inline-block;border:1px solid #dadad7;padding:5px 8px;border-radius:4px;color:#92999f;font:12px ui-monospace,monospace;margin-right:13px;vertical-align:8px}
.list{width:100%}
.rowhead{padding:13px 12px;background:#f7f7f5;color:#899199;font:11px ui-monospace,monospace}
.btn{border:1px solid #d8d8d5;background:#fff;padding:11px 14px;font-weight:800;font-size:13px;cursor:pointer}
.btn.black{background:#111;color:#fff;border-color:#111}
.btn.sm{padding:6px 11px;font-size:12px}
.summary{margin-top:40px;border-top:3px solid #111;background:#fafaf8;display:grid;grid-template-columns:1.18fr repeat(4,1fr)}
.summaryIntro{grid-column:1/-1}
.metric{padding:29px;border-right:1px solid var(--line);min-height:154px}
.metric:last-child{border-right:0}
.metric .mcap{font-size:12px;color:#8a9299}
.flash{min-height:20px;color:#087341;font-size:13px;margin:14px 0}
.flash.error{color:#bf2337}
.empty{padding:45px 14px;color:#8a9299}
.pill{display:inline-block;border-radius:18px;padding:5px 9px;font-size:11px;font-weight:800}
"""
