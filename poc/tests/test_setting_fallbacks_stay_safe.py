"""설정을 못 읽었을 때의 폴백이 **게이트를 끄지 않는가**.

왜 이 파일이 있는가(2026-09-06). `pipeline.py` 에 이런 코드가 있었다:

    try:
        _min_ev = float(settings.rule_fallback_min_evidence)
    except Exception:
        _min_ev = 0.0        # 아래가 `if _min_ev > 0:` 이라 게이트가 안 돈다

선언된 기본값은 **0.9** 다. 0.0 은 기본값이 아니라 **게이트를 끄는 값**이다. 설정 읽기가
한 번 실패하면 sparse-evidence 게이트가 조용히 사라진다.

그 게이트가 막는 것은 코드 주석이 적고 있다 — "단 하나의 약한 매치가 conf=1.0 으로
자동확정돼 저신뢰 게이트를 통과하는 silent FNR(골든셋 TS 5건: TS신호 0인데 S1/S2로
conf=1.0 확정)". 이 사업이 1차로 막아야 하는 실패다.

⚠ 이 파일은 개별 결함만이 아니라 **그 부류**를 막는다 — 하드코딩 폴백이 config 기본값과
  어긋나면 잡는다. 실측 결과 폴백 6개 중 어긋난 것은 1개였다.
"""
from __future__ import annotations

import pathlib
import re

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "koipa"
_PIPELINE = _SRC / "modules" / "m5_inference" / "pipeline.py"
_CONFIG = _SRC / "config.py"

# 폴백이 값이 아니라 설명 문자열인 자리 — 코드가 그 아래에서 다른 경로로 떨어진다.
#   classifier_temperature: 로그 문구일 뿐이고 실제로는 모델 동봉 temperature.json 을 쓴다.
_DESCRIPTIVE_FALLBACKS = {"classifier_temperature"}


def _declared_fallbacks() -> list[tuple[str, str]]:
    """`_warn_setting_fallback("이름", 폴백, exc)` 에서 (이름, 폴백표현)을 뽑는다."""
    src = _PIPELINE.read_text("utf-8")
    return re.findall(r'_warn_setting_fallback\("(\w+)",\s*([^,]+),', src)


def _config_default(name: str) -> str | None:
    m = re.search(
        r"^\s+%s:\s*[\w\[\]| ]+\s*=\s*(.+?)(?:\s*#.*)?$" % re.escape(name),
        _CONFIG.read_text("utf-8"), re.M,
    )
    return m.group(1).strip() if m else None


def test_every_hardcoded_fallback_matches_the_declared_default():
    """코드가 손으로 적은 폴백 == config 의 선언된 기본값.

    어긋나면 "설정을 못 읽었을 때"의 동작이 "설정을 안 줬을 때"와 달라진다. 그 차이를
    아무도 모르게 되는 것이 문제다 — 로그 한 줄이 나가도 값 자체가 틀렸으면 소용없다.
    """
    src = _PIPELINE.read_text("utf-8")
    mismatched = []
    for name, fallback in _declared_fallbacks():
        if name in _DESCRIPTIVE_FALLBACKS:
            continue
        fallback = fallback.strip()
        default = _config_default(name)
        if default is None:
            continue  # config 에 없는 이름(파생값)은 대조 대상이 아니다
        if fallback.startswith("_"):
            # 지역 변수로 넘긴 경우 — 그 변수에 대입된 리터럴을 찾는다
            m = re.search(r"%s\s*=\s*([\d.]+)\s*\n\s*_warn_setting_fallback" % re.escape(fallback), src)
            if m:
                fallback = m.group(1)
        if fallback != default:
            mismatched.append("%s: 코드 %s ≠ config %s" % (name, fallback, default))
    assert not mismatched, "폴백이 선언된 기본값과 다르다: %s" % mismatched


def test_sparse_evidence_fallback_does_not_disable_the_gate():
    """폴백이 0 이면 `if _min_ev > 0:` 이 거짓이 되어 **게이트가 통째로 꺼진다.**

    [2026-09-06] 실제로 0.0 이었다. 설정 읽기 실패 한 번에 미탐 방지 장치가 사라졌다.
    """
    src = _PIPELINE.read_text("utf-8")
    m = re.search(
        r"try:\s*\n\s*_min_ev = float\(settings\.rule_fallback_min_evidence\)\s*\n"
        r".*?except[^\n]*\n(.*?)\n\s*if _min_ev > 0:",
        src, re.S,
    )
    assert m, "sparse-evidence 폴백 블록을 못 찾았다 — 이 시험이 아무것도 안 지키고 있다"
    block = m.group(1)
    assign = re.search(r"_min_ev\s*=\s*([\d.]+)", block)
    assert assign, "폴백에서 _min_ev 대입을 못 찾았다: %r" % block
    assert float(assign.group(1)) > 0, (
        "폴백이 %s 다 — 이 값이면 게이트가 안 돈다(아래가 `if _min_ev > 0:`)" % assign.group(1)
    )


def test_sparse_evidence_fallback_leaves_a_trace():
    """조용히 폴백하면 운영자는 게이트가 꺼진 줄 모른다 — 흔적이 있어야 한다."""
    src = _PIPELINE.read_text("utf-8")
    assert '_warn_setting_fallback("rule_fallback_min_evidence"' in src, (
        "폴백에 흔적이 없다 — 같은 파일의 다른 설정 다섯 곳은 남긴다"
    )


def test_the_gate_this_protects_still_exists():
    """게이트가 사라지면 위 시험들이 조용히 무의미해진다."""
    src = _PIPELINE.read_text("utf-8")
    assert "sparse-evidence: rule total score" in src, "sparse-evidence 경고가 사라졌다"
    # classify_service 가 그 경고를 읽어 검수로 보내야 의미가 있다
    cs = (_SRC / "services" / "classify_service.py").read_text("utf-8")
    assert "sparse-evidence" in cs, "경고를 내도 서비스가 안 읽으면 검수로 안 간다"
