#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""서빙 경로 하니스 v2 — 모델 단독 등급 + 최종 등급 + 가려진 합의 게이트 + typed_facts metadata 주입 + benchmark.json.

v1(run_mock1000_serving.py)과 다른 점
  · 모델 단독 등급(pipeline 결과)과 최종 등급(게이트 통과 후)을 함께 기록한다 — 두 측정면을 한 실행에서 나란히 낸다.
  · **가려진 합의 게이트**: 게이트는 순차라 앞 게이트가 needs_review 로 만들면 합의 게이트는 호출되지 않는다.
    pipeline 결과를 잡아 두었다가, 다른 사유로 이미 검수가 된 문서에 합의 게이트를 직접 호출해 '앞 게이트가 가리지 않았다면 걸렸을 건수'를 센다. 서빙 코드는 고치지 않는다.
  · `--facts`: 문서별 metadata(typed_facts 에서만 만든 ICD 값 source_type·access_scope)를 ClassifyRequest.metadata 로 넣는다. manifest 의 source_doc_id 등 등급이 새는 값은 절대 쓰지 않는다.
  · 문서 집합: 기본 = 개발 분할 k 의 평가 문서. `--docs PATH`(jsonl: doc_id·text·label[·typed_facts]) 를 주면 그 문서를 5개 분할 모델 전부로 평가한다.
설정은 배포 프로필(onprem-local, LLM noop)이고 스냅샷을 결과에 남긴다. 등급 정의는 v1 과 같다(무음 미탐 = 정답 고등급 ∧ 예측이 더 낮음 ∧ status != needs_review).
사용:  python scripts/run_mock1000_serving_v2.py run --model-dir DIR --docs PATH [--facts] --out OUT.json [--limit N]
       python scripts/run_mock1000_serving_v2.py summarize OUT.json [OUT2.json ...]
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
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
RANK = {"S3": 0, "S2": 1, "S1": 2, "TS": 3}
G = ["TS", "S1", "S2", "S3"]
ICD_SOURCE = {"public", "registered_patent", "academic", "internal", "external_confidential"}
ICD_SCOPE = {"approved_only", "designated", "department", "all_employees"}


def metadata_from_typed_facts(tf: dict | None) -> dict | None:
    """typed_facts → ClassifyRequest.metadata. ICD 규약값(source_type·access_scope)만 통과시킨다. 등급·S/V/M 값은 넣지 않는다."""
    if not tf:
        return None
    md = {}
    if str(tf.get("source_type", "")).lower() in ICD_SOURCE:
        md["source_type"] = tf["source_type"]
    if str(tf.get("access_scope", "")).lower() in ICD_SCOPE:
        md["access_scope"] = tf["access_scope"]
    return md or None


def evaluate(model_dir: Path, docs: list[dict], use_facts: bool) -> tuple[list[dict], dict]:
    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")
    os.environ.setdefault("DEPLOY_PROFILE", "onprem-local")
    os.environ.setdefault("LLM_PROVIDER", "noop")
    os.environ["CLASSIFIER_MODEL_DIR"] = str(model_dir)
    sys.path.insert(0, str(POC / "src"))
    from koipa.config import settings  # noqa: PLC0415
    from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415
    from koipa.services.review_reasons import causal_review_reason  # noqa: PLC0415

    snap = {n: str(getattr(settings, n, "?")) for n in ("deploy_profile", "classifier_escalation_tau", "classifier_temperature", "review_confidence_threshold", "metadata_floor_enabled",
                                                       "agreement_gate_enabled", "no_auto_confirm_grades", "llm_provider", "classifier_model_dir")}
    svc = ClassifyService()
    captured: dict = {}
    orig_run = svc.inference.run

    def wrapped(*a, **kw):
        r = orig_run(*a, **kw)
        captured["pred"] = r
        return r

    svc.inference.run = wrapped
    rows = []
    for i, d in enumerate(docs):
        md = metadata_from_typed_facts(d.get("typed_facts")) if use_facts else None
        captured.pop("pred", None)
        try:
            res = svc.classify(ClassifyRequest(doc_id=f"v2-{d['doc_id']}", content=d["text"], return_evidence=False, metadata=md))
            final = res.label.value if hasattr(res.label, "value") else str(res.label)
            status = str(res.status)
            warnings = list(getattr(res, "warnings", None) or [])
        except Exception as exc:  # noqa: BLE001 — 실패도 흔적
            final, status, warnings = None, f"error:{type(exc).__name__}", [str(exc)[:160]]
        pred = captured.get("pred")
        model_lab = None
        if pred is not None:
            lab = getattr(pred, "label", None)
            model_lab = lab.value if hasattr(lab, "value") else (str(lab) if lab is not None else None)
        reason = causal_review_reason(warnings, status)
        hidden_agree = None
        if pred is not None and status == "needs_review" and reason != "agreement-gate":
            try:
                hidden_agree = svc._agreement_gate(pred, d["text"]) is not None      # noqa: SLF001 — 앞 게이트가 가리지 않았다면 걸렸을까
            except Exception:  # noqa: BLE001
                hidden_agree = None
        rows.append({"doc_id": d["doc_id"], "truth": d.get("label"), "model": model_lab, "final": final, "status": status, "reason": reason,
                     "hidden_agreement_gate": hidden_agree, "metadata_injected": md, "warnings": warnings})
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(docs)}", flush=True)
    return rows, snap


def summarize_rows(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["final"] in RANK and r["truth"] in RANK]
    hi = [r for r in ok if r["truth"] in ("TS", "S1")]

    def under(r, key):
        return RANK[r[key]] < RANK[r["truth"]]

    per = {}
    for g in G:
        sub = [r for r in ok if r["truth"] == g]
        if not sub:
            continue
        per[g] = {"n": len(sub), "recall_final": sum(r["final"] == g for r in sub) / len(sub), "recall_model": sum(r["model"] == g for r in sub if r["model"]) / len(sub),
                  "under_final": sum(under(r, "final") for r in sub) / len(sub), "under_model": sum(1 for r in sub if r["model"] in RANK and under(r, "model")) / len(sub)}
    silent = [r for r in hi if under(r, "final") and r["status"] != "needs_review"]
    nr = [r for r in ok if r["status"] == "needs_review"]
    reasons = Counter(r["reason"] for r in nr)
    hidden = sum(1 for r in nr if r["hidden_agreement_gate"])
    return {"n": len(ok), "accuracy_final": sum(r["final"] == r["truth"] for r in ok) / max(1, len(ok)), "accuracy_model": sum(r["model"] == r["truth"] for r in ok) / max(1, len(ok)),
            "per_grade": per, "high_n": len(hi), "high_under_model": sum(1 for r in hi if r["model"] in RANK and under(r, "model")), "high_under_final": sum(under(r, "final") for r in hi),
            "silent_miss": len(silent), "review_rate": len(nr) / max(1, len(ok)), "review_reasons": dict(reasons),
            "hidden_agreement_gate_docs": hidden, "hidden_agreement_gate_share_of_reviews": hidden / max(1, len(nr)), "errors": sum(1 for r in rows if r["final"] is None)}


def cmd_run(a) -> int:
    docs = [json.loads(x) for x in Path(a.docs).read_text(encoding="utf-8").splitlines() if x.strip()]
    if a.limit:
        docs = docs[: a.limit]
    t0 = time.perf_counter()
    rows, snap = evaluate(Path(a.model_dir), docs, a.facts)
    summ = summarize_rows(rows)
    out = {"created": "2026-09-21", "model_dir": a.model_dir, "docs": a.docs, "facts_injected": a.facts, "settings": snap, "seconds": round(time.perf_counter() - t0), "summary": summ, "rows": rows}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: summ[k] for k in ("n", "accuracy_final", "accuracy_model", "high_under_model", "high_under_final", "silent_miss", "review_rate", "hidden_agreement_gate_docs", "review_reasons", "errors")}, ensure_ascii=False))
    return 0


def cmd_summarize(a) -> int:
    for f in a.files:
        o = json.loads(Path(f).read_text(encoding="utf-8"))
        s = o["summary"]
        print(f"{Path(f).name}: n={s['n']} 정확도 모델 {s['accuracy_model']:.1%}/최종 {s['accuracy_final']:.1%} · 고등급 하향 모델 {s['high_under_model']}/최종 {s['high_under_final']}/{s['high_n']} · 무음 미탐 {s['silent_miss']} · 검수 {s['review_rate']:.1%} · 가려진 합의 게이트 {s['hidden_agreement_gate_docs']} · 사유 {s['review_reasons']}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--model-dir", required=True)
    r.add_argument("--docs", required=True)
    r.add_argument("--facts", action="store_true", help="typed_facts 에서만 만든 ICD metadata 를 요청에 넣는다")
    r.add_argument("--out", required=True)
    r.add_argument("--limit", type=int, default=0)
    s = sub.add_parser("summarize")
    s.add_argument("files", nargs="+")
    a = ap.parse_args()
    return cmd_run(a) if a.cmd == "run" else cmd_summarize(a)


if __name__ == "__main__":
    sys.exit(main())
