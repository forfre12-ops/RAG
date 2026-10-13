#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사전 품질시험 보강 — 새 모의문서 1,000건 5분할 교차검증의 평가 문서를 **서빙 경로 전체**(ClassifyService)로 태운다.

왜: run_mock1000_cv.py 는 InferencePipeline.run(모델+τ) 까지만 탔다. 검수 전환(needs_review)·무음 미탐·검수 사유는
    classify_service 의 게이트가 정하므로 별도로 재야 한다. 무음 미탐 = 정답 고등급(TS·S1) ∧ 예측이 더 낮음 ∧ status != needs_review
    (scripts/measure_serving_fnr.py 와 같은 정의). 분할마다 그 분할의 모델(fold{k}/model[_s{seed}])을 CLASSIFIER_MODEL_DIR 로 지정한다.
프로필: DEPLOY_PROFILE=onprem-local (배포 컨테이너 koipa-cust-api-1 의 값). 설정 스냅샷을 결과에 함께 남긴다 — 프로필이 안 맞으면 표가 그럴듯해도 무효다.
       (local-run-must-match-deploy-profile-flags 메모리 — τ·온도·검수 임계·메타데이터 하한을 출력해 확인)
사용:  python scripts/run_mock1000_serving.py fold 0 [--seed 42]     (분할마다 별도 프로세스 — 설정이 임포트 시점에 정해진다)
       python scripts/run_mock1000_serving.py aggregate
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # cp949 콘솔에서 '—' 출력이 UnicodeEncodeError 로 죽던 것 방지(결과에는 영향 없었음)
except Exception:  # noqa: BLE001
    pass

POC = Path(__file__).resolve().parents[1]
WORK = POC / "reports" / "mock1000_cv_20260921"
G = ["TS", "S1", "S2", "S3"]
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
K = 5


def _load(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def fold(k: int, seed: int, tag: str = "") -> int:
    sfx = "" if seed == 42 else f"_s{seed}"
    d = WORK / f"fold{k}"
    mdir = sorted((d / f"model{sfx}").glob("v-*"))[-1]
    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("DEPLOY_PROFILE", "onprem-local")
    os.environ.setdefault("LLM_PROVIDER", "noop")             # 배포 컨테이너(koipa-cust-api-1)와 같게 — LLM 2차의견 경로 차단
    os.environ["CLASSIFIER_MODEL_DIR"] = str(mdir)
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415
    from koipa.services.review_reasons import causal_review_reason  # noqa: PLC0415

    snap = {n: str(getattr(settings, n, "?")) for n in (
        "deploy_profile", "classifier_escalation_tau", "classifier_temperature", "review_confidence_threshold", "metadata_floor_enabled",
        "agreement_gate_enabled", "no_auto_confirm_grades", "llm_provider", "llm_second_opinion_enabled", "classifier_model_dir")}
    print("설정 스냅샷", json.dumps(snap, ensure_ascii=False), flush=True)
    svc = ClassifyService()
    rows, t0 = [], time.perf_counter()
    for i, r in enumerate(_load(d / "test.jsonl")):
        try:
            res = svc.classify(ClassifyRequest(doc_id=f"mockcv-{r['doc_id']}", content=r["text"], return_evidence=False))
            pred = res.label.value if hasattr(res.label, "value") else str(res.label)
            status = str(res.status)
            warnings = list(getattr(res, "warnings", None) or [])
            conf = float(getattr(res, "confidence", 0.0) or 0.0)
        except Exception as exc:  # noqa: BLE001 — 실패도 흔적으로 남긴다(조용히 버리지 않는다)
            pred, status, warnings, conf = None, f"error:{type(exc).__name__}", [str(exc)[:200]], 0.0
        rows.append({"doc_id": r["doc_id"], "truth": r["label"], "pred": pred, "status": status, "conf": conf, "warnings": warnings,
                     "reason": causal_review_reason(warnings, status), "source": r["source_name"]})
        if (i + 1) % 50 == 0:
            print(f"  fold{k} {i + 1}/200 · {time.perf_counter() - t0:.0f}s", flush=True)
    (d / f"serving{sfx}{tag}.json").write_text(json.dumps({"settings": snap, "model_dir": str(mdir), "rows": rows}, ensure_ascii=False), encoding="utf-8")
    print(f"fold{k} 시드 {seed} 서빙 경로 완료: {len(rows)}건 · 오류 {sum(1 for x in rows if x['pred'] is None)}건 · {time.perf_counter() - t0:.0f}s", flush=True)
    return 0


def prf(rows: list[dict]) -> dict:
    conf = {a: {b: 0 for b in G} for a in G}
    for r in rows:
        conf[r["truth"]][r["pred"]] += 1
    per = {}
    for g in G:
        tp = conf[g][g]
        n = sum(conf[g].values())
        pn = sum(conf[a][g] for a in G)
        p = tp / pn if pn else 0.0
        rc = tp / n if n else 0.0
        per[g] = {"n": n, "P": p, "R": rc, "F1": 2 * p * rc / (p + rc) if p + rc else 0.0}
    return {"per": per, "macroP": sum(v["P"] for v in per.values()) / 4, "macroR": sum(v["R"] for v in per.values()) / 4,
            "macroF1": sum(v["F1"] for v in per.values()) / 4, "acc": sum(conf[g][g] for g in G) / len(rows), "conf": conf}


def aggregate(seed: int) -> int:
    sfx = "" if seed == 42 else f"_s{seed}"
    rows, snaps = [], []
    for k in range(K):
        f = WORK / f"fold{k}" / f"serving{sfx}.json"
        if not f.exists():
            print(f"{f.name} 없음 — 끝나지 않았다")
            return 1
        o = json.loads(f.read_text(encoding="utf-8"))
        rows += o["rows"]
        snaps.append(o["settings"])
    L = [f"서빙 경로 전체(ClassifyService) · 시드 {seed} · 평가 {len(rows)}건 · 설정 스냅샷(분할 0): {json.dumps(snaps[0], ensure_ascii=False)}"]
    L.append(f"  분할별 설정이 같은가: {'예' if all(s == {**snaps[0], 'classifier_model_dir': s['classifier_model_dir']} for s in snaps) else '아니오(확인 필요)'}")
    err = [r for r in rows if r["pred"] is None]
    L.append(f"  분류 오류 {len(err)}건" + (f" · {Counter(r['status'] for r in err)}" if err else ""))
    ok = [r for r in rows if r["pred"] in RANK]
    m = prf(ok)
    L += ["", "[최종 등급(게이트 통과 후) 기준 Precision/Recall/F1]", "  등급  건수  Precision  Recall   F1"]
    for g in G:
        p = m["per"][g]
        L.append(f"  {g:<3} {p['n']:>5}   {p['P']:>7.1%}  {p['R']:>7.1%} {p['F1']:>7.1%}")
    L.append(f"  macro  {m['macroP']:.1%}/{m['macroR']:.1%}/{m['macroF1']:.1%} · 정확도 {m['acc']:.1%}")
    hi = [r for r in ok if r["truth"] in ("TS", "S1")]
    under = [r for r in hi if RANK[r["pred"]] < RANK[r["truth"]]]
    silent = [r for r in under if r["status"] != "needs_review"]
    caught = [r for r in under if r["status"] == "needs_review"]
    L += ["", "[고등급 미탐 · 무음 미탐]", f"  고등급 정답 {len(hi)}건 중 정답보다 낮게 판정 {len(under)}건 = {len(under) / len(hi):.1%}",
          f"  그중 검수 전환으로 잡힌 것 {len(caught)}건 · **무음 미탐(자동확정까지 간 것) {len(silent)}건 = {len(silent) / len(hi):.1%}**"]
    if silent:
        L.append("  무음 미탐 전이: " + str(dict(Counter(f"{r['truth']}->{r['pred']}" for r in silent))))
    nr = [r for r in ok if r["status"] == "needs_review"]
    L += ["", "[검수 전환(needs_review)]", f"  {len(nr)}/{len(ok)} = {len(nr) / len(ok):.1%} · 상태 분포 {dict(Counter(r['status'] for r in ok))}",
          f"  사유(문서당 처음 성립한 게이트 1개): {dict(Counter(r['reason'] for r in nr).most_common())}"]
    L.append("  등급(최종 예측)별 검수 전환: " + " · ".join(
        f"{g} {sum(1 for r in nr if r['pred'] == g)}/{sum(1 for r in ok if r['pred'] == g)}" for g in G))
    L.append("  정답 등급별 검수 전환: " + " · ".join(
        f"{g} {sum(1 for r in nr if r['truth'] == g)}/{sum(1 for r in ok if r['truth'] == g)}" for g in G))
    # 정책 — 등급별 자동확정 제외(no_auto_confirm_grades). 게이트가 순서상 뒤에서 `status != needs_review` 일 때만 발동하므로,
    # 기준 실행에서 자동확정으로 남은 문서 중 예측이 제외 등급인 것이 곧 '추가 검수 전환'이다.
    L += ["", "[정책 시나리오 · 등급별 자동확정 제외(no_auto_confirm_grades) — 기준 실행에서 유도]"]
    auto = [r for r in ok if r["status"] != "needs_review"]
    for blocked in (["S2"], ["S1", "S2"], ["TS", "S1", "S2"]):
        extra = [r for r in auto if r["pred"] in blocked]
        still = [r for r in silent if r["pred"] not in blocked]
        L.append(f"  제외 {blocked}: 추가 검수 전환 {len(extra)}건(검수 {len(nr)}→{len(nr) + len(extra)} = {(len(nr) + len(extra)) / len(ok):.1%}) · "
                 f"추가 전환 중 정답이 더 높았던 것 {sum(1 for r in extra if RANK[r['truth']] > RANK[r['pred']])}건 · 무음 미탐 {len(silent)}→{len(still)}")
    L += ["", "[출처별 · 최종 등급]"]
    for name in ("selfconsistent_v3", "v8_factor_balance_fill"):
        sub = [r for r in ok if r["source"] == name]
        sh = [r for r in sub if r["truth"] in ("TS", "S1")]
        sil = [r for r in sh if RANK[r["pred"]] < RANK[r["truth"]] and r["status"] != "needs_review"]
        L.append(f"  {name}: {len(sub)}건 · 정확도 {sum(1 for r in sub if r['pred'] == r['truth']) / len(sub):.1%} · 검수 전환 {sum(1 for r in sub if r['status'] == 'needs_review')}건 · 무음 미탐 {len(sil)}/{len(sh)}")
    text = "\n".join(L)
    (WORK / f"serving_result{sfx}_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def compare(tag: str, blocked: list[str]) -> int:
    """등급별 자동확정 제외(no_auto_confirm_grades) 실행 결과를 기준 실행과 문서 단위로 대조한다 — 유도값이 맞았는지 확인."""
    base, pol = {}, {}
    for k in range(K):
        for r in json.loads((WORK / f"fold{k}" / "serving.json").read_text(encoding="utf-8"))["rows"]:
            base[r["doc_id"]] = r
        o = json.loads((WORK / f"fold{k}" / f"serving{tag}.json").read_text(encoding="utf-8"))
        for r in o["rows"]:
            pol[r["doc_id"]] = r
        snap = o["settings"]
    derived = {d for d, r in base.items() if r["pred"] in RANK and r["status"] != "needs_review" and r["pred"] in blocked}
    actual = {d for d, r in pol.items() if r["status"] == "needs_review" and base[d]["status"] != "needs_review"}
    dropped = {d for d, r in pol.items() if r["status"] != "needs_review" and base[d]["status"] == "needs_review"}
    grade_changed = sum(1 for d in pol if pol[d]["pred"] != base[d]["pred"])
    hi = [d for d, r in base.items() if r["truth"] in ("TS", "S1") and r["pred"] in RANK]

    def silent(rows):
        return sum(1 for d in hi if RANK[rows[d]["pred"]] < RANK[rows[d]["truth"]] and rows[d]["status"] != "needs_review")

    nb = sum(1 for r in base.values() if r["status"] == "needs_review")
    npol = sum(1 for r in pol.values() if r["status"] == "needs_review")
    L = [f"등급별 자동확정 제외 {blocked} 실제 실행 vs 기준 실행 · 설정 스냅샷 no_auto_confirm_grades={snap.get('no_auto_confirm_grades')}",
         f"  유도값(기준 실행에서 자동확정이고 예측이 제외 등급) {len(derived)}건 · 실제 추가 검수 전환 {len(actual)}건 · 두 집합 일치 {derived == actual} (유도만 {len(derived - actual)} · 실제만 {len(actual - derived)})",
         f"  검수→자동확정으로 바뀐 것 {len(dropped)}건 · 예측 등급이 바뀐 것 {grade_changed}건(0 이어야 함)",
         f"  검수 전환 {nb}/1000 = {nb / 10:.1f}% → {npol}/1000 = {npol / 10:.1f}% · 무음 미탐 {silent(base)}/{len(hi)} → {silent(pol)}/{len(hi)}"]
    text = "\n".join(L)
    (WORK / f"policy_check{tag}_20260921.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fold", "aggregate", "compare"])
    ap.add_argument("k", nargs="?", type=int)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--tag", default="")
    ap.add_argument("--blocked", default="")
    a = ap.parse_args()
    if a.cmd == "compare":
        return compare(a.tag, [g for g in a.blocked.split(",") if g])
    return fold(a.k, a.seed, a.tag) if a.cmd == "fold" else aggregate(a.seed)


if __name__ == "__main__":
    sys.exit(main())
