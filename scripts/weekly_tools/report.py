# -*- coding: utf-8 -*-
"""
report.py — 주간보고 .docx 읽기/차주 생성 컴포넌트

문서 구조(구글닥스 export):
  body 안에 [주라벨 문단 '<2026년 N월 M주>'][빈 문단][4행 표] 블록이 최신순으로 쌓임.
  표 4행 = row0 '금주 수행(mon ~ fri)' / row1 금주 내용 / row2 '차주 계획(mon ~ fri)' / row3 차주 내용.

사용법:
    r = WeeklyReport(path)
    for b in r.week_blocks(): print(b['label'], b['geumju'], b['chaju'])
    out = r.roll_forward()          # 다음 주 블록을 맨 위에 추가한 새 .docx 생성(파일명 자동)
    # carry=True(기본): 지난 '차주 계획' 내용을 새 '금주 수행'으로 이월, 새 '차주 계획'은 (작성 예정)
"""
import os
import re
import zipfile
import shutil
import datetime
from copy import deepcopy

from lxml import etree

from . import kr_calendar as cal

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
DOC_XML = "word/document.xml"
RANGE_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*~\s*(\d+)\s*/\s*(\d+)")
LABEL_RE = re.compile(r"<\s*(\d{4})\s*년\s*(\d+)\s*월\s*(\d+)\s*주\s*>")


def _text(el):
    return "".join(t.text or "" for t in el.findall(".//w:t", NS))


def _set_text(el, new_text):
    """el 내부 첫 w:t에 new_text를 몰아넣고 나머지 w:t는 비움(단일 서식 라인 전제)."""
    ts = el.findall(".//w:t", NS)
    if not ts:
        return False
    ts[0].text = new_text
    # xml:space 보존(앞뒤 공백 유지)
    ts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for t in ts[1:]:
        t.text = ""
    return True


class WeeklyReport:
    def __init__(self, path):
        self.path = path
        with zipfile.ZipFile(path) as z:
            self.doc_xml = z.read(DOC_XML)
        self.root = etree.fromstring(self.doc_xml)
        self.body = self.root.find("w:body", NS)

    # ---- 블록 파싱 ----
    def _blocks(self):
        """(label_p, table) 블록 목록을 body 순서대로 반환."""
        kids = list(self.body)
        blocks = []
        i = 0
        while i < len(kids):
            k = kids[i]
            if etree.QName(k).localname == "p" and LABEL_RE.search(_text(k)):
                # 다음 표를 찾음
                tbl = None
                j = i + 1
                while j < len(kids):
                    if etree.QName(kids[j]).localname == "tbl":
                        tbl = kids[j]
                        break
                    if (etree.QName(kids[j]).localname == "p"
                            and LABEL_RE.search(_text(kids[j]))):
                        break
                    j += 1
                blocks.append((k, tbl))
            i += 1
        return blocks

    def week_blocks(self):
        out = []
        for label_p, tbl in self._blocks():
            label = _text(label_p)
            rows = tbl.findall(".//w:tr", NS) if tbl is not None else []
            geumju = _text(rows[0]) if len(rows) > 0 else ""
            chaju = _text(rows[2]) if len(rows) > 2 else ""
            out.append({
                "label": label,
                "geumju": geumju,
                "chaju": chaju,
                "geumju_body": _text(rows[1]) if len(rows) > 1 else "",
                "chaju_body": _text(rows[3]) if len(rows) > 3 else "",
            })
        return out

    # ---- 날짜 계산 ----
    def _next_dates(self):
        """최신 블록의 '차주 계획' 시작일 = 새 '금주 수행'의 월요일."""
        latest = self.week_blocks()[0]
        m = LABEL_RE.search(latest["label"])
        base_year = int(m.group(1)) if m else 2026
        label_month = int(m.group(2)) if m else 6
        rr = RANGE_RE.search(latest["chaju"])
        if not rr:
            raise ValueError(f"차주 계획 날짜 파싱 실패: {latest['chaju']!r}")
        sm, sd = int(rr.group(1)), int(rr.group(2))
        yr = base_year + 1 if (sm == 1 and label_month == 12) else base_year
        new_geumju_mon = datetime.date(yr, sm, sd)
        # 안전: 월요일로 스냅
        new_geumju_mon = cal.monday_of(new_geumju_mon)
        new_geumju = cal.work_week(new_geumju_mon)          # (mon, fri)
        new_chaju = cal.next_work_week(new_geumju_mon)      # (mon, fri)
        return new_geumju, new_chaju

    # ---- 차주 생성 ----
    def roll_forward(self, out_path=None, carry=True):
        blocks = self._blocks()
        if not blocks:
            raise ValueError("주 블록을 찾지 못함")
        label_p, tbl = blocks[0]
        if tbl is None:
            raise ValueError("최신 블록에 표가 없음")

        (g_mon, g_fri), (c_mon, c_fri) = self._next_dates()
        new_label = cal.week_label(g_fri)                  # '2026년 7월 2주'
        # 클론
        new_label_p = deepcopy(label_p)
        new_tbl = deepcopy(tbl)
        _set_text(new_label_p, f"<{new_label}>")
        rows = new_tbl.findall(".//w:tr", NS)
        _set_text(rows[0], f"금주 수행({cal.fmt_range(g_mon, g_fri)})")
        _set_text(rows[2], f"차주 계획({cal.fmt_range(c_mon, c_fri)})")

        if carry and len(rows) > 3:
            # 지난 '차주 계획' 내용(원본 표 row3)을 새 '금주 수행'(row1)으로 이월
            src_rows = tbl.findall(".//w:tr", NS)
            self._copy_cell_body(src_rows[3], rows[1])
            # 새 '차주 계획' 내용은 플레이스홀더
            self._set_cell_placeholder(rows[3], "(작성 예정)")

        # 클론한 [label_p, (사이 빈 문단들), table] 을 맨 위에 삽입.
        # 원본 최신 블록 사이의 스페이서 문단도 함께 복제해 간격 유지.
        insert_nodes = [new_label_p]
        kids = list(self.body)
        li = kids.index(label_p)
        ti = kids.index(tbl)
        for k in kids[li + 1:ti]:
            insert_nodes.append(deepcopy(k))
        insert_nodes.append(new_tbl)
        # 표 뒤 스페이서 문단(간격)을 다음 블록 라벨 전까지 전부 복제(원본과 동일 간격)
        j = ti + 1
        while j < len(kids) and etree.QName(kids[j]).localname == "p" \
                and not LABEL_RE.search(_text(kids[j])):
            insert_nodes.append(deepcopy(kids[j]))
            j += 1

        first = list(self.body)[0]
        for node in insert_nodes:
            first.addprevious(node)

        # 저장
        if out_path is None:
            out_path = self._derive_out_path(new_label, g_fri)
        self._write(out_path)
        return out_path

    def _copy_cell_body(self, src_tr, dst_tr):
        """src 행의 셀 내용(문단들)을 dst 행 셀에 복제. tcPr(셀속성)은 dst 것 유지."""
        src_tc = src_tr.find("w:tc", NS)
        dst_tc = dst_tr.find("w:tc", NS)
        if src_tc is None or dst_tc is None:
            return
        # dst_tc 에서 tcPr 제외한 자식 제거
        for ch in list(dst_tc):
            if etree.QName(ch).localname != "tcPr":
                dst_tc.remove(ch)
        # src_tc 의 tcPr 제외 자식 복제해 추가
        for ch in list(src_tc):
            if etree.QName(ch).localname != "tcPr":
                dst_tc.append(deepcopy(ch))

    def _set_cell_placeholder(self, tr, text):
        tc = tr.find("w:tc", NS)
        if tc is None:
            return
        paras = tc.findall("w:p", NS)
        # 첫 문단만 남기고 나머지 제거, 첫 문단 텍스트 세팅
        for p in paras[1:]:
            tc.remove(p)
        if paras:
            _set_text(paras[0], text)

    def _derive_out_path(self, new_label, friday):
        d = os.path.dirname(self.path)
        # 라벨에서 'N월 M주' 추출
        m = LABEL_RE.search(f"<{new_label}>")
        wk = f"{m.group(2)}월 {m.group(3)}주" if m else new_label
        fname = (f"-주간보고- 지재원 AI 영업비밀관리시스템 - {wk} - "
                 f"{cal.file_date_tag(friday)}.docx")
        return os.path.join(d, fname)

    def _write(self, out_path):
        """document.xml만 교체해 새 zip 생성(나머지 파트 원본 유지)."""
        new_doc = etree.tostring(self.root, xml_declaration=True,
                                 encoding="UTF-8", standalone=True)
        tmp = out_path + ".tmp"
        with zipfile.ZipFile(self.path) as zin, \
                zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = new_doc if item.filename == DOC_XML else zin.read(item.filename)
                zout.writestr(item, data)
        if os.path.exists(out_path):
            os.remove(out_path)
        shutil.move(tmp, out_path)
        return out_path
