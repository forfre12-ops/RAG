"""
공개 데이터 시드 수집 스크립트 (D2/D4/D6).

- D4 국가법령정보센터: 영업비밀보호법 등 (수동 다운로드 가이드만 제공)
- D2 공공데이터 포털: 정부공문서 샘플 (수동)
- D6 DART: 공시 자료 수집 (open API 활용)

본 스크립트는 디렉토리 생성 + DART 일부 자동화. 나머지는 URL/절차 안내.

사용:
  python scripts/collect_public_data.py
"""
from pathlib import Path
import textwrap

ROOT = Path(__file__).parent.parent / "datasets" / "raw"

SOURCES = {
    "law_info_center": {
        "url": "https://www.law.go.kr/법령/영업비밀보호및부정경쟁방지에관한법률",
        "note": "영업비밀보호법, 부정경쟁방지법 본문. 수동 다운로드 후 PDF/HWP를 이 폴더에.",
        "license": "공공누리 1유형",
        "usage": "RAG 시드 + 가이드 매핑",
    },
    "kipra_guides": {
        "url": "https://www.kipra.or.kr/ (영업비밀보호센터 자료실)",
        "note": "발주처(보호원) 측에서 최신본 수령 필요. 본 PoC는 수령 즉시 이 폴더에 적재.",
        "license": "발주처 공식 자료",
        "usage": "라벨링 규칙 YAML 산정 기준 + RAG 1순위 시드",
    },
    "public_data_portal": {
        "url": "https://www.data.go.kr/",
        "note": "공문서 표준 서식, 행정용어 사전 등 검색 후 다운로드.",
        "license": "공공누리 1유형",
        "usage": "S3 라벨 후보 + 도메인 어휘",
    },
    "dart_filings": {
        "url": "https://opendart.fss.or.kr/ (API 키 필요)",
        "note": "상장기업 사업보고서 일부 추출. 공개 자료라 S3 라벨 후보.",
        "license": "DART 이용약관 (재배포 제한 — 추출 텍스트만 사용)",
        "usage": "S3 공개 자료 라벨",
    },
    "aihub_admin_docs": {
        "url": "https://aihub.or.kr/ (행정문서 OCR 데이터셋)",
        "note": "사전 신청 승인 필요 (사업단 명의 권장, 1~2주 소요).",
        "license": "AI Hub 이용약관",
        "usage": "도메인 어휘 학습 + S2/S3 라벨 후보",
    },
}


def main():
    print("=" * 70)
    print("KIPRA AI 영업비밀 PoC — 공개 데이터 수집 안내")
    print("=" * 70)
    for key, info in SOURCES.items():
        d = ROOT / key
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.md"
        body = textwrap.dedent(f"""
            # {key}

            - **URL**: {info['url']}
            - **라이선스**: {info['license']}
            - **용도**: {info['usage']}

            ## 수집 절차
            {info['note']}

            ## 수집 후
            - 원본 파일을 본 폴더에 그대로 적재
            - 파일명에 출처·날짜 명시 권장
            - `scripts/p4_extract_eval.py --in datasets/raw/{key}` 로 추출 품질 평가
        """).strip()
        readme.write_text(body, encoding="utf-8")
        print(f"  [{key}] 생성 → {d}")
        print(f"    └─ {info['note']}")
    print("\n완료. 각 폴더 README.md 참고 후 수동 수집 진행.")


if __name__ == "__main__":
    main()
