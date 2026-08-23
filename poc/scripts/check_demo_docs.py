"""시연 문서가 **대본·화면에 적힌 대로** 나오는지 업로드 경로로 확인한다.

두 세트를 검사한다(`--set`):

    upload   (기본) poc/demo_formats - **실제 시연 대본이 쓰는 실업로드 세트**
    oneclick        static/demo_docs - 화면에 접어 둔 참고 샘플(짧은 예시 문서)


왜 필요한가. 등급 시연 화면(index.html)의 버튼 설명은 2026-08-21 에 223 실측으로 한 번
고쳐졌다 - 그때도 "자동확정으로 광고했는데 실제로는 검수"가 셋 중 둘이었다. 그 실측 이후
서빙 판정이 두 번 바뀌었다(2026-08-22):

    classifier_temperature 3.0 -> 2.03   confidence 가 통째로 올라간다
    agreement-gate 룰 무근거 abstain     룰이 근거 0건이면 불일치로 안 친다

두 번째는 07 번 문서(비공개 추진 메모 · 키워드 무)의 시연 대본과 정면으로 맞물린다 - 그
문서의 검수 사유가 바로 "룰이 못 잡음"이기 때문이다. 그러니 시연 전에 사람 눈으로 한 번
누르는 것으로는 부족하고, **화면 문구와 실제 판정을 기계가 대조**해야 한다.

무엇을 검사하나. 화면이 광고하는 것만 검사한다(등급 · 자동확정/검수 · 검수 사유).
확신도 수치는 검사하지 않는다 - 모델·온도가 바뀌면 움직이는 값이라 고정하면 그게 거짓말이 된다.

정책. 실측이 화면과 다르면 **문서를 손보지 않는다**(시연용 조작). 화면 문구를 사실에
맞추거나 문서를 교체한다 - index.html:229 주석이 정한 원칙 그대로다.

사용:
    # 체크아웃한 코드로(기본)
    python scripts/check_demo_docs.py --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b

    # 배포 서버로(리허설 - 시연할 그 서버에 대고 돌린다)
    python scripts/check_demo_docs.py --api http://223.130.156.134:8000 --api-key ... --repeat 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
for _p in (str(_POC / "src"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DEMO_DIR = _POC / "src" / "koipa" / "api" / "static" / "demo_docs"

# 화면(index.html:234-247)이 광고하는 내용. grade=None 이면 화면이 등급을 말하지 않는 것.
# reason 은 검수로 갈 때 화면이 대는 이유(koipa.services.review_reasons 의 태그).
DEMO_EXPECTATIONS: tuple[dict, ...] = (
    {"file": "01_TS_semiconductor_euv.docx", "grade": "TS", "status": "staging",
     "reason": None, "shown_as": "TS 특급 · 반도체 EUV - 자동확정"},
    {"file": "02_S1_recsys_source_license.docx", "grade": "S1", "status": "staging",
     "reason": None, "shown_as": "S1 1급 · 소스·라이선스 - 자동확정"},
    {"file": "04_S3_press_release.docx", "grade": "S3", "status": "staging",
     "reason": None, "shown_as": "S3 공개 · 보도자료 - 자동확정"},
    {"file": "03_S2_supplier_price.xlsx", "grade": "S2", "status": "staging",
     "reason": None, "shown_as": "S2 2급 · 납품단가표 - 자동확정"},
    # [2026-08-22] 화면에서는 뺐지만(사용자 결정) 파일과 판정은 계속 확인한다 - 이 문서가
    # 합의 게이트로 검수행인지가 abstain 범위 조정의 회귀 신호다(gate 가 풀리면 자동확정된다).
    {"file": "07_HYBRID_semantic_secret.docx", "grade": "S2", "status": "needs_review",
     "reason": "agreement-gate", "on_screen": False,
     "shown_as": "(화면 제외) 비공개 M&A 메모 - 룰 무근거지만 관리표시 있어 검수"},
    # [2026-08-23 정정] 배포본에서는 **자동확정**이다(0.949). 로컬 in-process 는 pdfplumber 가
    # 없어 표 추출이 열화로 잡혀 검수로 갔다 - 로컬 쪽이 틀린 것이다.
    {"file": "05_S1_tech_transfer.pdf", "grade": "S1", "status": "staging",
     "reason": None, "shown_as": "S1 1급 · 기술이전 PDF - 자동확정"},
    {"file": "06_FAIL_thin_text.txt", "grade": None, "status": "needs_review",
     "reason": "low-confidence", "allow_no_classification": True,
     "shown_as": "검수 · 얇은 본문 - 확신 부족"},
)

# 자동확정 문서의 confidence 가 임계에 이만큼 이내면 경고한다(실패는 아님). 03 납품단가표가
# 온도 변경 하나로 0.596 -> 0.714 가 되며 검수에서 자동확정으로 넘어왔다 - 그 폭이 0.014 다.
NEAR_THRESHOLD_MARGIN = 0.05

# [2026-08-22] **실업로드 시연 세트**(poc/demo_formats). 원클릭 버튼 대신 파일을 직접 끌어다
# 놓는 대본이라(DEMO_RUNBOOK_2026-08-23 §3-3) 이쪽이 실제 시연 대상이다. 파일명에 등급이
# 없고 같은 문서가 4포맷으로 있어 "포맷 무관 동일 판정"을 그 자리에서 보여줄 수 있다.
UPLOAD_DEMO_DIR = _POC / "demo_formats"

# ⚠ 기대값은 **배포본(223) 실측 기준**이다. 로컬 in-process 로 돌리면 PDF 두 건이 다르게
# 나온다 - 추출기 구성이 다르기 때문이다(실측 2026-08-23):
#     배포 이미지  pdfplumber 있음 · fitz 없음   → PDF 표까지 추출 → 근거 많아 확신 높음
#     로컬 venv    pdfplumber 없음 · fitz 있음   → 표 추출 실패 → extraction_gate → 검수
# 즉 **로컬 결과로 배포본을 진단하면 틀린다.** 리허설은 반드시 --api 로 배포 서버에 대고 한다.
UPLOAD_DEMO_EXPECTATIONS: tuple[dict, ...] = (
    {"file": "차세대 메모리 공정 핵심기술 검토 보고서.pdf", "grade": "TS", "status": "staging",
     "reason": None, "shown_as": "TS 최고등급 자동확정(대본 1) · 서버 0.926"},
    {"file": "차세대 메모리 공정 핵심기술 검토 보고서.docx", "grade": "TS",
     "status": "needs_review", "reason": "low-confidence",
     "shown_as": "같은 문서 docx - 등급은 같은 TS, 확신 미달로 검수(대본 2) · 서버 0.668"},
    {"file": "분기 보도자료·공시 본문 초안.docx", "grade": "S3", "status": "staging",
     "reason": None, "shown_as": "공개 보도자료 자동확정(대본 3) · 서버 0.946"},
    {"file": "분기 보도자료·공시 본문 초안.hwpx", "grade": "S3", "status": "staging",
     "reason": None, "shown_as": "같은 문서 한/글 - 같은 판정(대본 3) · 서버 0.946"},
    {"file": "핵심 알고리즘·모듈 소스 분리 보관 정책.docx", "grade": "TS",
     "status": "needs_review", "reason": "low-confidence",
     "shown_as": "의도 S1인데 TS - 안전 방향 과분류(대본 4) · 서버 0.465"},
)

_MIME = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    # [2026-08-22] 한/글 계열이 빠져 있어 demo_formats 16개 중 hwpx 4개가 조용히 건너뛰어졌다.
    # 실업로드 시연은 한/글이 핵심 포맷이라 없으면 안 된다.
    ".hwpx": "application/hwp+zip",
    ".hwp": "application/x-hwp",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="시연 문서 화면 문구 대 실제 판정 대조")
    ap.add_argument("--api", default=None,
                    help="배포 서버 base URL. 미지정 시 체크아웃한 코드를 in-process 로 태운다")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--model-dir", default=None, help="in-process 모드에서 분류기 경로")
    ap.add_argument("--profile", default="onprem-local", choices=("onprem-local", "full-train"))
    ap.add_argument("--set", dest="doc_set", default="upload", choices=("upload", "oneclick"),
                    help="검사할 세트. upload=실업로드 시연 세트(기본·demo_formats), "
                         "oneclick=화면 참고 샘플(static/demo_docs)")
    ap.add_argument("--dir", default=None,
                    help="측정할 문서 폴더(기본: --set 이 정한 폴더). --measure 와 같이 쓴다")
    ap.add_argument("--measure", action="store_true",
                    help="기대값 없이 폴더의 모든 문서를 태워 판정만 표로 낸다(실업로드 후보 선별용)")
    # [2026-08-22] ICD §3.1~3.3 문서 속성. 운영에서는 KL 포털이 이 값을 함께 보낸다
    # (documents.py:346-357 Form 필드). 본문만으로는 관리성(M)이 관측되지 않으므로
    # 같은 문서라도 이 값이 붙으면 판정이 달라진다 - 그 차이를 재려면 조건을 줄 수 있어야 한다.
    ap.add_argument("--icd-source-type", default=None,
                    help="public | registered_patent | academic | internal | external_confidential")
    ap.add_argument("--icd-security-marking", default=None,
                    help="top_secret | secret | confidential | none")
    ap.add_argument("--icd-access-scope", default=None,
                    help="approved_only | designated | department | all_employees")
    ap.add_argument("--repeat", type=int, default=1,
                    help="같은 문서를 N 회 반복해 판정이 흔들리지 않는지 본다(리허설 권장 3)")
    ap.add_argument("--out", default=None, help="결과 JSON 경로")
    ap.add_argument("--allow-drift", action="store_true")
    args = ap.parse_args(argv)

    import measure_serving_records as msr  # noqa: PLC0415 - 프로파일 정합 로직 재사용

    effective: dict = {}
    drift: dict = {}
    if args.api:
        client_desc = args.api
        import httpx  # noqa: PLC0415

        client = httpx.Client(base_url=args.api.rstrip("/"), timeout=180.0)
        api_key = args.api_key or os.environ.get("KOIPA_API_KEY") or ""
    else:
        client_desc = "in-process TestClient"
        os.environ["TESTING"] = "1"
        os.environ.setdefault("API_KEY", "check-demo-docs")
        if args.model_dir:
            os.environ["CLASSIFIER_MODEL_DIR"] = str(Path(args.model_dir).resolve())
        expected_settings = msr._profile_expected(args.profile, msr.PARITY_KEYS)
        for key in msr.PARITY_KEYS:
            os.environ[key.upper()] = msr._env_value(expected_settings[key])

        from fastapi.testclient import TestClient  # noqa: PLC0415
        from koipa.api.app import app  # noqa: PLC0415
        from koipa.config import settings  # noqa: PLC0415

        effective = {k: getattr(settings, k, None) for k in msr.PARITY_KEYS}
        drift = {k: {"expected": expected_settings[k], "effective": effective[k]}
                 for k in msr.PARITY_KEYS if effective[k] != expected_settings[k]}
        print(f"[profile] {args.profile}")
        for k in msr.PARITY_KEYS:
            print(f"    {k:32} {effective[k]!r}{'  <- 불일치' if k in drift else ''}")
        if drift and not args.allow_drift:
            print(f"[중단] 유효 설정이 프로파일과 다르다: {sorted(drift)}", file=sys.stderr)
            return 2
        client = TestClient(app)
        api_key = settings.api_key or "check-demo-docs"

    from koipa.services.review_reasons import causal_review_reason  # noqa: PLC0415

    print(f"[target] {client_desc} · 문서 {len(DEMO_EXPECTATIONS)}건 × {args.repeat}회")
    headers = {"X-API-Key": api_key} if api_key else {}

    def analyze(path: Path) -> dict:
        with path.open("rb") as fh:
            files = {"file": (path.name, fh.read(), _MIME.get(path.suffix.lower(), "application/octet-stream"))}
        form = {"return_evidence": "false"}
        for key, val in (("source_type", args.icd_source_type),
                         ("security_marking", args.icd_security_marking),
                         ("access_scope", args.icd_access_scope)):
            if val:
                form[key] = val
        r = client.post("/api/v1/documents/analyze", headers=headers, files=files, data=form)
        if r.status_code != 200:
            return {"http_error": f"{r.status_code}: {r.text[:200]}"}
        return r.json()

    # [2026-08-22] 실업로드 시연 후보를 고르려면 "이 폴더의 문서들이 지금 어떻게 나오나"를
    # 먼저 봐야 한다. 기대값 표는 원클릭 세트에만 있으므로, 다른 폴더는 측정만 한다.
    default_dir = UPLOAD_DEMO_DIR if args.doc_set == "upload" else DEMO_DIR
    default_exp = UPLOAD_DEMO_EXPECTATIONS if args.doc_set == "upload" else DEMO_EXPECTATIONS
    doc_dir = Path(args.dir) if args.dir else default_dir
    if args.measure or args.dir:
        if not doc_dir.is_dir():
            raise SystemExit(f"폴더가 없다: {doc_dir}")
        targets = [
            {"file": p.name, "grade": None, "status": None, "reason": None,
             "shown_as": "(측정 전용)", "measure_only": True}
            for p in sorted(doc_dir.iterdir())
            if p.is_file() and p.suffix.lower() in _MIME
        ]
        if not targets:
            raise SystemExit(f"태울 문서가 없다: {doc_dir} (지원 확장자 {sorted(_MIME)})")
    else:
        targets = list(default_exp)

    rows: list[dict] = []
    failures: list[str] = []
    for exp in targets:
        path = doc_dir / exp["file"]
        if not path.is_file():
            failures.append(f"{exp['file']}: 파일이 없다 ({path})")
            continue
        observations = []
        for _ in range(max(1, args.repeat)):
            j = analyze(path)
            if "http_error" in j:
                observations.append({"error": j["http_error"]})
                continue
            cls = j.get("classification")
            parse = j.get("parse") or {}
            obs = {
                "text_len": parse.get("text_length") or parse.get("chars") or len(j.get("text_preview") or ""),
                "extract_error": parse.get("extract_error"),
                "label": (cls or {}).get("label"),
                "model_grade": (cls or {}).get("model_grade"),
                "rule_grade": (cls or {}).get("rule_grade"),
                "confidence": (cls or {}).get("confidence"),
                "status": (cls or {}).get("status"),
                "decision_path": (cls or {}).get("decision_path"),
                "warnings": (cls or {}).get("warnings") or [],
            }
            obs["causal_review_reason"] = causal_review_reason(obs["warnings"], obs["status"])
            observations.append(obs)

        first = observations[0]
        unstable = any(
            (o.get("label"), o.get("status")) != (first.get("label"), first.get("status"))
            for o in observations[1:]
        )
        verdict = []
        if exp.get("measure_only"):
            if first.get("error"):
                verdict.append(f"오류: {first['error']}")
            if first.get("extract_error"):
                verdict.append(f"추출 오류: {first['extract_error']}")
            if unstable:
                verdict.append("반복 실행에서 판정이 흔들림")
            rows.append({**exp, "observed": first, "repeats": len(observations),
                         "unstable": unstable, "mismatch": verdict})
            if verdict:
                failures.append(f"{exp['file']}: " + " · ".join(verdict))
            continue
        if first.get("error"):
            verdict.append(f"오류: {first['error']}")
        else:
            no_cls = first.get("status") is None
            if no_cls and not exp.get("allow_no_classification"):
                verdict.append("분류 결과 없음")
            if not no_cls:
                if exp["grade"] and first["label"] != exp["grade"]:
                    verdict.append(f"등급 {exp['grade']} 광고 -> 실제 {first['label']}")
                if first["status"] != exp["status"]:
                    verdict.append(f"{exp['status']} 광고 -> 실제 {first['status']}")
                if exp["reason"] and first["causal_review_reason"] != exp["reason"]:
                    verdict.append(
                        f"사유 {exp['reason']} 광고 -> 실제 {first['causal_review_reason']}")
            if first.get("extract_error"):
                verdict.append(f"추출 오류: {first['extract_error']}")
        if unstable:
            verdict.append("반복 실행에서 판정이 흔들림")

        rows.append({**exp, "observed": first, "repeats": len(observations),
                     "unstable": unstable, "mismatch": verdict})
        if verdict:
            failures.append(f"{exp['file']}: " + " · ".join(verdict))

    print()
    print(f"{'문서':<36} {'광고':<22} {'실제 등급':<9} {'상태':<13} {'conf':<7} 사유")
    print("-" * 108)
    for r in rows:
        o = r["observed"]
        ad = f"{r['grade'] or '-'}/{r['status']}"
        conf = f"{o.get('confidence'):.3f}" if isinstance(o.get("confidence"), float) else "-"
        mark = "  <<< 불일치" if r["mismatch"] else ""
        print(f"{r['file']:<36} {ad:<22} {str(o.get('label')):<9} {str(o.get('status')):<13} "
              f"{conf:<7} {o.get('causal_review_reason') or '-'}{mark}")

    # 임계 근접 경고 - 실패로 치지 않되 시연 전에 알고 있어야 한다.
    threshold = float(effective.get("review_confidence_threshold") or 0.70)
    near = [
        (r["file"], r["observed"]["confidence"])
        for r in rows
        if r["observed"].get("status") == "staging"
        and isinstance(r["observed"].get("confidence"), float)
        and r["observed"]["confidence"] - threshold < NEAR_THRESHOLD_MARGIN
    ]
    if near:
        print()
        print(f"[임계 근접] 자동확정이지만 임계({threshold:.2f})에 {NEAR_THRESHOLD_MARGIN} 이내:")
        for name, conf in near:
            print(f"  - {name}: conf {conf:.3f} (여유 {conf - threshold:+.3f})"
                  " - 모델·온도가 바뀌면 검수로 넘어간다")

    print()
    if failures:
        print(f"[불일치 {len(failures)}건]")
        for f in failures:
            print(f"  - {f}")
    else:
        print(f"[일치] 문서 {len(rows)}건이 기대대로 나온다")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "script": "scripts/check_demo_docs.py",
            "measured_at": date.today().isoformat(),
            "target": client_desc,
            "git_commit": msr._git("rev-parse", "HEAD"),
            "git_dirty": bool(msr._git("status", "--porcelain")),
            "settings_effective": effective,
            "settings_profile_drift": drift,
            "repeat": args.repeat,
            "results": rows,
            "failures": failures,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"[report] {out}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
