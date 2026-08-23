# 적대적 파싱 픽스처 (위험 경로 회귀 방어)

`test_e2e_parse_classify.py::TestAdversarialFixturesGateFires` 가 이 폴더의 파일을 자동
수집해, **위험한 문서가 들어오면 검수 게이트(needs_review)가 반드시 발동하는지** 검증한다.
깨끗한 문서 회귀(무오탐)는 gold 코퍼스가 커버하고, 여기는 그 반대 — *탐지 실패* 를 잠근다.

## 파일명 규칙 (접두사 → 기대되는 게이트 사유)

| 접두사 | 기대 사유(하나 이상 포함) | 대표 위험 |
|---|---|---|
| `hwp_table_*` | `table_incomplete` | HWP/HWPX 표 셀 텍스트 미추출 → 표 속 비밀 미탐 |
| `scan_*` / `ocr_*` | `ocr`, `low_quality` | 스캔본(텍스트 레이어 없음) → OCR 품질 저하 |
| `corrupt_*` | `extract_error`, `low_quality` | 손상·암호화·깨진 파일 |
| `thin_*` | `low_quality` | 본문이 얇거나 깨진 추출 |
| `empty_*` | (게이트 아님) 빈 본문 → failed 경로 | 추출 0자 |

예: `hwp_table_noori.hwp`, `scan_계약서.pdf`, `corrupt_encrypted.docx`.

## 실파일 반입 방법

발주처/협력사에서 받은 **실제 위험 문서**(특히 표 포함 HWP — noori/koipa류)를 위 규칙에
맞춰 이 폴더에 두기만 하면 다음 pytest 실행부터 자동으로 회귀 방어에 편입된다.
파일이 하나도 없으면 테스트는 skip 된다(방어망은 그대로 유지).

## corrupt_smoke.pdf

실파일 도착 전에도 게이트 발동 **메커니즘 자체가 살아있음**을 보장하는 최소 합성 자기검증
픽스처(손상 PDF). 실제 위험 케이스의 대체물이 아니라 슬롯이 조용히 썩는 것을 막는 용도.
