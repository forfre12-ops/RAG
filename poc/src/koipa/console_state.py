"""콘솔의 대기·실패·미존재 상태 화면.

검수 본문은 작업대여야 하지만, 잡이 아직 끝나지 않았거나 보관 기간이 끝난 경우까지
브라우저 기본 HTML을 내보내면 로그인·검수·오류 화면이 서로 다른 제품처럼 보인다.
상태 화면은 공통 헤더·글꼴·버튼·경고 색만 공유하는 가벼운 셸이다. 로그인처럼
사이드바를 강제하지 않아, 업무 맥락이 없는 상태에서도 읽기 쉽다.
"""
from __future__ import annotations

import html

from koipa.console_nav import HEADER_CSS, NAV_CSS, REVIEW_SCREEN_EXCLUDE, header_html


def render_console_state_html(
    *,
    title: str,
    eyebrow: str,
    message: str,
    detail: str = "",
    action_html: str = "",
    tone: str = "neutral",
) -> str:
    """외부 상태값을 이스케이프한 self-contained 콘솔 상태 화면을 만든다."""
    if tone not in {"neutral", "warning", "danger"}:
        tone = "neutral"
    safe_action = action_html  # 호출부가 만든 고정 마크업만 허용한다.
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>한국지식재산보호원 | {html.escape(title)}</title><style>"
        ':root{--ink:#132a52;--muted:#64748b;--line:#dbe3ee;--canvas:#f7f9fc;'
        '--blue:#1b4ea8;--warning:#a65a00;--danger:#b42318;--paper:#fff;'
        '--font:-apple-system,BlinkMacSystemFont,"Segoe UI","Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif}'
        '*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font:15px var(--font);line-height:1.65}'
        '.state-wrap{max-width:780px;margin:0 auto;padding:72px 32px}.state-card{border:1px solid var(--line);border-radius:14px;background:var(--paper);box-shadow:0 14px 35px rgba(15,23,42,.06);padding:38px}'
        '.state-eyebrow{font:800 11px ui-monospace,monospace;letter-spacing:.08em;color:var(--blue)}.state-card h1{margin:10px 0 12px;font-size:31px;line-height:1.25;letter-spacing:-.04em}.state-card p{margin:0;color:var(--muted)}'
        '.state-detail{margin-top:20px;padding:14px 16px;border-left:4px solid var(--blue);background:#f5f8fc;color:#334155;white-space:pre-wrap;overflow-wrap:anywhere}.state-card.warning .state-detail{border-left-color:var(--warning);background:#fff8ed}.state-card.danger .state-detail{border-left-color:var(--danger);background:#fff4f2}'
        '.state-actions{margin-top:24px}.state-actions a,.state-actions button{display:inline-block;border:1px solid var(--ink);background:var(--ink);color:#fff;text-decoration:none;padding:10px 14px;font:800 13px var(--font);border-radius:7px}.state-actions a:focus-visible{outline:2px solid var(--blue);outline-offset:2px}'
        '@media(max-width:640px){.state-wrap{padding:36px 16px}.state-card{padding:26px 22px}.state-card h1{font-size:25px}}'
        + HEADER_CSS + NAV_CSS + "</style></head><body>"
        + header_html(title, exclude=REVIEW_SCREEN_EXCLUDE)
        + f'<main class="state-wrap"><section class="state-card {tone}"><div class="state-eyebrow">{html.escape(eyebrow)}</div><h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>'
        + (f'<div class="state-detail">{html.escape(detail)}</div>' if detail else "")
        + (f'<div class="state-actions">{safe_action}</div>' if safe_action else "")
        + "</section></main></body></html>"
    )
