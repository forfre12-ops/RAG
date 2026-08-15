"""Build an operational readiness summary from existing evaluation artifacts.

The script is intentionally offline: it reads JSON reports and dataset files that
already exist in the workspace, then writes a compact Markdown/JSON gate report.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Gate:
    name: str
    status: str
    detail: str


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _count_jsonl(path: Path) -> tuple[int, Counter, Counter]:
    grade_counts: Counter = Counter()
    source_counts: Counter = Counter()
    if not path.exists():
        return 0, grade_counts, source_counts
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        n += 1
        grade = row.get("label") or row.get("expected_grade")
        if grade:
            grade_counts[str(grade)] += 1
        source = row.get("label_source") or row.get("source")
        if source:
            source_counts[str(source)] += 1
    return n, grade_counts, source_counts


def _p1_gate(public_report: dict, llm_report: dict) -> tuple[Gate, dict]:
    public_m = public_report.get("metrics", {})
    llm_m = llm_report.get("metrics", {})
    public_pass = (
        public_m.get("f1_macro", 0) >= 0.75
        and public_m.get("fnr_underclass", 1) <= 0.05
        and public_m.get("high_risk_to_s3", 999) == 0
    )
    status = "PASS" if public_pass else "FAIL"
    detail = (
        f"public/case/nkt F1={public_m.get('f1_macro', 0):.3f}, "
        f"FNR={public_m.get('fnr_underclass', 0):.3f}, "
        f"high-risk->S3={public_m.get('high_risk_to_s3', 'N/A')}; "
        f"llm pseudo F1={llm_m.get('f1_macro', 0):.3f}, "
        f"high-risk->S3={llm_m.get('high_risk_to_s3', 'N/A')}"
    )
    return Gate("P1 classifier", status, detail), {"public": public_m, "llm_pseudo": llm_m}


def _p2_gate(p2_report: dict) -> tuple[Gate, dict]:
    best = p2_report.get("best_config", {})
    metrics = best.get("retrieval_metrics", {})
    recall = metrics.get("recall_at_k", best.get("recall_at_k", 0))
    latency = best.get("latency_ms_p50", 999999)
    status = "PASS" if recall >= 0.80 and latency <= 200 else "FAIL"
    detail = (
        f"{best.get('label', 'N/A')}: Recall@5={recall:.3f}, "
        f"MRR={metrics.get('mrr', 0):.3f}, nDCG@5={metrics.get('ndcg_at_k', 0):.3f}, "
        f"p50={latency:.0f}ms"
    )
    return Gate("P2 retrieval", status, detail), {"best_config": best}


HIGH_RISK = {"TS", "S1", "S2"}

_STRICT_VALIDATOR_CACHE: list = []


def _strict_signoff_validator():
    """golden_tiers.is_real_locked_eval 로더 — 유효 서명 envelope(실계정 reviewer·gate_version·
    signed_at·reviewer_ids) + 실문서 출처(document_origin∈{public_real,customer_real})를 함께 요구.

    [#8] readiness 의 human_review 게이트가 raw label_source=="human_review" 만 세면, 서명·출처
    envelope 없는 가짜 40건으로 PASS 를 만들 수 있었다(감사 재현). 이 검증기로 '엄격 골든 계약'을
    강제한다. import 실패(레포 밖 실행 등) 시 None → 호출부가 strict 인정 0 으로 fail-closed 처리
    (가짜 통과 재개 대신 BLOCKED). 결과를 memo 해 파일당 반복 import 를 피한다.
    """
    if _STRICT_VALIDATOR_CACHE:
        return _STRICT_VALIDATOR_CACHE[0]
    fn = None
    try:
        import sys

        _src = Path(__file__).resolve().parent.parent / "src"
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        from koipa.golden_tiers import is_real_locked_eval as fn  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        fn = None
    _STRICT_VALIDATOR_CACHE.append(fn)
    return fn


def _human_review_agreement(path: Path) -> dict:
    """Collect human_review gold records and measure how often the model
    under-protected a high-risk doc (human=TS/S1/S2 but model predicted S3).

    [#8] 두 층을 함께 센다:
      - count      : raw label_source=="human_review" 건수(투명성용).
      - count_strict: golden_tiers.is_real_locked_eval 통과분(유효 서명 envelope + 실문서 출처).
        게이트 임계는 이 strict 값을 쓴다 → 서명·출처 없는 가짜 human_review 로 게이트를 넘길 수
        없다. 동의(underclass) 품질 지표도 strict 모집단에서만 계산한다(진짜 검수 대상).
    """
    validate = _strict_signoff_validator()
    n = strict = high_risk = underclass = missing_model = 0
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            if (row.get("label_source") or row.get("source")) != "human_review":
                continue
            n += 1
            if not (validate and validate(row)):
                continue  # 서명 envelope/실문서 출처 미충족 → strict 집계·품질계산에서 제외
            strict += 1
            human = str(row.get("label") or row.get("expected_grade") or "").upper()
            model = str(row.get("model_label") or "").upper()
            if not model:
                missing_model += 1
            if human in HIGH_RISK:
                high_risk += 1
                if model == "S3":
                    underclass += 1
    rate = (underclass / high_risk) if high_risk else 0.0
    return {
        "count": n,
        "count_strict": strict,
        "count_unsigned": n - strict,
        "strict_validator_loaded": validate is not None,
        "high_risk": high_risk,
        "underclass": underclass,
        "missing_model_label": missing_model,
        "high_risk_underclass_rate": round(rate, 4),
    }


def _data_gates(
    gold_path: Path,
    retrieval_gold_path: Path,
    min_human_review: int,
    max_high_risk_underclass: float,
) -> tuple[list[Gate], dict]:
    gold_n, grade_counts, source_counts = _count_jsonl(gold_path)
    retrieval_n, _, retrieval_sources = _count_jsonl(retrieval_gold_path)
    hr = _human_review_agreement(gold_path)
    # [#8] 게이트 임계는 raw 가 아니라 strict(서명 envelope + 실문서 출처)만 센다.
    human_review = hr["count_strict"]
    human_review_raw = hr["count"]
    _drop = f" (raw label_source=human_review={human_review_raw}, {hr['count_unsigned']} dropped: no valid signoff/real-origin)"
    if not hr["strict_validator_loaded"]:
        _drop += " [WARN: golden_tiers validator unavailable → strict counted as 0, fail-closed]"

    if human_review < min_human_review:
        hr_status = "BLOCKED"
        hr_detail = (
            f"human_review(strict)={human_review}/{min_human_review}{_drop}; "
            "external reviewed samples still required"
        )
    elif hr["missing_model_label"] > 0:
        hr_status = "FAIL"
        hr_detail = (
            f"human_review(strict)={human_review}{_drop}; "
            f"{hr['missing_model_label']} records missing model_label "
            "(cannot compute model-vs-human agreement)"
        )
    elif hr["high_risk_underclass_rate"] > max_high_risk_underclass:
        hr_status = "FAIL"
        hr_detail = (
            f"human_review(strict)={human_review}; high-risk underclass "
            f"{hr['underclass']}/{hr['high_risk']} "
            f"rate={hr['high_risk_underclass_rate']:.3f} > {max_high_risk_underclass:.3f}"
        )
    else:
        hr_status = "PASS"
        hr_detail = (
            f"human_review(strict)={human_review}; high-risk underclass "
            f"{hr['underclass']}/{hr['high_risk']} "
            f"rate={hr['high_risk_underclass_rate']:.3f} <= {max_high_risk_underclass:.3f}"
        )

    gates = [
        Gate(
            "classification gold size",
            "PASS" if gold_n >= 700 else "FAIL",
            f"{gold_n} records, grades={dict(sorted(grade_counts.items()))}",
        ),
        Gate("human review gold", hr_status, hr_detail),
        Gate(
            "retrieval gold size",
            "PASS" if retrieval_n >= 80 else "FAIL",
            f"{retrieval_n} doc-id queries, sources={dict(sorted(retrieval_sources.items()))}",
        ),
    ]
    return gates, {
        "human_review_agreement": hr,
        "classification_gold": {
            "path": str(gold_path),
            "records": gold_n,
            "grade_distribution": dict(sorted(grade_counts.items())),
            "source_distribution": dict(sorted(source_counts.items())),
        },
        "retrieval_gold": {
            "path": str(retrieval_gold_path),
            "records": retrieval_n,
            "source_distribution": dict(sorted(retrieval_sources.items())),
        },
    }


def _norm_model(p: str) -> str:
    return (p or "").replace("\\", "/").strip().rstrip("/")


def _git_sha() -> str:
    """이 리포트를 만든 코드의 커밋. git 이 없으면 빈 문자열(게이트가 그것을 잡는다)."""
    import subprocess  # noqa: PLC0415

    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _deploy_profile() -> str:
    """리포트를 만든 환경의 배포 프로파일. 다른 프로파일 증거를 재사용하는 것을 막는다."""
    import os as _os  # noqa: PLC0415

    return (_os.environ.get("DEPLOY_PROFILE") or "").strip()


def _deployed_model_default() -> str:
    """배포 모델 단일 진실원 = settings.classifier_model_dir(.env).

    raw os.environ는 .env가 셸에 export되지 않은 컨텍스트에선 비어 보여 parity를
    엉뚱하게 BLOCKED로 만든다(게이트 판정이 *실행 방식*에 좌우되는 버그). settings를
    우선 읽어 실제 .env 설정을 반영하고, 실패 시 os.environ로 폴백한다.
    """
    try:
        import sys

        _src = Path(__file__).resolve().parent.parent / "src"
        if str(_src) not in sys.path:
            sys.path.insert(0, str(_src))
        from koipa.config import settings  # noqa: PLC0415

        if settings.classifier_model_dir:
            return settings.classifier_model_dir
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("CLASSIFIER_MODEL_DIR", "")


def _model_parity_gate(evaluated: str, deployed: str) -> Gate:
    """The F1/FNR gates describe the *evaluated* model. If the *deployed* model
    (CLASSIFIER_MODEL_DIR) differs, those numbers do not describe what is live;
    so readiness cannot be claimed on them. BLOCKED until the deploy is promoted.
    """
    ev, dp = _norm_model(evaluated), _norm_model(deployed)
    if not dp:
        return Gate(
            "model parity",
            "BLOCKED",
            f"deployed model unknown (CLASSIFIER_MODEL_DIR unset); evaluated={ev}. "
            "Cannot confirm the gated metrics describe the live model.",
        )
    if ev == dp:
        return Gate("model parity", "PASS", f"deployed == evaluated ({ev})")
    return Gate(
        "model parity",
        "BLOCKED",
        f"deployed={dp} != evaluated={ev}; F1/FNR gates describe the evaluated model, "
        "not what is live. Promote CLASSIFIER_MODEL_DIR before release.",
    )


def _overall(gates: list[Gate]) -> str:
    if any(g.status == "FAIL" for g in gates):
        return "FAIL"
    if any(g.status == "BLOCKED" for g in gates):
        return "CONDITIONALLY_READY"
    return "PASS"


def _write_md(payload: dict, out: Path) -> None:
    gates = payload["gates"]
    lines = [
        "# Operational Readiness",
        "",
        f"- Verdict: **{payload['verdict']}**",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Git sha: `{payload.get('git_sha') or 'unknown'}`",
        f"- Deploy profile: `{payload.get('deploy_profile') or 'unset'}`",
        f"- Evaluated model: `{payload['evaluated_model']}`",
        f"- Deployed model: `{payload['deployed_model']}`",
        f"- Retrieval config: `{payload['retrieval_config']}`",
        "",
        "## Gates",
        "",
        "| Gate | Status | Detail |",
        "|---|:---:|---|",
    ]
    for g in gates:
        lines.append(f"| {g['name']} | {g['status']} | {g['detail']} |")

    lines += [
        "",
        "## Required Next Actions",
        "",
    ]
    for item in payload["next_actions"]:
        lines.append(f"- {item}")

    if payload.get("known_limitations"):
        lines += [
            "",
            "## Known Limitations",
            "",
            "These are documented and expected, not open bugs. Read before reacting to any FAIL/low-F1 artifact.",
            "",
        ]
        for item in payload["known_limitations"]:
            lines.append(f"- {item}")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    # 2026-06-05: 티어 분리. p1-public = *법적근거 티어*(real-ish 라벨: public_definitive/
    # nkt_designated — koipa는 2026-07-03 인용조작 확인으로 강등, curated_scenario로 분리 보고)
    # 를 신뢰 신호로(게이트 판정 기준). p1-llm = 전체 홀드아웃
    # (llm_judge ~47% 노이즈 포함)을 보수적 하한으로. 'FAIL on 노이즈'를 'FAIL on 진짜 약점'과
    # 구분한다. 두 리포트는 `make p1-eval`이 배포 모델로 생성(단일 진실원).
    ap.add_argument("--p1-public", default="reports/p1_release_legal_direct.json")
    ap.add_argument("--p1-llm", default="reports/p1_release_holdout_direct.json")
    ap.add_argument("--p2", default="reports/p2_gold_kure_es_hybrid_v3.json")
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--retrieval-gold", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_v5_clean/v-fe4b386b",
                    help="evaluated model - the one the F1/FNR reports describe")
    ap.add_argument("--deployed-model", default=_deployed_model_default(),
                    help="live deployment model (defaults to settings.classifier_model_dir / .env)")
    ap.add_argument("--out", default="reports/operational_readiness.md")
    ap.add_argument("--min-human-review", type=int, default=40)
    ap.add_argument("--max-high-risk-underclass", type=float, default=0.10)
    args = ap.parse_args()

    p1_public_report = _load_json(Path(args.p1_public))
    p1_gate, p1_payload = _p1_gate(p1_public_report, _load_json(Path(args.p1_llm)))
    # evaluated 모델 = 리포트가 *실제로* 기술하는 모델(report.model_dir)을 진실원으로.
    # parity 게이트가 "F1 리포트가 라이브 배포 모델을 기술하나?"를 정확히 묻게 한다.
    evaluated_model = p1_public_report.get("model_dir") or args.model_dir
    p2_gate, p2_payload = _p2_gate(_load_json(Path(args.p2)))
    data_gates, data_payload = _data_gates(
        Path(args.gold),
        Path(args.retrieval_gold),
        args.min_human_review,
        args.max_high_risk_underclass,
    )

    parity_gate = _model_parity_gate(evaluated_model, args.deployed_model)
    gate_objects = [p1_gate, p2_gate, *data_gates, parity_gate]
    public_f1 = p1_payload.get("public", {}).get("f1_macro", 0)
    pseudo_f1 = p1_payload.get("llm_pseudo", {}).get("f1_macro", 0)

    # 블로커 요약을 동적으로 생성 — 과거엔 "human-review가 유일 블로커"를 하드코딩하면서
    # model parity 게이트도 BLOCKED를 내보내 리포트가 자기모순이었다. human_review는
    # 외부 의존(실제 검수 라벨)이라 진짜 블로커지만, model parity는 배포 시
    # CLASSIFIER_MODEL_DIR 설정으로 자가해소되는 내부 액션이므로 구분해 표기한다.
    parity_blocked = parity_gate.status == "BLOCKED"
    p1_failed = p1_gate.status == "FAIL"
    hr_blocked = any(
        g.name == "human review gold" and g.status == "BLOCKED" for g in data_gates
    )
    if hr_blocked:
        blocker_line = (
            "human_review gold is the only EXTERNALLY-DEPENDENT release blocker "
            "(needs real human-reviewed samples; cannot be auto-filled)."
        )
    else:
        blocker_line = "No externally-dependent human_review blocker remains."
    if parity_blocked:
        blocker_line += (
            " NOTE: the 'model parity' gate is also currently BLOCKED, but it self-resolves "
            "at deploy time by setting CLASSIFIER_MODEL_DIR to the evaluated model "
            "(internal deploy action, not an external dependency)."
        )
    parity_note = (
        f"Evaluated model ({_norm_model(evaluated_model)}) may differ from the live deployment "
        f"({_norm_model(args.deployed_model) or 'unknown'}); the 'model parity' gate blocks release until they match."
        if parity_blocked
        else f"Evaluated model matches deployed model ({_norm_model(evaluated_model)})."
    )
    known_limitations = [
        blocker_line + " Everything else below is by design, not an open defect.",
        f"Classifier F1 is source-dependent: public/case/nkt release tier = {public_f1:.3f}, "
        f"llm_judge pseudo = {pseudo_f1:.3f} (lower bound). Never cite a single F1 without its source.",
        "Pseudo-label noise: ~47% of court-ruling records in the llm_judge tiers are over-graded S1/S2 "
        "(published rulings should be S3 — non-publicity fails). This deflates the pseudo-set F1 and inflates "
        "apparent high-risk->S3 'errors'. Quantify via scripts/analyze_label_noise.py; route corrections "
        "through the human-review loop, do not auto-relabel.",
        "The production path (m5_inference, api-like) applies an FNR-safe override that intentionally "
        "over-classifies toward higher grades: precision/F1 drop but high-risk under-classification (TS/S1/S2 -> S3) "
        "is driven to ~0. A low api-mode F1 is the safety trade-off working, not a regression.",
        "reports/eval_human_review_gold.* is produced by `p1_train_classifier.py --mode dryrun`, which scores the "
        "m3 keyword rule-labeler, NOT the release candidate model. Its FAIL verdict does not describe model "
        "performance; use reports/p1_release_*_direct.* (eval_p1_model_gold.py) for the trained release model.",
        "Operational cost of the over-classification (S3 docs flagged as S1/S2 -> reviewer false-positive load) "
        "is not yet quantified; defer until real human_review labels exist.",
        parity_note,
    ]
    next_actions = [
        f"Collect at least {args.min_human_review} human_review gold samples; "
        "this remains the externally-dependent release blocker.",
        f"Human-review gate now also requires high-risk underclass rate <= {args.max_high_risk_underclass:.2f} "
        "(human=TS/S1/S2 but model=S3); a filled queue alone no longer passes it.",
        "Reporting is split by source: public/case/nkt (definitive) vs llm_judge pseudo. "
        "Treat the pseudo-set F1 as the conservative bound, not the public-set F1.",
        "Review LLM pseudo-gold S2->S3 cases and either relabel or add boundary examples.",
        "Release candidate is the clean model; readiness reads reports/p1_release_* generated from that same model.",
        "Run p2-full-gold after every ES reindex or embedding-model change.",
    ]
    if p1_failed:
        next_actions.insert(
            -1,
            "P1 still fails the F1 target. Treat threshold/model swaps as secondary; the primary fix is more trusted "
            "S1/S2 human-reviewed data and boundary examples.",
        )
    else:
        next_actions.insert(
            -1,
            "P1 release-tier classifier gate now passes; keep it green by regenerating p1_release_* after model or "
            "source-prior policy changes.",
        )
    payload = {
        "generated_at": _dt.date.today().isoformat(),
        # [증거 신원] 이 리포트가 **어느 커밋·어느 프로파일**에서 나왔는지를 함께 남긴다.
        # 없으면 릴리스 게이트가 "이 빌드에서 나온 증거인가" 를 물을 수 없고, 두 달 반
        # 묵은 READY 가 다른 빌드에 재사용된다(실측 2026-08-15: manifest 6/1 READY vs
        # readiness 8/5 FAIL). check_release_gate.py --require-fresh 가 이 필드를 본다.
        "git_sha": _git_sha(),
        "deploy_profile": _deploy_profile(),
        "verdict": _overall(gate_objects),
        "evaluated_model": _norm_model(evaluated_model),
        "deployed_model": _norm_model(args.deployed_model) or "unknown",
        "retrieval_config": "KURE-v1 + Elasticsearch hybrid + chunk=1200/overlap=100",
        "release_gate_policy": {
            "min_human_review": args.min_human_review,
            "max_high_risk_underclass": args.max_high_risk_underclass,
        },
        "gates": [g.__dict__ for g in gate_objects],
        "p1": p1_payload,
        "p2": p2_payload,
        "data": data_payload,
        "next_actions": next_actions,
        "known_limitations": known_limitations,
    }

    out = Path(args.out)
    _write_md(payload, out)
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": payload["verdict"], "gates": payload["gates"]}, ensure_ascii=True, indent=2))
    return 0 if payload["verdict"] in {"PASS", "CONDITIONALLY_READY"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
