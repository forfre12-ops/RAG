#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""재학습 후보를 승격할지 **한 명령으로** 판정한다 — 기준을 문서가 아니라 코드에 둔다.

## 왜 (2026-09-14)

재학습이 여섯 번 실패했고 여섯 번 다 같은 방식이었다.

    v6 판례교정      미탐 16 → 83
    판례 재라벨       미탐 0.179 → 0.801   (과분류는 0.850 → 0.000)
    v7 계열          "교환일 뿐"
    v8 요소모델       자동확정이 전부 최고등급 → 승격 불가
    LLM 코퍼스        미탐 0.2% 인데 사람라벨셋 정확도 0.31~0.33
    창 단위 학습      세 면 전부 하락 · 공개문서 과탐 9~13 → 19~37

공통점은 **판정 기준이 없었다**는 것이다. 한 축만 보면 늘 좋아 보인다 —
과탐을 줄이면 미탐이 늘고, 미탐을 줄이면 과탐이 는다. 그 교환을 한 화면에서
보지 않으면 매번 "좋아졌다" 고 착각한다.

## 이 도구가 정하는 것

    1) 어느 면에서 재는가        전체 학습 명세·정답 권위·입력 결합 검사 통과 면만
    2) 무엇을 재는가             미탐 3축 + 과탐 4축 (합치지 않는다)
    3) 무엇을 주장할 수 있는가    eval_authority 게이트가 판정한다
    4) 승격 가능한가             아래 규칙

승격 규칙 — **한 축이라도 나빠지면 자동 승격은 없다.**

    REJECT     어느 면에서든 미탐이 늘었다 (계약 핵심목표가 미탐 최소화다)
    REJECT     어느 면에서든 고등급 격상(severe overclass)이 늘었다
    HOLD       개선은 있으나 다른 축이 나빠졌다 — 교환이므로 사람이 정한다
    HOLD       판정면의 claim_status 가 BLOCKED 다 (정답 권위 없음)
    REGRESSION_OK  필수 면·지표·측정 조건이 완비되고 상대 회귀가 관측되지 않음

⚠ v2는 PROMOTE를 출력하지 않는다. REGRESSION_OK도 고객사 성능 합격이나 배포 허가가 아니다.
   이전 스냅샷은 누락된 메타데이터를 추정해 채우지 않고 재측정해야 한다.

사용:
    # 기준선 스냅샷 (현행 배포본)
    ... judge_model_candidate.py --snapshot reports/MODEL_BASELINE.json \\
        --records <면>=<records.jsonl> --eval <면>=<eval.jsonl> ...

    # 후보 판정
    ... judge_model_candidate.py --baseline reports/MODEL_BASELINE.json \\
        --records <면>=<후보 records.jsonl> --eval <면>=<eval.jsonl> ...
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
import sys
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))
sys.path.insert(0, str(POC / "scripts"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from koipa.eval_authority import assess_comparison, load_suite_exclusions  # noqa: E402
from measure_four_metrics import METRICS_SCHEMA_VERSION, compute, suite_context  # noqa: E402
from evaluation_inputs import check_training, read_rows, record_binding, sha256  # noqa: E402
from koipa.golden_tiers import document_origin  # noqa: E402

INPUT_CONTRACT_VERSION = "classification-comparison-v2"

#: 나빠지면 즉시 REJECT 인 축 — 계약 핵심목표(미탐 최소화)와 감리 185(가)(공개문서 격상)
HARD_AXES = (
    ("s2_underclass", "S2→S3 과소분류"),
    ("high_grade_auto_confirm_fn_rate", "고등급 자동확정 미탐"),
    ("serving_recall", "서빙 미탐"),
    ("severe_overclass", "고등급 격상"),
)
#: 나빠지면 HOLD 인 축 — 교환일 수 있으므로 사람이 본다
SOFT_AXES = (
    ("exact_grade_error", "4등급 오분류"),
    *((f"grade_error_{g}", f"{g} 등급 오분류") for g in ("TS", "S1", "S2", "S3")),
    ("model_overclass", "모델 과탐"),
    ("serving_overclass", "서빙 과탐"),
    ("model_recall", "모델 미탐"),
    ("review_load", "검수 업무량"),
)
#: 비율 비교에서 이 이하 차이는 같다고 본다(부동소수 잡음)
EPS = 1e-9


def _pair(arg: str) -> tuple[str, str]:
    if "=" not in arg:
        raise argparse.ArgumentTypeError(f"'면=경로' 형식이어야 한다: {arg}")
    k, v = arg.split("=", 1)
    return k.strip(), v.strip()


def measure(face: str, rec_rel: str, eval_rel: str, exclusions: dict, *,
            training_manifest: Path | None = None, context: dict | None = None) -> dict:
    try:
        rows = read_rows(POC / rec_rel)
        evaluation = read_rows(POC / eval_rel)
        problems = record_binding(rows, evaluation)
        training = check_training(POC, training_manifest, evaluation)
        m = compute(rows)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"face": face, "error": f"입력 검증 실패: {exc}"}
    excl = exclusions.get(eval_rel.replace("\\", "/"), {})
    excl_reason = (str(excl.get("reason") or "")
                   if "scoring" in (excl.get("excluded_from") or []) else "")
    tier, _, tier_ct = suite_context(POC / eval_rel, train_paths=())
    overlap = training["total_overlap"]
    authority = assess_comparison(truth_tier=tier.value, overlap_checked=training["checked"],
                                  overlap_count=overlap, excluded_reason=excl_reason)
    problems.extend(training["reasons"])
    problems.extend(authority["reasons"])
    context = context or {}
    record_hash, eval_hash = sha256(POC / rec_rel), sha256(POC / eval_rel)
    if context.get("records_sha256") != record_hash or context.get("eval_sha256") != eval_hash:
        problems.append("측정 시점 기록·평가셋 해시 미확인")
    if not training["model_id"] or context.get("model_id") != training["model_id"]:
        problems.append("측정 모델과 학습 명세 모델 불일치 또는 미확인")
    for key in ("org_id", "policy_version", "measurement_config_sha256"):
        if not context.get(key):
            problems.append(f"측정 조건 누락: {key}")
    if m["model_grade_missing"] or m["status_missing"] or m["status_unrecognized"]:
        problems.append("모델 원시 등급 또는 라우팅 상태 미기록")
    usable = not problems
    return {
        "input_contract_version": INPUT_CONTRACT_VERSION,
        "face": face, "records": rec_rel, "eval_set": eval_rel,
        "eval_sha256": eval_hash, "records_sha256": record_hash,
        "model_id": context.get("model_id"), "org_id": context.get("org_id"),
        "policy_version": context.get("policy_version"),
        "measurement_config_sha256": context.get("measurement_config_sha256"),
        "training_manifest_sha256": training["manifest_sha256"],
        "training_overlap_checked": training["checked"],
        "family_overlap": training["family_overlap"],
        "comparison_status": "DIAGNOSTIC_ONLY" if usable else "BLOCKED",
        "claim_status": "NOT_ASSESSED", "validation_reasons": problems,
        "truth_tier": tier.value, "truth_tier_counts": dict(tier_ct),
        "document_origins": dict(Counter(document_origin(r) for r in evaluation)),
        "evaluation_scopes": dict(Counter(str(r.get("evaluation_scope") or "unrecorded") for r in evaluation)),
        "training_overlap": overlap,
        "excluded_reason": excl_reason,
        "usable_for_judgement": usable,
        "metrics": m,
    }


def _rate(m: dict, key: str) -> float | None:
    d = m.get(key)
    if not isinstance(d, dict):
        return None
    return d.get("rate")


def judge(baseline: dict, current: list[dict]) -> dict:
    base_by_face = {f["face"]: f for f in baseline.get("faces", []) if "metrics" in f}
    verdict = "REGRESSION_OK"
    reasons: list[str] = []
    deltas: list[dict] = []
    judged_faces = 0

    def hold(reason):
        nonlocal verdict
        reasons.append(reason)
        if verdict != "REJECT":
            verdict = "HOLD"

    current_names = [f.get("face") for f in current]
    baseline_names = [f.get("face") for f in baseline.get("faces", [])]
    if len(set(current_names)) != len(current_names) or len(set(baseline_names)) != len(baseline_names):
        hold("평가면 이름 중복")
    for missing in set(baseline_names) - set(current_names):
        hold(f"필수 평가면 누락: {missing}")

    for cur in current:
        face = cur["face"]
        if "metrics" not in cur:
            hold(f"[{face}] {cur.get('error')}")
            continue
        if not cur.get("usable_for_judgement"):
            why = cur.get("excluded_reason") or (
                f"학습 겹침 {cur.get('training_overlap', 0)}건" if cur.get("training_overlap_checked")
                else "전체 학습 중복 미검사")
            hold(f"[{face}] 판정면으로 못 씀 — {why[:60]} " + "; ".join(cur.get("validation_reasons", [])))
            continue
        base = base_by_face.get(face)
        if not base:
            hold(f"[{face}] 기준선에 없는 면 — 비교 불가")
            continue
        invalid = False
        for side, item in (("기준선", base), ("후보", cur)):
            auth = assess_comparison(truth_tier=item.get("truth_tier", "NONE"),
                overlap_checked=item.get("training_overlap_checked") is True,
                overlap_count=item.get("training_overlap", 0),
                excluded_reason=item.get("excluded_reason", ""))
            if (auth["status"] == "BLOCKED" or not item.get("usable_for_judgement") or
                    item.get("validation_reasons") or item.get("family_overlap", 0) or
                    item.get("input_contract_version") != INPUT_CONTRACT_VERSION or
                    item["metrics"].get("schema_version") != METRICS_SCHEMA_VERSION or
                    item.get("comparison_status") != "DIAGNOSTIC_ONLY"):
                hold(f"[{face}] {side} 자격·스키마 미완비 — 다시 측정해야 함")
                invalid = True
            for key in ("eval_sha256", "records_sha256", "measurement_config_sha256", "training_manifest_sha256"):
                if not re.fullmatch(r"[0-9a-f]{64}", str(item.get(key, ""))):
                    hold(f"[{face}] {side} 지문 누락: {key}")
                    invalid = True
            for key in ("model_id", "org_id", "policy_version"):
                if not item.get(key):
                    hold(f"[{face}] {side} 조건 누락: {key}")
                    invalid = True
        for key in ("eval_sha256", "org_id", "policy_version", "measurement_config_sha256"):
            if base.get(key) != cur.get(key):
                hold(f"[{face}] 비교 조건 불일치: {key}")
                invalid = True
        if invalid:
            continue
        judged_faces += 1
        for key, label in HARD_AXES + SOFT_AXES:
            bd, cd = base["metrics"].get(key), cur["metrics"].get(key)
            if not isinstance(bd, dict) or not isinstance(cd, dict) or "n" not in bd or "n" not in cd:
                hold(f"[{face}] 필수 지표 누락: {key}")
                continue
            b, c = _rate(base["metrics"], key), _rate(cur["metrics"], key)
            if bd["n"] == cd["n"] == 0 and b is None and c is None:
                continue
            if (bd["n"] != cd["n"] or not isinstance(bd["n"], int) or bd["n"] <= 0 or
                    any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1
                        for v in (b, c))):
                hold(f"[{face}] 지표 분모·비율 불일치 또는 미기록: {key}")
                continue
            if abs(c - b) <= EPS:
                continue
            row = {"face": face, "axis": key, "label": label,
                   "baseline": b, "candidate": c, "delta": c - b}
            deltas.append(row)
            if c > b:  # 비율이 커졌다 = 나빠졌다 (미탐·과탐·검수 모두 낮을수록 좋다)
                hard = any(key == k for k, _ in HARD_AXES)
                row["worse"] = True
                if hard:
                    verdict = "REJECT"
                    reasons.append(
                        f"[{face}] {label}이 {b * 100:.2f}% → {c * 100:.2f}% 로 나빠졌다 — 즉시 기각"
                    )
                elif verdict != "REJECT":
                    verdict = "HOLD"
                    reasons.append(
                        f"[{face}] {label}이 {b * 100:.2f}% → {c * 100:.2f}% 로 나빠졌다 — 교환 판단 필요"
                    )

    if judged_faces == 0:
        verdict = "HOLD"
        reasons.append("판정 가능한 면이 하나도 없다 — 정답 권위·학습 겹침을 먼저 고칠 것")
    return {"verdict": verdict, "claim_status": "NOT_ASSESSED",
            "scope": "relative_regression_only", "reasons": reasons, "deltas": deltas,
            "judged_faces": judged_faces}


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", action="append", type=_pair, required=True,
                    help="면=records.jsonl (여러 번)")
    ap.add_argument("--eval", action="append", type=_pair, required=True,
                    help="면=eval.jsonl (여러 번)")
    ap.add_argument("--snapshot", default=None, help="기준선으로 저장할 경로")
    ap.add_argument("--baseline", default=None, help="이 기준선과 비교")
    ap.add_argument("--json", default=None)
    ap.add_argument("--training-manifest", help="전체 학습 파일·해시·모델 ID 명세")
    ap.add_argument("--context", action="append", type=_pair, default=[],
                    help="면=측정조건.json (org_id/policy_version/model_id/설정·입력 해시)")
    args = ap.parse_args()

    if len(dict(args.records)) != len(args.records) or len(dict(args.eval)) != len(args.eval):
        ap.error("Duplicate face name")
    recs = dict(args.records)
    evals = dict(args.eval)
    missing = set(recs) ^ set(evals)
    if missing:
        print(f"면 이름이 안 맞는다: {sorted(missing)}")
        return 2

    exclusions = load_suite_exclusions(POC)
    contexts = {name: json.loads((POC / path).read_text(encoding="utf-8")) for name, path in args.context}
    train_path = POC / args.training_manifest if args.training_manifest else None
    faces = [measure(f, recs[f], evals[f], exclusions, training_manifest=train_path,
                     context=contexts.get(f)) for f in sorted(recs)]

    print("=" * 86)
    print("면별 상태")
    print("=" * 86)
    print(f"{'면':<18} {'정답등급':<8} {'겹침':>5} {'판정가능':>8}  "
          f"{'미탐(자동)':>10} {'격상':>8} {'검수율':>8}")
    print("-" * 86)
    for f in faces:
        if "metrics" not in f:
            print(f"{f['face']:<18} {f.get('error')}")
            continue
        m = f["metrics"]

        def p(k):
            r = _rate(m, k)
            return "   -  " if r is None else f"{r * 100:5.1f}%"
        overlap_display = str(f["training_overlap"]) if f.get("training_overlap_checked") else "미검사"
        print(f"{f['face']:<18} {f['truth_tier']:<8} {overlap_display:>5} "
              f"{'예' if f['usable_for_judgement'] else '아니오':>8}  "
              f"{p('high_grade_auto_confirm_fn_rate'):>10} {p('severe_overclass'):>8} "
              f"{p('review_load'):>8}")
        if f["excluded_reason"]:
            print(f"{'':>18} ⛔ {f['excluded_reason'][:64]}")

    out = {"faces": faces}

    if args.snapshot:
        p = POC / args.snapshot
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("x", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
        print(f"\n기준선 저장: {p.relative_to(POC)}")
        usable = [f for f in faces if f.get("usable_for_judgement")]
        print(f"  판정에 쓸 수 있는 면 {len(usable)}/{len(faces)}개")
        if not usable:
            print("  ⚠ 판정 가능한 면이 0개다 — 이 기준선으로는 어떤 후보도 판정할 수 없다")
        return 0

    if args.baseline:
        base = json.loads((POC / args.baseline).read_text(encoding="utf-8"))
        res = judge(base, faces)
        out["judgement"] = res
        print()
        print("=" * 86)
        print(f"판정: {res['verdict']}   (판정한 면 {res['judged_faces']}개)")
        print("=" * 86)
        for r in res["reasons"]:
            print(f"  · {r}")
        if res["deltas"]:
            print()
            print("  축별 변화 (비율은 낮을수록 좋다)")
            for d in sorted(res["deltas"], key=lambda x: -abs(x["delta"]))[:12]:
                mark = "나빠짐" if d.get("worse") else "나아짐"
                print(f"    [{d['face']}] {d['label']:<14} "
                      f"{d['baseline'] * 100:6.2f}% → {d['candidate'] * 100:6.2f}%  {mark}")
        print()
        print("  ⚠ REGRESSION_OK 는 상대 회귀 없음이며 고객사 성능 합격·배포 허가가 아니다.")
        print("    배포는 지시가 있을 때만 한다.")

    if args.json:
        q = POC / args.json
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {q.relative_to(POC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
