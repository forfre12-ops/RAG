"""holdout109의 '금융보고서'(시황·거시경제 논평, 전부 truth=S3) 과대분류를
source_prior_cap(metadata.source_type)이 실제로 없앨 수 있는지 재현한다.

왜 필요한가(2026-09-27). [[no-real-market-commentary-docs-plus-tier-bug-fixed-2026-09-19]]가
"이 장르는 실문서가 없어 재학습으로 못 고친다"고 남겨둔 46.9% 과대분류를, 재학습이 아니라
이미 배포본에 켜져 있는 source_prior_cap 로 없앨 수 있는지 확인한다. 보도자료·판례에서는
이미 검증됐지만([[source-type-metadata-kills-public-overclass-2026-09-14]]) 이 장르는
그때 테스트하지 않았다.

배포 서빙 경로 그대로(POST /api/v1/classify, in-process, onprem-local 프로파일) 두 조건을
같은 문서에 태운다: ① 메타데이터 없음(현재 운영과 동일) ② metadata.source_type="public".

사용:
    poc/.venv/Scripts/python.exe scripts/check_finreport_source_prior.py \
        --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter
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
    ap.add_argument("--eval", default="datasets/gold_real/holdout_eval.jsonl")
    ap.add_argument("--source-field-value", default="금융보고서",
                     help="eval 파일의 'source' 필드에서 골라낼 값")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--profile", default="onprem-local", choices=("onprem-local", "full-train"))
    ap.add_argument("--out", default="reports/check_finreport_source_prior/result.json")
    args = ap.parse_args(argv)

    eval_path = (_POC / args.eval) if not Path(args.eval).is_absolute() else Path(args.eval)
    model_dir = (_POC / args.model_dir) if not Path(args.model_dir).is_absolute() else Path(args.model_dir)
    out_path = (_POC / args.out) if not Path(args.out).is_absolute() else Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    os.environ["TESTING"] = "1"
    os.environ["RATE_LIMIT_DISABLED"] = "1"
    os.environ.setdefault("API_KEY", "check-finreport-source-prior")
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
    if drift:
        raise SystemExit(f"[중단] 프로파일과 유효값이 다르다: {drift}")
    print(f"[profile] {args.profile}  source_prior_enabled={settings.source_prior_enabled} "
          f"cap_grade={settings.source_prior_cap_grade}")

    rows = [json.loads(l) for l in eval_path.read_text("utf-8").splitlines() if l.strip()]
    docs = [r for r in rows if r.get("source") == args.source_field_value]
    if not docs:
        raise SystemExit(f"source=={args.source_field_value!r} 인 문서가 0건이다")
    print(f"[eval] {eval_path} 중 source=={args.source_field_value!r} {len(docs)}건")

    client = TestClient(app)
    headers = {"X-API-Key": settings.api_key or "check-finreport-source-prior"}

    def run(condition_name: str, metadata: dict | None):
        out = []
        for i, row in enumerate(docs):
            text = str(row.get("text") or "")
            truth = str(row.get("label") or row.get("expected_grade") or "")
            payload = {"doc_id": f"{condition_name}-{i:04d}", "content": text}
            if metadata is not None:
                payload["metadata"] = metadata
            r = client.post("/api/v1/classify", headers=headers, json=payload)
            if r.status_code != 200:
                raise SystemExit(f"[{condition_name}] {i} HTTP {r.status_code}: {r.text[:200]}")
            j = r.json()
            out.append({
                "doc_id": row.get("doc_id"), "truth": truth, "predicted": j.get("label"),
                "status": j.get("status"), "warnings": j.get("warnings") or [],
            })
        return out

    baseline = run("nometa", None)
    with_meta = run("public", {"source_type": "public"})

    def summarize(records):
        over = [r for r in records if r["predicted"] != r["truth"] and r["truth"] == "S3"]
        return {
            "n": len(records),
            "overclass_n": len(over),
            "overclass_rate": round(len(over) / len(records), 4),
            "status_distribution": dict(sorted(Counter(r["status"] for r in records).items())),
            "review_rate": round(
                sum(1 for r in records if r["status"] == "needs_review") / len(records), 4),
        }

    result = {
        "script": "scripts/check_finreport_source_prior.py",
        "source_field_value": args.source_field_value,
        "n_docs": len(docs),
        "baseline_no_metadata": summarize(baseline),
        "with_source_type_public": summarize(with_meta),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[wrote] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
