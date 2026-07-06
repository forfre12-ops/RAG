# -*- coding: utf-8 -*-
"""
kr_calendar.py — 2026 사업연도 한국 근무 달력 (WBS/주간보고 공용)

- 2026 법정공휴일(대체공휴일 포함) 세트
- 근무일(월~금, 공휴일 제외) 판정
- 주(Mon~Fri) 범위·"N월 M주" 라벨 계산

공휴일 근거:
  제헌절 7/17 = 대통령령 제36290호(공포 2026-04-30·시행 2026-05-11)로 18년 만에 재지정.
  추석 9/24~26 대체휴일 없음(설·추석은 일요일 중복만 대체 발동, 9/26 토요일은 미발동).
  광복절 8/15(토)→8/17(월), 개천절 10/3(토)→10/5(월) 대체 적용.
"""
import datetime

# 2026 사업연도 in-range 법정공휴일 (+2027-01-01 신정 꼬리)
HOLIDAYS_2026 = {
    datetime.date(2026, 6, 6): "현충일",
    datetime.date(2026, 7, 17): "제헌절",
    datetime.date(2026, 8, 15): "광복절",
    datetime.date(2026, 8, 17): "광복절 대체",
    datetime.date(2026, 9, 24): "추석 연휴",
    datetime.date(2026, 9, 25): "추석",
    datetime.date(2026, 9, 26): "추석 연휴",
    datetime.date(2026, 10, 3): "개천절",
    datetime.date(2026, 10, 5): "개천절 대체",
    datetime.date(2026, 10, 9): "한글날",
    datetime.date(2026, 12, 25): "성탄절",
    datetime.date(2027, 1, 1): "신정",
}

KWD = ["월", "화", "수", "목", "금", "토", "일"]  # Monday=0


def is_holiday(d):
    return d in HOLIDAYS_2026


def is_weekend(d):
    return d.weekday() >= 5  # Sat/Sun


def is_workday(d):
    """월~금 이고 공휴일이 아니면 근무일."""
    return (not is_weekend(d)) and (not is_holiday(d))


def kweekday(d):
    return KWD[d.weekday()]


def monday_of(d):
    """그 날이 속한 주의 월요일."""
    return d - datetime.timedelta(days=d.weekday())


def work_week(any_day):
    """any_day가 속한 주의 (월요일, 금요일) 튜플."""
    mon = monday_of(any_day)
    return mon, mon + datetime.timedelta(days=4)


def next_work_week(mon):
    """주어진 월요일의 다음 주 (월, 금)."""
    nmon = mon + datetime.timedelta(days=7)
    return nmon, nmon + datetime.timedelta(days=4)


def week_of_month(d):
    """그 달의 몇째 주인지 (일자 기준: (day-1)//7 + 1). 주간보고 라벨 규칙과 일치."""
    return (d.day - 1) // 7 + 1


def week_label(friday):
    """금주 수행 주의 금요일 기준 라벨 '2026년 N월 M주'."""
    return f"{friday.year}년 {friday.month}월 {week_of_month(friday)}주"


def fmt_range(mon, fri):
    """'6/29 ~ 7/03' 형태(주간보고 표기: 시작은 0-pad 없음, 종료는 2자리 pad)."""
    return f"{mon.month}/{mon.day} ~ {fri.month}/{fri.day:02d}"


def file_date_tag(friday):
    """파일명용 날짜 태그 'YYMMDD' (금요일 기준)."""
    return friday.strftime("%y%m%d")


def workdays_between(start, end):
    """start~end(포함) 사이 근무일 리스트."""
    out, d = [], start
    while d <= end:
        if is_workday(d):
            out.append(d)
        d += datetime.timedelta(days=1)
    return out
