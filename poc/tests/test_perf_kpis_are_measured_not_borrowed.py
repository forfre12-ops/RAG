"""성능 KPI 가 **잰 값인가, 베낀 값인가.**

왜 이 파일이 있는가(2026-09-06). `perf/scenarios.py` 의 S6.2 가 이렇게 돼 있었다:

    try:
        provider = NoopProvider()
        txt = provider.synthesize(target_grade=target, domain="tech")   # 그런 메서드가 없다
        ...
    except Exception:
        # 모듈/시그니처 불일치 시 fallback: doc/02 §부록 D 800건 결과(100%)를 보고용으로 차용
        ctx.record("s6_2", 1.00)

`NoopProvider` 의 메서드는 `generate` 다. 측정은 **매번 AttributeError 로 죽었고**, except 가
1.00 을 기록했다. 주석이 스스로 말한다 — 문서에서 **차용**한 값이다.

그 1.00 이 납품 성능 보고서에 "S6.2 라벨 일치도 100.0% ≥ 90.0% · PASS" 로 실렸다.
같은 것을 제대로 재는 `scripts/p3_generate_synthetic.py` 는 **25.0%** 를 낸다(FAIL).

**무측정은 SKIP 이어야지 PASS 로 둔갑하면 안 된다.** 값을 안 남기면 하니스가 SKIP 으로
집계한다 — 그것이 정직한 보고다. 이 시험이 그 규칙을 고정한다.
"""
from __future__ import annotations

import ast
import pathlib

_SCENARIOS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "koipa" / "perf" / "scenarios.py"
)


def _records_in_except() -> list[tuple[int, str, object]]:
    """except 블록 안에서 `ctx.record(key, 리터럴)` 하는 자리를 모은다."""
    tree = ast.parse(_SCENARIOS.read_text("utf-8"))
    out: list[tuple[int, str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for st in ast.walk(node):
            if not isinstance(st, ast.Call):
                continue
            f = st.func
            if not (isinstance(f, ast.Attribute) and f.attr == "record"):
                continue
            if not st.args or len(st.args) < 2:
                continue
            key = st.args[0]
            val = st.args[1]
            if isinstance(val, ast.Constant) and isinstance(val.value, (int, float, bool)):
                kname = key.value if isinstance(key, ast.Constant) else "?"
                out.append((st.lineno, str(kname), val.value))
    return out


def test_no_kpi_records_a_literal_value_from_an_except_block():
    """예외 처리에서 리터럴 값을 기록하면 **못 잰 것이 잰 것처럼 보고된다.**

    [2026-09-06] S6.2 가 그랬다. 실패하면 아무것도 기록하지 말 것 — 그러면 SKIP 으로
    집계되고, 리포트는 "못 쟀다"고 정직하게 말한다.
    """
    hits = _records_in_except()
    assert not hits, (
        "except 안에서 리터럴 KPI 값을 기록한다 — 무측정이 PASS/FAIL 로 둔갑한다:\n  "
        + "\n  ".join("L%d  %s = %r" % h for h in hits)
    )


def test_s6_2_uses_a_generator_api_that_exists():
    """없는 메서드를 부르면 측정이 조용히 죽는다.

    [2026-09-06] `NoopProvider.synthesize` 를 불렀는데 그 클래스에는 `generate` 밖에 없다.
    """
    from koipa.adapters.llm.noop_provider import NoopProvider

    assert not hasattr(NoopProvider, "synthesize"), (
        "synthesize 가 생겼다면 S6.2 가 어느 경로를 쓰는지 다시 확인할 것"
    )
    assert hasattr(NoopProvider, "generate"), "noop 공급자의 생성 API 가 사라졌다"

    # 주석에도 이 이름이 나온다(고침 이유를 적어 뒀다). 그래서 **코드만** 본다.
    tree = ast.parse(_SCENARIOS.read_text("utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "synthesize"
    ]
    assert not calls, "없는 메서드를 다시 부른다 (L%s)" % [c.lineno for c in calls]
    assert "SyntheticDocGenerator" in _SCENARIOS.read_text("utf-8"), (
        "S6.2 가 실제 생성 경로를 안 쓴다"
    )


def test_label_agreement_is_measured_the_same_way_as_the_poc():
    """같은 이름의 지표가 두 도구에서 갈리면 둘 중 하나는 거짓말이다.

    perf S6.2 와 scripts/p3_generate_synthetic.py 는 둘 다 "라벨 일치도"를 낸다.
    2026-09-06 이전에는 100% 와 25% 로 갈렸다 — perf 쪽이 아무것도 안 재고 있었다.
    이제 둘 다 SyntheticDocGenerator + LabelingPipeline 을 쓴다.
    """
    src = _SCENARIOS.read_text("utf-8")
    poc = (
        pathlib.Path(__file__).resolve().parents[1]
        / "scripts" / "p3_generate_synthetic.py"
    ).read_text("utf-8")
    for token in ("SyntheticDocGenerator", "LabelingPipeline"):
        assert token in src, "perf 가 %s 를 안 쓴다" % token
        assert token in poc, "PoC 가 %s 를 안 쓴다 — 기준 경로가 바뀌었나" % token
