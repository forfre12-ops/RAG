#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""opt-in 플래그를 켜면 **무엇을 깨는가** — 켜기 전에 대조한다.

왜 이 도구가 있는가(2026-09-10). `model_secondopinion_llm_enabled` 를 "1순위 고도화"로
추천했다가 틀렸다. config 주석에 비용이 적혀 있었고("비용: 자동확정 비-TS 건당 LLM 1콜")
그걸 읽기까지 했는데, **그 비용이 어느 KPI 를 깨는지 대조하지 않았다.** 실측하니
건당 +7~8초였고 PER-002 핵심 KPI(S1.2 분류 p95 5,000ms·core)를 깬다.

그때 메모리에 "추천 전에 perf/kpis.py 와 대조한다"고 적었다. 그런데 **기억에 의존하는
규칙은 또 빼먹는다.** 그래서 도구로 만든다.

무엇을 하나.

  ① 배포 프로파일(onprem-local·full-train) 양쪽에서 꺼진 bool 플래그를 모은다.
  ② 각 플래그가 src/ 어디서 참조되는지 찾고, 그 위치를 경로로 분류한다.
       핫패스   api/ · services/classify_service · modules/m5_inference
                -> 켜면 **요청 지연**에 직접 붙는다. S1 KPI 와 대조 필수.
       배경     workers/ · services/partitions 등 beat·celery 경로
                -> 요청 지연과 무관. 자원(CPU/MEM) 쪽을 본다.
  ③ config.py 의 그 플래그 주석에서 **비용을 말하는 표현**을 뽑는다.
  ④ 핫패스에 붙는 플래그에는 S1 지연 KPI 를 나란히 찍어 여유를 보이게 한다.

무엇을 안 하나. **켜도 되는지 판정하지 않는다.** 실측은 켜 보고 재야 나온다
(A/B 방법은 llm-second-opinion 기록 참조 — 2차의견처럼 응답이 안 바뀌는 게이트는
지연 차이로만 동작을 확인할 수 있다). 이 도구는 "무엇을 재야 하는가"까지 한다.

    cd poc && python scripts/audit_flag_cost.py
    cd poc && python scripts/audit_flag_cost.py --flag factor_shadow_enabled
"""
from __future__ import annotations

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

import argparse
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
_SRC = _POC / "src"

PROFILES = ("lite-noapi", "onprem-local", "full-train")
DEPLOYED = ("onprem-local", "full-train")

# 핫패스 = 요청이 지나가는 길. 여기 붙는 플래그는 지연 KPI 와 대조해야 한다.
_HOT = (
    "koipa/api/",
    "koipa/services/classify_service",
    "koipa/modules/m5_inference/",
)
# 배경 = beat·celery·학습이 도는 길. 요청 지연과 무관하고 자원 쪽을 본다.
_BG = (
    "koipa/workers/",
    "koipa/services/partitions",
    "koipa/services/outbox",
    "koipa/services/retention",
    "koipa/services/training_service",
    "koipa/services/drift_monitor",
    "koipa/modules/m4_training/",
    "koipa/modules/m6_evaluation/",
    "koipa/proxy_model_comparison",
)

# 참조가 **동작**인지 **보고**인지 가른다.
#
# 왜 필요한가(2026-09-10 실측). 첫 판은 `retention_enabled` 를 핫패스로 찍었다. 근거가
# `api/health.py:286` 의 ``"enabled": bool(getattr(settings, "retention_enabled", False))``
# 였는데 그건 healthz 가 **값을 보고하는** 줄이지 지연을 만드는 일이 아니다. 실제 일은
# `services/retention.py` 에서 배경으로 돈다. 보고를 동작으로 세면 켜도 되는 플래그를
# "핫패스라 위험"으로 잘못 막는다.
_CONTROL = re.compile(r"^\s*(el)?if\b|^\s*(el)?if .*\bnot\b|\bif not \b|^\s*while\b|^\s*assert\b")

# 비용을 말하는 표현. 있으면 "켜면 뭔가 더 든다"는 신호다.
_COST = re.compile(
    r"지연|느려|추론이 한 번 더|한 번 더 돈다|비용|건당|OOM|메모리|CPU|초\b|분\b|재다운로드|부하"
)


def flags_off_in_deploy() -> tuple[list[str], dict]:
    """배포 프로파일 양쪽에서 꺼진 bool 플래그. audit_wiring 과 같은 방식으로 읽는다."""
    sys.path.insert(0, str(_SRC))
    table: dict[str, dict[str, bool]] = {}
    try:
        for prof in PROFILES:
            os.environ["DEPLOY_PROFILE"] = prof
            import koipa.config as _cfg  # noqa: PLC0415
            importlib.reload(_cfg)
            st = _cfg.settings
            row = {}
            for n in sorted(dir(st)):
                if not (n.endswith(("_enabled", "_on")) or n.startswith("enable_")):
                    continue
                v = getattr(st, n, None)
                if isinstance(v, bool):
                    row[n] = v
            table[prof] = row
    finally:
        os.environ.pop("DEPLOY_PROFILE", None)
    names = sorted({n for r in table.values() for n in r})
    off = [n for n in names if not any(table[p].get(n) for p in DEPLOYED)]
    return off, table


def comment_block(flag: str) -> list[str]:
    """config.py 에서 그 플래그 정의 위에 붙은 주석 블록. 빈 줄을 만나면 끊는다."""
    cfg = (_SRC / "koipa" / "config.py").read_text(encoding="utf-8", errors="replace")
    lines = cfg.splitlines()
    idx = next((i for i, ln in enumerate(lines)
                if re.match(rf"\s*{re.escape(flag)}\s*:", ln)), None)
    if idx is None:
        return []
    out: list[str] = []
    i = idx - 1
    while i >= 0:
        s = lines[i].strip()
        if s.startswith("#"):
            out.append(s.lstrip("# ").rstrip())
            i -= 1
            continue
        break
    return list(reversed(out))


def refs(flag: str) -> list[str]:
    """src/ 안 참조 위치(config.py 자신은 뺀다)."""
    r = subprocess.run(
        ["git", "grep", "-n", "--", flag, "src/"],
        cwd=str(_POC), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out = []
    for ln in r.stdout.decode("utf-8", "replace").splitlines():
        if "koipa/config.py" in ln:
            continue
        out.append(ln.strip())
    return out


def _ref_body(ref: str) -> str:
    """'경로:줄번호:코드' 에서 코드 부분만."""
    parts = ref.split(":", 2)
    return parts[2] if len(parts) == 3 else ref


# 같은 조건문에 AND 로 묶인 **동반 설정**. 이게 비면 플래그를 켜도 무동작이다.
_SETTING_REF = re.compile(r'getattr\(\s*[A-Za-z_][\w.]*\s*,\s*"([a-z_][a-z0-9_]*)"')


def companions(ref_line: str, flag: str) -> list[str]:
    """참조 한 줄에서 그 플래그와 **함께 요구되는** 다른 설정 이름.

    왜 필요한가(2026-09-10 실측). `factor_shadow_enabled` 를 켜고 A/B 를 돌렸는데
    지연 차이가 0 이었다. "요소모델은 빨라서 괜찮다"로 읽을 뻔했는데 실제로는
    **경로가 안 돌았다** — 조건이 AND 둘이고 짝이 비어 있었다:

        if getattr(_fs, "factor_shadow_enabled", False) and getattr(_fs, "factor_model_dir", ""):

    `factor_model_dir` 기본값이 `""` 이고 프로파일이 채워 주지 않는다. 첫 판의 이 도구는
    "핫패스에 붙는다"까지만 말하고 이 짝을 못 봤다. 짝이 비면 켜도 무동작이므로
    **켜기 전에 먼저 채울 것**을 말해야 한다.
    """
    body = _ref_body(ref_line)
    if " and " not in body and " or " not in body:
        return []
    names = [n for n in _SETTING_REF.findall(body) if n != flag]
    # 순서 유지하며 중복 제거
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def default_of(name: str) -> str | None:
    """config.py 의 그 설정 기본값 문자열. 못 찾으면 None."""
    cfg = (_SRC / "koipa" / "config.py").read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^\s*{re.escape(name)}\s*:[^=]*=\s*(.+?)\s*(?:#.*)?$", cfg, re.M)
    return m.group(1).strip() if m else None


_EMPTY = ('""', "''", "None", "False", "[]", "{}", "0")


def classify_path(ref: str) -> str:
    """참조 한 줄을 분류한다. 핫패스는 **동작일 때만** 준다 — 보고는 지연을 만들지 않는다."""
    is_control = bool(_CONTROL.search(_ref_body(ref)))
    if any(h in ref for h in _HOT):
        return "핫패스" if is_control else "핫패스(값 보고만)"
    if any(b in ref for b in _BG):
        return "배경"
    return "기타"


def latency_kpis() -> list[str]:
    """S1(단일 문서 분류 동기) 지연 KPI — 핫패스 플래그의 대조 기준."""
    p = _SRC / "koipa" / "perf" / "kpis.py"
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if re.search(r'KPI\("S1\.\d".*latency', ln):
            out.append(ln.strip().rstrip(","))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--flag", default=None, help="한 플래그만 본다")
    a = ap.parse_args()

    off, _table = flags_off_in_deploy()
    targets = [a.flag] if a.flag else off
    if a.flag and a.flag not in off:
        print(f"※ {a.flag} 은 배포 프로파일에서 꺼진 목록에 없다(이미 ON 이거나 이름 오류).")

    print("=" * 78)
    print(" 켜기 전 대조 — 배포 프로파일 양쪽에서 꺼진 플래그")
    print("=" * 78)
    print(f" 대상 {len(targets)}개" + ("" if a.flag else f" / 꺼진 플래그 {len(off)}개"))

    print("\n 핫패스 플래그의 대조 기준 (S1 = 단일 문서 분류 동기):")
    for k in latency_kpis():
        print(f"   {k}")

    hot_flags: list[str] = []
    for flag in targets:
        rs = refs(flag)
        kinds = {classify_path(r) for r in rs}
        if "핫패스" in kinds:
            where = "핫패스"
        elif "배경" in kinds:
            where = "배경"
        elif "핫패스(값 보고만)" in kinds:
            where = "핫패스(값 보고만 — 지연 없음)"
        else:
            where = "기타/미참조"
        if where == "핫패스":
            hot_flags.append(flag)
        cm = comment_block(flag)
        costs = [c for c in cm if _COST.search(c)]

        print("\n" + "-" * 78)
        print(f" {flag}")
        print(f"   붙는 곳 : {where}" + (f"  (참조 {len(rs)}곳)" if rs else "  (src 참조 0곳)"))
        for r in rs[:4]:
            print(f"     {r[:104]}")
        if len(rs) > 4:
            print(f"     … 외 {len(rs) - 4}곳")
        if costs:
            print("   비용 표현:")
            for c in costs[:4]:
                print(f"     · {c[:96]}")
        else:
            print("   비용 표현: (주석에 없음 — 없다는 뜻이 아니라 안 적혔다는 뜻)")
        # 동반 설정 — 비어 있으면 플래그를 켜도 무동작이다.
        comp: dict[str, str | None] = {}
        for r in rs:
            for c in companions(r, flag):
                comp.setdefault(c, default_of(c))
        if comp:
            print("   동반 조건(AND):")
            for c, dv in comp.items():
                blank = dv is not None and dv in _EMPTY
                tag = "  ← 비어 있다. 채우지 않으면 켜도 무동작" if blank else ""
                print(f"     · {c} = {dv if dv is not None else '(config.py 에서 못 찾음)'}{tag}")
            if any(dv is not None and dv in _EMPTY for dv in comp.values()):
                print("   ⛔ 짝이 빈 채로 A/B 를 돌리면 지연 차이 0 이 나오고 그것을 '안전'으로")
                print("      오독하게 된다(2026-09-10 factor_shadow 에서 실제로 그랬다).")

        if where == "핫패스":
            print("   ⚠ 요청 지연에 직접 붙는다 — 켜기 전에 **A/B 로 p95 를 재고** 위 KPI 와 대조할 것.")
        elif where == "배경":
            print("   → 요청 지연과 무관. 자원(CPU/MEM peak) 쪽 KPI(S11.6·S11.7)를 본다.")

    print("\n" + "=" * 78)
    print(" 요약")
    print("=" * 78)
    print(f"  핫패스에 붙는 꺼진 플래그 {len(hot_flags)}개 — 켜기 전 지연 실측 필수")
    for f in hot_flags:
        print(f"    {f}")
    print("\n  실측 방법: 같은 문서를 플래그만 바꿔 두 번 태우고 응답 시간을 비교한다.")
    print("  ⚠ 응답 내용이 안 바뀌는 게이트도 있다(2차의견은 검수 라우팅만 바꾼다) —")
    print("     그런 것은 **지연 차이만이** 동작의 증거다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
