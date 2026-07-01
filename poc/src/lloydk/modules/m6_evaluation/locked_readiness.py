"""[번들 D] locked_gold_eval readiness 가시화 — '등급별 locked 몇 개·부족분·배포 가능 여부'.

deploy gate는 자동활성(retrain_auto_activate) 시 등급별 min_locked_per_grade 충족을 요구한다
(죽음의 나선 #4, golden_tiers.eval_readiness). 그 readiness를 운영자가 직접 보게 노출한다 —
무실데이터 단계엔 locked가 비어 ready=False(진실)이고, 사람서명으로 채워지면 자동으로 켜진다.

순수 로직 + 파일 I/O만(DB 불요). settings.locked_eval_jsonl 경로의 jsonl 레코드를
golden_tiers.eval_readiness로 평가한다(locked tier만 인정). 빈 경로/부재 파일 → no_locked_records.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def _load_locked_records(path: str | Path | None) -> list[dict]:
    """jsonl 경로에서 레코드 로드(best-effort). 빈 경로·부재·파싱오류 → []."""
    if not path:
        return []
    p = Path(path)
    if not p.exists() or not p.is_file():
        return []
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def locked_eval_readiness(
    *,
    path: Optional[str] = None,
    min_per_grade: Optional[int] = None,
) -> dict:
    """locked_gold_eval readiness 요약 — {ready, per_grade, missing, reason, min_per_grade,
    require_locked_eval, deploy_locked_gate_passed}.

    path/min_per_grade 미지정 시 settings(locked_eval_jsonl / deploy_gate_min_locked_per_grade).
    deploy_locked_gate_passed = ready 이거나 require_locked_eval=False(게이트 미요구) — 자동활성이
    locked-eval 축에서 허용되는지(다른 게이트와 독립). 순수/파일 — 단위테스트 가능.
    """
    from lloydk.config import settings  # noqa: PLC0415
    from lloydk.golden_tiers import eval_readiness  # noqa: PLC0415

    p = path if path is not None else getattr(settings, "locked_eval_jsonl", "")
    mpg = int(
        min_per_grade
        if min_per_grade is not None
        else getattr(settings, "deploy_gate_min_locked_per_grade", 5)
    )
    require = bool(getattr(settings, "deploy_gate_require_locked_eval", True))

    records = _load_locked_records(p)
    summary = eval_readiness(records, min_per_grade=mpg)
    summary["min_per_grade"] = mpg
    summary["require_locked_eval"] = require
    summary["deploy_locked_gate_passed"] = bool(summary["ready"] or not require)
    return summary
