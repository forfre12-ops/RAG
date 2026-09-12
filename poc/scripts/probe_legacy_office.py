"""구형 Office(.ppt/.doc)를 **순수 파이썬으로 뽑을 수 있는지** 검증한다.

왜. 서식 코퍼스 실측에서 .ppt 1,050건 · .doc 348건이 전부 미지원으로 나왔다. 지원하려면
외부 바이너리가 필요한데 선택지가 셋뿐이고 전부 대가가 있다.

    catppt/antiword   GPL-2.0    배포 이미지 GPL-free 정책 위반
    LibreOffice       MPL-2.0    ~500MB · 폐쇄망 번들 동봉
    순수 파이썬        의존 없음   ← 되면 이게 최선. 이 스크립트가 그것을 잰다

두 포맷 모두 OLE2 복합문서이고 텍스트가 특정 스트림에 있다.

    .ppt   'PowerPoint Document' 스트림의 TextBytesAtom(0x0FA8) · TextCharsAtom(0x0FA0)
    .doc   'WordDocument' 스트림의 본문 구간(FIB 의 fcMin~fcMac)

⚠ 완전한 파서가 아니다. 표·도형 배치·서식은 못 살린다. 우리에게 필요한 것은 **분류에
   쓸 텍스트**이므로 그 수준이면 충분한지를 본다.

⚠ 정답 대조가 필요하면 --com 으로 MS Office COM 추출과 비교한다(Windows 개발기 전용,
   배포에는 못 쓴다).
"""
from __future__ import annotations

import argparse
import glob
import re
import struct
import sys
from pathlib import Path


def ppt_text(path: Path) -> str:
    """PowerPoint 97-2003 'PowerPoint Document' 스트림에서 텍스트 원자를 긁는다.

    레코드 헤더 8바이트: [recVer/recInstance 2][recType 2][recLen 4]
        0x0FA8 TextBytesAtom  본문이 CP1252/로컬 1바이트
        0x0FA0 TextCharsAtom  본문이 UTF-16LE  ← 한글은 이쪽
    """
    import olefile

    if not olefile.isOleFile(str(path)):
        return ""
    o = olefile.OleFileIO(str(path))
    try:
        if not o.exists("PowerPoint Document"):
            return ""
        buf = o.openstream("PowerPoint Document").read()
    finally:
        o.close()

    # 레코드는 **중첩 컨테이너**다. recVer(하위 4비트)가 0xF 면 컨테이너이고 본문이
    # 자식 레코드다 — 건너뛰면 그 안의 텍스트 원자를 영영 못 본다(첫 시도에서 0자가
    # 나온 이유가 이것이다). 컨테이너면 내려가고 원자면 소비한다.
    out: list[str] = []
    i, n = 0, len(buf)
    while i + 8 <= n:
        ver_inst, rec_type, rec_len = struct.unpack_from("<HHI", buf, i)
        body = i + 8
        if body + rec_len > n:
            i += 1
            continue
        is_container = (ver_inst & 0x000F) == 0xF
        if rec_type == 0x0FA0:  # TextCharsAtom — UTF-16LE
            out.append(buf[body:body + rec_len].decode("utf-16-le", "ignore"))
        elif rec_type == 0x0FA8:  # TextBytesAtom — 1바이트(로컬 코드페이지)
            out.append(buf[body:body + rec_len].decode("cp949", "ignore"))
        if is_container:
            i = body                      # 자식으로 내려간다
        else:
            i = body + rec_len if rec_len else body
    text = "\n".join(t for t in out if t.strip())
    # 슬라이드 텍스트는 \r 로 줄이 갈린다
    return re.sub(r"[\r\x0b]+", "\n", text)


def doc_text(path: Path) -> str:
    """Word 97-2003 'WordDocument' 스트림 본문 구간을 뽑는다.

    FIB(File Information Block) 앞부분:
        offset 0x000  wIdent(0xA5EC)
        offset 0x00A  flags — bit9(0x0200) fComplex(조각화) · bit... fExtChar
        offset 0x018  fcMin(4)   본문 시작
        offset 0x01C  fcMac(4)   본문 끝
    fComplex 인 문서는 조각(piece table)이라 이 단순 경로로는 부정확할 수 있다 —
    그 경우를 표시해 호출부가 판단하게 한다.
    """
    import olefile

    if not olefile.isOleFile(str(path)):
        return ""
    o = olefile.OleFileIO(str(path))
    try:
        name = "WordDocument" if o.exists("WordDocument") else None
        if name is None:
            return ""
        buf = o.openstream(name).read()
    finally:
        o.close()
    if len(buf) < 0x20 or struct.unpack_from("<H", buf, 0)[0] != 0xA5EC:
        return ""
    fc_min, fc_mac = struct.unpack_from("<II", buf, 0x18)
    if not (0 < fc_min < fc_mac <= len(buf)):
        return ""
    raw = buf[fc_min:fc_mac]
    # [인코딩] 널바이트 비율로 UTF-16 을 판별했더니 한 파일이 통째로 깨졌다(회수율 8.5%,
    # '촗휶밝짯' 같은 글자). fComplex 도 아니었다 - 순수 판별 실패다. 플래그 해석 대신
    # **둘 다 디코드하고 점수로 고른다.** 한글·영숫자 비율이 높고 치환문자가 적은 쪽이
    # 맞는 해석이다. 판별을 틀리면 쓰레기 텍스트가 그대로 분류로 흘러간다.
    def _score(t):
        if not t:
            return -1.0
        good = sum(1 for c in t if ("가" <= c <= "헬") or c.isalnum() or c.isspace())
        bad = t.count("�")
        return (good - bad * 3) / max(1, len(t))

    text = max((raw.decode(e, "replace") for e in ("utf-16-le", "cp949")), key=_score)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return re.sub(r"[\r\x07]+", "\n", text)


def _com_text(path: Path, kind: str) -> str:
    """MS Office COM 으로 뽑은 정답(개발기 전용)."""
    import pythoncom  # noqa: PLC0415
    import win32com.client  # noqa: PLC0415

    pythoncom.CoInitialize()
    try:
        if kind == "ppt":
            app = win32com.client.Dispatch("PowerPoint.Application")
            pres = app.Presentations.Open(str(path.resolve()), WithWindow=False)
            parts = []
            for sl in pres.Slides:
                for sh in sl.Shapes:
                    if sh.HasTextFrame and sh.TextFrame.HasText:
                        parts.append(sh.TextFrame.TextRange.Text)
            pres.Close()
            return "\n".join(parts)
        app = win32com.client.Dispatch("Word.Application")
        app.Visible = False
        d = app.Documents.Open(str(path.resolve()), ReadOnly=True)
        t = d.Content.Text
        d.Close(False)
        return t
    finally:
        pythoncom.CoUninitialize()


def _korean_ratio(s: str) -> float:
    if not s:
        return 0.0
    ko = sum(1 for c in s if "가" <= c <= "힣")
    return ko / max(1, len(s))


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="구형 Office 순수 파이썬 추출 검증")
    ap.add_argument("--root", default="../서식모음")
    ap.add_argument("--n", type=int, default=8, help="포맷당 표본")
    ap.add_argument("--com", action="store_true", help="MS Office COM 정답과 대조")
    args = ap.parse_args(argv)

    for ext, fn, kind in ((".ppt", ppt_text, "ppt"), (".doc", doc_text, "doc")):
        files = sorted(glob.glob(f"{args.root}/**/*{ext}", recursive=True))[:args.n]
        print(f"\n=== {ext}  표본 {len(files)}건")
        okn = 0
        for p in files:
            path = Path(p)
            try:
                t = fn(path)
            except Exception as exc:  # noqa: BLE001
                print(f"  {path.name[:34]:36s} 예외 {type(exc).__name__}")
                continue
            ko = _korean_ratio(t)
            okn += 1 if len(t.strip()) >= 30 else 0
            line = f"  {path.name[:34]:36s} {len(t):7d}자  한글 {ko:5.1%}"
            if args.com:
                try:
                    gt = _com_text(path, kind)
                    # 정답 대비 회수율 — 문자 집합 기준(순서·공백 차이 무시)
                    a, b = set(t.replace(" ", "")), set(gt.replace(" ", ""))
                    cov = len(a & b) / max(1, len(b))
                    line += f"  | COM {len(gt):7d}자  회수 {cov:5.1%}"
                except Exception as exc:  # noqa: BLE001
                    line += f"  | COM 실패 {type(exc).__name__}"
            print(line)
            if t.strip():
                print(f"        {t.strip()[:70]!r}")
        print(f"  -> 30자 이상 추출 {okn}/{len(files)}")

    print("\n판단 기준: 한글 비율이 정상이고 30자 이상이면 분류에 쓸 만하다.")
    print("깨진 글자만 나오면 인코딩 경로가 틀린 것이고, 0자면 이 방식으로는 안 된다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
