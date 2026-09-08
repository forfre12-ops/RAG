# -*- coding: utf-8 -*-
"""국정원 「AI 보안 가이드북」(2025.12) 부록1 체크리스트 대조 — 코드에 근거가 있는가.

왜(2026-09-08). 발주기관이 이 체크리스트를 요구하면 항목마다 "예/아니오/해당없음"을
적어야 한다. 기억으로 적으면 틀린다 - 실제로 grep 한 번에 오탐이 셋 나왔다:

    M25 취약점점검   "safety" 가 ci_check_safety_gates.py(판정 게이트)에 걸림  <- 무관
    M28 완전삭제     "삭제" 가 admin.html 의 화면 문구에 걸림                  <- 무관
    M21 비상정지     "비상" 이 seeds.py 의 키워드 시드에 걸림                  <- 무관

그래서 항목별 **이름 붙은 근거**를 두고 그것이 실재하는지 본다.

⚠ 이 도구가 내는 것은 **근거의 유무**이지 준수 판정이 아니다. 최종 판정은 발주기관이
   한다. 조직 통제(사전승인·용역업체 점검·사용자 교육)는 코드에 없는 것이 정상이며
   `scope="org"` 로 표시해 우리 소관이 아님을 분명히 한다.

사용:
    python scripts/audit_nis_ai_security.py             # 요약
    python scripts/audit_nis_ai_security.py --verbose   # 근거 위치까지
    python scripts/audit_nis_ai_security.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

try:
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

_POC = Path(__file__).resolve().parents[1]
_REPO = _POC.parent

_EXTS = {".py", ".yml", ".yaml", ".sh", ".html", ".js", ".toml", ".md", ".cfg", ".ini", ".conf"}
_SKIP_PARTS = {"__pycache__", ".venv", ".venv-gpu", "node_modules", ".git", "artifacts"}
# 체크리스트를 **다루는** 파일은 근거가 아니다. 항목 문구가 그대로 적혀 있어 빼지 않으면
# 검사기가 저 자신을 근거로 센다. 두 번 겪었다:
#   1차  M28 이 이 파일 200행("완전 삭제")을 근거로 O 가 됐다
#   2차  1차를 고친 뒤, 문서 생성기와 생성된 체크리스트 HTML 이 같은 문구를 담아 또 O 가 됐다
# 그래서 한 파일이 아니라 **이 체크리스트를 서술하는 파일 전부**를 뺀다.
_SELF_FILES = {
    Path(__file__).resolve(),
    (Path(__file__).resolve().parent / "build_nis_checklist_doc.py"),
    (Path(__file__).resolve().parents[2] / "doc" / "result"
     / "KL_AI자료_2026-08_미첨부문서" / "AI시스템_보안대책_체크리스트.html"),
}

# 근거 검색은 **문자열 그대로** 찾는다. 정규식을 쓰지 않는 이유가 있다 - 소스에서
# `\b` 가 제어문자 0x08 로 바뀌어 조용히 아무것도 안 맞은 적이 있다(2026-09-05).
Probe = tuple  # (설명, [찾을 문자열…], [뒤질 경로…])


def _iter_files(roots: list[str]):
    for root in roots:
        base = (_POC / root) if (_POC / root).exists() else (_REPO / root)
        if not base.exists():
            continue
        if base.is_file():
            if base.resolve() not in _SELF_FILES:
                yield base
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _EXTS:
                continue
            if any(part in _SKIP_PARTS for part in path.parts):
                continue
            if path.resolve() in _SELF_FILES:
                continue
            yield path


def _find(needles: list[str], roots: list[str]) -> list[str]:
    """근거 위치를 file:line 으로 돌려준다. 못 찾으면 빈 목록."""
    hits: list[str] = []
    for path in _iter_files(roots):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(n in text for n in needles):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if any(n in line for n in needles):
                rel = path.relative_to(_REPO).as_posix()
                hits.append(f"{rel}:{lineno}")
                break
        if len(hits) >= 3:      # 근거는 세 곳이면 충분하다
            break
    return hits


# ─────────────────────────────────────────────────────────────────────────────
# 부록1 가. 공통 보안대책 체크리스트 (M01~M30)
#
# scope:  code = 우리 코드에 근거가 있어야 하는 것
#         org  = 기관·원청의 조직 통제 (코드에 없는 것이 정상)
#         na   = 이 시스템에 해당하지 않는 것 (사유 필수)
# ─────────────────────────────────────────────────────────────────────────────
COMMON = [
    ("M01", "신뢰할 수 있는 출처의 데이터 활용", "code", "", [
        ("상용 LLM 반출 출처 허용목록", ["may_send_to_commercial_llm"], ["src/koipa"]),
        ("학습 행에 출처 기록", ["\"source\"", "'source'"], ["src/koipa/services/synth_coverage.py"]),
    ]),
    ("M02", "신뢰할 수 있는 출처의 AI모델 · 라이브러리 활용", "code", "", [
        ("의존성 잠금(해시 고정)", ["uv.lock"], ["poc-ci.yml", ".github"]),
        ("컨테이너 이미지 digest 고정", ["@sha256:"], ["docker-compose.airgap.mariadb.yml"]),
        ("라이선스 대장", ["dump_licenses"], ["scripts"]),
    ]),
    ("M03", "데이터 검사 (오염 · 비인가 민감정보)", "code", "", [
        ("PII 마스커", ["pii_masker"], ["src/koipa"]),
        ("합성 품질 검사", ["SYNTHETIC_QUALITY_POLICY", "_document_quality_errors"], ["src/koipa/services"]),
        ("학습셋 누출 게이트", ["grade_token_exposed", "tell_coverage"], ["src/koipa", "scripts"]),
    ]),
    ("M04", "데이터 암호화 (저장)", "code", "", [
        ("저장 암호화 어댑터 AES-256-GCM", ["EncryptingStorage"], ["src/koipa/adapters/storage"]),
    ]),
    ("M05", "데이터 접근통제", "code", "", [
        ("역할 기반 접근통제", ["X-Actor-Role"], ["src/koipa/api"]),
        ("포털 JWT 로그인(공유 키 거부)", ["requires a portal JWT", "portal JWT"], ["src/koipa"]),
    ]),
    ("M06", "민감정보 사용 사전 승인", "org",
     "기관 내부 보고 · 승인 절차. 발주기관(KL) 소관이며 코드로 대체할 수 없다.", []),
    ("M07", "보안등급에 맞는 학습데이터 구성 · 활용", "code", "", [
        ("등급 체계(TS/S1/S2/S3)", ["locked_gold_eval", "silver_train"], ["src/koipa/golden_tiers.py"]),
        ("등급별 커버리지 격자", ["coverage_report"], ["src/koipa/services/synth_coverage.py"]),
    ]),
    ("M08", "데이터 로깅 · 모니터링 (원시 · 학습데이터)", "code", "", [
        ("감사 체인(변조 탐지)", ["audit_chain"], ["src/koipa/services"]),
        ("학습 이력 적재", ["tb_training_runs"], ["src/koipa", "scripts"]),
    ]),
    ("M09", "AI시스템 로깅 · 모니터링 (입 · 출력)", "code", "", [
        ("감사 미들웨어", ["AuditMiddleware"], ["src/koipa/api"]),
        ("지표 수집", ["PrometheusMiddleware"], ["src/koipa/api"]),
    ]),
    ("M10", "데이터 수집 명세서 관리 (출처 · 일자 · 해시)", "code", "", [
        ("데이터셋 매니페스트 검사", ["check_dataset_manifest"], ["scripts"]),
        ("이관 매니페스트 sha256", ["MANIFEST_NAME", "sha256"], ["scripts/export_migration_data.py"]),
    ]),
    ("M11", "AI시스템 구성요소 명세서 관리", "code", "", [
        ("SBOM 산출", ["render_sbom_html"], ["scripts"]),
        ("릴리스 매니페스트", ["build_release_manifest"], ["scripts"]),
    ]),
    ("M12", "AI시스템 구성요소 무결성 검증", "code", "", [
        ("빌드 SHA 각인 · 대조", ["KOIPA_BUILD_SHA"], ["scripts", "docker-compose.airgap.yml"]),
        ("배포본 계약 해시", ["contract_sha256"], ["src/koipa"]),
    ]),
    ("M13", "입 · 출력 필터링", "code", "", [
        ("PII 마스킹", ["pii_masker"], ["src/koipa"]),
        ("상용 LLM 반출 차단(출처 미상 자동차단)", ["may_send_to_commercial_llm"], ["src/koipa"]),
    ]),
    ("M14", "입력 길이 · 형식 제한", "code", "", [
        ("본문 크기 상한 미들웨어", ["BodySizeLimitMiddleware"], ["src/koipa/api"]),
        ("업로드 확장자 · 크기 제한", ["max_upload", "ALLOWED_EXT", "allowed_extensions"], ["src/koipa"]),
    ]),
    ("M15", "가드레일 다중화", "code", "", [
        ("룰 + 분류기 합의 게이트", ["has_real_evidence", "agreement"], ["src/koipa"]),
        ("안전 게이트 CI", ["ci_check_safety_gates"], ["scripts", ".github"]),
    ]),
    # 문구가 "암호화**하거나** 접근권한을 통제" — 둘 중 하나면 충족(any).
    ("M16", "any:AI모델 구조 · 가중치 유출 방지", "code", "", [
        ("모델 볼륨 읽기전용 마운트", ["/models:ro"], ["docker-compose.airgap.yml"]),
        ("모델 가중치 암호화 저장", ["encrypt_model", "model_encryption"], ["src/koipa"]),
    ]),
    ("M17", "AI시스템 경계보안 강화", "code", "", [
        ("mTLS 역방향 프록시", ["nginx-mtls", "nginx.mtls.conf"], ["docker-compose.airgap.yml"]),
        ("DB 루프백 바인드", ["127.0.0.1:"], ["docker-compose.airgap.yml"]),
        ("배포 후 외부 바인드 검사", ["외부 바인드 검사"], ["scripts/verify_deploy_live.sh"]),
    ]),
    ("M18", "AI시스템 통신구간 보호", "code", "", [
        ("클라이언트 인증서 검증", ["ssl_verify_client"], ["infra/mtls"]),
        # [2026-09-08 정정] 종전 probe 는 "HttpOnly" 문자열을 찾았고, 그것이 걸린 곳은
        # _jwt_auth.py:324 의 "이 쿠키는 HttpOnly 가 아니다" 라는 **정정 주석**이었다.
        # 없는 보호를 있다고 판정해 체크리스트가 발주기관에 허위 주장을 내보냈다.
        # 실제로 쿠키에 붙는 속성만 검사한다(api/golden.py:778).
        ("쿠키 교차사이트 전송 제한", ["SameSite=Lax"], ["src/koipa"]),
        ("앱은 루프백만 - 노출은 프록시가", ["${API_BIND:-127.0.0.1}"], ["docker-compose.airgap.yml"]),
    ]),
    ("M19", "과도한 권한 부여 제한", "code", "", [
        ("역할 검사", ["X-Actor-Role"], ["src/koipa/api"]),
        ("비-root 컨테이너 uid", ["CONTAINER_UID", "USER "], ["scripts/preflight_host.sh", "Dockerfile"]),
    ]),
    ("M20", "민감 명령 승인 절차 마련", "code", "", [
        ("모델 활성 전환 관리자 승인", ["activate_model"], ["src/koipa/api/admin.py"]),
        ("등급 확정 사람 서명", ["is_valid_signoff", "human_signoff_v1"], ["src/koipa"]),
    ]),
    ("M21", "비상대응 체계 마련", "code", "", [
        ("모델 즉시 롤백", ["rollback_active_model"], ["src/koipa"]),
        ("백업 · 복구 절차", ["backup_dr"], ["scripts"]),
    ]),
    ("M22", "설명 가능한 AI 구성", "code", "", [
        ("근거 스팬 표출", ["_aggregate_evidence"], ["src/koipa/api/explain.py"]),
        ("요인별 판단 근거", ["factors"], ["src/koipa/api/explain.py"]),
    ]),
    ("M23", "AI모델 대상 적대적 모의공격 수행", "code", "", [
        ("적대 시나리오 회귀 게이트", ["eval_adversarial"], ["scripts"]),
        ("고위험 미탐 상한", ["max-high-risk-miss", "max_high_risk_miss"], ["scripts"]),
    ]),
    ("M24", "AI모델에 적대적 공격유형 학습", "code", "", [
        ("적대 케이스 학습셋 편입", ["golden_100"], ["scripts", "src/koipa"]),
    ]),
    ("M25", "구성요소 취약점 점검 및 보안업데이트", "code", "", [
        ("CI 의존성 취약점 스캔", ["pip-audit"], [".github"]),
        ("무시 목록 명시 관리", [".pip-audit-ignore"], [".github"]),
    ]),
    ("M26", "AI모델 복구", "code", "", [
        ("백업 · 복구 도구", ["backup_dr"], ["scripts"]),
        ("모델 버전 이력", ["tb_model_versions", "model_version"], ["src/koipa"]),
    ]),
    ("M27", "요청속도 제한", "code", "", [
        ("요청 속도 제한기", ["slowapi", "RateLimitExceeded"], ["src/koipa/api"]),
        ("본문 크기 상한", ["BodySizeLimitMiddleware"], ["src/koipa/api"]),
    ]),
    ("M28", "AI시스템 구성요소 완전 삭제 (폐기 시)", "code", "", [
        ("폐기 도구(암호 소거 + 구성요소 삭제)", ["crypto_erase_attested"], ["scripts/decommission.py"]),
        ("폐기 대상이 배포 볼륨과 일치", ["test_volume_list_matches_deployment_composes"], ["tests"]),
    ]),
    ("M29", "용역업체 보안관리", "org",
     "원청(KL)이 수급인(로이드케이)을 점검하는 항목. 우리가 자기점검으로 대체할 수 없다.", []),
    ("M30", "사용자 교육 및 보안정책 수립", "org",
     "기관 사용자 대상 교육 · 내부 보안정책. 발주기관 소관.", []),
]

# ── 나. 에이전틱 AI (A-M01~A-M17) · 다. 피지컬 AI (P-M01~P-M10) ──────────────
#
# 이 시스템은 문서 등급 분류기다. 도구를 자율 호출하지 않고(에이전트 아님), 구동기·
# 센서를 제어하지 않는다(피지컬 아님). 따라서 두 절은 해당없음이다.
#
# ⚠ 다만 합성문서 생성에서 상용 LLM API 를 부르는 구간이 있다. 그것은 '에이전틱'이
#   아니라 가이드북 제2장 유형②(내부 시스템의 외부 AI 연계)에 해당하며, 중점 항목
#   M09 · M13 · M14 · M24 · M27 로 위에서 이미 본다.
_AGENTIC_NA = "자율 도구호출 · 에이전트 간 통신이 없다(분류기). 외부 LLM 연계는 유형② M09/M13/M14/M24/M27 로 본다."
_PHYSICAL_NA = "구동기 · 센서 · 로봇 제어가 없다(문서 분류 서버)."

AGENTIC = [
    ("A-M01", "데이터 검사"), ("A-M02", "메모리 검사"), ("A-M03", "입 · 출력 필터링"),
    ("A-M04", "화이트리스트 기반 도구 사용"), ("A-M05", "에이전틱 AI 로깅 · 모니터링"),
    ("A-M06", "미승인 에이전트 권한 위임 차단"), ("A-M07", "AI 에이전트 자동 중단"),
    ("A-M08", "에이전트 간 악성행위 전파 차단"), ("A-M09", "AI 에이전트 목표 검증"),
    ("A-M10", "입력 · 출력 결과 검증"), ("A-M11", "과도한 권한 부여 제한"),
    ("A-M12", "민감 명령 승인 절차 마련"), ("A-M13", "민감 명령 승인 요청 임계값 설정"),
    ("A-M14", "설명 가능한 AI 구성"), ("A-M15", "AI 에이전트 신원 확인"),
    ("A-M16", "에이전틱 AI 통신구간 보호"), ("A-M17", "에이전틱 AI 구성요소 취약점 점검"),
]
PHYSICAL = [
    ("P-M01", "데이터 검사"), ("P-M02", "적대적 모의공격 수행"), ("P-M03", "적대적 공격유형 학습"),
    ("P-M04", "과도한 권한 부여 제한"), ("P-M05", "안전모드 동작"), ("P-M06", "하드웨어 보안성 강화"),
    ("P-M07", "센서 입력 범위 설정"), ("P-M08", "피지컬 AI 통신구간 보호"),
    ("P-M09", "피지컬 AI 로깅 · 모니터링"), ("P-M10", "비상대응 체계 마련"),
]


def audit() -> dict:
    results = []
    for cid, title, scope, na_reason, probes in COMMON:
        # 'any:' 접두 = 근거 하나만 있어도 충족하는 항목(체크리스트 문구가 "A하거나 B").
        any_mode = title.startswith("any:")
        title = title[4:] if any_mode else title
        entry = {"id": cid, "title": title, "scope": scope, "note": na_reason,
                 "probes": [], "any_mode": any_mode}
        for label, needles, roots in probes:
            hits = _find(list(needles), list(roots))
            entry["probes"].append({"label": label, "found": bool(hits), "where": hits})
        found = sum(1 for p in entry["probes"] if p["found"])
        entry["found"] = found
        entry["total"] = len(entry["probes"])
        if scope == "org":
            entry["verdict"] = "기관소관"
        elif entry["total"] == 0:
            entry["verdict"] = "근거없음"
        elif found == entry["total"] or (any_mode and found):
            entry["verdict"] = "근거있음"
        elif found:
            entry["verdict"] = "일부"
        else:
            entry["verdict"] = "근거없음"
        results.append(entry)

    for cid, title in AGENTIC:
        results.append({"id": cid, "title": title, "scope": "na", "verdict": "해당없음",
                        "note": _AGENTIC_NA, "probes": [], "found": 0, "total": 0})
    for cid, title in PHYSICAL:
        results.append({"id": cid, "title": title, "scope": "na", "verdict": "해당없음",
                        "note": _PHYSICAL_NA, "probes": [], "found": 0, "total": 0})
    return {"controls": results}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="국정원 AI 보안 체크리스트 근거 대조")
    ap.add_argument("--verbose", action="store_true", help="근거 위치(file:line)까지 출력")
    ap.add_argument("--json", help="결과를 JSON 으로 저장")
    args = ap.parse_args(argv)

    out = audit()
    rows = out["controls"]

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    print("국정원 「AI 보안 가이드북」(2025.12) 부록1 체크리스트 — 코드 근거 대조")
    print("=" * 78)
    for r in rows:
        if r["scope"] == "na":
            continue
        mark = {"근거있음": "O", "일부": "△", "근거없음": "X", "기관소관": "-"}[r["verdict"]]
        detail = f"{r['found']}/{r['total']}" if r["total"] else ""
        print(f"  {mark}  {r['id']:<5} {r['title']:<40s} {detail}")
        if r["note"]:
            print(f"         └ {r['note']}")
        if args.verbose:
            for p in r["probes"]:
                sign = "+" if p["found"] else "-"
                where = ("  " + ", ".join(p["where"])) if p["where"] else "  (없음)"
                print(f"         {sign} {p['label']}{where}")

    print("-" * 78)
    total_na = sum(1 for r in rows if r["scope"] == "na")
    print("  공통 M01~M30:  " + " · ".join(
        f"{k} {v}" for k, v in sorted(counts.items()) if k != "해당없음"))
    print(f"  에이전틱 · 피지컬 {total_na}개: 해당없음(분류기 - 자율 도구호출 · 구동기 제어 없음)")
    print()
    print("  이 도구가 내는 것은 **근거의 유무**다. 준수 판정은 발주기관이 한다.")

    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  JSON: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
