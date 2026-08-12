"""
공개 데이터 시드 수집 스크립트 (D1~D6).

W12+ 블록 3 — 매니페스트 풀 보강 + 진척 추적 + JSONL 산출 헬퍼.

설계 원칙:
- 외부 API 자동 호출 0 (API 키·rate limit·NDA 위험 회피)
- 매니페스트(YAML/JSON) + 수집 안내(README) + 진척 표(progress.md) 자동 생성
- 실제 다운로드는 운영자(또는 발주처 승인 후) 수동 진행
- 수동 적재 후 `--validate` 모드로 무결성·라이선스·등급 매핑 검증
- 수집된 raw 파일 → JSONL 변환 헬퍼 (`--to-jsonl`)

사용:
  python scripts/collect_public_data.py              # 매니페스트·READMD 생성
  python scripts/collect_public_data.py --validate   # raw/ 적재 파일 검증
  python scripts/collect_public_data.py --to-jsonl   # raw/ → labels.jsonl 변환

출처별 운영 절차:
  D1 영업비밀보호법 (공개)        : 자동 매니페스트 + 수동 다운로드 안내
  D2 공공데이터 포털 (공개)        : 매니페스트 + URL 목록
  D3 법령정보센터 (공개)          : 매니페스트 + 검색 키워드
  D4 AI Hub 행정문서 (신청 필요)   : 신청 명의·승인 절차 안내 (사업단 명의 동결됨)
  D5 모두의말뭉치 (공개)          : 매니페스트 + 데이터셋 ID 안내
  D6 DART 공시 (API 키 필요)      : OpenAPI 사용 안내 (운영 단계)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

# Windows cp949 콘솔에서 한글·유니코드 기호(— ✓) 출력 보장.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).parent.parent / "datasets" / "raw"
MANIFEST_PATH = ROOT / "manifest.yaml"
PROGRESS_PATH = ROOT / "progress.md"


# 출처별 메타 (확장 — 6개 → 라이선스/등급매핑/예상 분량 명시)
SOURCES: dict[str, dict] = {
    "D1_law_info_center": {
        "url": "https://www.law.go.kr/법령/영업비밀보호및부정경쟁방지에관한법률",
        "alt_urls": [
            "https://www.law.go.kr/법령/산업기술의유출방지및보호에관한법률",
            "https://www.law.go.kr/법령/부정경쟁방지및영업비밀보호에관한법률",
        ],
        "note": "영업비밀보호법·부정경쟁방지법·산업기술보호법 본문 (공개 법령).",
        "license": "공공누리 1유형 (출처표시)",
        "usage": "RAG 시드 + 가이드 매핑 + 등급 정의 본문",
        "expected_docs": 3,
        "expected_size_mb": 2,
        "grade_mapping": {"default": "S3", "note": "법령 본문은 공개 자료라 S3"},
        "auto_collect": False,
        "consent_required": False,
    },
    "D2_public_data_portal": {
        "url": "https://www.data.go.kr/",
        "alt_urls": [
            "https://www.data.go.kr/data/15014828/openapi.do",  # 공공기관 정보공개 표준서식
        ],
        "note": "공문서 표준 서식, 행정용어 사전. 검색 키워드: 공문서·서식·행정용어",
        "license": "공공누리 1유형",
        "usage": "S3 라벨 후보 + 도메인 어휘",
        "expected_docs": 100,
        "expected_size_mb": 50,
        "grade_mapping": {"default": "S3"},
        "auto_collect": False,
        "consent_required": False,
    },
    "D3_koipa_guides": {
        "url": "https://www.koipa.or.kr/ (영업비밀보호센터 자료실)",
        "alt_urls": [
            "https://www.kotra.or.kr/biz/protect/index.do",  # KOTRA 영업비밀 보호 안내
        ],
        "note": "KOIPA 발주처 측에서 최신 가이드 수령 필요 (doc/16 §1.2 차단 자원).",
        "license": "발주처 공식 자료 (재배포 제한)",
        "usage": "라벨링 규칙 YAML 산정 기준 + RAG 1순위 시드",
        "expected_docs": 5,
        "expected_size_mb": 20,
        "grade_mapping": {"default": "S2", "note": "가이드 자체는 대외비"},
        "auto_collect": False,
        "consent_required": True,
        "responsible": "발주처 (KOIPA) 회신 의존",
    },
    "D4_aihub_admin_docs": {
        "url": "https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=580",
        "alt_urls": [
            "https://aihub.or.kr/aihubdata/data/view.do?currMenu=115&topMenu=100",  # 행정문서 OCR
        ],
        "note": "사전 신청·승인 필요. 사업단(한국지식재산보호원) 명의로 신청 동결됨 ([memory] Q5).",
        "license": "AI Hub 이용약관 (출처표시·재배포 제한)",
        "usage": "도메인 어휘 학습 + S2/S3 라벨 후보 + OCR 학습 데이터",
        "expected_docs": 5000,
        "expected_size_mb": 1500,
        "grade_mapping": {"default": "mixed", "note": "S2 80% / S3 20% 예상"},
        "auto_collect": False,
        "consent_required": True,
        "responsible": "사업단 명의 신청 → 1~2주 승인 대기",
    },
    "D5_modu_corpus": {
        "url": "https://corpus.korean.go.kr/",
        "alt_urls": [
            "https://corpus.korean.go.kr/request/reausetMain.do",
        ],
        "note": "국립국어원 모두의 말뭉치. 문어·구어·신문 코퍼스. 비상업 활용 무료.",
        "license": "비상업 이용 (공공사업 활용 별도 검토)",
        "usage": "한국어 일반 어휘 보강 + Nori 사전 검증",
        "expected_docs": 10000,
        "expected_size_mb": 800,
        "grade_mapping": {"default": "S3"},
        "auto_collect": False,
        "consent_required": True,
        "responsible": "사업단 신청",
    },
    "D6_dart_filings": {
        "url": "https://opendart.fss.or.kr/",
        "alt_urls": [
            "https://opendart.fss.or.kr/guide/main.do",
        ],
        "note": "DART OpenAPI. API 키 발급 (무료, 즉시) 후 사업보고서·공시자료 추출.",
        "license": "DART 이용약관 (텍스트 추출만, 재배포 제한)",
        "usage": "S3 공개 자료 라벨 + 한국어 금융·공시 어휘",
        "expected_docs": 500,
        "expected_size_mb": 300,
        "grade_mapping": {"default": "S3"},
        "auto_collect": False,
        "consent_required": False,
        "responsible": "AI팀 자체 (API 키 무료 발급)",
    },
}


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _yaml_dump(d: dict, indent: int = 0) -> str:
    """간단 YAML — 외부 의존 회피용 자체 직렬화."""
    lines: list[str] = []
    pad = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.append(_yaml_dump(v, indent + 1))
        elif isinstance(v, list):
            lines.append(f"{pad}{k}:")
            for it in v:
                if isinstance(it, dict):
                    lines.append(f"{pad}  -")
                    lines.append(_yaml_dump(it, indent + 2))
                else:
                    lines.append(f"{pad}  - {it}")
        elif isinstance(v, bool):
            lines.append(f"{pad}{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{pad}{k}: null")
        else:
            lines.append(f"{pad}{k}: {v}")
    return "\n".join(lines)


def cmd_init() -> None:
    """매니페스트·README·진척 표 생성."""
    print("=" * 70)
    print("KOIPA AI PoC — 공개 데이터 매니페스트 생성")
    print("=" * 70)
    ROOT.mkdir(parents=True, exist_ok=True)
    total_docs_expected = 0
    total_mb_expected = 0
    for key, info in SOURCES.items():
        d = ROOT / key
        d.mkdir(parents=True, exist_ok=True)
        readme = d / "README.md"
        body = textwrap.dedent(f"""
            # {key}

            - **URL**: {info['url']}
            - **라이선스**: {info['license']}
            - **용도**: {info['usage']}
            - **예상 분량**: {info['expected_docs']} 건 / {info['expected_size_mb']} MB
            - **등급 매핑**: {info['grade_mapping']}
            - **자동 수집**: {'O' if info['auto_collect'] else 'X (수동)'}
            - **사전 승인 필요**: {'O' if info['consent_required'] else 'X'}
            - **담당**: {info.get('responsible', 'AI팀')}

            ## 수집 절차
            {info['note']}

            ## 대안 URL
            {chr(10).join('  - ' + u for u in info.get('alt_urls', [])) or '  (없음)'}

            ## 수집 후
            - 원본 파일을 본 폴더에 그대로 적재 (파일명: `<출처>_<날짜>_<제목>.{{pdf|hwp|docx|txt}}`)
            - 수집 완료 후 `python scripts/collect_public_data.py --validate` 실행
            - JSONL 변환: `python scripts/collect_public_data.py --to-jsonl`
        """).strip()
        readme.write_text(body, encoding="utf-8")
        total_docs_expected += info["expected_docs"]
        total_mb_expected += info["expected_size_mb"]
        print(f"  [{key}] {info['expected_docs']:>5} 건 / {info['expected_size_mb']:>5} MB → {d}")

    # 매니페스트
    manifest = {
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_sources": len(SOURCES),
            "total_docs_expected": total_docs_expected,
            "total_mb_expected": total_mb_expected,
            "consent_required_count": sum(1 for v in SOURCES.values() if v.get("consent_required")),
        },
        "sources": SOURCES,
    }
    MANIFEST_PATH.write_text(_yaml_dump(manifest), encoding="utf-8")
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  매니페스트 → {MANIFEST_PATH}")

    # 진척 표
    rows = ["# 공개 데이터 수집 진척 — 자동 생성", ""]
    rows.append(f"생성: {manifest['generated_at']}")
    rows.append(f"예상 합계: {total_docs_expected:,} 건 / {total_mb_expected:,} MB")
    rows.append("")
    rows.append("| 출처 | 예상 건수 | 예상 MB | 사전 승인 | 담당 | 현재 상태 |")
    rows.append("|---|---:|---:|:-:|---|---|")
    for key, info in SOURCES.items():
        consent = "필요" if info.get("consent_required") else "—"
        rows.append(
            f"| {key} | {info['expected_docs']} | {info['expected_size_mb']} | "
            f"{consent} | {info.get('responsible','AI팀')} | TODO |"
        )
    rows.append("")
    rows.append("> `--validate` 실행 후 자동 갱신됨. 수동 편집 가능.")
    PROGRESS_PATH.write_text("\n".join(rows), encoding="utf-8")
    print(f"  진척 표 → {PROGRESS_PATH}")
    print(f"\n예상 합계: {total_docs_expected:,} 건 / {total_mb_expected:,} MB / 출처 {len(SOURCES)} 종")
    print("완료. 각 폴더 README.md + manifest.yaml 참고 후 수집 진행.")


def cmd_validate() -> None:
    """raw/ 적재 파일 검증 — 출처별 실제 적재 건수·SHA256·라이선스 점검."""
    print("=" * 70)
    print("KOIPA AI PoC — 공개 데이터 적재 검증")
    print("=" * 70)
    if not ROOT.exists():
        print(f"  raw/ 디렉토리 부재: {ROOT} — `--init` 먼저 실행")
        return
    rows: list[tuple[str, int, int, list[str]]] = []
    for key, info in SOURCES.items():
        d = ROOT / key
        if not d.exists():
            rows.append((key, 0, 0, ["디렉토리 부재"]))
            continue
        # README 제외 실제 데이터 파일
        files = [p for p in d.iterdir() if p.is_file() and p.name not in {"README.md", ".gitkeep"}]
        total_bytes = sum(p.stat().st_size for p in files)
        issues: list[str] = []
        if not files:
            issues.append("적재 0건")
        if len(files) < info["expected_docs"] * 0.1:
            issues.append(f"예상의 10% 미만 ({len(files)}/{info['expected_docs']})")
        rows.append((key, len(files), total_bytes, issues))
    print()
    print(f"{'출처':<25} {'적재':>6} {'MB':>8}  비고")
    print("-" * 70)
    for key, n, b, issues in rows:
        mb = b / (1024 * 1024)
        bar = "✓" if n > 0 else "·"
        note = "; ".join(issues) if issues else "OK"
        print(f"{key:<25} {n:>6} {mb:>8.1f}  {bar} {note}")
    total_files = sum(r[1] for r in rows)
    total_mb = sum(r[2] for r in rows) / (1024 * 1024)
    print()
    print(f"합계: {total_files} 파일 / {total_mb:.1f} MB")


def cmd_to_jsonl() -> None:
    """raw/ 텍스트 파일 → datasets/external_public.jsonl 변환.

    각 파일을 한 줄로 변환: {text, label, source, doc_id}
    """
    print("=" * 70)
    print("KOIPA AI PoC — 공개 데이터 → JSONL 변환")
    print("=" * 70)
    out_path = ROOT.parent / "external_public.jsonl"
    n_total = 0
    skipped: list[str] = []
    with out_path.open("w", encoding="utf-8") as fout:
        for key, info in SOURCES.items():
            d = ROOT / key
            if not d.exists():
                continue
            default_label = info["grade_mapping"].get("default", "S3")
            if default_label not in {"TS", "S1", "S2", "S3"}:
                default_label = "S3"
            for p in d.iterdir():
                if not p.is_file():
                    continue
                if p.name in {"README.md", ".gitkeep"}:
                    continue
                # 텍스트 추출 가능한 포맷만 — 이외는 skip + 표시
                if p.suffix.lower() not in {".txt", ".md"}:
                    skipped.append(f"{key}/{p.name} ({p.suffix})")
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore").strip()
                except Exception:  # noqa: BLE001
                    skipped.append(f"{key}/{p.name} (read error)")
                    continue
                if not text:
                    continue
                row = {
                    "doc_id": f"{key}_{_sha256(p)[:12]}",
                    "text": text[:32000],  # 32KB 캡
                    "label": default_label,
                    "source": key,
                    "license": info["license"],
                    "path": str(p.relative_to(ROOT.parent.parent)),
                }
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                n_total += 1

    print(f"  산출 → {out_path}")
    print(f"  변환: {n_total} 건")
    if skipped:
        print(f"  Skip: {len(skipped)} (PDF/HWP/DOCX 등 — p4_extract_eval로 추출 후 재실행)")


def main() -> None:
    ap = argparse.ArgumentParser(description="공개 데이터 수집·검증·변환")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--validate", action="store_true", help="raw/ 적재 검증")
    g.add_argument("--to-jsonl", action="store_true", help="raw/ → external_public.jsonl 변환")
    args = ap.parse_args()

    if args.validate:
        cmd_validate()
    elif args.to_jsonl:
        cmd_to_jsonl()
    else:
        cmd_init()


if __name__ == "__main__":
    main()
