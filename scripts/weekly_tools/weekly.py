# -*- coding: utf-8 -*-
"""
weekly.py — 매주 WBS/주간보고 갱신 CLI

실행(리포 루트에서):
  python -m scripts.weekly_tools.weekly read-wbs
  python -m scripts.weekly_tools.weekly set-status 29 완료
  python -m scripts.weekly_tools.weekly set-bar 29 2026-07-06 2026-07-24 done
  python -m scripts.weekly_tools.weekly format-wbs
  python -m scripts.weekly_tools.weekly read-report
  python -m scripts.weekly_tools.weekly new-week

--wbs / --report 로 파일 경로 지정 가능(기본=doc/result/감리정본/).
쓰기 명령은 기본 --dry-run 아님(바로 저장). 미리보기는 --dry 옵션.
"""
import os
import re
import sys
import glob
import argparse
import datetime

# 패키지/단독 실행 모두 지원
try:
    from . import kr_calendar as cal
    from .wbs import WBSGantt
    from .report import WeeklyReport
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from scripts.weekly_tools import kr_calendar as cal
    from scripts.weekly_tools.wbs import WBSGantt
    from scripts.weekly_tools.report import WeeklyReport

# [2026-08-02] 정본 이전 — doc/result/open/ → doc/result/감리정본/.
CANON_DIR = os.path.join("doc", "result", "감리정본")
DEFAULT_WBS = os.path.join(CANON_DIR, "[지재원] AI 영업비밀관리시스템 구축 WBS.xlsx")


def latest_report():
    """open/ 에서 가장 최근(파일명 YYMMDD 최대) 주간보고 .docx 를 반환.
    없으면 정적 기본값. 임시 잠금파일(~$) 제외."""
    cands = [c for c in glob.glob(os.path.join(CANON_DIR, "*주간보고*지재원*.docx"))
             if not os.path.basename(c).startswith("~$")]
    if not cands:
        return os.path.join(
            CANON_DIR, "-주간보고- 지재원 AI 영업비밀관리시스템 - 7월 1주 - 260703.docx")
    def tag(c):
        m = re.search(r"(\d{6})\.docx$", os.path.basename(c))
        return m.group(1) if m else "000000"
    return max(cands, key=tag)


DEFAULT_REPORT = latest_report()

COLOR_ALIAS = {
    "done": WBSGantt.C_DONE, "완료": WBSGantt.C_DONE,
    "milestone": WBSGantt.C_MILESTONE, "마일스톤": WBSGantt.C_MILESTONE,
    "plan": WBSGantt.C_PLAN_BLUE, "예정": WBSGantt.C_PLAN_BLUE,
    "blue": WBSGantt.C_PLAN_BLUE, "olive": WBSGantt.C_PLAN_OLIVE,
    "purple": WBSGantt.C_PLAN_PURPLE,
}


def _p(*a):
    print(*a)


def cmd_read_wbs(args):
    w = WBSGantt(args.wbs)
    _p(f"# WBS: {args.wbs}")
    _p(f"# 작업행 {5}..{w.last_task_row}, 달력열 G..{__import__('openpyxl').utils.get_column_letter(w.last_col)} "
       f"({w.date_of(w.start_col)} ~ {w.date_of(w.last_col)})")
    _p(f"{'행':>3} {'구분':<12} {'작업':<16} {'상태':<12} 막대")
    for t in w.tasks():
        span = f"{t.bar_start}~{t.bar_end}" if t.bar_start else "-"
        _p(f"{t.row:>3} {t.gubun[:12]:<12} {t.jakup[:16]:<16} {str(t.status)[:12]:<12} {span}")


def cmd_set_status(args):
    w = WBSGantt(args.wbs)
    old = w.ws.cell(row=args.row, column=6).value
    w.set_status(args.row, args.text)
    _p(f"R{args.row} 상태: {old!r} -> {args.text!r}")
    if not args.dry:
        w.save()
        _p("저장됨:", args.wbs)


def cmd_set_bar(args):
    w = WBSGantt(args.wbs)
    color = COLOR_ALIAS.get(args.color, args.color)
    s = datetime.date.fromisoformat(args.start)
    e = datetime.date.fromisoformat(args.end)
    w.set_bar(args.row, s, e, color)
    w.reapply_formatting()   # 휴무 음영 복구
    _p(f"R{args.row} 막대: {s}~{e} 색={color} (근무일만)")
    if not args.dry:
        w.save()
        _p("저장됨:", args.wbs)


def cmd_format_wbs(args):
    w = WBSGantt(args.wbs)
    w.reapply_formatting()
    _p("달력 띠·휴무 음영·테두리·폰트 재도색 완료")
    if not args.dry:
        w.save()
        _p("저장됨:", args.wbs)


def cmd_read_report(args):
    r = WeeklyReport(args.report)
    _p(f"# 주간보고: {args.report}")
    for b in r.week_blocks():
        _p(f"\n== {b['label']} ==")
        _p(f"  {b['geumju']}")
        _p(f"  {b['chaju']}")


def cmd_new_week(args):
    r = WeeklyReport(args.report)
    latest = r.week_blocks()[0]
    _p(f"# 대상 파일: {args.report}")
    _p(f"# 최신 블록: {latest['label']}  ({latest['geumju']} / {latest['chaju']})")
    if args.dry:
        (g_mon, g_fri), (c_mon, c_fri) = r._next_dates()
        _p(f"[dry] 생성 예정: <{cal.week_label(g_fri)}> "
           f"금주 수행({cal.fmt_range(g_mon, g_fri)}) / 차주 계획({cal.fmt_range(c_mon, c_fri)})")
        _p(f"[dry] 출력 경로: {args.out or r._derive_out_path(cal.week_label(g_fri), g_fri)}")
        return
    out = r.roll_forward(out_path=args.out, carry=not args.no_carry)
    _p(f"# 차주 블록 생성 -> {out}")
    _p("  (금주 수행 = 지난 차주 계획 이월, 차주 계획 = '작성 예정'. 내용은 Word에서 편집)")


def build_parser():
    # 공용 플래그: 부모 파서에 SUPPRESS 기본값으로 두어 서브커맨드 앞/뒤 어디서든 인식
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--wbs", default=argparse.SUPPRESS)
    common.add_argument("--report", default=argparse.SUPPRESS)
    common.add_argument("--dry", action="store_true", default=argparse.SUPPRESS,
                        help="저장하지 않고 미리보기")

    ap = argparse.ArgumentParser(prog="weekly", parents=[common],
                                 description="WBS/주간보고 매주 갱신 도구")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("read-wbs", parents=[common]).set_defaults(func=cmd_read_wbs)

    s = sub.add_parser("set-status", parents=[common])
    s.add_argument("row", type=int); s.add_argument("text")
    s.set_defaults(func=cmd_set_status)

    s = sub.add_parser("set-bar", parents=[common])
    s.add_argument("row", type=int); s.add_argument("start"); s.add_argument("end")
    s.add_argument("color", help="done|milestone|plan|olive|purple 또는 ARGB hex")
    s.set_defaults(func=cmd_set_bar)

    sub.add_parser("format-wbs", parents=[common]).set_defaults(func=cmd_format_wbs)
    sub.add_parser("read-report", parents=[common]).set_defaults(func=cmd_read_report)

    s = sub.add_parser("new-week", parents=[common])
    s.add_argument("--out", default=None, help="출력 경로(기본=자동 파일명)")
    s.add_argument("--no-carry", action="store_true", help="차주→금주 내용 이월 안 함")
    s.set_defaults(func=cmd_new_week)
    return ap


def main(argv=None):
    # Windows cp949 콘솔에서도 '—' 등 비-cp949 문자 출력 시 크래시 방지
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    # SUPPRESS로 누락된 공용 플래그에 실제 기본값 적용
    if not hasattr(args, "wbs"):
        args.wbs = DEFAULT_WBS
    if not hasattr(args, "report"):
        args.report = DEFAULT_REPORT
    if not hasattr(args, "dry"):
        args.dry = False
    args.func(args)


if __name__ == "__main__":
    main()
