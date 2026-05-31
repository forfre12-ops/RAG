# tests/fixtures — 실제 문서 샘플

`test_document_ingestion.py::TestRealFixtures`가 여기 있는 **실제 파일**로 ingestion을 검증한다.
HWP는 독점 바이너리라 합성으로 만들 수 없으므로, **발주처/협력사가 제공한 실제 문서**를 넣어야 한다.

## 넣어야 할 파일 (있으면 자동 검증, 없으면 skip)

| 파일명 | 용도 |
|---|---|
| `sample.hwp` | HWP5 바이너리 추출 검증 (rhwp) |
| `sample.hwpx` | HWPX(ZIP+XML) 추출 검증 (rhwp) |
| `sample_kr.pdf` | 한국어 PDF 텍스트 레이어 추출 검증 (pdfminer) |

## 주의
- **실제 영업비밀 문서를 넣지 말 것.** 무해한 공개/더미 내용의 샘플만.
- 파일은 작게(수십 KB). 대용량은 별도 성능 테스트에서.
- 스캔본(텍스트 레이어 없는 PDF/이미지 HWP)은 OCR 엔진 도입 후 별도 fixture로 추가.
