# OSS 라이선스 보고서 — KOIPA AI 영업비밀관리시스템 (로이드케이 AI 파트)

작성일: 2026-05-27
버전: v0.9 (의존성 변경 시 갱신)
산출: 발주처 검수 산출물 / 공공사업 의무 산출물
대상: KOIPA / KL / Lloydk 내부 감사

---

## 0. 본 문서의 목적

공공사업에서 OSS(오픈소스 소프트웨어) 의존성 사용 시 다음을 명시 의무:

1. **사용 패키지 + 라이선스 + 출처 URL**
2. **공공사업·온프레미스 상용 배포 적합성 평가**
3. **GPL/AGPL/LGPL 등 카피레프트 또는 제한 라이선스 식별 + 대응**
4. **라이선스 충돌·재배포 요건·고지 의무**

본 문서는 [`poc/pyproject.toml`](../poc/pyproject.toml) 정의 의존성을 기준으로 작성합니다. transitive(전이) 의존성은 `pip-licenses`로 별도 추출 가능 (§7 참고).

---

## 1. 종합 평가 (한 줄)

> **본 시스템의 직접 의존성 전체가 공공사업·온프레미스 상용 배포에 적합한 OSS 라이선스(Apache-2.0 / MIT / BSD / MPL-2.0)로 구성됨.**
> 단, PyMuPDF가 **AGPL-3.0 / Artifex 상용** 듀얼 라이선스 → 운영망에 그대로 배포 시 AGPL 적용. 상용 사용 권장 (§3.1).

---

## 2. 라이선스 등급 정의

| 등급 | 라이선스 | 공공사업 적합 | 온프레미스 상용 |
|---|---|:---:|:---:|
| **★ 허용형 (Permissive)** | MIT / BSD / Apache-2.0 / ISC / Python-2.0 | ✅ | ✅ |
| **△ Weak Copyleft** | LGPL / MPL-2.0 | ✅ | ✅ (정적 링킹만 주의) |
| **⚠ Strong Copyleft** | GPL-2.0 / GPL-3.0 / AGPL-3.0 | ⚠ 별도 검토 | ⚠ 소스 공개 의무 |
| **❌ 사유 제한** | 상용 전용 / 평가용 / no-redistribution | ❌ | ❌ |

---

## 3. 기본 dependencies (23개)

`pyproject.toml [project].dependencies` — CI·기본 설치 시 자동 install.

### 3.1 PyMuPDF 듀얼 라이선스 주의 ⚠

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| **PyMuPDF (fitz)** | ≥ 1.24 | **AGPL-3.0 OR Artifex Commercial** | **PDF 추출 핵심**. AGPL 적용 시 우리 시스템 소스 전체 공개 의무 발생 가능. |

**대응 옵션**:
- **(a) Artifex 상용 라이선스 구매** — 발주처/Lloydk 사업비에 포함 협의 필요
- **(b) pdfminer.six (MIT) 대체** — `_extract_pdf`를 PyMuPDF → pdfminer.six로 교체, 품질·속도 약간 저하
- **(c) AGPL 그대로 채택** — 본 시스템 전체 코드 공개 (공공사업이라면 일부 정당화 가능, KL과 협의 필요)

→ 본 사업은 **온프레미스 + 기업별 배포**이므로 (a) 또는 (b) 권장. **K1 회신 시 KL과 협의 필요**.

### 3.2 허용형 라이선스 (★) — 22개

| 패키지 | 버전 | 라이선스 | 출처 |
|---|---|---|---|
| fastapi | ≥0.115 | MIT | https://github.com/fastapi/fastapi |
| uvicorn | ≥0.30 | BSD-3-Clause | https://github.com/encode/uvicorn |
| pydantic | ≥2.8 | MIT | https://github.com/pydantic/pydantic |
| pydantic-settings | ≥2.4 | MIT | https://github.com/pydantic/pydantic-settings |
| httpx | ≥0.27 | BSD-3-Clause | https://github.com/encode/httpx |
| sqlalchemy | ≥2.0 | MIT | https://github.com/sqlalchemy/sqlalchemy |
| psycopg | ≥3.2 | LGPL-3.0-or-later | https://github.com/psycopg/psycopg ⚠ (§3.3) |
| alembic | ≥1.13 | MIT | https://github.com/sqlalchemy/alembic |
| celery | ≥5.4 | BSD-3-Clause | https://github.com/celery/celery |
| redis | ≥5.0 | MIT | https://github.com/redis/redis-py |
| minio | ≥7.2 | Apache-2.0 | https://github.com/minio/minio-py |
| elasticsearch | ≥8.15,<10 | Apache-2.0 | https://github.com/elastic/elasticsearch-py |
| qdrant-client | ≥1.10 | Apache-2.0 | https://github.com/qdrant/qdrant-client |
| transformers | ≥4.44 | Apache-2.0 | https://github.com/huggingface/transformers |
| torch | ≥2.3 | BSD-3-Clause | https://github.com/pytorch/pytorch |
| accelerate | ≥0.33 | Apache-2.0 | https://github.com/huggingface/accelerate |
| datasets | ≥2.20 | Apache-2.0 | https://github.com/huggingface/datasets |
| evaluate | ≥0.4 | Apache-2.0 | https://github.com/huggingface/evaluate |
| scikit-learn | ≥1.5 | BSD-3-Clause | https://github.com/scikit-learn/scikit-learn |
| mlflow | ≥2.16 | Apache-2.0 | https://github.com/mlflow/mlflow |
| python-docx | ≥1.1 | MIT | https://github.com/python-openxml/python-docx |
| pyyaml | ≥6.0 | MIT | https://github.com/yaml/pyyaml |

### 3.3 LGPL 주의 — psycopg (△ Weak Copyleft)

| 패키지 | 라이선스 | 영향 |
|---|---|---|
| **psycopg** (3.x) | LGPL-3.0-or-later | LGPL은 **동적 링킹 시 카피레프트 미적용** → Python 패키지로 import만 하면 우리 코드는 공개 의무 없음. **현재 사용 방식(import)으로 안전**. |

→ 동적 import만 사용하므로 영향 없음. 본 시스템 소스 공개 의무 없음.

---

## 4. Optional Extras (선택 의존성)

### 4.1 `[hwp]` — HWP/HWPX 추출

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| rhwp-python | ≥0.5.1 | MIT | https://github.com/DanMeon/rhwp-python — Rust 엔진 PyO3 바인딩 |

### 4.2 `[nlp]` — 한국어 형태소

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| soynlp | ≥0.0.493 | LGPL-2.1+ | https://github.com/lovit/soynlp ⚠ 동적 import 사용 시 안전 |
| konlpy | ≥0.6 | GPL-3.0 | https://github.com/konlpy/konlpy **⚠ Strong Copyleft** |

**konlpy GPL-3.0 주의**:
- konlpy를 우리 코드에서 직접 import하면 본 시스템도 GPL 영향 가능성
- 현재 우리 코드는 konlpy를 **한 번도 import하지 않음** (오늘 분리로 확인됨)
- 향후 `[nlp]` extras 활성 + 실제 사용 시 다음 중 택1:
  - (a) konlpy 사용 부분을 **별도 프로세스/서비스로 분리** (네트워크 호출 → GPL 전파 차단)
  - (b) Mecab-ko-msvc / khaiii(Apache-2.0) 등 GPL 비종속 한국어 형태소로 교체
  - (c) 발주처와 GPL 수용 협의

→ **현재 사용 안 함, K1·Q4 회신 + 실제 한국어 분석 필요 시점에 재평가**.

### 4.3 `[embedding]` — 임베딩 가속

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| FlagEmbedding | ≥1.2 | MIT | https://github.com/FlagOpen/FlagEmbedding |
| sentence-transformers | ≥3.0 | Apache-2.0 | https://github.com/UKPLab/sentence-transformers |

### 4.4 `[llm]` — 상용 LLM SDK

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| anthropic | ≥0.39 | MIT | https://github.com/anthropics/anthropic-sdk-python |
| openai | ≥1.50 | Apache-2.0 | https://github.com/openai/openai-python |

SDK 자체는 OSS — **상용 API 호출 비용은 별개**.

### 4.5 `[orchestration]` — LangChain 계열

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| langchain | ≥0.3 | MIT | https://github.com/langchain-ai/langchain |
| langchain-anthropic | ≥0.2 | MIT | 동상 |
| langchain-openai | ≥0.2 | MIT | 동상 |
| langgraph | ≥0.2 | MIT | https://github.com/langchain-ai/langgraph |

### 4.6 `[lint]` / `[dev]`

| 패키지 | 라이선스 |
|---|---|
| openapi-spec-validator | Apache-2.0 |
| pytest | MIT |
| pytest-asyncio | Apache-2.0 |
| ruff | MIT |
| ipykernel | BSD-3-Clause |

---

## 5. 모델 가중치 라이선스

OSS 패키지와는 별개로 **모델 가중치**도 라이선스 명시 필수.

| 모델 | 출처 | 라이선스 | 상용 사용 |
|---|---|---|---|
| **KF-DeBERTa-base** | kakaobank | MIT | ✅ |
| KoELECTRA-base | monologg (구글 ETRI 기반) | Apache-2.0 | ✅ |
| **KURE-v1** | 고려대 nlpai-lab (BGE-M3 fine-tuned) | MIT | ✅ |
| BGE-M3 | BAAI | MIT | ✅ |
| ko-sroberta-multitask | jhgan | Apache-2.0 | ✅ |
| **Qwen3-14B(-Instruct-AWQ)** | Alibaba | Apache-2.0 | ✅ |
| EXAONE 3.5 32B | LG AI Research | **EXAONE License (별도)** | **⚠ Q6 회신 필요** |
| Claude Sonnet 4.6 | Anthropic | 상용 API (Anthropic ToS) | API 비용 |

**EXAONE 라이선스**: 공공사업·온프레미스 상용 배포에 사용 가능한지 [Q6 회신](06_협의요청서_KL_발주처.md) 필요 — 현재 doc/06 Q6 미회신.

---

## 6. ES 플러그인·인프라 OSS

[doc/12 폐쇄망 배포 설계](12_폐쇄망_배포_설계.md)의 자기완비 번들에 포함되는 추가 OSS.

| 컴포넌트 | 라이선스 |
|---|---|
| Elasticsearch (서버) | Elastic License 2.0 (ELv2) + SSPL (듀얼) **⚠** |
| analysis-nori 플러그인 | Apache-2.0 |
| repository-s3 플러그인 | Apache-2.0 |
| PostgreSQL 16 | PostgreSQL License (BSD-like) ✅ |
| MinIO | AGPL-3.0 **⚠** |
| Redis 7 | BSD-3-Clause + RSALv2/SSPL (7.4+) **⚠** |
| MLflow | Apache-2.0 |

**중요 주의**:

### 6.1 Elasticsearch (ELv2 + SSPL)

- ELv2: **재배포 시 ES 자체를 "managed service"로 제공 금지** — 본 사업은 KOIPA 내부 사용이라 적용 안 됨
- SSPL: **동일 — 우리는 ES를 KL에 임베디드 형태로 운영** → SSPL 적용 안 됨
- → 본 사업 사용 OK, 단 ES 자체를 SaaS화하지 않음

### 6.2 MinIO (AGPL-3.0)

- AGPL은 **네트워크를 통한 사용에도 소스 공개 의무 발생**
- **MinIO를 우리 시스템에서 API로 호출** → 우리 시스템 코드 공개 의무 가능성
- **대응**:
  - (a) MinIO Commercial License 구매 (AGPL 면제)
  - (b) AWS S3 / NHN Object Storage 등 호환 서비스로 교체 (운영망 폐쇄망이면 불가)
  - (c) 발주처와 AGPL 수용 협의 (공공사업이면 소스 공개가 오히려 부합 가능)
- → **K1 회신 시 발주처/KL과 협의 필요**. 본 사업이 KOIPA 내부 + 비공개 시스템이면 (c) 가장 현실적.

### 6.3 Redis 7.4+ (RSALv2/SSPL)

- 7.4+부터 라이선스 변경됨
- 7.2까지는 BSD-3-Clause → **본 사업은 Redis 7-alpine(BSD 시점)을 docker-compose에 고정**해두는 게 안전
- docker-compose.yml의 `redis:7-alpine`이 어느 minor 버전인지 확인 필요. 7.4+면 7.2-alpine으로 다운 고려.

---

## 7. Transitive 의존성 (전이 의존)

직접 의존성만 명시했고, transitive(예: torch가 의존하는 nvidia-cudnn 등)는 자동 install 됨. 본 보고서가 발주처 검수 대상이면 다음 명령으로 전체 추출 가능:

```bash
# 전체 의존성 + 라이선스 (현재 환경)
pip install pip-licenses
pip-licenses --format=markdown --with-urls --output-file=licenses_full.md

# 또는 SBOM (CycloneDX) — 더 표준적
pip install cyclonedx-bom
cyclonedx-py environment -o sbom.json
```

→ 운영 전환 시점에 `[full]` extras로 install한 환경에서 위 명령으로 SBOM 산출, 발주처 검수에 첨부 권장.

---

## 8. 라이선스 충돌·고지 의무

### 8.1 고지 의무 (NOTICE / attribution)

다음 라이선스는 배포 시 **저작권·라이선스 전문 동봉** 의무:

- Apache-2.0: NOTICE 파일 보존
- BSD-3-Clause: 저작권 고지 + 비보증 문구
- MIT: 저작권 고지 + 라이선스 전문

→ 폐쇄망 번들 ([doc/12 §3.1](12_폐쇄망_배포_설계.md))의 `licenses/third-party-licenses.txt`에 모두 포함 예정. 자동 추출:

```bash
pip install pip-licenses
pip-licenses --format=plain --with-license-file --no-license-path \
  --output-file=licenses/third-party-licenses.txt
```

### 8.2 충돌 매트릭스

| 조합 | 충돌? |
|---|---|
| MIT + Apache-2.0 | ✅ 호환 |
| MIT + GPL-3.0 (konlpy) | ⚠ 우리 코드가 GPL 영향 받음 — **현재 미사용** |
| Apache-2.0 + AGPL-3.0 (PyMuPDF/MinIO) | ⚠ 우리 시스템 전체 AGPL 영향 가능 — **§3.1·§6.2 대응** |
| MIT + LGPL (psycopg) | ✅ 동적 링킹 시 안전 |

---

## 9. 회신 의존 라이선스 확정 사항

KL/발주처 회신과 함께 확정해야 할 라이선스 사안:

| 항목 | 회신 변수 | 결정 사항 |
|---|---|---|
| PyMuPDF 듀얼 라이선스 | 별도 협의 | Artifex 상용 구매 vs pdfminer.six 교체 vs AGPL 수용 |
| MinIO AGPL | 별도 협의 | Commercial 구매 vs 소스 공개 협의 vs S3 호환 대체 |
| EXAONE 라이선스 | **Q6** | 공공사업 사용 가능 여부 |
| ES 라이선스 등급 | **E3** | Basic / Platinum / Enterprise (운영 라이선스 ToS 별개) |
| konlpy GPL-3.0 영향 | 사용 시점에 재평가 | 현재 미사용 |
| Redis 7.x 버전 고정 | 운영 결정 | BSD 시점(7.2)로 고정 vs RSALv2 수용 |

---

## 10. 결정 사항 요약 (v0.9)

1. **직접 의존성 23개 모두 OSS 적합** — Apache/MIT/BSD/MPL 중심
2. **PyMuPDF AGPL/Artifex 듀얼** → 운영 전환 전 상용 또는 pdfminer.six 결정 필요
3. **konlpy GPL-3.0** → 현재 미사용, 향후 사용 시 별도 프로세스 분리 또는 대체
4. **MinIO AGPL-3.0** → 발주처·KL과 소스 공개 vs 상용 라이선스 협의
5. **Redis 7-alpine** → 7.2-alpine으로 명시 고정 권장 (BSD 시점)
6. **모델 가중치**는 KF-DeBERTa·KURE-v1·BGE-M3·Qwen3 모두 MIT/Apache → ✅
7. **EXAONE Q6 회신** 시 라이선스 결정
8. **폐쇄망 번들** [doc/12 §3.1](12_폐쇄망_배포_설계.md) `licenses/third-party-licenses.txt`에 모든 NOTICE/BSD 고지 자동 포함

---

## 11. 다음 액션

### 11.1 즉시 (회신 무관)

- [ ] `docker-compose.yml`의 `redis:7-alpine`을 `redis:7.2-alpine`으로 고정 (BSD 시점 명시)
- [ ] `licenses/third-party-licenses.txt` 자동 생성 스크립트 추가 (`scripts/dump_licenses.py`)

### 11.2 회신 의존

- [ ] **Q6 EXAONE** 회신 후 §5 행 갱신
- [ ] **PyMuPDF AGPL** 발주처 협의 → §3.1 결정
- [ ] **MinIO AGPL** 발주처 협의 → §6.2 결정
- [ ] **E3 ES 라이선스 등급** 회신 후 §6.1 갱신

### 11.3 운영 전환 시점

- [ ] SBOM (CycloneDX) 산출 → 발주처 검수 첨부
- [ ] [doc/12 폐쇄망 번들](12_폐쇄망_배포_설계.md) `licenses/` 디렉터리 완성
- [ ] 본 문서 v1.0으로 확정
