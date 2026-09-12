"""합성으로 채울 자리 — 등급 × 도메인 격자.

왜 이것이 필요한가.
  합성을 **양**으로 늘리는 것은 실측으로 막혀 있다: 합성-only 로 학습해 실문서를 재면
  F1 0.26(실데이터 학습 0.736). 아무리 많이 만들어도 실문서 성능이 그만큼 오르지 않는다.
  그래서 합성의 쓸모를 **"빈 칸 채우기"**로 좁힌다 — 실데이터로 구조적으로 얻을 수 없는
  등급×도메인 조합만 지목해 만든다.

  실제로 지금 얇은 칸은 전부 고등급·산업 도메인이다(TS ai 1건 · S1 배터리 1건 …).
  반출 제약 때문에 앞으로도 실문서로는 안 채워지는 자리다.

왜 src 로 옮겼는가(2026-09-06).
  계산은 scripts/synth_coverage_gaps.py 안에만 있었다. 그래서 사람이 터미널에서 표를 읽고
  → 조합을 외우고 → 콘솔 폼에 손으로 다시 입력해야 했다. 필요한 정보가 이미 다 있는데
  화면이 그것을 모르는 상태였다. 계산을 여기로 올려 API·스크립트가 **같은 것을 쓴다.**

⚠ 이 모듈은 **판단하지 않는다.** 빈 칸이라고 다 채울 것은 아니다 — 현실에 없는 조합
  (예: 공개 보도자료 도메인의 TS)을 억지로 채우면 모델에 없는 규칙을 가르친다.
  출력은 후보일 뿐이고 무엇을 만들지는 사람이 고른다. 화면도 그렇게 보여야 한다.
"""

from __future__ import annotations

import collections
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

logger = logging.getLogger(__name__)

GRADES: tuple[str, ...] = ("TS", "S1", "S2", "S3")
# 이 수 미만이면 '얇은 칸'. 학습 한 칸에 최소 몇 건은 있어야 그 조합을 배운다고 보는 값이다.
DEFAULT_MIN_PER_CELL = 12
# 화면·터미널이 함께 띄우는 경고. 이 격자는 후보이지 판단이 아니다 — 한 곳에만 둔다.
CAVEAT = (
    "빈 칸이라고 다 채울 것은 아니다. 현실에 없는 조합(예: 공개 보도자료 도메인의 TS)을 "
    "억지로 채우면 모델에 없는 규칙을 가르친다. 실문서가 이미 있는 칸은 합성이 급하지 않다."
)
_SPLITS = ("train", "val", "test")


def row_domain(row: dict) -> str:
    """도메인을 **정본으로 접어** 센다.

    같은 산업이 영문·한글 두 칸으로 갈리면 빈 칸·얇은 칸이 부풀려진다 — 실측:
    semiconductor 1 vs 반도체 159 · pharma 2 vs 화학_제약 102 · battery 2 vs 배터리 29.
    TS battery 2 + 배터리 12 = 14 라 합치면 얇지 않은데 둘 다 '얇은 칸'으로 보고됐다.
    """
    raw = row.get("domain") or row.get("doc_type") or "(미상)"
    try:
        from koipa.modules.m1_synthesis.generator import canonical_domain  # noqa: PLC0415

        return canonical_domain(raw)
    except Exception:  # noqa: BLE001 — 생성기를 못 읽어도 격자는 내야 한다
        logger.warning("도메인 정본 변환 실패 — 원본 이름으로 센다: %r", raw)
        return str(raw)


def load_training_rows(root: Path | str) -> list[dict]:
    """학습셋 디렉터리의 train/val/test.jsonl 을 읽는다. 없는 split 은 건너뛴다."""
    base = Path(root)
    rows: list[dict] = []
    for name in _SPLITS:
        path = base / f"{name}.jsonl"
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def analyse(
    rows: Iterable[dict], *, min_per_cell: int = DEFAULT_MIN_PER_CELL
) -> dict[str, Any]:
    """등급 × 도메인 격자와 빈 칸·얇은 칸.

    각 칸에 **실문서 유래 건수(real)**를 함께 낸다. 빈 칸이라도 실문서가 있으면 합성이
    급하지 않다 — 화면이 그 구분을 보여줘야 사람이 고를 수 있다.
    """
    grid: collections.Counter = collections.Counter()
    real: collections.Counter = collections.Counter()
    for row in rows:
        label = row.get("label")
        if label not in GRADES:
            continue
        cell = (label, row_domain(row))
        grid[cell] += 1
        if (row.get("source") or "") != "synthetic":
            real[cell] += 1

    domains = sorted({d for _g, d in grid})
    empty: list[dict] = []
    thin: list[dict] = []
    for grade in GRADES:
        for domain in domains:
            n = grid[(grade, domain)]
            item = {"grade": grade, "domain": domain, "n": n, "real": real[(grade, domain)]}
            if n == 0:
                empty.append(item)
            elif n < min_per_cell:
                thin.append(item)
    thin.sort(key=lambda x: x["n"])
    total = sum(grid.values())
    return {
        "documents": total,
        "grades": {g: sum(v for (gg, _d), v in grid.items() if gg == g) for g in GRADES},
        "domains": domains,
        "cells_total": len(GRADES) * len(domains),
        "cells_filled": len(grid),
        "empty": empty,
        "thin": thin,
        "min_per_cell": min_per_cell,
        "grid": {f"{g}|{d}": grid[(g, d)] for g in GRADES for d in domains},
        "real_share": round(sum(real.values()) / total, 3) if total else 0.0,
    }


def coverage_report(
    *, dataset_dir: str | None = None, min_per_cell: int = DEFAULT_MIN_PER_CELL
) -> dict[str, Any]:
    """설정된 학습셋으로 격자를 낸다. **읽기 전용**이고 아무것도 고치지 않는다.

    학습셋을 못 읽으면 500 을 내지 않고 ``available=False`` 와 사유를 돌려준다 —
    격자는 참고 정보이고, 이것 때문에 생성 화면 전체가 죽으면 안 된다.
    """
    from koipa.config import settings  # noqa: PLC0415

    root = dataset_dir or getattr(settings, "training_dataset_dir", "") or ""
    if not root:
        return {"available": False, "reason": "training_dataset_dir 이 설정되지 않았다"}
    try:
        rows = load_training_rows(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("커버리지 격자: 학습셋을 읽지 못했다 (%s)", exc)
        return {"available": False, "reason": f"학습셋을 읽지 못했다: {exc}",
                "dataset_dir": str(root)}
    if not rows:
        return {"available": False, "reason": "학습셋에 행이 없다", "dataset_dir": str(root)}
    out = analyse(rows, min_per_cell=min_per_cell)
    out["available"] = True
    out["dataset_dir"] = str(root)
    # 화면이 그대로 띄울 경고. 판단은 사람이 한다는 것을 서버가 말해 준다.
    out["caveat"] = CAVEAT
    return out


def render_text(out: dict, *, top_thin: int = 12) -> list[str]:
    """터미널용 표. 스크립트와 화면이 같은 계산을 쓰도록 렌더링만 여기 둔다."""
    if not out.get("available", True):
        return [f"격자를 낼 수 없다 — {out.get('reason')}"]
    lines = ["=" * 74, " 합성으로 채울 자리 — 등급 × 도메인 격자", "=" * 74]
    lines.append(
        f"  문서 {out['documents']:,}건 · 도메인 {len(out['domains'])}종 · "
        f"칸 {out['cells_filled']}/{out['cells_total']} 채워짐"
    )
    lines.append(f"  실문서 유래 비중 {out['real_share']:.1%}  (나머지는 합성)")
    lines.append(f"  등급 분포 {out['grades']}")
    lines.append("")
    width = max((len(d) for d in out["domains"]), default=8) + 2
    lines.append("  " + " " * width + "".join(f"{g:>8}" for g in GRADES))
    for domain in out["domains"]:
        line = f"  {domain:<{width}}"
        for grade in GRADES:
            n = out["grid"][f"{grade}|{domain}"]
            line += f"{('  .' if n == 0 else str(n)):>8}"
        lines.append(line)
    lines.append("")
    lines.append(
        f"  빈 칸 {len(out['empty'])}개 · 얇은 칸(<{out['min_per_cell']}) {len(out['thin'])}개"
    )
    if out["thin"]:
        lines.append("")
        lines.append("  가장 얇은 칸 (합성 후보 — 현실에 있는 조합인지 사람이 고를 것)")
        for item in out["thin"][:top_thin]:
            lines.append(
                f"    {item['grade']:<3} {item['domain']:<16} {item['n']:>3}건 "
                f"(실문서 {item['real']}건)"
            )
    lines.append("")
    lines.append("  ⚠ " + (out.get("caveat") or CAVEAT))
    return lines


__all__: Sequence[str] = (
    "CAVEAT",
    "GRADES",
    "DEFAULT_MIN_PER_CELL",
    "row_domain",
    "load_training_rows",
    "analyse",
    "coverage_report",
    "render_text",
)
