"""S3(공개등급) 예측에서 룰-모델 합의가 confidence 게이트를 대신할 근거가 있는지 실측한다.

왜 필요한가(2026-09-27). 콘솔에서 룰·분류기·결합 결과가 전부 S3로 일치했는데도
confidence<threshold 로 검수 라우팅된 사례가 나왔다. "합의됐으면 confidence 낮아도
자동확정해도 되는 거 아니냐"는 물음에, 추측이 아니라 배포 서빙 경로 그대로(POST
/api/v1/classify, in-process TestClient) 태워서 재보기 위한 스크립트다. 게이트/설정은
전혀 건드리지 않는다 - 관찰만 한다.

측정 항목:
  - predicted==S3 인 문서를 rule_grade·automation_assessment.rule_has_evidence 로 삼분:
    ① 룰이 실제 근거를 갖고 S3에 동의 ② 룰이 근거 없이(기본값) 우연히 S3 로 일치
    ③ 룰이 다른 등급을 냄(불일치)
  - "합의(①+②) 인데 confidence<review_confidence_threshold 라 검수로 간" 건만 따로 추림 -
    바로 이게 사용자가 화면에서 본 케이스다.

결론은 이 스크립트가 내지 않는다 - n=1 처럼 표본이 작으면 "안전하다/위험하다" 어느 쪽도
주장하지 말고 표본 부족을 그대로 보고할 것. 재현: 아래 사용법 그대로 실행하면 된다.

사용:
    poc/.venv/Scripts/python.exe scripts/check_s3_agreement_gate.py \
        --eval datasets/gold_real/holdout_eval.jsonl \
        --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b \
        --out reports/s3_agreement_gate_check_2026-09-27/holdout109
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
_SRC = _POC / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

PARITY_KEYS = (
    "classifier_temperature", "classifier_escalation_tau",
    "review_confidence_threshold", "review_confidence_threshold_public",
    "agreement_gate_enabled", "metadata_floor_enabled",
    "source_prior_enabled", "source_prior_cap_grade",
)


def _profile_expected(profile: str) -> dict:
    code = (
        "import json,sys;"
        f"sys.path.insert(0, r'{_SRC}');"
        "from koipa.config import _PROFILE_DEFAULTS, Settings;"
        f"p=_PROFILE_DEFAULTS[{profile!r}];"
        f"ks={PARITY_KEYS!r};"
        "print(json.dumps({k: (p[k] if k in p else Settings.model_fields[k].default) for k in ks}))"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"프로파일 표를 못 읽음: {proc.stderr[:500]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out", required=True, help="출력 접두어(.records.jsonl 붙음)")
    ap.add_argument("--profile", default="onprem-local", choices=("onprem-local", "full-train"))
    args = ap.parse_args(argv)

    eval_path = (_POC / args.eval) if not Path(args.eval).is_absolute() else Path(args.eval)
    model_dir = (_POC / args.model_dir) if not Path(args.model_dir).is_absolute() else Path(args.model_dir)
    out_prefix = (_POC / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)

    os.environ["TESTING"] = "1"
    os.environ["RATE_LIMIT_DISABLED"] = "1"
    os.environ.setdefault("API_KEY", "measure-s3-gate")
    os.environ["CLASSIFIER_MODEL_DIR"] = str(model_dir.resolve())

    expected = _profile_expected(args.profile)
    for k in PARITY_KEYS:
        v = expected[k]
        if v is None:
            os.environ.pop(k.upper(), None)
        elif isinstance(v, bool):
            os.environ[k.upper()] = "1" if v else "0"
        else:
            os.environ[k.upper()] = str(v)

    from fastapi.testclient import TestClient  # noqa: PLC0415
    from koipa.api.app import app  # noqa: PLC0415
    from koipa.config import settings  # noqa: PLC0415

    effective = {k: getattr(settings, k, None) for k in PARITY_KEYS}
    drift = {k: (expected[k], effective[k]) for k in PARITY_KEYS if effective[k] != expected[k]}
    print(f"[profile] {args.profile}")
    for k in PARITY_KEYS:
        mark = "  <- 불일치!" if k in drift else ""
        print(f"    {k:32} {effective[k]!r}{mark}")
    if drift:
        raise SystemExit(f"[중단] 프로파일과 유효값이 다르다: {drift}")

    print(f"[model] {model_dir} exists={model_dir.is_dir()}")

    client = TestClient(app)
    headers = {"X-API-Key": settings.api_key or "measure-s3-gate"}

    rows = []
    for line in eval_path.read_text("utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    print(f"[eval] {eval_path} - {len(rows)}건")

    records = []
    errors = 0
    for i, row in enumerate(rows):
        text = str(row.get("text") or row.get("body") or "")
        truth = str(row.get("label") or row.get("expected_grade") or "")
        payload = {"doc_id": f"s3gate-{i:04d}", "content": text}
        try:
            r = client.post("/api/v1/classify", headers=headers, json=payload)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  {i} 예외: {exc}")
            continue
        if r.status_code != 200:
            errors += 1
            print(f"  {i} HTTP {r.status_code}: {r.text[:200]}")
            continue
        j = r.json()
        aa = j.get("automation_assessment") or {}
        records.append({
            "doc_id": row.get("doc_id"),
            "truth": truth,
            "predicted": j.get("label"),
            "rule_grade": j.get("rule_grade"),
            "confidence": j.get("confidence"),
            "status": j.get("status"),
            "score_margin": aa.get("score_margin"),
            "rule_has_evidence": aa.get("rule_has_evidence"),
            "rule_agrees": aa.get("rule_agrees"),
            "review_gate_hits": aa.get("review_gate_hits"),
            "causal_review_reason": aa.get("causal_review_reason"),
            "warnings": j.get("warnings"),
        })
        if (i + 1) % 20 == 0:
            print(f"  {i+1}/{len(rows)}", flush=True)

    print(f"[done] {len(records)}건 성공, {errors}건 오류")
    if errors:
        raise SystemExit(f"{errors}건 오류 - 부분집계는 신뢰 불가, 원인부터 해결")

    rec_path = out_prefix.with_suffix(".records.jsonl")
    rec_path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    print(f"[wrote] {rec_path}")

    s3_pred = [r for r in records if r["predicted"] == "S3"]
    agree = [r for r in s3_pred if r["rule_grade"] == "S3"]
    agree_evidence = [r for r in agree if r["rule_has_evidence"] is True]
    agree_no_evidence = [r for r in agree if r["rule_has_evidence"] is not True]
    disagree = [r for r in s3_pred if r["rule_grade"] != "S3"]

    def _prec(rows):
        return round(sum(1 for r in rows if r["predicted"] == r["truth"]) / len(rows), 4) if rows else None

    def _group_report(name, rows):
        reviewed = [r for r in rows if r["status"] == "needs_review"]
        auto = [r for r in rows if r["status"] != "needs_review"]
        return {
            "name": name, "n": len(rows),
            "confidence_range": [min((r["confidence"] for r in rows), default=None),
                                  max((r["confidence"] for r in rows), default=None)],
            "reviewed": len(reviewed), "auto_confirmed": len(auto),
            "precision_all": _prec(rows), "precision_auto": _prec(auto),
        }

    borderline = [r for r in agree if r["confidence"] is not None
                  and r["confidence"] < effective["review_confidence_threshold"]]

    summary = {
        "script": "scripts/check_s3_agreement_gate.py",
        "eval_set": str(eval_path), "n_total": len(records),
        "n_predicted_s3": len(s3_pred),
        "groups": [
            _group_report("rule=S3 + rule_has_evidence=True", agree_evidence),
            _group_report("rule=S3 + rule_has_evidence!=True(근거 없이 우연히 일치)", agree_no_evidence),
            _group_report("rule!=S3(불일치)", disagree),
        ],
        "borderline_agree_low_confidence": {
            "n": len(borderline),
            "threshold": effective["review_confidence_threshold"],
            "records": borderline,
            "note": "n이 작으면(관례적으로 <30) 이 값만으로 정책을 바꾸지 말 것 - 표본부족을 그대로 보고한다.",
        },
        # [2026-09-27] S3만 보면 이 패턴이 드물어 보이지만(37건 중 1건), 예측 등급 전체로
        # 넓히면 훨씬 흔하고(전체의 8%대) 그 다수가 "룰·모델이 같이 틀린(과분류)" 사례를
        # confidence 게이트가 잡아낸 것이었다 - S3만 보고 "드물다"고 결론내리면 과소평가한다.
        "all_grades_agree_low_confidence": {
            grade: {
                "predicted_n": len([r for r in records if r["predicted"] == grade]),
                "rule_agrees_n": len([r for r in records
                                       if r["predicted"] == grade and r["rule_grade"] == grade]),
                "agree_and_below_threshold": [
                    {k: r[k] for k in ("doc_id", "truth", "confidence", "rule_has_evidence")}
                    | {"correct": r["predicted"] == r["truth"]}
                    for r in records
                    if r["predicted"] == grade and r["rule_grade"] == grade
                    and r["confidence"] is not None
                    and r["confidence"] < effective["review_confidence_threshold"]
                ],
            }
            for grade in ("TS", "S1", "S2", "S3")
        },
    }
    summary_path = out_prefix.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[wrote] {summary_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
