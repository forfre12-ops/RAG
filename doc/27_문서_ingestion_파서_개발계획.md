# 문서 Ingestion·파서 개발계획

> 최종 업데이트: 2026-05-31
> 상태: 핵심 파이프라인 구현·테스트 완료 / 업로드 API·HWP 샘플 검증 진행 중

---

## 1. 배경 및 문제 인식

### 문제 1 — Ingestion 파이프라인 통째 부재
`ClassifyService`는 이미 추출된 텍스트(`req.content`)를 받는 구조이고,
`documents` 행이 **이미 존재해야만** 저장한다.
그런데 "파일 → 추출 → 원본 보관 → `documents` 생성 → 청크"를 하는 주체가 코드에 없었다.
`extract()` / `run_file()`은 만들어만 두고 운영 코드 어디서도 호출되지 않는 상태였다.

### 문제 2 — 원본이 어디에도 안 남음
`documents` 테이블에 `source_format / file_hash / raw_text_uri / extraction_method` 칸이 설계돼 있었지만
아무도 채우지 않았다. 원본 파일 자체도 object storage에 저장되지 않았다.

### 문제 3 — 진짜 파일로 테스트한 적 없음
리포 전체에 실제 `.hwp / .pdf / .docx` 픽스처가 없었고,
HWP·PDF 테스트는 `b"not a real hwp"` 가짜 바이트로 "안 터지나"만 확인했다.

### 문제 4 — 가이드 문서와 분류 대상 문서를 혼동 가능
시스템에는 서로 다른 두 흐름이 있다.

| | 가이드 문서 | 분류 대상 문서 |
|---|---|---|
| 입구 | `POST /guide/documents` | `POST /documents` (신설) |
| 성격 | 영업비밀보호 가이드라인 (공개) | 직원이 올리는 실제 비밀문서 |
| 등급 | 없음 | TS / S1 / S2 / S3 판정 |
| 저장 | ES 지식베이스 | `documents` / `chunks` / `classifications` |

가이드 문서를 `documents` 테이블에 넣으면 도메인 오염이다.

---

## 2. 책임 경계

```
협력사:  파일 선택 UI + multipart로 API 호출 + 토큰 첨부 + 결과 표시
          ↓ 파일이 서버에 도착하면 전부 우리 책임
로이드케이: 파싱/OCR → 원본 보관 → DB 기록 → 색인 → 분류 → 재학습
            + 협력사에 줄 API 계약서 (필드·인증·에러·포맷·한도)
```

협력사가 올리는 것이 곧 실제 발주처 비밀문서이므로
**원본 보관·출처 기록은 단순 기능이 아니라 감사·증빙 요건**이다.

---

## 3. 파서 현황 (2026-05-31 기준)

| 포맷 | 라이브러리 | 라이선스 | 설치 | 실파일 테스트 |
|---|---|---|---|---|
| txt / md / csv | 내장 | — | ✅ | ✅ |
| docx | python-docx | MIT | ✅ (base) | ✅ 실파일 생성·파싱 |
| pdf (텍스트레이어) | pdfminer.six | MIT | ✅ (base) | ✅ 실파일 생성·파싱 |
| pdf (스캔본) | Tesseract OCR + pdf2image | Apache 2.0 + MIT | ✅ Tesseract, ⚠️ pdf2image·poppler 미설치 | ⏭ poppler 설치 후 |
| jpg / png / tiff | Tesseract OCR | Apache 2.0 | ✅ | ✅ 영문 인식 확인 |
| **hwp / hwpx** | rhwp-python 0.5.1 | MIT | ✅ `[hwp]` extra | ⏭ **샘플 파일 대기** |
| 스캔 HWP (이미지 내장) | Tesseract OCR | Apache 2.0 | ✅ | ⏭ 샘플 파일 대기 |

> **HWP 실파일 테스트 방법**: 한글 프로그램에서 더미 텍스트 입력 → `poc/tests/fixtures/sample.hwp` 에 저장
> → 자동으로 `TestRealFixtures::test_real_document_fixture[sample.hwp]` 가 실행됨.

---

## 4. 구현 완료 항목

### 4-1. `DocumentRepo.create()` — 신설
파일: [poc/src/lloydk/repositories/document_repo.py](../poc/src/lloydk/repositories/document_repo.py)

`documents` 행을 생성하는 메서드가 없었음 (get/delete만 존재). 신설.
provenance 필드(`source_format / file_hash / file_size_bytes / raw_text_uri /
normalized_text_uri / extraction_method / extraction_quality / ocr_used`)를 한 번에 채운다.

### 4-2. `DocumentIngestionService` — 신설
파일: [poc/src/lloydk/services/document_ingestion_service.py](../poc/src/lloydk/services/document_ingestion_service.py)

```
ingest(filename, content_bytes, tenant_id, ...)
  1. SHA-256 해시 계산
  2. 원본 bytes → object storage(RAW_BUCKET) 저장 → raw_text_uri
  3. 임시파일 경유 extract() 포맷 라우팅
  4. 정규화 + PII 마스킹 + 청크 (PreprocessPipeline.run_file)
  5. 정규화 텍스트 → object storage(NORM_BUCKET) → normalized_text_uri
  6. DocumentRepo.create() → documents 행 (processing_status=ready|failed)
  7. ChunkRepo.upsert_chunks() → chunks 테이블
  반환: IngestResult (doc_id, provenance, warnings)
```

Best-effort 패턴: storage·DB 미가용 시 예외 안 던지고 `persisted=False` + warnings.

### 4-3. extractor.py — OCR 경로 구현
파일: [poc/src/lloydk/modules/m2_preprocess/extractor.py](../poc/src/lloydk/modules/m2_preprocess/extractor.py)

- 이미지 포맷 (`jpg/png/tiff` 등) 직접 OCR 추가
- 스캔 PDF: pdfminer → 텍스트 없으면 `_ocr_pdf_pages()` (pdf2image+Tesseract) 연결
- Tesseract 경로 자동 탐지 (`C:\Program Files\Tesseract-OCR\tesseract.exe`) + `TESSERACT_CMD` 환경변수 override
- `kor+eng` 병행 인식, 설치된 언어팩 자동 확인

### 4-4. pyproject.toml — `[ocr]` extra 신설
```toml
ocr = [
  "pytesseract>=0.3.10",
  "Pillow>=10.0",
  "pdf2image>=1.16",   # poppler 별도 필요
]
```

### 4-5. 테스트 — 실제 파일 E2E
파일: [poc/tests/test_document_ingestion.py](../poc/tests/test_document_ingestion.py)

| 테스트 | 검증 내용 | 결과 |
|---|---|---|
| txt 추출·보관·청크 | 전 구간 | ✅ |
| txt 원본 무손실 복원 | raw_text_uri 로 재취득 | ✅ |
| 실 docx 파싱 | "영업비밀", "제조 공정" 무손실 | ✅ |
| 실 pdf 텍스트레이어 | "Trade Secret" 추출 | ✅ |
| 이미지 OCR | "Trade Secret ALD" 인식 | ✅ |
| documents 행 provenance | 7개 필드 1:1 검증 | ✅ |
| 미지원 포맷 graceful | 원본 보관 + 경고만 | ✅ |
| hwp / hwpx / 한국어 pdf | 샘플 있으면 자동 실행 | ⏭ skip |

---

## 5. 잔여 작업 (우선순위 순)

### P1 — 업로드 API 엔드포인트 (협력사 입구)
`POST /documents` multipart 신설. `DocumentIngestionService.ingest()` 호출.
```
Form: tenant_id, filename, doc_type, actor(JSON), external_ref
File: 실제 파일 (hwp/pdf/docx/txt, 최대 N MB)
Response: { doc_id, source_format, file_hash, chunk_count, warnings }
```
`POST /guide/documents` 와 분리 유지 (도메인 다름).

### P2 — HWP 실파일 투입
발주처·협력사로부터 더미 `.hwp` / `.hwpx` 샘플 수령
→ `poc/tests/fixtures/`에 두면 `TestRealFixtures` 자동 실행
→ rhwp 추출 품질 확인, 깨짐 있으면 pyhwp 폴백 경로 점검.

### P3 — 스캔 PDF OCR (pdf2image + poppler)
현재 Tesseract는 설치됐으나 `pdf2image`가 요구하는 **poppler** 바이너리 미설치.
```
winget install osm-poppler.poppler   # 또는 conda/chocolatey
pip install pdf2image
```
설치 후 스캔본 PDF 픽스처 추가 → `_ocr_pdf_pages()` 검증.

### P4 — OSS 시험지 출처 관리
`poc/datasets/raw/manifest.yaml` D7·D8 등재:
- `joonhok-exo-ai/korean_law_open_data_precedents` (판례)
- `nmixx-fin/synthetic_financial_report_korean` (금융보고서)
각 HF revision 해시, 수집일, 라이선스 기록 → 평가셋 재현성 보장.

### P5 — 가이드 업로드 extract() 배선
`GuideService._decode_best_effort()`를 `extract()`로 교체.
HWP 가이드를 올려도 파싱된 텍스트가 ES에 색인되도록.
저장은 `guides` 테이블 (documents와 분리).

### P6 — 협력사 API 계약서
`POST /documents` 스펙 (필드 정의, 인증, 에러코드, 허용 포맷·한도, 필수 메타) 별도 문서화.
P1 완료 후 OpenAPI spec 기반으로 작성.

---

## 6. 시스템 초기 설치 항목 (폐쇄망 포함)

```bash
# Python 의존성 (venv 내)
pip install -e ".[hwp,ocr]"

# 시스템 바이너리 (초기 1회)
winget install UB-Mannheim.TesseractOCR          # Tesseract 5.x
# kor.traineddata → C:\Program Files\Tesseract-OCR\tessdata\ 복사
winget install osm-poppler.poppler               # 스캔 PDF OCR용
```

폐쇄망에서는 위 인스톨러·traineddata·pip wheel을 오프라인 번들에 포함.

---

## 7. 테스트 실행 방법

```bash
cd poc

# 신규 ingestion 테스트만
pytest tests/test_document_ingestion.py -v

# HWP 실파일이 생기면 자동으로 추가 pass
# sample.hwp / sample.hwpx / sample_kr.pdf → tests/fixtures/ 에 넣고 재실행

# 전체 회귀
pytest -q
```
