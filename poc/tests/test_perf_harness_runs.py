"""성능 시나리오 하니스(PSH)가 **실제로 도는가** — PER-002 근거를 만드는 도구다.

왜 이 파일이 있는가(2026-09-06). `scripts/run_perf_scenarios.py` 가 **TypeError 로 죽어
있었다.** 시나리오 하나도 못 돌았다.

    TypeError: capture_env() got an unexpected keyword argument 'vector_backend'

원인: 2026-09-04 커밋 319069b9 가 유사문서 조회를 걷어내며 `perf/env.py` 에서 그 필드를
지웠는데 호출부가 남았다. 그 커밋 메시지는 "판정면 회귀 0" 이다 — **회귀 게이트가 이
하니스를 안 돌리니까** 통과했다.

그 사이 `doc/20_시나리오_성능_보고서.html` 은 2026-08-17 자 옛 보고서(KPI 67개 기준)
그대로 있었다. 지금 KPI 는 64개다. 문서가 없는 지표를 근거로 내걸고 있었다.

`perf/scenarios.py` 는 커버리지 10.3% 다 — pytest 가 아니라 이 하니스가 돌리기 때문이다.
그런데 **그 하니스가 도는지 재는 시험이 없었다.** 이 파일이 그 자리를 막는다.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import sys


_POC = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _POC / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ── 호출 계약 — 인자 이름이 어긋나면 첫 줄에서 죽는다 ──────────────────────
def test_build_report_only_passes_kwargs_capture_env_accepts():
    """`build_report` 가 `capture_env` 에 없는 인자를 넘기지 않는가.

    이게 정확히 하니스를 죽인 결함이다. 소스를 파싱해서 실제 호출 인자를 뽑고,
    시그니처와 대조한다 — 하니스 전체를 돌리지 않고 즉시 잡는다.
    """
    from koipa.perf.env import capture_env

    accepted = set(inspect.signature(capture_env).parameters)
    src = (_SCRIPTS / "run_perf_scenarios.py").read_text("utf-8")
    tree = ast.parse(src)

    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "capture_env"
    ]
    assert calls, "capture_env 호출을 못 찾았다 — 이 시험이 아무것도 안 지키고 있다"

    for call in calls:
        passed = {kw.arg for kw in call.keywords if kw.arg}
        unknown = passed - accepted
        assert not unknown, (
            "capture_env 가 안 받는 인자를 넘긴다: %s\n"
            "  (받는 것: %s)" % (sorted(unknown), sorted(accepted))
        )


def test_capture_env_runs_without_probing_services():
    """오프라인에서도 환경 캡처가 된다 — 폐쇄망 실행 경로다."""
    from koipa.perf.env import capture_env

    env = capture_env(probe_services=False, probe_pytest=False)
    assert env.python and env.platform
    assert env.cpu_count >= 1


# ── 정본 보호 — 부분 실행이 전수 보고서를 덮지 않는가 ──────────────────────
def test_partial_run_does_not_overwrite_the_canonical_report():
    """`--only`/`--skip` 은 정본 옆에 `.partial` 로 쓴다.

    [2026-09-06] 실측: `--only S5` 한 번으로 doc/20_시나리오_성능_보고서.html 이
    전수 64개짜리에서 **KPI 2개짜리 0/2 문서**로 바뀌었다. JSON 쪽은 partial_run
    표식으로 이미 막고 있었는데 HTML 만 그대로 덮어썼다.
    """
    from run_perf_scenarios import html_output_path

    base = pathlib.Path("doc/20_시나리오_성능_보고서.html")
    assert html_output_path(base) == base, "전수 실행은 정본에 쓴다"
    for kwargs in ({"only": "S5"}, {"skip": "S1"}, {"only": "S5", "skip": "S1"}):
        out = html_output_path(base, **kwargs)
        assert out != base, "부분 실행(%s)이 정본을 덮는다" % kwargs
        assert out.name.endswith(".partial.html"), out.name


# ── 연기 시험 — 실제로 한 시나리오를 돌려 본다 ──────────────────────────────
def test_harness_actually_runs_a_scenario():
    """한 시나리오를 실제로 돌린다 — 계약 시험이 놓치는 런타임 고장을 잡는다.

    계약만 보면 "인자는 맞는데 돌리면 죽는" 경우를 못 잡는다. 그리고 이 하니스가 죽어 있던
    방식이 정확히 그것이었다.

    ⚠ slow 마커를 안 붙였다. 그 마커는 "ML 모델 로드·10초 초과"용인데 이 시험은 모델을
      안 쓰고 5초대다. 무엇보다 **죽어 있던 도구를 지키는 시험을 빠른 경로에서 빼면**
      다음에도 같은 방식으로 죽는다.
    """
    from run_perf_scenarios import build_report

    report = build_report(mode="dryrun", probe_services=False, only="S5")
    assert report["summary"]["total_kpis"] >= 1, "KPI 를 하나도 못 쟀다"
    assert report["mode"] == "dryrun"
    assert report.get("partial_run") is None or report["partial_run"]["only"] == "S5"


def test_scenario_specs_are_not_empty():
    """시나리오 정의가 비면 하니스는 0/0 을 '성공'으로 보고한다 — 잴 것이 없는데."""
    from koipa.perf.scenarios import SPECS

    assert len(SPECS) >= 10, "시나리오 %d개 — doc/19·20 은 S1~S18 을 규정한다" % len(SPECS)
    ids = [getattr(s, "scenario", None) or getattr(s, "id", None) for s in SPECS]
    assert len(set(ids)) == len(ids), "시나리오 id 가 겹친다: %s" % ids


def test_kpi_definitions_cover_the_scenarios():
    """KPI 가 정의된 시나리오와 실행되는 시나리오가 같은가.

    한쪽에만 있으면 보고서가 조용히 항목을 빠뜨리거나 영구 SKIP 을 낸다.
    """
    from koipa.perf.kpis import KPIS
    from koipa.perf.scenarios import SPECS

    spec_ids = {getattr(s, "scenario", None) or getattr(s, "id", None) for s in SPECS}
    kpi_scenarios = {k.scenario for k in (KPIS.values() if isinstance(KPIS, dict) else KPIS)}
    orphan = kpi_scenarios - spec_ids
    assert not orphan, "KPI 는 있는데 실행할 시나리오가 없다: %s" % sorted(orphan)
