# -*- coding: utf-8 -*-
"""소스에 제어문자가 섞여 정규식이 조용히 죽는 것을 잡는다.

왜 필요한가 (실측 2026-09-05). 소스 두 곳에서 `\b`(단어 경계)가 **백스페이스 바이트
0x08** 로 바뀌어 있었다. 파이썬은 이것을 오류로 보지 않는다 — 정규식이 "본문에 백스페이스
문자가 있어야 한다"는 뜻이 되어 **아무것도 맞지 않는다.** 시험도 초록이었다.

  poc/src/koipa/modules/m1_synthesis/generator.py
      합성 문서의 휴대전화·이메일 검출이 죽어 `pii_violations` 가 늘 비었다
  poc/scripts/audit_unused.py
      원시 SQL 쓰기 탐지가 죽어 tb_advisory_locks 가 "쓰기 0 · 영원히 빈다"로 잡혔다

둘 다 "안 잡히는 것"이라 화면에는 정상으로 보인다. 그래서 바이트를 직접 센다.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

# 소스에 있어서는 안 되는 제어문자. 탭·개행·복귀는 뺀다.
_FORBIDDEN = {i for i in range(0x00, 0x20)} - {0x09, 0x0A, 0x0D}
_FORBIDDEN.add(0x7F)

_SCAN_DIRS = ("src", "scripts", "tests")
_SKIP = ("__pycache__", ".venv", "site-packages", ".pytest_cache")


def _files() -> list[Path]:
    out: list[Path] = []
    for d in _SCAN_DIRS:
        root = _ROOT / d
        if not root.exists():
            continue
        out += [p for p in root.rglob("*.py") if not any(s in str(p) for s in _SKIP)]
    return sorted(out)


def test_no_control_bytes_in_python_sources() -> None:
    bad: list[str] = []
    for p in _files():
        s = io.open(p, encoding="utf-8", errors="replace").read()
        for lineno, line in enumerate(s.splitlines(), 1):
            hits = sorted({ord(c) for c in line if ord(c) in _FORBIDDEN})
            if hits:
                bad.append("%s:%d %s" % (p.relative_to(_ROOT), lineno,
                                         " ".join("0x%02X" % h for h in hits)))
    assert not bad, (
        "소스에 제어문자가 섞였다. 정규식의 " + chr(92) + "b 가 백스페이스(0x08)로 "
        "바뀌면 아무것도 맞지 않는다:\n  " + "\n  ".join(bad))


def test_synthesis_pii_patterns_actually_match() -> None:
    """합성 QC 의 PII 정규식이 실제 값을 잡는지 — 패턴의 존재가 아니라 동작을 본다."""
    from koipa.modules.m1_synthesis.generator import _PII_PATTERNS  # noqa: PLC0415

    samples = [
        ("주민등록번호", "담당자 주민등록번호는 800101-1234567 입니다"),
        ("외국인등록번호", "외국인등록번호 800101-5234567 확인"),
        ("휴대전화", "연락처 010-1234-5678 로 회신 바랍니다"),
        ("이메일", "문의는 hong.gildong@example.co.kr 로 주십시오"),
    ]
    for label, text in samples:
        assert any(p.search(text) for p in _PII_PATTERNS), f"{label} 를 못 잡는다: {text}"

    clean = "본 문서는 개인정보를 담지 않는다. 문서번호 2026-0901 · 3건."
    assert not any(p.search(clean) for p in _PII_PATTERNS), "깨끗한 본문을 PII 로 오탐한다"


@pytest.mark.parametrize("name", ["rrn", "phone_mobile", "email"])
def test_ingest_pii_masker_patterns_alive(name: str) -> None:
    """수집 경로의 마스커도 같은 방식으로 죽을 수 있다 — 함께 본다."""
    from koipa.modules.m2_preprocess.pii_masker import _PII_PATTERNS  # noqa: PLC0415

    text = {
        "rrn": "800101-1234567",
        "phone_mobile": "010-1234-5678",
        "email": "hong.gildong@example.co.kr",
    }[name]
    pats = [p for n, p, _m in _PII_PATTERNS if n == name]
    assert pats, f"{name} 패턴이 없다"
    assert any(p.search(text) for p in pats), f"{name} 패턴이 {text} 를 못 잡는다"
