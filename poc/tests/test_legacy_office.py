"""구형 Office(97-2003) 순수 파이썬 추출 계약.

왜(실측 2026-08-15). 실제 업무 문서 코퍼스 4,637건에 `.ppt` 1,050 · `.doc` 348 이 있는데
배포 이미지가 전부 미지원으로 떨어뜨렸다. 컨테이너에 antiword·catppt·soffice 가 하나도
없다(GPL-free 정책). 그래서 olefile 만으로 직접 파싱하는 경로를 넣었다.

이 파일이 고정하는 것:
    1. .ppt 가 컨테이너 레코드를 **내려가서** 텍스트를 찾는다(안 내려가면 0자)
    2. .doc 인코딩을 **점수로 고른다**(널바이트 판별은 한 파일을 통째로 깨뜨렸다)
    3. antiword 가 없어도 .doc 이 추출된다(운영 상황)
    4. 텍스트가 없으면 조용히 통과하지 않는다
"""
from __future__ import annotations

import shutil
import struct
from pathlib import Path

import pytest

from koipa.modules.m2_preprocess.legacy_office import (
    _decode_score,
    extract_doc_text,
    extract_ppt_text,
)


def _ppt_bytes(text: str) -> bytes:
    """TextCharsAtom 을 **컨테이너 안에** 넣은 최소 .ppt 스트림.

    컨테이너를 건너뛰는 구현은 여기서 0자를 낸다 - 그게 첫 구현의 버그였다.
    """
    payload = text.encode("utf-16-le")
    atom = struct.pack("<HHI", 0x0000, 0x0FA0, len(payload)) + payload
    # recVer 하위 4비트 = 0xF → 컨테이너
    return struct.pack("<HHI", 0x000F, 0x03E8, len(atom)) + atom


def _write_ole(tmp_path: Path, stream_name: str, data: bytes) -> Path | None:
    """olefile 은 읽기 전용이라 OLE 을 못 만든다 - 실파일이 있으면 그것을 쓴다."""
    return None


def test_ppt_descends_into_containers(monkeypatch, tmp_path):
    """컨테이너를 안 내려가면 텍스트 원자를 못 본다."""
    from koipa.modules.m2_preprocess import legacy_office as lo

    monkeypatch.setattr(lo, "_ole_stream", lambda p, n: _ppt_bytes("영업비밀 공정 조건"))
    got = extract_ppt_text(tmp_path / "x.ppt")
    assert "영업비밀 공정 조건" in got


def test_ppt_empty_stream_returns_empty(monkeypatch, tmp_path):
    from koipa.modules.m2_preprocess import legacy_office as lo

    monkeypatch.setattr(lo, "_ole_stream", lambda p, n: b"")
    assert extract_ppt_text(tmp_path / "x.ppt") == ""


def _doc_bytes(text: str, encoding: str) -> bytes:
    body = text.encode(encoding)
    head = bytearray(0x20)
    struct.pack_into("<H", head, 0, 0xA5EC)
    struct.pack_into("<II", head, 0x18, 0x20, 0x20 + len(body))
    return bytes(head) + body


@pytest.mark.parametrize("encoding", ["utf-16-le", "cp949"])
def test_doc_picks_correct_encoding(monkeypatch, tmp_path, encoding):
    """둘 다 디코드하고 점수로 고른다 - 널바이트 판별은 한 파일을 통째로 깨뜨렸다."""
    from koipa.modules.m2_preprocess import legacy_office as lo

    text = "가압류해제신청 채권자 채무자 사건번호"
    monkeypatch.setattr(lo, "_ole_stream", lambda p, n: _doc_bytes(text, encoding))
    got = extract_doc_text(tmp_path / "x.doc")
    assert "가압류해제신청" in got, f"{encoding} 인코딩을 잘못 골랐다: {got[:40]!r}"


def test_doc_bad_signature_returns_empty(monkeypatch, tmp_path):
    from koipa.modules.m2_preprocess import legacy_office as lo

    monkeypatch.setattr(lo, "_ole_stream", lambda p, n: b"\x00" * 64)
    assert extract_doc_text(tmp_path / "x.doc") == ""


def test_decode_score_prefers_readable_korean():
    """점수 함수가 깨진 해석을 걸러야 한다.

    ⚠ 손으로 쓴 "깨진 글자" 로는 이 테스트가 성립하지 않는다 - 무작위 한글 음절도
      가~힣 범위라 좋은 점수를 받는다(처음에 그렇게 썼다가 1.0 == 1.0 으로 깨졌다).
      실제 판별력은 **오디코딩이 만드는 치환문자**에서 온다. 그래서 진짜로 잘못
      디코드해서 비교한다.
    """
    text = "가압류해제신청 채권자 채무자 사건번호 200타기"
    raw = text.encode("utf-16-le")
    right = raw.decode("utf-16-le", "replace")
    wrong = raw.decode("cp949", "replace")      # 실제 오디코딩
    assert _decode_score(right) > _decode_score(wrong), (
        f"right={_decode_score(right):.3f} wrong={_decode_score(wrong):.3f}"
    )


def test_doc_falls_back_when_antiword_absent(monkeypatch, tmp_path):
    """운영 컨테이너에는 antiword 가 없다 - 그때도 .doc 이 추출돼야 한다."""
    from koipa.modules.m2_preprocess import extractor as E
    from koipa.modules.m2_preprocess import legacy_office as lo

    monkeypatch.setattr(shutil, "which", lambda n, *a, **k: None)
    monkeypatch.setattr(
        lo, "_ole_stream", lambda p, n: _doc_bytes("영업비밀 공정 레시피", "utf-16-le")
    )
    r = E._extract_doc(tmp_path / "x.doc")
    assert r.text.strip(), "antiword 없으면 .doc 이 빈다 - 폴백이 안 걸렸다"
    assert r.method == "doc"


def test_ppt_is_dispatched_by_suffix(monkeypatch, tmp_path):
    """.ppt 가 pptx 경로로 새면 python-pptx 가 못 읽어 미지원이 된다."""
    from koipa.modules.m2_preprocess import extractor as E
    from koipa.modules.m2_preprocess import legacy_office as lo

    monkeypatch.setattr(lo, "_ole_stream", lambda p, n: _ppt_bytes("슬라이드 본문"))
    p = tmp_path / "deck.ppt"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    r = E.extract(p)
    assert r.method == "ppt", f"확장자 분기가 틀렸다: {r.method}"
    assert "슬라이드 본문" in r.text
