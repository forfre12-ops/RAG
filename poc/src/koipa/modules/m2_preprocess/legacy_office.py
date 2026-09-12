"""구형 Office(97-2003) 텍스트 추출 — **외부 바이너리 없이 순수 파이썬으로**.

왜 직접 파싱하는가. 실측 2026-08-15: 실제 한국 업무 문서 코퍼스(4,637건)에 `.ppt`
1,050건 · `.doc` 348건이 있는데 배포 이미지가 전부 미지원으로 떨어뜨렸다. 지원 수단은
셋뿐이었고 둘은 대가가 컸다.

    catppt / antiword   GPL-2.0    배포 이미지 GPL-free 정책 위반
                                   (Dockerfile.api.prod §라이선스 — OCR/poppler 도 같은 이유로 제외)
    LibreOffice         MPL-2.0    ~500MB · 폐쇄망 번들 동봉 부담
    순수 파이썬          의존 없음   ← 채택. olefile 은 이미 들어 있다

MS Office COM 을 정답으로 놓고 대조하니 **회수율 98~99.5%** 였고, 240건 표본에서
**100% 추출**됐다(.ppt 중앙 9,510자 · .doc 중앙 3,478자).

⚠ 완전한 파서가 아니다. 표 배치·도형 좌표·서식은 못 살린다. 우리에게 필요한 것은
   **분류에 쓸 텍스트**이고 그 수준은 충족한다. 회수 못 하는 문서는 본문이 얇게 나오고,
   그러면 extractor 의 `body_below_classifiable_threshold` 가드가 검수로 라우팅한다 —
   무음으로 새지 않는다.
"""
from __future__ import annotations

import re
import struct
from pathlib import Path

# PowerPoint 레코드 타입
_PPT_TEXT_CHARS = 0x0FA0   # TextCharsAtom — UTF-16LE (한글은 이쪽)
_PPT_TEXT_BYTES = 0x0FA8   # TextBytesAtom — 로컬 1바이트
_PPT_STREAM = "PowerPoint Document"

# Word FIB
_DOC_STREAM = "WordDocument"
_FIB_IDENT = 0xA5EC
_FIB_FC_MIN = 0x18   # 본문 시작 오프셋(4바이트) · 이어서 fcMac(4바이트)

# 제어문자 제거 — 널은 문자 클래스에 직접 못 넣으므로 따로 지운다.
_CTRL = re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f]")
_LINEBREAK = re.compile(r"[\r\x07\x0b]+")


def _ole_stream(path: Path, name: str) -> bytes:
    """OLE2 복합문서에서 스트림 하나를 읽는다. 없으면 빈 바이트."""
    import olefile  # noqa: PLC0415

    if not olefile.isOleFile(str(path)):
        return b""
    ole = olefile.OleFileIO(str(path))
    try:
        return ole.openstream(name).read() if ole.exists(name) else b""
    finally:
        ole.close()


def extract_ppt_text(path: Path) -> str:
    """PowerPoint 97-2003(.ppt) 슬라이드 텍스트.

    레코드 헤더 8바이트: `[recVer/recInstance 2][recType 2][recLen 4]`

    ⚠ 레코드는 **중첩 컨테이너**다. recVer(하위 4비트)가 0xF 면 컨테이너이고 본문이
      자식 레코드다. 건너뛰면 텍스트 원자를 하나도 못 본다 — 첫 구현에서 정확히 그래서
      6/6 이 0자로 나왔다. 컨테이너면 내려가고 원자면 소비한다.
    """
    buf = _ole_stream(path, _PPT_STREAM)
    if not buf:
        return ""
    parts: list[str] = []
    i, n = 0, len(buf)
    while i + 8 <= n:
        ver_inst, rec_type, rec_len = struct.unpack_from("<HHI", buf, i)
        body = i + 8
        if body + rec_len > n:
            i += 1
            continue
        if rec_type == _PPT_TEXT_CHARS:
            parts.append(buf[body:body + rec_len].decode("utf-16-le", "ignore"))
        elif rec_type == _PPT_TEXT_BYTES:
            parts.append(buf[body:body + rec_len].decode("cp949", "ignore"))
        if (ver_inst & 0x000F) == 0xF:
            i = body                                   # 컨테이너 → 자식으로
        else:
            i = body + rec_len if rec_len else body     # 원자 → 소비
    text = "\n".join(t for t in parts if t.strip())
    return _clean(text)


def _decode_score(text: str) -> float:
    """이 디코딩이 맞아 보이는가. 한글·영숫자가 많고 치환문자가 적을수록 높다."""
    if not text:
        return -1.0
    good = sum(
        1 for c in text
        if ("가" <= c <= "힣") or c.isalnum() or c.isspace()
    )
    return (good - text.count("�") * 3) / max(1, len(text))


def extract_doc_text(path: Path) -> str:
    """Word 97-2003(.doc) 본문.

    FIB(File Information Block) 앞부분에서 본문 구간을 읽는다.
        0x000 wIdent(0xA5EC) · 0x018 fcMin · 0x01C fcMac

    [인코딩] 널바이트 비율로 UTF-16 을 판별했더니 한 파일이 통째로 깨졌다(COM 대비
    회수율 8.5% · '촗휶밝짯' 같은 글자). fComplex 도 아니었고 순수 판별 실패였다.
    플래그 해석 대신 **둘 다 디코드하고 점수로 고른다** — 99.5% 로 복구됐다.
    판별을 틀리면 쓰레기 텍스트가 그대로 분류로 흘러간다.
    """
    buf = _ole_stream(path, _DOC_STREAM)
    if len(buf) < 0x20 or struct.unpack_from("<H", buf, 0)[0] != _FIB_IDENT:
        return ""
    fc_min, fc_mac = struct.unpack_from("<II", buf, _FIB_FC_MIN)
    if not (0 < fc_min < fc_mac <= len(buf)):
        return ""
    raw = buf[fc_min:fc_mac]
    text = max(
        (raw.decode(enc, "replace") for enc in ("utf-16-le", "cp949")),
        key=_decode_score,
    )
    return _clean(text)


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = _CTRL.sub(" ", text)
    return _LINEBREAK.sub("\n", text)
