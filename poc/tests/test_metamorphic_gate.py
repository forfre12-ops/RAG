"""Release 메타모픽 게이트(check_metamorphic_gate) + 재현 로더(_load_samples_fixture) 단위 테스트.

deploy_gate 의 opt-in 가시적-skip("리포트 미제공 → passed=True") 과 달리, 이 게이트는
리포트 미생성/표본부족(측정불가)을 fail-closed 로 막는다 — "안 돌린 metamorphic 을 조용히 통과"
시키던 배포전 ④ fake-green 을 닫는다. 로더는 커밋된 패러프레이즈 픽스처를 live LLM 없이
재분류하게 하는 재현성의 핵심.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import check_metamorphic_gate  # noqa: E402
from gen_metamorphic_pairs import _load_samples_fixture  # noqa: E402
from koipa.modules.m6_evaluation.metamorphic import build_metamorphic_report  # noqa: E402
from koipa.modules.m6_evaluation.paraphrase_gen import GeneratedSample  # noqa: E402


def _report(n: int, violations: int) -> dict:
    """forward n쌍 중 violations건이 미탐(TS 앵커 → S3 예측)인 리포트(to_dict)."""
    pairs = [
        {"anchor_id": f"a{i}", "anchor_grade": "TS",
         "paraphrase_pred": ("S3" if i < violations else "TS")}
        for i in range(n)
    ]
    return build_metamorphic_report(pairs).to_dict()


def _write(tmp_path: Path, obj: dict) -> str:
    p = tmp_path / "meta.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_gate_passes_clean_measurable(tmp_path, monkeypatch):
    path = _write(tmp_path, _report(n=8, violations=0))
    monkeypatch.setattr("sys.argv", ["check_metamorphic_gate.py", "--report", path])
    assert check_metamorphic_gate.main() == 0


def test_gate_blocks_forward_regression(tmp_path, monkeypatch):
    path = _write(tmp_path, _report(n=8, violations=3))
    monkeypatch.setattr("sys.argv", ["check_metamorphic_gate.py", "--report", path])
    assert check_metamorphic_gate.main() == 1


def test_gate_fails_not_measurable(tmp_path, monkeypatch):
    # n=3 < min_n=5 -> 측정불가 = FAIL (fake-green 차단: 안 돌린/표본부족 eval 을 통과시키지 않음).
    path = _write(tmp_path, _report(n=3, violations=0))
    monkeypatch.setattr("sys.argv", ["check_metamorphic_gate.py", "--report", path])
    assert check_metamorphic_gate.main() == 1


def test_gate_fails_missing_report(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr("sys.argv", ["check_metamorphic_gate.py", "--report", str(missing)])
    assert check_metamorphic_gate.main() == 1


def test_gate_unwraps_nested_producer_shape(tmp_path, monkeypatch):
    # 생산자 중첩형 {"report": rd} 도 언랩되어 forward 를 본다(조용한 통과 회귀 잠금).
    path = _write(tmp_path, {"model_dir": "x", "report": _report(n=8, violations=0)})
    monkeypatch.setattr("sys.argv", ["check_metamorphic_gate.py", "--report", path])
    assert check_metamorphic_gate.main() == 0


def test_load_samples_fixture_splits_and_preserves(tmp_path):
    fx = tmp_path / "fx.jsonl"
    fx.write_text(
        "\n".join([
            json.dumps({"anchor_id": "a1", "anchor_grade": "S1", "kind": "forward",
                        "admitted": True, "generated_text": "패러프레이즈 본문", "required_tokens": ["t"]}),
            json.dumps({"anchor_id": "a1", "anchor_grade": "S1", "kind": "reverse",
                        "admitted": True, "generated_text": "사실 제거 본문", "required_tokens": ["t"]}),
        ]) + "\n",
        encoding="utf-8",
    )
    gen = _load_samples_fixture(fx, GeneratedSample)
    assert len(gen["forward"]) == 1 and len(gen["reverse"]) == 1
    assert gen["forward"][0].generated_text == "패러프레이즈 본문"
    assert gen["forward"][0].admitted is True
    assert gen["forward"][0].anchor_grade == "S1"
