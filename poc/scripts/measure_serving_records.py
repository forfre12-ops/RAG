"""평가셋을 **배포 서빙 경로 그대로** 태워 문서별 레코드와 집계를 남긴다(in-process).

왜 이 스크립트가 저장소에 있어야 하는가(2026-08-22). 커밋 5367c896 본문이 인용한
hardened42 실측(auto_confirm_rate 40.48%→50%→64.29%)을 만든 하니스가 세션 스크래치패드에만
있었다. 저장소의 measure_serving_fnr.py 는 요청 doc_id 를 `servingfnr-NNNNN` 으로 주는데 그
런의 레코드는 `tsweep-NNNN` 이라 **같은 스크립트가 아니고**, 리포트 키도 달랐다. 즉 감리에서
인용될 수치를 버전관리된 코드로 재현할 수 없었다. 이 파일이 그 자리를 메운다.

measure_serving_fnr.py 와의 차이:

    measure_serving_fnr.py   **떠 있는 서버**에 HTTP 로 물어본다(배포본 현장 실측용).
    이 스크립트              체크아웃한 코드를 in-process TestClient 로 태운다(재현용).

레코드에 남기는 것(종전 5필드 → 12필드). 종전 레코드는 truth/predicted/status/confidence/
warnings 뿐이라 **어느 문서인지 알 수 없었다** - 시연 문서 선정도, 오분류 재확인도 못 한다.
doc_id·text_sha256·model_grade(게이트 전 모델 단독 판정)·causal_review_reason 을 같이 남긴다.

설정 정합. 판정에 영향을 주는 설정은 손으로 적지 않고 **배포 프로파일 표에서 끌어온다**
(config._PROFILE_DEFAULTS). 온도 드리프트(프로파일 3.0 이 모델 동봉 2.03 을 덮어쓰던 건)가
바로 이 값들을 손으로 맞추다 생긴 일이다. 실행 후 유효값이 프로파일과 다르면 멈춘다.

TESTING=1 과 하드닝 프로파일(onprem-local/full-train)은 같이 못 쓴다 - config.py:1172 가
"운영에 테스트 env 유입"으로 보고 startup 을 막는다(정상 동작). 그래서 프로파일 이름을
그대로 주는 대신, **그 프로파일이 정한 판정 관련 값만** env 로 내보내고 정합을 확인한다.

사용:
    python scripts/measure_serving_records.py \
        --eval datasets/gold_real/holdout_eval.hardened.jsonl \
        --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b \
        --out reports/serving_records_hardened42_t203_abstain.json \
        --purpose "agreement-gate abstain-on-no-evidence fix, on top of T=2.03"

    # 메타데이터 조건 비교(T0-1 처럼 paired):
    python scripts/measure_serving_records.py --eval ... --out ... \
        --condition no_metadata= \
        --condition "source_type_public={\"source_type\": \"public\"}"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
_SRC = _POC / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:  # 콘솔 코드페이지가 cp949 여도 한국어 로그가 깨지지 않게.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

GRADES = ("TS", "S1", "S2", "S3")
ORDER = {g: i for i, g in enumerate(GRADES)}      # TS=0 이 가장 높다
HIGH = ("TS", "S1")
RECORD_SCHEMA_VERSION = 2

# 등급 판정에 실제로 영향을 주는 설정. 프로파일 표에서 끌어오고, 실행 후 유효값을 대조한다.
PARITY_KEYS = (
    "classifier_temperature",
    "classifier_escalation_tau",
    "review_confidence_threshold",
    "review_confidence_threshold_public",
    "agreement_gate_enabled",
    "metadata_floor_enabled",
    "source_prior_enabled",
    "source_prior_cap_grade",
)


def _sha256_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _env_value(v: object) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(_POC.parent), *args],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _profile_expected(profile: str, keys: tuple[str, ...]) -> dict[str, object]:
    """프로파일이 정한 판정 관련 값 - **별도 프로세스**에서 읽는다.

    koipa.config 는 import 시점에 settings 싱글턴을 만든다. 이 프로세스에서 표를 읽으려고
    먼저 import 하면 그 뒤에 내보내는 env 가 반영되지 않는다(실측 2026-08-22: 그렇게 짰다가
    유효 온도가 1.0 으로 나왔다). 그래서 표만 자식 프로세스에서 뽑아 오고, 이 프로세스는
    env 를 먼저 세운 뒤에 config 를 처음 import 한다.
    """
    code = (
        "import json,sys;"
        "sys.path.insert(0, r'{src}');"
        "from koipa.config import _PROFILE_DEFAULTS, Settings;"
        "p=_PROFILE_DEFAULTS[{profile!r}];"
        "ks={keys!r};"
        "print(json.dumps({{k: (p[k] if k in p else Settings.model_fields[k].default) "
        "for k in ks}}))"
    ).format(src=str(_SRC), profile=profile, keys=keys)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"프로파일 표를 읽지 못했다: {proc.stderr.strip()[:400]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _parse_overrides(items: list[str] | None) -> dict[str, str]:
    """--set KEY=VALUE 를 dict 로. 판정 관련 키(PARITY_KEYS)만 허용한다.

    아무 설정이나 덮어쓰게 두면 '무엇을 바꿔서 잰 수치인지' 가 리포트에서 사라진다.
    PARITY_KEYS 로 제한하면 덮어쓴 값이 반드시 settings_profile_drift 에도 나타난다.
    """
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--set 은 KEY=VALUE 형식이다: {item!r}")
        key, _, value = item.partition("=")
        key = key.strip().lower()
        if key not in PARITY_KEYS:
            raise SystemExit(
                f"--set 은 판정 관련 키만 허용한다: {key!r} (가능: {', '.join(PARITY_KEYS)})"
            )
        out[key] = value.strip()
    return out


def _load_rows(path: Path, limit: int) -> list[dict]:
    rows = []
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows[:limit] if limit else rows


def _parse_conditions(raw: list[str] | None) -> dict[str, dict | None]:
    if not raw:
        return {"no_metadata": None}
    conds: dict[str, dict | None] = {}
    for item in raw:
        name, _, payload = item.partition("=")
        name = name.strip()
        payload = payload.strip()
        if not name:
            raise SystemExit(f"--condition 이름이 비었다: {item!r}")
        conds[name] = json.loads(payload) if payload else None
    return conds


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="배포 서빙 경로 문서별 실측(in-process)")
    ap.add_argument("--eval", required=True)
    ap.add_argument("--out", required=True,
                    help="집계 리포트 경로(.json). 레코드는 같은 이름의 .records.jsonl")
    ap.add_argument("--model-dir", default=None, help="미지정 시 CLASSIFIER_MODEL_DIR/설정값")
    ap.add_argument("--profile", default="onprem-local", choices=("onprem-local", "full-train"),
                    help="판정 관련 설정을 끌어올 배포 프로파일(기본 onprem-local=고객사 폐쇄망)")
    ap.add_argument("--condition", action="append", default=None,
                    help="이름=메타데이터JSON. 반복 가능. 미지정 시 메타데이터 없이 1회")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--purpose", default="")
    ap.add_argument("--compare-to", action="append", default=None)
    ap.add_argument("--id-prefix", default=None, help="요청 doc_id 접두어(기본 out 파일 이름)")
    ap.add_argument("--set", action="append", default=None, metavar="KEY=VALUE",
                    help="프로파일 값을 **의도적으로** 덮어쓴다(판정 관련 키만). 반복 가능. "
                         "임계 스윕처럼 '한 값만 바꾼 같은 경로'를 재려고 쓴다. 덮어쓴 값은 "
                         "프로파일과 달라지므로 --allow-drift 가 함께 필요하고, 리포트의 "
                         "settings_profile_drift·settings_overrides 에 그대로 남는다.")
    ap.add_argument("--allow-drift", action="store_true",
                    help="유효 설정이 프로파일과 달라도 진행(권장하지 않음 - 리포트에 그대로 기록된다)")
    ap.add_argument("--allow-rule-fallback", action="store_true",
                    help="모델 미로드(룰 폴백)여도 진행. 기본은 중단 - 그 수치는 모델 성능이 아니다")
    args = ap.parse_args(argv)

    eval_path = Path(args.eval)
    if not eval_path.is_file():
        raise SystemExit(f"평가셋이 없다: {eval_path}")
    out_path = Path(args.out)
    conditions = _parse_conditions(args.condition)

    # ── 설정: 프로파일 표에서 끌어와 env 로 내보낸다(손으로 적은 값 금지) ──────────
    os.environ["TESTING"] = "1"   # in-process TestClient. 하드닝 프로파일과 겸용 불가(config.py:1172)
    # [2026-08-23] 레이트리밋을 끈다. 켜 두면 60건/분을 넘는 순간부터 429 가 나고, 그 문서들은
    # 레코드에서 **조용히 빠진 채** 리포트가 n=67 로 나온다(실측: holdout109 109건 중 42건 유실).
    # 유실된 줄이 화면에는 뜨지만 스윕 표에는 '자동확정률 61.7%' 처럼 정상값처럼 보인다.
    # 이건 측정 하니스이지 서비스가 아니므로 한도를 둘 이유가 없다(rate_limit.py 가 이 용도로
    # 두고 있는 스위치다). 운영 프로세스에는 config.py:1311 가 별도로 fail-fast 를 건다.
    os.environ["RATE_LIMIT_DISABLED"] = "1"
    os.environ.setdefault("API_KEY", "measure-serving-records")
    if args.model_dir:
        os.environ["CLASSIFIER_MODEL_DIR"] = str(Path(args.model_dir).resolve())

    expected = _profile_expected(args.profile, PARITY_KEYS)
    for key in PARITY_KEYS:
        # None(미설정)은 env 로 표현할 수 없다 - "None" 문자열을 넣으면 float|None 파싱이 깨진다.
        # 미설정은 env 를 아예 지워서 Settings 기본값(None)이 그대로 서게 한다.
        if expected[key] is None:
            os.environ.pop(key.upper(), None)
            continue
        os.environ[key.upper()] = _env_value(expected[key])

    # 프로파일 export **뒤에** 적용한다 - 위 루프가 나중에 돌면 덮어쓰기가 조용히 무시된다
    # (실측 2026-08-23: REVIEW_CONFIDENCE_THRESHOLD 를 env 로 줬는데 유효값이 0.7 그대로였다).
    overrides = _parse_overrides(args.set)
    for key, raw in overrides.items():
        os.environ[key.upper()] = raw

    from fastapi.testclient import TestClient  # noqa: PLC0415
    from koipa.api.app import app  # noqa: PLC0415
    from koipa.config import settings  # noqa: PLC0415
    from koipa.services.review_reasons import (  # noqa: PLC0415
        UNMAPPED,
        causal_review_reason,
        count_causal_reasons,
        gate_hits,
    )

    effective = {k: getattr(settings, k, None) for k in PARITY_KEYS}
    drift = {k: {"expected": expected[k], "effective": effective[k]}
             for k in PARITY_KEYS if effective[k] != expected[k]}
    print(f"[profile] {args.profile} (판정 관련 {len(PARITY_KEYS)}개 키를 이 표에서 끌어옴)")
    for k in PARITY_KEYS:
        mark = "  <- 불일치" if k in drift else ""
        print(f"    {k:32} {effective[k]!r}{mark}")
    if drift and not args.allow_drift:
        print(f"[중단] 유효 설정이 프로파일과 다르다: {sorted(drift)}", file=sys.stderr)
        return 2

    model_dir = Path(str(settings.classifier_model_dir or ""))
    if not str(settings.classifier_model_dir or "") or not model_dir.is_dir():
        raise SystemExit(f"분류기 모델 디렉터리가 없다: {str(model_dir)!r} (--model-dir 로 지정)")
    temp_json = model_dir / "temperature.json"
    temp_sha = _sha256_file(temp_json)
    temp_value = None
    if temp_json.is_file():
        try:
            temp_value = json.loads(temp_json.read_text("utf-8")).get("temperature")
        except Exception:  # noqa: BLE001
            temp_value = None
    print(f"[model] {model_dir}")
    print(f"[temperature.json] value={temp_value} sha256={temp_sha}")

    commit = _git("rev-parse", "HEAD")
    dirty = bool(_git("status", "--porcelain"))
    print(f"[git] commit={commit} dirty={dirty}")

    rows = _load_rows(eval_path, args.limit)
    eval_sha = _sha256_file(eval_path)
    print(f"[eval] {eval_path} · {len(rows)}건 · sha256={eval_sha}")

    client = TestClient(app)
    headers = {"X-API-Key": settings.api_key or "measure-serving-records"}
    id_prefix = args.id_prefix or out_path.stem
    model_versions: set[str] = set()

    def run_condition(name: str, metadata: dict | None) -> tuple[list[dict], int]:
        records: list[dict] = []
        errors = 0
        for i, row in enumerate(rows):
            text = str(row.get("text") or row.get("body") or "")
            # 요청 doc_id 는 비-UUID 로 준다 - DB persist 를 건너뛰어 운영 데이터를 오염시키지 않는다.
            request_doc_id = f"{id_prefix}-{name}-{i:04d}"
            payload: dict = {"doc_id": request_doc_id, "content": text, "use_rag": False}
            if metadata is not None:
                payload["metadata"] = metadata
            try:
                r = client.post("/api/v1/classify", headers=headers, json=payload)
                if r.status_code != 200:
                    errors += 1
                    print(f"  [{name}] {i} HTTP {r.status_code}: {r.text[:160]}")
                    continue
                j = r.json()
            except Exception as exc:  # noqa: BLE001
                errors += 1
                print(f"  [{name}] {i} 예외: {exc}")
                continue
            model_versions.add(str(j.get("model_version")))
            warnings = j.get("warnings") or []
            status = str(j.get("status") or "")
            records.append({
                "doc_id": str(row.get("doc_id") or f"idx-{i:04d}"),
                "request_doc_id": request_doc_id,
                "truth": str(row.get("label") or row.get("expected_grade") or ""),
                "model_grade": j.get("model_grade"),
                "predicted": str(j.get("label") or ""),
                "status": status,
                "confidence": j.get("confidence"),
                "warnings": warnings,
                "causal_review_reason": causal_review_reason(warnings, status),
                "review_gate_hits": gate_hits(warnings),
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text_len": len(text),
            })
            if (i + 1) % 25 == 0:
                print(f"  [{name}] {i+1}/{len(rows)}", flush=True)
        return records, errors

    def summarize(records: list[dict]) -> dict:
        n = len(records)
        high = [r for r in records if r["truth"] in HIGH]
        under = [r for r in high
                 if r["predicted"] in ORDER and ORDER[r["predicted"]] > ORDER[r["truth"]]]
        silent = [r for r in under if r["status"] != "needs_review"]
        auto = [r for r in records if r["status"] != "needs_review"]
        correct_auto = sum(1 for r in auto if r["predicted"] == r["truth"])
        return {
            "n": n,
            "status_distribution": dict(sorted(Counter(r["status"] for r in records).items())),
            "auto_confirm_rate": round(len(auto) / n, 4) if n else None,
            "auto_confirm_precision": round(correct_auto / len(auto), 4) if auto else None,
            "high_grade_documents": len(high),
            "underclassified": len(under),
            "silent_miss": len(silent),
            "caught_by_review": len(under) - len(silent),
            "silent_miss_rate": round(len(silent) / len(high), 4) if high else None,
            "silent_miss_detail": [
                {k: r[k] for k in
                 ("doc_id", "truth", "predicted", "confidence", "status", "warnings")}
                for r in silent
            ],
            # 문서 하나당 사유 하나 - 상태 판정과 무관한 경고(persistence skipped 등)는 안 센다.
            "causal_review_reason_counts": count_causal_reasons(records),
            "unmapped_review_reasons": sum(
                1 for r in records if r.get("causal_review_reason") == UNMAPPED
            ),
            "model_grade_distribution": dict(sorted(
                Counter(str(r["model_grade"]) for r in records).items())),
            "predicted_distribution": dict(sorted(Counter(r["predicted"] for r in records).items())),
            "empty_text_documents": sum(1 for r in records if r["text_len"] == 0),
        }

    results: dict[str, dict] = {}
    for name, metadata in conditions.items():
        records, errors = run_condition(name, metadata)
        if not records:
            raise SystemExit(f"[{name}] 성공한 레코드가 0건이다 - 설정/모델을 먼저 확인하라")
        # [2026-08-23] 일부 실패는 **멈춘다**. 종전엔 실패분을 빼고 그대로 집계해서, 429 로 42건이
        # 빠진 런이 n=67 짜리 리포트로 나왔고 스윕 표에서는 정상 수치처럼 읽혔다. 평가셋 전건이
        # 안 돌았으면 그 수치는 다른 셋의 수치다.
        if errors:
            raise SystemExit(
                f"[{name}] {len(rows)}건 중 {errors}건 실패 - 부분 집계는 다른 평가셋의 수치가 된다. "
                "위 실패 줄의 사유를 먼저 해결하라"
            )
        # 모델 미로드면 model_grade 가 None 이다(schemas/classify.py:112). 그 수치는 룰 폴백이라
        # 모델 성능으로 읽으면 안 된다 - 기본은 여기서 멈춘다.
        if all(r["model_grade"] is None for r in records) and not args.allow_rule_fallback:
            raise SystemExit(
                f"[{name}] model_grade 가 전건 None = 분류기 미로드(룰 폴백). "
                "--model-dir 를 확인하라(그대로 진행하려면 --allow-rule-fallback)"
            )
        suffix = f".{name}" if len(conditions) > 1 else ""
        rec_path = out_path.with_name(out_path.stem + suffix + ".records.jsonl")
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        print(f"[wrote] {rec_path} ({len(records)}건, 오류 {errors})")
        results[name] = {"records_file": str(rec_path), "errors": errors, **summarize(records)}

    report = {
        "script": "scripts/measure_serving_records.py",
        "record_schema_version": RECORD_SCHEMA_VERSION,
        "measured_at": date.today().isoformat(),
        "purpose": args.purpose,
        "compare_to": args.compare_to or [],
        "scoring_path": "FULL serving path via in-process POST /api/v1/classify "
                        "(post-model guards INCLUDED: FNR-safe override, source-prior cap, "
                        "metadata floor, escalation tau, agreement gate)",
        "git_commit": commit,
        "git_dirty": dirty,
        "eval_set": str(eval_path),
        "eval_sha256": eval_sha,
        "eval_rows": len(rows),
        "model_dir": str(model_dir),
        "model_versions_seen": sorted(model_versions),
        "temperature_json_value": temp_value,
        "temperature_json_sha256": temp_sha,
        "settings_profile": args.profile,
        "settings_effective": {k: effective[k] for k in PARITY_KEYS},
        "settings_profile_drift": drift,
        "settings_overrides": overrides,
        "conditions": results,
        "note": "silent_miss 만이 운영상 놓친 것이다. needs_review 로 간 것은 사람이 본다. "
                "이 값을 모델 단독 FNR(score_model_on_eval)과 나란히 놓지 말 것 - 다른 경로다.",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(f"[report] {out_path}")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
