"""`make readiness` 가 릴리스 게이트 입력을 **전부** 갱신하는가.

왜 이 파일이 있는가(2026-09-06). `check_release_gate` 가 판정에 쓰는 측정 입력은 셋이다:

    p1_public    reports/p1_release_legal_direct.json
    p1_serving   reports/SERVING_VS_RAW.json
    p1_llm       reports/p1_release_holdout_direct.json

그런데 "권장 진입점"인 `make readiness` 는 **둘만** 만들었다. 셋째(p1_serving)는 어떤
타깃도 만들지 않아 계속 낡았다 — 실측 **20일**(한도 14일).

권장 경로를 그대로 따랐는데도 게이트가 "이 입력은 지금 코드가 아닌 옛 코드를 잰 것"이라고
경고하면, 사람은 결국 그 경고를 무시하게 된다. 경고가 무시되기 시작하면 게이트는 없는 것과
같다. 그래서 **입력 목록과 갱신 경로가 어긋나지 않는지**를 시험이 지킨다.

⚠ 이 시험은 파일이 실제로 만들어지는지는 안 본다(모델 로드가 필요해 느리다). 갱신 명령이
  **선언돼 있는지**만 본다. 그것만으로 "아무 데서도 안 만든다"는 종류의 구멍은 막힌다.
"""
from __future__ import annotations

import pathlib
import re

_POC = pathlib.Path(__file__).resolve().parents[1]
_MAKEFILE = _POC / "Makefile"
_READINESS = _POC / "scripts" / "build_operational_readiness.py"


def _gate_input_paths() -> dict[str, str]:
    """게이트가 읽는 측정 입력 — argparse 기본값에서 뽑는다(선언 한 곳)."""
    src = _READINESS.read_text("utf-8")
    out: dict[str, str] = {}
    for name in ("p1-public", "p1-serving", "p1-llm"):
        m = re.search(
            r'add_argument\(\s*"--%s"\s*,\s*default\s*=\s*"([^"]+)"' % re.escape(name), src
        )
        assert m, "%s 의 기본값을 못 찾았다 — 이 시험이 아무것도 안 지키고 있다" % name
        out[name.replace("-", "_")] = m.group(1)
    return out


def _readiness_recipe() -> str:
    """`readiness` 가 의존하는 타깃들의 명령을 한 덩어리로."""
    text = _MAKEFILE.read_text("utf-8").replace("\r\n", "\n")
    m = re.search(r"^readiness:\s*([^\n#]*)", text, re.M)
    assert m, "readiness 타깃이 없다"
    deps = [d for d in m.group(1).split() if d and not d.startswith("#")]
    assert deps, "readiness 가 아무것도 의존하지 않는다"

    body = ""
    for dep in deps + ["readiness"]:
        b = re.search(r"^%s:.*?\n((?:\t.*\n|\n)*)" % re.escape(dep), text, re.M)
        if b:
            body += b.group(1)
    return body


def test_readiness_declares_a_refresh_for_every_gate_input():
    """입력 셋 전부가 readiness 경로 안에서 갱신되는가.

    [2026-09-06] p1_serving(SERVING_VS_RAW.json)이 여기서 빠져 있었다. 20일 낡은 값으로
    릴리스 게이트가 판정하고 있었다.
    """
    recipe = _readiness_recipe()
    missing = []
    for key, path in _gate_input_paths().items():
        stem = pathlib.Path(path).stem          # 확장자는 .md/.json 로 갈린다
        if stem not in recipe:
            missing.append("%s (%s)" % (key, path))
    assert not missing, (
        "make readiness 가 안 만드는 게이트 입력: %s\n"
        "  → 권장 경로를 따라도 이 입력은 계속 낡는다." % missing
    )


def test_gate_inputs_are_three_and_named():
    """입력이 늘거나 이름이 바뀌면 위 시험이 조용히 헐거워진다 — 개수를 못박는다."""
    paths = _gate_input_paths()
    assert set(paths) == {"p1_public", "p1_serving", "p1_llm"}, sorted(paths)


def test_readiness_tells_the_operator_about_the_model_dir():
    """CLASSIFIER_MODEL_DIR 없이 돌리면 model parity 가 'deployed model unknown' 으로 막힌다.

    안내가 없으면 운영자는 그 BLOCKED 를 구조적 한계로 오해한다 — 실제로는 환경변수 하나다.
    """
    recipe = _readiness_recipe()
    assert "CLASSIFIER_MODEL_DIR" in recipe, "readiness 가 필요한 환경변수를 안 알려 준다"
