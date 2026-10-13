"""서빙 레코드 두 벌을 **문서 단위로** 대조한다 — "미탐이 늘었나"에 답하는 도구.

왜 필요한가(2026-09-10). "우리 미탐이 늘었어?"라는 질문에 답하려고 measure_serving_records.py
를 전·후 커밋에서 돌렸는데, 그 스크립트는 한 벌의 집계만 낸다. 집계 수치(무음 미탐 1건 → 1건)
가 같아도 **서로 다른 문서가 하나 생기고 하나 사라졌을 수 있다** — 그러면 미탐이 늘어난 것을
놓친다. 그래서 두 레코드 파일을 doc_id 로 맞춰 새로 생긴 것과 사라진 것을 따로 센다.
처음에는 세션 스크래치패드에서 짰다. 다음에 같은 질문이 오면 다시 짜지 말고 이것을 돌린다.

세는 것:
    무음 미탐  = 정답 고등급(TS·S1) ∧ 예측 더 낮음 ∧ status ≠ needs_review   (운영상 놓친 것)
    저분류     = 정답 고등급(TS·S1) ∧ 예측 더 낮음                          (검수로 간 것 포함)
    바뀐 문서  = 최종등급 · 상태 · 모델 단독 등급 중 하나라도 달라진 문서

⚠ 두 레코드는 **같은 평가셋 · 같은 모델 · 같은 프로파일**로 떠야 비교가 성립한다.
  각 레코드 옆의 리포트(.json)에 eval_sha256 · model_versions_seen · settings_effective 가
  있으면 대조하고, 다르면 멈춘다.

사용:
    python scripts/compare_serving_records.py \\
        reports/serving_records_h42_current.records.jsonl \\
        reports/serving_records_h42_after.records.jsonl

종료코드: 0 = 새 무음 미탐 없음 · 1 = 새 무음 미탐 있음 · 2 = 비교 불가
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:  # 콘솔 코드페이지가 cp949 여도 한국어 출력이 깨지지 않게.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}      # TS=0 이 가장 높다
HIGH = ("TS", "S1")
# 리포트끼리 같아야 비교가 성립하는 필드. 다르면 델타는 코드 변화가 아니라 조건 차이다.
PARITY_FIELDS = ("eval_sha256", "model_versions_seen", "settings_effective")


def _load(path: Path) -> dict[tuple[str, str], dict]:
    rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
    # 평가셋에 doc_id 가 겹치는 행이 있을 수 있어 입력 순번(요청 doc_id 끝 4자리)을 함께 키로 쓴다.
    return {(r["doc_id"], r["request_doc_id"][-4:]): r for r in rows}


def _report_of(records_path: Path) -> dict | None:
    """레코드 옆의 집계 리포트. 조건이 붙은 레코드(<stem>.<조건>.records.jsonl)도 찾는다."""
    stem = records_path.name[: -len(".records.jsonl")]
    for cand in (stem, stem.rsplit(".", 1)[0]):
        p = records_path.with_name(cand + ".json")
        if p.is_file():
            return json.loads(p.read_text("utf-8"))
    return None


def is_under(r: dict) -> bool:
    return (r["truth"] in HIGH and r["predicted"] in ORDER
            and ORDER[r["predicted"]] > ORDER[r["truth"]])


def is_silent(r: dict) -> bool:
    return is_under(r) and r["status"] != "needs_review"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="서빙 레코드 전·후 문서 단위 대조")
    ap.add_argument("before", help="기준 레코드(.records.jsonl)")
    ap.add_argument("after", help="비교 레코드(.records.jsonl)")
    ap.add_argument("--allow-mismatch", action="store_true",
                    help="리포트의 평가셋·모델·설정이 달라도 진행(그 델타는 코드 변화가 아니다)")
    a = ap.parse_args(argv)

    bp, ap_ = Path(a.before), Path(a.after)
    rb, ra = _report_of(bp), _report_of(ap_)
    if rb is None or ra is None:
        print("⚠ 리포트(.json)를 못 찾아 평가셋·모델·설정 일치를 **확인하지 못했다**.")
    else:
        diff = [f for f in PARITY_FIELDS if rb.get(f) != ra.get(f)]
        print(f"기준 {str(rb.get('git_commit', '?'))[:8]} → 비교 {str(ra.get('git_commit', '?'))[:8]}"
              f" · 모델 {ra.get('model_versions_seen')} · 평가셋 {str(ra.get('eval_sha256', '?'))[:12]}")
        if diff:
            for f in diff:
                print(f"  [불일치] {f}: {rb.get(f)!r} → {ra.get(f)!r}")
            if not a.allow_mismatch:
                print("비교 불가 — 같은 조건으로 다시 뜰 것(그대로 보려면 --allow-mismatch)")
                return 2

    b, n = _load(bp), _load(ap_)
    common = set(b) & set(n)
    print(f"문서: 기준 {len(b)} · 비교 {len(n)} · 공통 {len(common)}")
    if len(common) != len(b) or len(common) != len(n):
        # 한쪽에만 있는 문서가 있으면 그 문서의 미탐은 안 세어진다 — 조용히 넘기지 않는다.
        print(f"  ⚠ 한쪽에만 있는 문서 {len(set(b) ^ set(n))}건 — 이 문서들은 대조하지 못했다")

    new_silent = 0
    for label, fn in (("무음 미탐", is_silent), ("저분류(검수 포함)", is_under)):
        sb = {k for k in common if fn(b[k])}
        sa = {k for k in common if fn(n[k])}
        if fn is is_silent:
            new_silent = len(sa - sb)
        print(f"  {label:<14} {len(sb):>4} → {len(sa):>4}   새로 생김 {len(sa - sb)} · 사라짐 {len(sb - sa)}")
        for sign, keys in (("+", sa - sb), ("-", sb - sa)):
            for k in sorted(keys):
                print(f"      {sign}{k[0]} 정답={n[k]['truth']} "
                      f"{b[k]['predicted']}/{b[k]['status']} → {n[k]['predicted']}/{n[k]['status']}")

    pred = sum(1 for k in common if b[k]["predicted"] != n[k]["predicted"])
    stat = sum(1 for k in common if b[k]["status"] != n[k]["status"])
    mg = sum(1 for k in common if b[k].get("model_grade") != n[k].get("model_grade"))
    print(f"  바뀐 문서: 최종등급 {pred} · 상태 {stat} · 모델 단독 등급 {mg}")
    trans = Counter((b[k]["status"], n[k]["status"]) for k in common if b[k]["status"] != n[k]["status"])
    for (x, y), c in trans.most_common():
        print(f"      상태 {x} → {y}: {c}")

    return 1 if new_silent else 0


if __name__ == "__main__":
    raise SystemExit(main())
