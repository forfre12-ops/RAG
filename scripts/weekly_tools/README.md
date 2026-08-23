# weekly_tools — WBS · 주간보고 매주 갱신 도구

`[지재원] AI 영업비밀관리시스템 구축 WBS.xlsx` 와 `-주간보고- …docx` 를 매주 읽고/쓰기 위한 재사용 컴포넌트.

- `kr_calendar.py` — 2026 사업연도 한국 근무 달력(공휴일·근무일·주 범위·라벨)
- `wbs.py` — `WBSGantt`: WBS 간트 읽기/상태·막대 쓰기/서식 재도색
- `report.py` — `WeeklyReport`: 주간보고 읽기 + 차주 블록 생성
- `weekly.py` — CLI

의존성: `openpyxl`, `python-docx`(검증용), `lxml`. (모두 설치돼 있음)

> ⚠️ 편집 전 대상 파일이 Word/Excel에서 **닫혀 있어야** 함(열려 있으면 `~$…` 잠금·저장 실패).
> WBS.xlsx 는 git 미추적이라 커밋 대상 아님. 편집은 파일에 직접 저장됨 → **작업 전 백업 권장**.

---

## CLI (리포 루트에서 실행)

```bash
python -m scripts.weekly_tools.weekly read-wbs          # 작업행·상태·막대 일람
python -m scripts.weekly_tools.weekly set-status 29 완료 # F열 상태 변경
python -m scripts.weekly_tools.weekly set-bar 29 2026-07-06 2026-07-24 done  # 막대(근무일만)
python -m scripts.weekly_tools.weekly format-wbs        # 달력띠·휴무음영·테두리·폰트 재도색
python -m scripts.weekly_tools.weekly read-report       # 주간보고 주 블록 일람
python -m scripts.weekly_tools.weekly new-week          # 차주 블록 추가한 새 .docx 생성
```

- `--dry` : 저장 없이 미리보기(`set-status`·`set-bar`·`format-wbs`·`new-week` 모두 지원)
- `--wbs PATH` / `--report PATH` : 대상 파일 경로. **서브커맨드 앞/뒤 아무 위치**나 인식.
  - `--wbs` 기본 = 정본 WBS. `--report` 기본 = `open/` 안 **가장 최근 주간보고 자동 감지**(파일명 YYMMDD 최대). 그래서 `new-week` 는 보통 인자 없이 실행하면 최신본을 이어서 롤함.
- 색 별칭: `done`(완료) · `milestone`(마일스톤) · `plan`/`blue` · `olive` · `purple`, 또는 ARGB hex(`FF3F7D58`)
- 콘솔 인코딩은 자동 처리(맑은고딕·`—` 등 비-cp949 문자도 크래시 없이 출력).

## 파이썬 API

```python
from scripts.weekly_tools import WBSGantt, WeeklyReport
import datetime

# --- WBS ---
w = WBSGantt("doc/result/open/[지재원] AI 영업비밀관리시스템 구축 WBS.xlsx")
for t in w.tasks():
    print(t.row, t.gubun, t.jakup, t.status, t.bar_start, t.bar_end)
w.set_status(29, "완료")
w.set_bar(29, datetime.date(2026,7,6), datetime.date(2026,7,24), WBSGantt.C_DONE)
w.reapply_formatting()   # 막대 편집 후 휴무 음영·달력띠·테두리·폰트 재적용(idempotent)
w.save()                 # 원본 덮어쓰기. w.save("사본.xlsx") 로 다른 경로 저장

# --- 주간보고 ---
r = WeeklyReport("doc/result/open/-주간보고- … - 7월 1주 - 260703.docx")
for b in r.week_blocks():
    print(b["label"], b["geumju"], b["chaju"])
out = r.roll_forward()   # 차주 블록을 맨 위에 추가한 새 파일(파일명 자동 = 다음 주 라벨·날짜)
```

## 매주 루틴(권장 순서)

1. **백업**: 두 파일을 다른 이름으로 복사.
2. **주간보고 차주 생성**: `new-week` → 새 `…N주 - YYMMDD.docx` 생성.
   - 새 **금주 수행** = 지난 **차주 계획** 이월(완료 여부만 손보면 됨), 새 **차주 계획** = `(작성 예정)`.
   - Word에서 내용만 다듬고 저장.
3. **WBS 갱신**:
   - 이번 주 완료분 상태 변경: `set-status <행> 완료`
   - 필요 시 막대 조정: `set-bar <행> <시작> <종료> <색>`
   - **마무리**: `format-wbs` (또는 `set-bar` 는 자동으로 재도색) → 달력·휴무·테두리·폰트 정합.
4. Excel/Word로 열어 눈으로 확인.

## 도메인 규칙(도구가 자동 유지)

- **달력 축**: 열 G(=`2026-06-02`, 화)부터 하루 1열. 월 띠는 실제 월경계, 주차는 월 내부 nest(3일 미만 슬리버는 이웃 주 흡수).
- **휴무 음영**: 주말·공휴일 본문 회색 `FFD9D9D9`, 공휴일 머리글 빨강 `FFC00000`. 막대는 근무일에만(공휴일·주말서 끊김).
- **2026 공휴일**: 현충일6/6 · **제헌절7/17**(대통령령 제36290호로 재지정) · 광복절8/15+대체8/17 · 추석9/24~26(대체없음) · 개천절10/3+대체10/5 · 한글날10/9 · 성탄12/25 · 신정(’27)1/1.
- **선 굵기**: 굵은선=외곽+열 세로선+구분(대공정) 경계 / 일반선=구분 내부 행선.
- **폰트**: 가시 텍스트 전부 맑은 고딕.
- **막대 색**: 완료 `FF3F7D58` · 마일스톤 `FFFFC000` · 예정 `FF9EADCC`/`FF6E8F72`/`FF8064A2`.

## 한계 / 주의

- **작업 행 추가/삭제**는 미지원(간트 병합·틀고정이 얽혀 위험). 행 추가는 Excel에서 하고, 그 뒤 `format-wbs` 로 서식만 재적용.
- 주간보고 `new-week` 는 **날짜·라벨·내용 이월**까지만. 실제 실적/계획 문구는 Word에서 편집.
- 달력 축(시작일·연도)이 바뀌면 `wbs.py` 의 `GRID_START_DATE` 를 수정. 기준점 불일치 시 로드에서 예외 발생.
