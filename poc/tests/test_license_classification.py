"""라이선스 등급 분류와 산출물 무음 통과를 잠근다.

왜(실측 2026-08-17). 두 결함이 같은 자리에서 나왔다.

1) 정규식이 `v3` 접미형을 놓쳤다.

     `\bLGPL\b` 가 "GNU Lesser General Public License v3 (LGPLv3)" 를 **못 잡는다** —
     LGPL 뒤에 v 가 붙어 끝 단어경계가 없기 때문이다. kiwipiepy(LGPLv3)가 weak 이 아니라
     unknown 으로 떨어졌다. 같은 이유로 "AGPLv3"·"GPLv3" 만 적힌 패키지는 strong 이 아니라
     unknown 이 된다 — **위험한 쪽 누락**이다.

2) 산출 경로가 tier 구성과 무관하게 "OK" 를 찍었다.

     배포 이미지에 pip-licenses 가 없어 30개 전부 unknown 인 산출물을 만들고도
       [licenses] OK - 30 packages, tiers: {'unknown': 30}
     이라고 보고했다. 그대로 발주처에 제출하면 라이선스 근거가 없는 문서가 나간다.

⚠ STRONG 을 WEAK 보다 먼저 판정하므로 순서가 바뀌면 LGPL 이 strong 으로 오분류된다.
  그 순서도 함께 잠근다.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dump_licenses.py"


def _mod():
    spec = importlib.util.spec_from_file_location("dump_licenses", _SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.parametrize(
    ("license_str", "want"),
    [
        # v3 접미형 — 이것들이 unknown 으로 떨어지던 것이 결함이었다
        ("GNU Lesser General Public License v3 (LGPLv3)", "weak"),
        ("LGPLv2.1", "weak"),
        ("AGPLv3", "strong"),
        ("GPLv3", "strong"),
        # SPDX 표기 — 원래 되던 것. 고치면서 깨지면 안 된다
        ("LGPL-3.0-only", "weak"),
        ("GPL-3.0", "strong"),
        ("GPL-2.0", "strong"),
        ("GNU Affero General Public License v3 or later (AGPLv3+)", "strong"),
        ("Mozilla Public License 2.0 (MPL 2.0)", "weak"),
        ("MPL-2.0 AND MIT", "weak"),
        ("MIT", "permissive"),
        ("BSD-3-Clause", "permissive"),
        ("Apache-2.0", "permissive"),
        ("UNKNOWN", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_license(license_str, want):
    assert _mod().classify_license(license_str) == want


def test_lgpl_is_not_swallowed_by_strong_patterns():
    """LGPL 이 strong 으로 오분류되면 안 된다 - STRONG 을 먼저 보기 때문에 실재하는 위험이다."""
    import re

    m = _mod()
    for pat in m.STRONG_COPYLEFT_PATTERNS:
        for s in ("LGPLv3", "LGPL-3.0-only", "GNU Lesser General Public License v3"):
            assert not re.search(pat, s, re.IGNORECASE), f"{pat!r} 가 {s!r} 를 strong 으로 잡는다"


def test_generation_path_has_unknown_threshold():
    """산출 경로가 unknown 비율을 판정하는지 - 없으면 쓸모없는 문서를 OK 로 보고한다."""
    m = _mod()
    assert hasattr(m, "_MAX_UNKNOWN_RATIO"), "unknown 비율 한계가 없다"
    assert 0 < m._MAX_UNKNOWN_RATIO < 1, m._MAX_UNKNOWN_RATIO
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "_MAX_UNKNOWN_RATIO" in src.split("def main")[-1], (
        "main 이 한계를 안 본다 - 산출 경로가 여전히 무조건 OK 를 찍는다"
    )
    assert "--allow-degraded" in src, "드라이런 탈출구가 없다"


def test_strong_verdict_respects_allowlist():
    """산출 경로와 --check 가 같은 허용목록을 봐야 한다 - 한쪽만 보면 두 경로가 다른 말을 한다."""
    m = _mod()
    src = _SCRIPT.read_text(encoding="utf-8")
    tail = src.split("def main")[-1]
    assert "KNOWN_RISKS_ALLOWLIST" in tail, "산출 경로가 허용목록을 안 본다"
    assert "PyMuPDF" in m.KNOWN_RISKS_ALLOWLIST
