"""3축 게이트 판정이 **모델 활성화를 실제로 막는지** 고정한다.

왜(2026-08-16). `scripts/gate_p1_candidate.py` 가 3축으로 후보를 판정하고
`reports/v5_gate/*.json` 에 결과를 남기는데 **그 파일을 읽는 코드가 0곳이었다**
(실측: `grep -rn "gate_verdict|v5_gate" src/`). 오늘 두 후보를 FAIL 시킨 판정이
파일로만 남고 활성화를 못 막는 상태였다.

이 파일이 고정하는 것:
    1. 판정 있고 미통과 -> 막는다 (required 와 무관)
    2. 판정 있고 통과   -> 안 막는다
    3. 판정 없음        -> 기본은 안 막는다(fail-open) · required=True 면 막는다
    4. 다른 후보의 리포트로는 통과 못 한다 (리포트 하나로 전부 통과시키는 회피 차단)
    5. 같은 후보 리포트가 여럿이면 **최근 것 하나만** 본다 (통과한 것 골라 쓰기 차단)
"""
from __future__ import annotations

import json
import time

from koipa.modules.m6_evaluation.axis_gate_link import (
    axis_gate_blocks,
    lookup_axis_gate,
)


def _verdict(path, candidate: str, passed: bool, fails: tuple[str, ...] = ()) -> None:
    v = {
        "candidate": candidate,
        "OVERALL_PASS": passed,
        "axis1_hardened_holdout": {"PASS": "axis1" not in fails},
        "axis2_public_fpr": {"PASS": "axis2" not in fails},
        "axis3_adversarial": {"PASS": "axis3" not in fails},
    }
    path.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")


def test_failed_verdict_blocks(tmp_path):
    """명시적으로 떨어진 후보는 required 와 무관하게 막는다."""
    _verdict(tmp_path / "g.json", "artifacts/x/v-bad", False, ("axis1", "axis3"))
    block, res = axis_gate_blocks("artifacts/x/v-bad", report_dir=str(tmp_path))
    assert block is True
    assert res.found and not res.passed
    assert "axis1" in res.reason and "axis3" in res.reason


def test_passed_verdict_does_not_block(tmp_path):
    _verdict(tmp_path / "g.json", "artifacts/x/v-good", True)
    block, res = axis_gate_blocks("artifacts/x/v-good", report_dir=str(tmp_path))
    assert block is False
    assert res.found and res.passed


def test_missing_verdict_is_fail_open_by_default(tmp_path):
    """판정 없음은 '탈락' 이 아니다.

    3축은 사람이 손으로 돌리는 절차다. 없다고 막으면 기존 수동 활성이 전부 멈춘다.
    다만 found=False 로 구분돼 사유에 남는다.
    """
    block, res = axis_gate_blocks("artifacts/x/v-none", report_dir=str(tmp_path))
    assert block is False
    assert res.found is False and res.passed is False


def test_missing_verdict_blocks_when_required(tmp_path):
    block, res = axis_gate_blocks("artifacts/x/v-none", required=True, report_dir=str(tmp_path))
    assert block is True
    assert res.found is False


def test_other_candidates_report_does_not_count(tmp_path):
    """다른 후보의 통과 리포트로는 통과 못 한다.

    이게 없으면 '아무 모델이나 통과한 리포트 하나' 로 전부 통과시킬 수 있다.
    """
    _verdict(tmp_path / "other.json", "artifacts/x/v-other", True)
    block, res = axis_gate_blocks("artifacts/x/v-mine", required=True, report_dir=str(tmp_path))
    assert block is True
    assert res.found is False


def test_latest_report_wins(tmp_path):
    """같은 후보 리포트가 여럿이면 최근 것만 본다 - 통과한 것 골라 쓰기 차단."""
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    _verdict(old, "artifacts/x/v-c", True)
    time.sleep(0.02)
    _verdict(new, "artifacts/x/v-c", False, ("axis2",))
    res = lookup_axis_gate("artifacts/x/v-c", report_dir=str(tmp_path))
    assert res.found and res.passed is False, "옛 통과 리포트를 골라 쓰면 안 된다"


def test_version_label_match(tmp_path):
    """경로 표기가 달라도 버전 라벨이 같으면 같은 후보로 본다(Windows/Linux 구분자)."""
    _verdict(tmp_path / "g.json", "artifacts\\x\\v-c", False, ("axis1",))
    block, res = axis_gate_blocks("artifacts/other/v-c", report_dir=str(tmp_path))
    assert res.found and block is True


def test_activation_path_consults_axis_gate():
    """활성화 서비스가 실제로 이 함수를 부르는지 - 배선이 끊기면 전부 무의미하다."""
    import inspect

    from koipa.services import training_service

    src = inspect.getsource(training_service)
    assert "axis_gate_blocks" in src, "활성화 경로가 3축 게이트를 조회하지 않는다"
    assert "axis_gate_blocked" in src, "차단 사유가 응답에 안 실린다"
