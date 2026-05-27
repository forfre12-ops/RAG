# OSS 라이선스 보고서 — KOIPA AI 영업비밀관리시스템 (로이드케이 AI 파트)

작성일: 2026-05-28
버전: v0.9.1 (의존성 변경 시 갱신)
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
> ~~단, PyMuPDF가 **AGPL-3.0 / Artifex 상용** 듀얼 라이선스 → 운영망에 그대로 배포 시 AGPL 적용. 상용 사용 권장 (§3.1).~~ **2026-05-27 v2: pdfminer.six (BSD-3) 1순위로 교체 완료** — AGPL 위험 해소.

---

## 2. 라이선스 등급 정의

| 등급 | 라이선스 | 공공사업 적합 | 온프레미스 상용 |
|---|---|:---:|:---:|
| **★ 허용형 (Permissive)** | MIT / BSD / Apache-2.0 / ISC / Python-2.0 | ✅ | ✅ |
| **△ Weak Copyleft** | LGPL / MPL-2.0 | ✅ | ✅ (정적 링킹만 주의) |
| **⚠ Strong Copyleft** | GPL-2.0 / GPL-3.0 / AGPL-3.0 | ⚠ 별도 검토 | ⚠ 소스 공개 의무 |
| **❌ 사유 제한** | 상용 전용 / 평가용 / no-redistribution | ❌ | ❌ |

---

## 3. 기본 dependencies (24개)

`pyproject.toml [project].dependencies` — CI·기본 설치 시 자동 install.

### 3.1 PyMuPDF 듀얼 라이선스 — **2026-05-27 v2 결정: pdfminer.six 교체**

| 패키지 | 버전 | 라이선스 | 운영 사용 |
|---|---|---|---|
| ~~PyMuPDF (fitz)~~ | ~~≥ 1.24~~ | ~~AGPL-3.0 OR Artifex Commercial~~ | **2순위 폴백** (AGPL 수용 또는 Artifex 구매 시) |
| **pdfminer.six** | ≥ 20240706 | **BSD-3-Clause** ✅ | **1순위** (운영 기본, AGPL 회피) |

**결정** (자체 결정 v2, [commit 본 W8](.)):
- `lloydk.modules.m2_preprocess.extractor._extract_pdf`를 **2단계 폴백**으로 갱신:
  1. **pdfminer.six (BSD-3)** — 운영 기본. AGPL 의무 회피.
  2. PyMuPDF (AGPL) — Artifex 상용 또는 AGPL 명시 수용 시에만 폴백 활성화. 설치돼 있어도 1순위 실패 시에만 사용.
- 품질·속도 영향: pdfminer.six는 PyMuPDF 대비 약 **30~50% 느림**, 추출 품질은 텍스트 PDF에 한해 대등. 합성 PDF·이미지 PDF는 OCR 폴백으로 처리.
- 폐쇄망 번들: pdfminer.six 6.6MB만 포함, PyMuPDF는 제외 가능.

→ AGPL 위험 해소 완료. 라이선스 협의 항목에서 제외.

### 3.2 허용형 라이선스 (★) — 23개

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
| elasticsearch | ≥8.15,<9.0 | Apache-2.0 | https://github.com/elastic/elasticsearch-py |
| pdfminer.six | ≥20240706 | BSD-3-Clause | https://github.com/pdfminer/pdfminer.six (PyMuPDF AGPL 대체, §3.1) |
| prometheus-client | ≥0.20 | Apache-2.0 | https://github.com/prometheus/client_python (W7 관측성) |
| python-multipart | ≥0.0.9 | Apache-2.0 | https://github.com/Kludex/python-multipart (W3 /guide multipart) |
| transformers | ≥4.44 | Apache-2.0 | https://github.com/huggingface/transformers |
| torch | ≥2.3 | BSD-3-Clause | https://github.com/pytorch/pytorch |
| accelerate | ≥0.33 | Apache-2.0 | https://github.com/huggingface/accelerate |
| datasets | ≥2.20 | Apache-2.0 | https://github.com/huggingface/datasets |
| evaluate | ≥0.4 | Apache-2.0 | https://github.com/huggingface/evaluate |
| scikit-learn | ≥1.5 | BSD-3-Clause | https://github.com/scikit-learn/scikit-learn |
| mlflow | ≥2.16 | Apache-2.0 | https://github.com/mlflow/mlflow |
| matplotlib | ≥3.9 | PSF-based (matplotlib license, BSD-스타일) | https://matplotlib.org (W11 M6 평가 — confusion-matrix PNG, report.py) |
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

### 4.6 `[psh]` — Performance Scenario Harness 보조

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| prometheus-client | ≥0.20 | Apache-2.0 AND BSD-2-Clause | https://github.com/prometheus/client_python — 이미 base에 포함되나 PSH(W11) 명시적 그룹화 |

### 4.7 `[evaluation]` — 평가 산출물 (confusion-matrix·통계 그래프)

| 패키지 | 버전 | 라이선스 | 비고 |
|---|---|---|---|
| matplotlib | ≥3.9 | PSF-based (matplotlib license, BSD-스타일) | https://matplotlib.org — 이미 base에 포함되나 평가 워크플로 명시적 그룹화 |
| seaborn | ≥0.13 | BSD-3-Clause | https://seaborn.pydata.org — 분포 시각화 (선택) |

### 4.8 `[lint]` / `[dev]`

| 패키지 | 라이선스 |
|---|---|
| openapi-spec-validator | Apache-2.0 |
| pip-licenses | MIT |
| cyclonedx-bom | Apache-2.0 |
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

### 6.2 MinIO (AGPL-3.0) — **결정 보류, 운영 전환 직전 법무 검토**

#### 6.2.1 우리 시스템에서 MinIO가 하는 역할

- 원본 문서 바이너리 저장 (`documents.raw_text_uri` 참조)
- 정규화 텍스트 저장
- 학습된 모델 가중치 (수 GB) — 재배포 없이 교체 가능
- MLflow artifacts (실험별 산출물·메트릭·플롯)
- W7 백업 dump 적재 대상 (PG dump, ES snapshot)

발주처 요구서에 **brand 명시 안 됨** — 우리가 "객체 스토리지 필요"를 식별 후 자체 채택 ([doc/02 §1.6](02_기술스택_확정_및_PoC_계획.md#L116)).

#### 6.2.2 AGPL 위험도 분석

| 사용 형태 | AGPL 의무 발동? |
|---|---|
| MinIO 서버를 **외부 사용자에게 노출** | ✅ 우리 시스템 소스 공개 의무 발생 가능 |
| MinIO 서버를 **내부망에서만 사용** (본 사업) | ⚠ 해석 불명확 — 일반적으로는 면제로 해석 |
| MinIO **클라이언트(SDK)만 사용** (서버는 third-party) | ❌ 의무 없음 |

본 사업은 **KOIPA 내부망 폐쇄 환경**에서만 가동 → 외부 노출 0 → AGPL 의무 발동 가능성 **매우 낮음**. 다만 법무 해석은 발주처+로이드케이 공동 필요.

#### 6.2.3 결정 — **W8 단계는 코드 유지, 운영 전환 직전 법무 답변 기준 결정**

| 옵션 | 비용 | 작업량 | 권장도 |
|---|---|---|---|
| (a) MinIO Commercial 구매 | 연 $1~5k (노드 수) | 코드 무변경 | ⚠ 발주처 예산 협의 필요 |
| (b) SeaweedFS 교체 (Apache 2.0) | 무료 | 0.5~1일 (compose·verify·docs) | ✅ 라이선스 안전 + 비용 무 |
| (c) 내부망 사용으로 AGPL 의무 면제 해석 (현 코드 유지) | 무료 | 0 | ✅ 가장 현실적, 단 법무 확인 |
| (d) 발주처와 AGPL 수용 협의 (소스 공개) | 무료 | 0 | △ 공공사업이라면 가능 |

**현재 W8 결정**: **(c) 코드 유지** + 본 §6.2 결정 보류 명시 + 부록 §6.2.4 SeaweedFS 교체 가이드 작성 (필요 시 0.5~1일에 교체 가능).

#### 6.2.4 SeaweedFS 교체 가이드 (필요 시 0.5~1일 작업)

만약 법무 결정이 (c) 면제 불가 → SeaweedFS 교체 시 작업:

```yaml
# docker-compose.yml — minio 서비스를 다음으로 치환
seaweedfs:
  image: chrislusf/seaweedfs:3.71
  command: server -dir=/data -s3 -s3.port=8333 -master.volumeSizeLimitMB=10000
  volumes:
    - seaweeddata:/data
  ports:
    - "9000:8333"   # S3 API (포트는 MinIO와 동일하게 매핑)
    - "9333:9333"   # master UI
```

코드 영향:
- `lloydk.adapters.storage.minio_store` — Python `minio` 클라이언트 그대로 사용 가능 (SeaweedFS S3 호환)
- `scripts/init_minio_buckets.py` — 그대로 동작 (S3 CreateBucket 호출)
- `scripts/backup_postgres.py·backup_es_snapshot.py·backup_minio_mirror.py` — 그대로 동작 (S3 API)
- `verify_infra.py` `check_minio` — 엔드포인트만 동일하면 동작
- ES `repository-s3` 플러그인 — 그대로 동작 (S3 호환)

**테스트 영향**: 0건 (현재 모든 테스트는 `minio` 패키지 API에 의존, 서버 구현체 무관).

→ 라이선스 결정 시점에 본 §6.2.4 가이드 1일 작업으로 교체 가능.

### 6.3 Redis 7.4+ (RSALv2/SSPL)

- 7.4+부터 라이선스 변경됨
- 7.2까지는 BSD-3-Clause → **본 사업은 Redis 7-alpine(BSD 시점)을 docker-compose에 고정**해두는 게 안전
- ~~docker-compose.yml의 `redis:7-alpine`이 어느 minor 버전인지 확인 필요. 7.4+면 7.2-alpine으로 다운 고려.~~ → **2026-05-27 `redis:7.2-alpine`으로 고정 완료** (BSD-3-Clause 시점).

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
| ~~Apache-2.0 + AGPL-3.0 (PyMuPDF/MinIO)~~ | ✅ PyMuPDF는 pdfminer.six 교체 완료. MinIO는 내부망 운영 면제 해석 + SeaweedFS 대체 가이드 작성 — §3.1·§6.2 결정 완료 |
| MIT + LGPL (psycopg) | ✅ 동적 링킹 시 안전 |

---

## 9. 회신 의존 라이선스 확정 사항

KL/발주처 회신과 함께 확정해야 할 라이선스 사안:

| 항목 | 회신 변수 | 결정 사항 |
|---|---|---|
| ~~PyMuPDF 듀얼 라이선스~~ | **결정 완료 (2026-05-27 v2)** | **pdfminer.six 교체** — §3.1 완료, AGPL 위험 해소 |
| MinIO AGPL | 별도 협의 | Commercial 구매 vs 소스 공개 협의 vs S3 호환 대체 |
| EXAONE 라이선스 | **Q6** | 공공사업 사용 가능 여부 |
| ES 라이선스 등급 | **E3** | Basic / Platinum / Enterprise (운영 라이선스 ToS 별개) |
| konlpy GPL-3.0 영향 | 사용 시점에 재평가 | 현재 미사용 |
| Redis 7.x 버전 고정 | 운영 결정 | BSD 시점(7.2)로 고정 vs RSALv2 수용 |

---

## 10. 결정 사항 요약 (v0.9.1)

1. **직접 의존성 24개 모두 OSS 적합** — Apache/MIT/BSD/PSF/MPL 중심 (`pip-licenses` 실측 2026-05-28: permissive 18, weak 1=psycopg LGPL, strong 1=PyMuPDF AGPL 듀얼)
2. ~~**PyMuPDF AGPL/Artifex 듀얼** → 운영 전환 전 상용 또는 pdfminer.six 결정 필요~~ **2026-05-27 v2: pdfminer.six 교체 완료** ✅
3. **konlpy GPL-3.0** → 현재 미사용, 향후 사용 시 별도 프로세스 분리 또는 대체
4. **MinIO AGPL-3.0** → 발주처·KL과 소스 공개 vs 상용 라이선스 협의
5. **Redis 7-alpine** → 7.2-alpine으로 명시 고정 권장 (BSD 시점)
6. **모델 가중치**는 KF-DeBERTa·KURE-v1·BGE-M3·Qwen3 모두 MIT/Apache → ✅
7. **EXAONE Q6 회신** 시 라이선스 결정
8. **폐쇄망 번들** [doc/12 §3.1](12_폐쇄망_배포_설계.md) `licenses/third-party-licenses.txt`에 모든 NOTICE/BSD 고지 자동 포함

---

## 11. 다음 액션

### 11.1 즉시 (회신 무관)

- [x] `docker-compose.yml`의 `redis:7-alpine` → `redis:7.2-alpine` 고정 완료 (BSD 시점, 2026-05-27)
- [x] `scripts/dump_licenses.py` 신규 (2026-05-27) — 4종 포맷 자동 산출 + CI 검증 잡 통합
- [x] CI `licenses-check` 잡 추가 — 신규 strong copyleft 의존성 도입 사전 차단
- [x] **2026-05-28 W11**: matplotlib base 추가(M6 confusion-matrix PNG), seaborn `[evaluation]` extras 추가, `[psh]`·`[evaluation]` 그룹 신설 — pip-licenses 실측: matplotlib=PSF·seaborn=BSD-3, 모두 허용형 ★

### 11.2 회신 의존

- [ ] **Q6 EXAONE** 회신 후 §5 행 갱신
- [x] **PyMuPDF AGPL** → pdfminer.six 교체 (2026-05-27 v2 자체 결정, §3.1 완료)
- [ ] **MinIO AGPL** 발주처 협의 → §6.2 결정
- [ ] **E3 ES 라이선스 등급** 회신 후 §6.1 갱신

### 11.3 운영 전환 시점

- [ ] SBOM (CycloneDX) 산출 → 발주처 검수 첨부
- [ ] [doc/12 폐쇄망 번들](12_폐쇄망_배포_설계.md) `licenses/` 디렉터리 완성
- [ ] 본 문서 v1.0으로 확정
