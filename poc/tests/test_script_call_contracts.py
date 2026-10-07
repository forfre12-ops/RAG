"""스크립트가 koipa 함수를 **없는 인자로 부르지 않는가** — 돌려 보지 않고.

왜 이 파일이 있는가(2026-09-06). `scripts/run_perf_scenarios.py` 가 이틀간 죽어 있었다:

    TypeError: capture_env() got an unexpected keyword argument 'vector_backend'

2026-09-04 커밋 319069b9 가 `perf/env.py` 에서 그 인자를 지웠는데 호출부가 남았다.
그 커밋은 **"판정면 회귀 0"** 으로 통과했다 — 회귀 게이트가 스크립트를 돌리지 않는다.
그 사이 PER-002 성능 증빙은 2026-08-17 자 옛 보고서(KPI 67개 기준, 지금은 64개)였다.

**스크립트에는 시험이 없다.** 라이브러리를 고치면 스크립트가 조용히 깨지고, 그 스크립트가
만들던 증빙은 낡은 채로 남는다. 돌려서 확인할 수도 없다 — 대부분 파일을 쓰거나 모델을
로드한다. 그래서 부르지 않고 **인자 이름만** 대조한다(약 3.5초, 스크립트 301개).
"""
from __future__ import annotations

import pathlib
import sys

_POC = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _POC / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def test_no_script_calls_a_koipa_function_with_an_unknown_kwarg():
    """어긋난 호출 0건.

    실패하면 그 스크립트는 **첫 줄에서 TypeError 로 죽는다.** 그 스크립트가 만드는 산출물
    (성능 보고서·릴리스 게이트 입력·데이터셋)은 그때부터 낡은 채로 남고, 아무도 모른다.
    """
    from audit_call_contracts import scan

    bad = []
    for py in sorted(_SCRIPTS.glob("*.py")):
        for line, dotted, unknown, accepted in scan(py):
            bad.append(
                "%s:%d — %s(...) 가 안 받는 인자 %s (받는 것: %s)"
                % (py.name, line, dotted, unknown, accepted)
            )
    assert not bad, "koipa 함수를 없는 인자로 부른다:\n  " + "\n  ".join(bad)


def test_the_auditor_actually_resolves_signatures():
    """검사기가 아무것도 못 읽고 있으면 위 시험은 늘 통과한다 — 0건이 무의미해진다.

    알려진 함수 하나를 실제로 풀어 본다.
    """
    from audit_call_contracts import _accepted_kwargs, _resolve

    fn = _resolve("koipa.perf.capture_env")
    assert fn is not None and callable(fn), "capture_env 를 못 찾았다"
    acc = _accepted_kwargs(fn)
    assert acc is not None, "시그니처를 못 읽었다"
    accepted, star = acc
    assert not star, "**kwargs 를 받으면 이름 대조가 무의미해진다 — 시험을 다시 짜야 한다"
    assert "probe_services" in accepted, sorted(accepted)
    # 하니스를 죽인 그 인자 — 지금은 없어야 맞다
    assert "vector_backend" not in accepted, (
        "vector_backend 가 되살아났다면 run_perf_scenarios 쪽도 함께 봐야 한다"
    )


def test_scripts_importing_koipa_are_actually_scanned():
    """koipa 를 들여오는 스크립트가 하나도 안 잡히면 검사 범위가 빈 것이다."""
    from audit_call_contracts import _imported_koipa_names
    import ast

    n = 0
    for py in sorted(_SCRIPTS.glob("*.py")):
        try:
            tree = ast.parse(py.read_text("utf-8"))
        except SyntaxError:
            continue
        if _imported_koipa_names(tree):
            n += 1
    assert n >= 20, "koipa 를 직접 들여오는 스크립트가 %d개뿐 — 검사 범위를 확인할 것" % n
