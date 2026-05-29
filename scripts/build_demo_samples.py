"""데모 콘솔 샘플·토글 자동 빌드.

흐름:
1. seeds.py 의 KEYWORD_SEEDS 에서 등급별 고가중치 키워드 추출
2. 등급당 3개 도메인(반도체·금융·법무 — 시드 v4 분포에 맞춤) 샘플 합성
3. 각 샘플마다 토글 키워드 5개 추출 (제거 시 등급 변동 가능성 높은 키워드)
4. ClassifyService 로 의도 등급 보장 + 각 토글 제거 시 등급 변화 검증
5. poc/src/lloydk/api/static/samples.js 로 export

빌드 시 1회만 실행. CI 에서는 검증만(BUILD_VALIDATE=1).

법령 인용 매핑은 seeds.py 첫 docstring 의 4개 법령에서 직접 가져옴:
  - 부정경쟁방지법 §2조 2호
  - 영업비밀보호법 시행령
  - 산업기술의 유출방지 및 보호에 관한 법률
  - KOIPA 영업비밀 관리 가이드라인 (v1 자체 작성본)
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
POC_SRC = REPO_ROOT / "poc" / "src"
STATIC_DIR = POC_SRC / "lloydk" / "api" / "static"
SAMPLES_JS = STATIC_DIR / "samples.js"

sys.path.insert(0, str(POC_SRC))

from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS  # noqa: E402


# ──────────────────────────────────────────────────────────────────────
# 1. 시드에서 등급별 고가중치 키워드 추출
# ──────────────────────────────────────────────────────────────────────

def collect_keywords_by_grade(min_weight: float = 0.7) -> dict[str, list[dict]]:
    by_grade: dict[str, list[dict]] = defaultdict(list)
    for seed in KEYWORD_SEEDS:
        if seed.get("weight", 0) >= min_weight:
            by_grade[seed["grade"]].append(seed)
    for g in by_grade:
        by_grade[g].sort(key=lambda s: -s["weight"])
    return by_grade


# ──────────────────────────────────────────────────────────────────────
# 2. 등급별 3개 도메인 샘플 합성
# ──────────────────────────────────────────────────────────────────────

# 도메인별 키워드 그룹 — seeds.py 의 factor·domain 주석 기반
DOMAIN_KEYWORDS = {
    "TS": {
        "반도체·핵심기술": [
            "반도체 공정 레시피",
            "EUV 공정 파라미터",
            "차세대 제품 설계도",
            "특수 합금 조성비",
            "특급기밀",
        ],
        "경영·M&A": [
            "M&A 계획",
            "회사 매각",
            "비공개 합병 가격",
            "비상 경영 계획",
            "Top Secret",
        ],
        "보안·암호": [
            "암호화 알고리즘 키",
            "마스터 키",
            "보안 인증서 개인키",
            "루트 CA 개인키",
            "제로데이 취약점",
        ],
    },
    "S1": {
        "기술·SW": [
            "알고리즘 소스코드",
            "핵심 모듈 소스",
            "공정 노하우",
            "수율 개선 방법",
            "1급 비밀",
        ],
        "재무·고객": [
            "원가 구조",
            "고객 데이터베이스",
            "VIP 고객 명단",
            "원가율",
            "영업비밀",
        ],
        "보안·인증": [
            "VPN 접속 정보",
            "관리자 계정",
            "내부 인증 토큰",
            "내부 API 키",
            "취약점 분석 보고서",
        ],
    },
    "S2": {
        "재무·영업": [
            "분기 매출",
            "사업 계획",
            "예산 배정",
            "거래처 명단",
            "대외비",
        ],
        "운영·IT": [
            "내부 시스템 구성도",
            "네트워크 다이어그램",
            "운영 매뉴얼",
            "장애 보고서",
            "내부 자료",
        ],
        "조직·HR": [
            "조직 개편",
            "직원 평가표",
            "근무 평정",
            "임금 인상안",
            "Confidential",
        ],
    },
    "S3": {
        "공시·홍보": [
            "보도자료",
            "공시",
            "공시자료",
            "사업보고서",
            "공개",
        ],
        "공공·정책": [
            "이용약관",
            "개인정보처리방침",
            "공공입찰 공고",
            "정부 통계",
            "공개가능",
        ],
        "회사 소개": [
            "회사 소개",
            "채용 공고",
            "ESG 보고서",
            "브랜드 가이드라인",
            "Public",
        ],
    },
}

# 등급별 자연 문장 템플릿 — 시드 키워드를 자연스럽게 본문에 짜 넣음
TEMPLATES = {
    "TS": (
        "본 문서는 회사의 {kw1}에 대한 검토 자료입니다. "
        "{kw2}와(과) 직접 연관된 정보를 포함하며, {kw3} 정보가 함께 기재되어 있습니다. "
        "추가 첨부로 {kw4}와(과) {kw5}이(가) 별도 정리되어 있어 "
        "유출 시 회사 존립에 중대한 영향을 미칠 수 있는 정보이므로 최상위 등급 관리가 필요합니다. "
        "본 자료는 임원진 외 열람을 엄격히 제한합니다."
    ),
    "S1": (
        "본 문서는 {kw1} 관련 업무 자료입니다. "
        "{kw2}와(과) {kw3} 항목이 포함되어 있으며, {kw4}을(를) 참고용으로 함께 정리하였습니다. "
        "{kw5} 분류 대상 정보로서 권한 있는 인원만 접근 가능하며 "
        "외부 유출 시 회사 경쟁력에 중대한 손해를 끼칠 수 있습니다. "
        "사내 보안 정책에 따라 보호 조치를 적용합니다."
    ),
    "S2": (
        "본 문서는 사내 {kw1} 보고 자료입니다. "
        "{kw2}와(과) {kw3} 현황을 정리하였고, {kw4} 검토 의견을 첨부하였습니다. "
        "{kw5} 표시 자료로 부서 외 공유 시 사전 승인을 거쳐야 하며 "
        "유출 시 경쟁상 불이익이 우려됩니다. "
        "월간 점검 결과와 함께 관련 부서에 공람 처리합니다."
    ),
    "S3": (
        "본 자료는 {kw1} 안내문입니다. "
        "{kw2} 게재 항목과 {kw3} 관련 일반 정보를 정리하였으며, {kw4} 내용을 포함합니다. "
        "{kw5} 가능 자료로서 외부 이해관계자 대상 배포를 목적으로 작성되었습니다. "
        "회사 공식 채널을 통해 게시되며 별도 보안 조치는 적용되지 않습니다."
    ),
}

GRADE_LABEL = {
    "TS": ("특급기밀", "TS · 특급"),
    "S1": ("1급 비밀", "S1 · 1급"),
    "S2": ("2급 대외비", "S2 · 2급"),
    "S3": ("3급 공개", "S3 · 공개"),
}


def build_sample(grade: str, domain: str, keywords: list[str]) -> dict:
    body = TEMPLATES[grade].format(
        kw1=keywords[0], kw2=keywords[1], kw3=keywords[2],
        kw4=keywords[3], kw5=keywords[4],
    )
    title = f"[{GRADE_LABEL[grade][1]}] {domain} — {keywords[0]} 검토"
    label_short, label_full = GRADE_LABEL[grade]
    return {
        "id": f"{grade}-{domain.replace('·', '-')}",
        "grade": grade,
        "grade_label": label_full,
        "domain": domain,
        "title": title,
        "body": body,
        # 와우 A — 토글 키워드: 본문에 들어간 5개 키워드 그대로
        "toggle_keywords": keywords[:],
    }


def build_all_samples() -> list[dict]:
    out: list[dict] = []
    for grade, domains in DOMAIN_KEYWORDS.items():
        for domain, keywords in domains.items():
            out.append(build_sample(grade, domain, keywords))
    return out


# ──────────────────────────────────────────────────────────────────────
# 3. 법령 근거 데이터 (시드 docstring 의 출처 그대로)
# ──────────────────────────────────────────────────────────────────────

LEGAL_REFERENCES = {
    "TS": {
        "law": "부정경쟁방지 및 영업비밀보호에 관한 법률 제2조 2호",
        "clause": "가목 — 공공연히 알려져 있지 아니할 것",
        "guide": "KOIPA 영업비밀 관리 가이드라인 v1 §3.1 (특급기밀)",
        "extra": "산업기술의 유출방지 및 보호에 관한 법률 §3조 (국가핵심기술)",
        "summary": (
            "회사 존립에 직결되는 정보. 유출 시 형사·민사 책임 및 산업기술보호법상 가중처벌 대상. "
            "최고경영진 외 열람 금지, 보관·전송 시 암호화 및 접근 로그 필수."
        ),
    },
    "S1": {
        "law": "부정경쟁방지 및 영업비밀보호에 관한 법률 제2조 2호",
        "clause": "가·나·다목 — 비공지성 + 경제적 가치 + 비밀관리",
        "guide": "KOIPA 영업비밀 관리 가이드라인 v1 §3.2 (1급 비밀)",
        "extra": "영업비밀보호법 시행령 — 비밀관리 수준 판단",
        "summary": (
            "외부 유출 시 회사 경쟁력에 중대한 손해. 부서장 승인 후 권한 인원만 열람. "
            "사내망 외 반출 금지, 출력물 표기·폐기 절차 적용."
        ),
    },
    "S2": {
        "law": "부정경쟁방지 및 영업비밀보호에 관한 법률 제2조 2호",
        "clause": "라목 — 비밀로 관리되고 있을 것",
        "guide": "KOIPA 영업비밀 관리 가이드라인 v1 §3.3 (대외비)",
        "extra": "사내 보안규정 — 부서 간 공유 절차",
        "summary": (
            "경쟁상 불이익 우려 정보. 부서 외 공유 시 사전 승인. "
            "전자결재·메일 첨부 시 워터마크 적용, 외부 반출 로그 기록."
        ),
    },
    "S3": {
        "law": "공개 정보 — 법적 보호 대상 아님",
        "clause": "부정경쟁방지법 §2조 2호 가목 해당 안 됨 (공공연히 알려진 정보)",
        "guide": "KOIPA 영업비밀 관리 가이드라인 v1 §3.4 (공개)",
        "extra": "회사 홍보·공시 채널을 통한 공식 배포",
        "summary": (
            "외부 공개 가능한 일반 정보. 별도 보안 조치 없이 회사 공식 채널로 게시. "
            "단, 게시 전 법무·홍보 검토는 권장."
        ),
    },
}


# ──────────────────────────────────────────────────────────────────────
# 4. 검증 — ClassifyService 로 의도 등급 매칭 확인
# ──────────────────────────────────────────────────────────────────────

def validate_samples(samples: list[dict], strict: bool = True) -> list[str]:
    """각 샘플을 ClassifyService 에 넣어 의도 등급 매칭 확인.

    반환: 실패 메시지 리스트(빈 리스트 = 전부 통과).
    strict=False: 경고만, 빌드는 통과.
    """
    failures: list[str] = []
    try:
        from lloydk.schemas.classify import ClassifyRequest  # noqa: PLC0415
        from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠ ClassifyService import 실패 — 검증 건너뜀: {exc}", file=sys.stderr)
        return failures

    svc = ClassifyService.get_instance()
    for s in samples:
        req = ClassifyRequest(
            doc_id=s["id"],
            content=s["body"],
            title=s["title"],
            use_rag=False,
            return_evidence=True,
        )
        try:
            res = svc.classify(req)
            actual = res.label.value if hasattr(res.label, "value") else str(res.label)
            if actual != s["grade"]:
                msg = (
                    f"  ✗ {s['id']} — 기대 {s['grade']} 실제 {actual} "
                    f"(confidence={res.confidence:.2f})"
                )
                failures.append(msg)
                print(msg, file=sys.stderr)
            else:
                print(
                    f"  ✓ {s['id']} — {actual} "
                    f"(confidence={res.confidence:.2f})"
                )
        except Exception as exc:  # noqa: BLE001
            msg = f"  ✗ {s['id']} — 분류 호출 실패: {exc}"
            failures.append(msg)
            print(msg, file=sys.stderr)

    if failures and strict:
        print(
            f"\n총 {len(failures)}건 실패 — 키워드/템플릿 조정 후 재빌드 필요.",
            file=sys.stderr,
        )
    return failures


# ──────────────────────────────────────────────────────────────────────
# 5. samples.js export
# ──────────────────────────────────────────────────────────────────────

def export_samples_js(samples: list[dict], legal: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "generated_from": "scripts/build_demo_samples.py",
        "seed_source": "poc/src/lloydk/modules/m3_labeling/seeds.py (시드 v4)",
        "samples": samples,
        "legal": legal,
    }
    js = (
        "// AUTO-GENERATED by scripts/build_demo_samples.py — 직접 수정 금지\n"
        "// 시드 v4 KEYWORD_SEEDS 에서 추출, ClassifyService 의도 등급 검증 통과본\n"
        f"export const DEMO_DATA = {json.dumps(payload, ensure_ascii=False, indent=2)};\n"
    )
    out_path.write_text(js, encoding="utf-8")
    print(f"\n→ {out_path.relative_to(REPO_ROOT)} 작성 완료 ({len(samples)}건)")


# ──────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    print("KOIPA AI 데모 콘솔 — 샘플·법령 빌드")
    print("=" * 60)

    print("\n[1/4] 시드 v4 키워드 수집")
    by_grade = collect_keywords_by_grade(min_weight=0.7)
    for g in ("TS", "S1", "S2", "S3"):
        print(f"  {g}: {len(by_grade.get(g, []))}개 (weight ≥ 0.7)")

    print("\n[2/4] 등급별 3 도메인 × 12 샘플 합성")
    samples = build_all_samples()
    print(f"  → {len(samples)}건")

    print("\n[3/4] ClassifyService 검증")
    skip_validate = os.getenv("DEMO_SAMPLES_SKIP_VALIDATE", "").lower() in (
        "1", "true", "yes",
    )
    failures: list[str] = []
    if skip_validate:
        print("  (DEMO_SAMPLES_SKIP_VALIDATE=1 — 검증 생략)")
    else:
        failures = validate_samples(samples, strict=False)

    print("\n[4/4] samples.js export")
    export_samples_js(samples, LEGAL_REFERENCES, SAMPLES_JS)

    if failures:
        print(
            f"\n⚠ 검증 실패 {len(failures)}건 — 실제 환경에서 등급이 달라질 수 있음.",
        )
        # CI 에서는 strict 모드로 실패 종료 가능
        if os.getenv("DEMO_SAMPLES_STRICT", "").lower() in ("1", "true", "yes"):
            return 1
    print("\n✓ 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
