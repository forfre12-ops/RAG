"""3축 게이트 판정을 **모델 활성화 경로에 연결**한다.

왜(2026-08-16). `scripts/gate_p1_candidate.py` 가 3축(경화 홀드아웃·공개문서 FPR·적대 셋)
으로 후보를 판정하고 `reports/v5_gate/*.json` 에 결과를 남기는데, **그 파일을 읽는 코드가
어디에도 없었다.** 실측: `grep -rn "gate_verdict\\|v5_gate" src/` 결과 0곳.

즉 오늘 두 후보를 FAIL 시킨 판정은 **파일로만 남고 활성화를 막지 못한다.** 누군가
`POST /admin/models/activate` 를 부르면 3축을 안 돌린 모델도 서빙에 들어간다.

활성화 경로에는 이미 게이트가 있지만 보는 것이 다르다.

    deploy_gate(기존)   fnr_high · f1_macro 두 지표 (+ 앵커·메타모픽 opt-in)
    3축 게이트(이것)     경화 홀드아웃 · 공개문서 FPR · 적대 셋

두 게이트는 겹치지 않는다. 3축은 **다른 판정면**을 보므로 둘 다 통과해야 한다.

⚠ fail-secure 가 아니라 **fail-open 으로 둔다.** 리포트가 없으면 막지 않는다.
   이유: 3축 게이트는 사람이 손으로 돌리는 절차이고, 없다고 활성화를 통째로 막으면
   기존 운영(수동 활성)이 전부 멈춘다. 대신 **없다는 사실을 사유에 남긴다.**
   강제하려면 `axis_gate_required=True` 로 옵트인한다.

⚠ 리포트가 낡으면(다른 후보의 판정이면) 없는 것으로 본다 - 후보 경로가 일치해야 인정한다.
   이게 없으면 '아무 모델이나 통과한 리포트 하나' 로 전부 통과시킬 수 있다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_REPORT_DIR = "reports/v5_gate"


@dataclass(frozen=True)
class AxisGateResult:
    """3축 게이트 조회 결과.

    found=False 는 '판정한 적 없음' 이고 passed=False 와 다르다. 둘을 같은 말로 다루면
    '측정 안 함' 이 '탈락' 으로 보고돼 원인을 못 찾는다.
    """

    found: bool
    passed: bool
    report_path: str | None
    reason: str
    detail: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "found": self.found, "passed": self.passed,
            "report": self.report_path, "reason": self.reason,
        }


def _candidate_matches(verdict: dict, model_dir: str) -> bool:
    """리포트가 이 후보를 판정한 것인가.

    경로 표기가 OS 마다 다르므로(`\\` vs `/`) 정규화 후 마지막 조각(버전 라벨)으로 본다.
    """
    cand = str(verdict.get("candidate") or "").replace("\\", "/").rstrip("/")
    want = str(model_dir or "").replace("\\", "/").rstrip("/")
    if not cand or not want:
        return False
    return cand == want or cand.split("/")[-1] == want.split("/")[-1]


def lookup_axis_gate(model_dir: str, *, report_dir: str = DEFAULT_REPORT_DIR) -> AxisGateResult:
    """이 후보에 대한 3축 게이트 판정을 찾는다.

    같은 후보의 리포트가 여러 개면 **가장 최근 것**을 쓴다. 그리고 그 하나만 본다 -
    여러 리포트 중 통과한 것을 고르면 그것이 곧 게이트 회피다.
    """
    d = Path(report_dir)
    if not d.is_dir():
        return AxisGateResult(False, False, None,
                              f"3축 게이트 리포트 디렉터리 없음: {report_dir}")

    best: tuple[float, Path, dict] | None = None
    for p in d.glob("*.json"):
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if "OVERALL_PASS" not in v:
            continue
        if not _candidate_matches(v, model_dir):
            continue
        mtime = p.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, p, v)

    if best is None:
        return AxisGateResult(
            False, False, None,
            f"이 후보({model_dir})에 대한 3축 게이트 판정 없음 - "
            f"scripts/gate_p1_candidate.py --candidate {model_dir} 를 먼저 돌릴 것",
        )

    _, path, v = best
    passed = bool(v.get("OVERALL_PASS"))
    fails = [k.split("_")[0] for k in ("axis1_hardened_holdout", "axis2_public_fpr",
                                       "axis3_adversarial")
             if isinstance(v.get(k), dict) and v[k].get("PASS") is False]
    reason = ("3축 게이트 통과" if passed
              else f"3축 게이트 미통과: {', '.join(fails) or 'unknown'}")
    return AxisGateResult(True, passed, str(path), reason, detail=v)


def axis_gate_blocks(
    model_dir: str,
    *,
    required: bool = False,
    report_dir: str = DEFAULT_REPORT_DIR,
) -> tuple[bool, AxisGateResult]:
    """활성화를 막아야 하는가.

    Returns:
        (막을지, 조회결과)

    판정 규칙:
        판정 있음 · 통과      -> 안 막음
        판정 있음 · 미통과     -> **막음** (required 무관 - 명시적으로 떨어진 것이다)
        판정 없음             -> required 일 때만 막음 (기본은 fail-open + 사유 기록)
    """
    res = lookup_axis_gate(model_dir, report_dir=report_dir)
    if res.found:
        return (not res.passed), res
    return bool(required), res
