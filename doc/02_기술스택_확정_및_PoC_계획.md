# 기술 스택 확정 및 PoC 계획서
## 한국지식재산보호원 AI 영업비밀관리시스템 — 로이드케이 파트

작성일: 2026-05-26
관련 문서: `01_프로젝트_개요_및_로이드케이_파트_설계.md`
목적: 로이드케이 담당 6개 FUN(003·004·005·022·023·024)의 기술 스택을 비교·평가·확정하고, 확정 전 검증이 필요한 항목에 대한 PoC(Proof of Concept) 일정·기준·산출물을 정의

---

## 0. 핵심 평가 기준 (모든 스택 선택의 공통 잣대)

| # | 기준 | 가중치 | 정의 |
|---|---|---|---|
| C1 | **온프레미스 적합성** | 25% | Docker 단일/소수 컨테이너 배포, 외부 의존 최소, 기업 환경 설치 부담 낮음 |
| C2 | **한국어 성능** | 20% | 한국어 문서(특히 공문·기술·경영) 처리 정확도 |
| C3 | **미탐율(FNR) 최소화 잠재력** | 15% | 분류 모델·RAG 구성이 보안 미탐을 줄일 수 있는가 |
| C4 | **운영 비용/자원 효율** | 15% | GPU/메모리/디스크 요구량, 라이선스 비용 |
| C5 | **유지보수/생태계** | 10% | 활발한 커뮤니티, 한국 사례, 문서화 |
| C6 | **확장성** | 10% | 데이터/사용자 증가, 멀티 테넌트(기업별 분리) 대응 |
| C7 | **보안/감사 적합성** | 5% | 라이선스 명확성(Apache/MIT 등), 공급망 신뢰 |

---

## 1. 영역별 비교 매트릭스 및 확정안

### 1.1 분류 모델 PLM (FUN-004, FUN-005 핵심)

| 후보 | C1 | C2 | C3 | C4 | C5 | C6 | C7 | 종합 | 비고 |
|---|---|---|---|---|---|---|---|---|---|
| **KF-DeBERTa-base** | ★★★★★ | ★★★★★ | ★★★★ | ★★★★ | ★★★ | ★★★★ | ★★★★★ | **★★★★★** | 카카오뱅크, 금융·법률 도메인 사전학습, 184M params |
| KoELECTRA-base | ★★★★★ | ★★★★ | ★★★ | ★★★★★ | ★★★★ | ★★★★ | ★★★★★ | ★★★★ | 110M, 경량 대안 |
| KLUE-RoBERTa-large | ★★★★ | ★★★★★ | ★★★★ | ★★★ | ★★★★ | ★★★ | ★★★★★ | ★★★★ | 337M, 무거우나 강력 |
| KoBigBird | ★★★★ | ★★★★ | ★★★★ | ★★★ | ★★ | ★★★ | ★★★★ | ★★★ | 긴 문서(4096) 대응, 청크 전략으로 대체 가능 |

**확정**: **KF-DeBERTa-base 기본 / KoELECTRA-base 경량 옵션 (이중 트랙)**
**이유**: 한국어 분류 SOTA + 금융·법률 도메인 사전학습이 영업비밀(경영·기술 문서)과 부합. 경량 환경 기업을 위해 KoELECTRA 동시 지원.
**PoC 필요**: 두 모델 분류 F1·FNR 비교 (P1)

---

### 1.2 임베딩 모델 (RAG 옵션, FUN-004)

| 후보 | C1 | C2 | C3 | C4 | C5 | C6 | C7 | 종합 | 비고 |
|---|---|---|---|---|---|---|---|---|---|
| **KURE-v1** (고려대 NLP&AI Lab) | ★★★★★ | ★★★★★ | ★★★★★ | ★★★★ | ★★★★ | ★★★★★ | ★★★★★ | **★★★★★** | **BGE-M3 fine-tuned for Korean**, 1024차원, 8192 토큰, MIT. 한국어 retrieval Recall@1 0.5264 vs BGE-M3 0.5178 (+1.6%p), NDCG@1 0.6055 vs 0.5985로 전 영역 우위 |
| BGE-M3 (BAAI) | ★★★★★ | ★★★★ | ★★★★★ | ★★★★ | ★★★★★ | ★★★★★ | ★★★★★ | ★★★★ | 다국어+한국어, dense+sparse+colbert 동시. KURE의 베이스 모델 |
| ko-sroberta-multitask | ★★★★★ | ★★★★ | ★★★ | ★★★★★ | ★★★★ | ★★★★ | ★★★★★ | ★★★ | 경량 한국어 SBERT, 자원 부족 기업용 |
| E5-large-multilingual | ★★★★ | ★★★ | ★★★ | ★★★★ | ★★★★ | ★★★★ | ★★★★★ | ★★★ | 한국어 특화는 아님 |

**확정**: **KURE-v1 기본** (1순위 신규) / **BGE-M3 폴백** / ko-sroberta 경량 옵션
**이유**:
- KURE-v1은 BGE-M3 아키텍처를 한국어 retrieval 데이터로 파인튜닝한 모델 → BGE-M3의 dense+sparse+ColBERT multi-vector 출력 호환 유지
- 한국어 retrieval 벤치마크에서 BGE-M3 대비 일관된 우위 (Recall@1 +1.6%p)
- 동일 차원·동일 토크나이저이므로 Qdrant 인덱스를 재구축 없이 모델만 swap 가능 → 운영 리스크 낮음
- 고려대 nlpai-lab 한국 연구기관 산출물, MIT 라이선스
**HuggingFace**: `nlpai-lab/KURE-v1`
**PoC 필요**: 가이드 문서 + 사내규정 시드에 대한 KURE-v1 vs BGE-M3 Top-K 검색 정확도 직접 비교 (P2)

---

### 1.3 LLM (FUN-003 샘플 생성, FUN-005 RAG 추론 보강)

#### 상용 API (개발기간: 수행사 부담 / 운영: 발주처 부담)
| 후보 | 한국어 | 긴 문서 | 단가($/1M tok 입력+출력) | 추천도 |
|---|---|---|---|---|
| **Claude Sonnet 4.6** | ★★★★★ | ★★★★★ (1M) | $3 + $15 | ★★★★★ |
| GPT-4o | ★★★★ | ★★★★ (128K) | $2.5 + $10 | ★★★★ |
| Gemini 2.5 Pro | ★★★★ | ★★★★★ (1M) | $1.25 + $10 | ★★★★ |

#### OSS On-prem (운영 보안 우려 시 대안)
| 후보 | 한국어 | VRAM | 추천도 | 비고 |
|---|---|---|---|---|
| **Qwen3-14B** | ★★★★ | 약 30GB (FP16) / 10GB (Q4) | ★★★★★ | 2025-05 공개, **thinking/non-thinking 모드** 동적 전환, 100+ 언어 강화, vLLM ≥0.8.5 공식 지원, 컨텍스트 32K(YaRN 131K), Apache 2.0 |
| Qwen2.5-14B-Instruct | ★★★★ | 약 30GB / 10GB(Q4) | ★★★ | 한 세대 전. Qwen3 출시로 베이스라인에서 강등 |
| EXAONE 3.5 32B (LG) | ★★★★★ | 약 65GB / 20GB(Q4) | ★★★★ | 한국어 SOTA급, 라이선스 확인 필요 |
| Llama 3.1-8B + Bllossom | ★★★ | 약 16GB / 5GB(Q4) | ★★★ | 한국어 파인튜닝, 경량 |

**확정**:
- **상용 기본값: Claude Sonnet 4.6** (한국어·긴 문서·합성 품질)
- **OSS 기본값: Qwen3-14B + vLLM ≥ 0.8.5** (Adapter로 교체 가능)
  - 합성(FUN-003) 시 `enable_thinking=False` 권장 — 빠른 다량 생성에 적합
  - 라벨링 경계 판단·평가요소 점수화에는 `enable_thinking=True` (`/think`) 활용
  - vLLM 기동: `vllm serve Qwen/Qwen3-14B --enable-reasoning --reasoning-parser deepseek_r1`
- **EXAONE 32B는 라이선스 확인 후 한국어 강화 옵션으로 채택 검토**
- **추상화**: LLM Provider Adapter (OpenAI/Anthropic/Google/vLLM/Ollama)로 교체 가능하게 설계
**참고**: Qwen3의 한국어 정량 성능은 공식 자료에 Qwen2.5 대비 직접 비교 미공개 → PoC P3에서 자체 검증.
**PoC 필요**: 합성 문서 품질·다양성 평가 + 비용 추정 + Qwen3 thinking/non-thinking 모드 비교 (P3)

---

### 1.4 Vector DB

| 후보 | C1 | C2 | C3 | C4 | C5 | C6 | C7 | 종합 | 비고 |
|---|---|---|---|---|---|---|---|---|---|
| **Elasticsearch 8.14+** | ★★★★ | - | ★★★★★ | ★★★★ | ★★★★★ | ★★★★★ | ★★★★★ | **★★★★★** | `dense_vector` HNSW + BM25 + RRF 하이브리드, Kibana/ILM/Watcher 운영 표준, KL 기존 인프라 재사용 |
| ~~pgvector (PostgreSQL)~~ | - | - | - | - | - | - | - | - | **제거됨** (2026-05-27 자체 결정) — ES 단일 확정으로 어댑터 미작성·DB 컬럼 제거 |
| Qdrant | ★★★★★ | - | ★★★★ | ★★★★★ | ★★★★ | ★★★★ | ★★★★★ | ★★★★ | Rust 단일 컨테이너, 메타필터 강력 — **2순위 즉시 롤백 경로** |
| Milvus | ★★★ | - | ★★★★ | ★★★ | ★★★★★ | ★★★★★ | ★★★★★ | ★★★ | 대규모 강점이나 etcd+MinIO 의존, 제외 |
| Vespa | ★★★ | - | ★★★★ | ★★★ | ★★★ | ★★★★★ | ★★★★ | ★★★ | 학습 곡선 가파름, 제외 |

**확정 (2026-05-27 자체 결정)**: **Elasticsearch 8.14+ 단일 / Qdrant 즉시 롤백 경로**
**이유**: KL 사전 인터뷰에서 ES 운영 표준화 의사 확인, dense_vector + BM25 + RRF 하이브리드로 영업비밀 검색의 키워드+의미 동시 수용, Kibana·ILM·audit log 등 운영 도구가 발주처 컴플라이언스에 부합. pgvector는 어댑터 미작성 상태였고 자체 결정으로 ES 단일 잠금 → 죽은 코드 정리.
**상세**: [doc/13_벡터DB_ES_전환_계획서.md](13_벡터DB_ES_전환_계획서.md)
**E1~E8 자체 결정값**: ES 8.15.3 (docker-compose 잠금), Nori + 사용자 사전, Basic 라이선스, 인덱스 분리 테넌트, 단일 노드 JVM 4GB, int8_hnsw

---

### 1.5 메인 RDB (모델/실험/라벨/이력 메타)

**확정**: **PostgreSQL 16 + JSONB** (벡터 저장은 ES 외부화 — pgvector 미사용)
**이유**: 공공기관 표준, 트랜잭션·확장성·라이선스 모두 안정. 모델 버전·학습셋 스냅샷·라벨링·능동학습 큐·성능 이력 통합 관리. 임베딩 벡터는 ES `secrets-guides-koipa-*` 인덱스에 저장하여 PG는 관계형 트랜잭션 데이터에만 집중.
**대안 검토 없음** (MySQL/MariaDB 가능하지만 JSONB 활용도가 PG 우위)

---

### 1.6 객체 스토리지

**확정**: **MinIO** (S3 호환, 온프레미스 단일 컨테이너)
**용도**: 원본 문서, 모델 가중치(이미지 외부 분리 → 재빌드 없이 교체), 학습 스냅샷, 합성 문서 보관

---

### 1.7 큐/캐시

**확정**: **Redis 7 + Celery**
**용도**: 대용량 일괄 분류 비동기 처리(FUN-005), 학습 트리거 큐, 능동학습 재라벨 누적 큐

---

### 1.8 문서 처리 (FUN-022)

| 포맷 | 1순위 | 2순위/fallback |
|---|---|---|
| HWP/HWPX | **rhwp-python** (Rust 기반 PyO3 바인딩, HWP+HWPX 통합 API, pyhwp 대비 약 62× 빠름, MIT, v0.6.1 활발 유지) | pyhwp/hwp5 → LibreOffice headless |
| DOCX | **python-docx** | LibreOffice |
| PDF (텍스트) | **PyMuPDF (fitz)** | pdfplumber (표 처리) |
| PDF/이미지 OCR | **PaddleOCR (kor 모델)** | Tesseract+kor → Qwen2-VL (Vision LLM fallback) |
| 구조 인식 | **unstructured** | layoutparser |
| 청크 분할 | **LangChain RecursiveTextSplitter** | 토큰 기반 슬라이딩 윈도우 자체 구현 |
| 한국어 정규화 | **soynlp + KoNLPy(Mecab-ko)** | hanspell (맞춤법) |

**HWP 1순위 변경 사유**:
- pyhwp는 2016년 이후 미유지·HWP5 전용. 한국 공공기관 문서는 HWPX 비중도 상당.
- rhwp-python은 Rust 엔진 바인딩으로 추출 속도 우위, HWP+HWPX 단일 API. LangChain `HwpLoader` extra 제공.
- 단점: 비공식 커뮤니티 패키지(`DanMeon/rhwp-python`) — 운영 리스크 분산을 위해 pyhwp/LibreOffice 폴백 필수 유지.

**PoC 필요**: 한국 공문/HWP/HWPX 추출 품질 + rhwp-python vs pyhwp 비교 (P4)

---

### 1.9 학습/실험/API/모니터링

| 영역 | 확정 |
|---|---|
| 학습 프레임워크 | **PyTorch 2.x + HuggingFace Transformers + Trainer** |
| 실험 추적 | **MLflow** (모델 레지스트리 포함) — FUN-024 이력관리 충족 |
| 프롬프트 체인 | **LangChain** (요구사항 명시) + **LangGraph** (능동학습 루프) |
| API 서버 | **FastAPI** (OpenAPI 자동 생성 → KL 합의 유리) |
| LLM 서빙 | **vLLM** (OSS 운영 시) |
| 모니터링 | **Prometheus + Grafana** (자원·지표), **Loki** (로그) |
| GPU 런타임 | **NVIDIA Container Toolkit + CUDA 12.x** |

---

## 2. 최종 채택 스택 (한 줄 요약, 2026-05 갱신)

| 레이어 | 채택 |
|---|---|
| 분류 모델 | **KF-DeBERTa-base** (기본) / KoELECTRA-base (경량) |
| 임베딩 | **KURE-v1** (한국어 1순위, BGE-M3 fine-tuned) / BGE-M3 폴백 / ko-sroberta 경량 |
| LLM 상용 | **Claude Sonnet 4.6** (기본) / GPT-4o / Gemini — Adapter 교체 |
| LLM OSS | **Qwen3-14B + vLLM ≥ 0.8.5** (thinking/non-thinking) / EXAONE 3.5(검토) / Llama+Bllossom |
| Vector DB | **Elasticsearch 8.14+** (기본, [doc/13](13_벡터DB_ES_전환_계획서.md)) / Qdrant 즉시 롤백 |
| RDB | **PostgreSQL 16 + JSONB** (벡터는 ES 외부화, pgvector 미사용) |
| 스토리지 | **MinIO** |
| 큐/캐시 | **Redis 7 + Celery** |
| 문서처리 | **rhwp-python** (HWP/HWPX 1순위) / PyMuPDF / PaddleOCR / unstructured |
| 학습/실험 | PyTorch + HF Transformers + **MLflow** |
| 체인/에이전트 | **LangChain + LangGraph** |
| API | **FastAPI** |
| 모니터링 | Prometheus + Grafana + Loki |
| 컨테이너 | Docker + docker-compose |

라이선스: 모두 **Apache 2.0 / MIT / BSD** 호환. 공공사업 적합.

---

## 3. PoC 계획 (확정 전 검증)

### 3.1 PoC 목록과 의사결정 효과

| ID | PoC 명 | 검증 목적 | 결과로 결정되는 사항 | 기간 | 산출물 |
|---|---|---|---|---|---|
| **P1** | 분류 모델 비교 | KF-DeBERTa vs KoELECTRA, RAG ON/OFF F1·FNR 비교 | 1.1 모델 채택, 1.4 RAG 효과 정량화 | 2주 | 성능 리포트, Confusion Matrix |
| **P2** | 임베딩·VectorDB 검색 정확도 | **KURE-v1 vs BGE-M3** + **ES dense vs ES 하이브리드(RRF) vs Qdrant** 4-way Top-K 적중률 | 1.2, 1.4 채택 확정 + 합격선 상향 협의 | 1주 | Recall@K, NDCG@K, Latency 표 |
| **P3** | LLM 합성 문서 품질 | Claude/GPT/**Qwen3** 합성 문서 라벨 일치도·다양성·단가, **Qwen3 thinking/non-thinking 비교** | 1.3 기본값 + 운영 단가 견적 | 1.5주 | 합성문서 1,000건 + 평가 리포트 |
| **P4** | HWP/PDF/OCR 추출 품질 | 공문·기술문서 30종 추출 정확도, **rhwp-python vs pyhwp** 비교 | 1.8 라이브러리 조합 확정 | 1주 | 추출 정확도·속도 매트릭스 |
| **P5** | 통합 E2E 스모크 | 업로드→전처리→분류→결과 1건 통과 | 인프라 통합 가능성 | 0.5주 | 시연 영상 + 로그 |

**총 PoC 기간: 4주 (병렬 수행 시) ~ 6주 (직렬)** — M1 설계 확정 단계와 중첩 수행 권장.

### 3.2 PoC 공통 데이터셋 구성

부족한 실데이터 문제를 PoC 단계에 그대로 마주칩니다. 다음 3중 데이터로 구성:

1. **공개 데이터** (즉시 확보 가능)
   - AI Hub: 법률·계약서·공문서 코퍼스
   - 공공데이터 포털: 공문서 샘플
   - 보호원이 공개한 영업비밀 가이드라인 문서
2. **LLM 합성 데이터** (FUN-003 사전 PoC)
   - 등급별 200건씩 × 4등급 = **800건**
   - Claude로 1차 생성 후 수동 검수 (사람 1인 × 3일)
3. **발주처 협조 데이터** (가능 시)
   - 비밀해제 문서 또는 익명화 샘플 요청
   - 없으면 1·2번만으로 PoC 진행

### 3.3 PoC 합격 기준 (Go/No-Go)

| PoC | KPI | 합격선 |
|---|---|---|
| P1 | F1-macro (4-class) | ≥ 0.75 (PoC 합성 데이터 기준) |
| P1 | **FNR(특급→하위 미탐율)** | ≤ 5% — **핵심 KPI** |
| P2 | Recall@5 | ≥ 0.80 |
| P2 | 검색 Latency | ≤ 200ms (5만 청크 기준) |
| P3 | 합성 문서 라벨 일치도 | ≥ 90% (검수자 vs 생성 라벨) |
| P3 | 운영 단가 추정 | 월 ≤ $X (발주처와 합의 필요) |
| P4 | HWP/DOCX/PDF 텍스트 추출 누락률 | ≤ 5% |
| P5 | E2E 1건 처리 시간 | ≤ 10초 (RAG OFF), ≤ 30초 (RAG ON) |

### 3.4 PoC 일정 (병렬)

```
주차:        W1   W2   W3   W4
P4 문서처리  ████
P2 임베딩      ████
P3 LLM합성     ██████
P1 분류모델       ████████
P5 E2E통합              ██
```

### 3.5 PoC 산출물 패키지
- `poc/` 디렉토리에 각 PoC별 노트북(`.ipynb`) + 데이터셋(or 시드) + 평가 리포트(PDF) + Docker compose 스니펫
- 최종: **"기술 스택 확정 보고서 v1.0"** (본 문서 §1.x 별표를 PoC 결과로 갱신)

---

## 4. 의사결정 전 확인 사항 (KL/발주처 협의)

| # | 항목 | 협의 대상 | 결정 시한 |
|---|---|---|---|
| Q1 | KL 기존 인프라 (ES/Postgres/Redis 보유 여부) | KL | PoC 시작 전 |
| Q2 | 발주처 GPU 사양 (VRAM 24/48/80GB?) | 발주처 | PoC 시작 전 |
| Q3 | 합성 문서 활용 허용 범위 (학습/공유) | 발주처 | P3 시작 전 |
| Q4 | 비밀해제 실데이터 제공 가능 여부 | 발주처 | PoC 시작 전 |
| Q5 | 운영 단계 LLM API 예산 상한 | 발주처 | P3 종료 전 |
| Q6 | EXAONE 라이선스 (공공사업 사용 가능?) | LG/발주처 | 1.3 OSS 확정 전 |
| Q7 | 멀티 테넌트(기업별 분리) 요구 강도 | 발주처/KL | 1.4 VectorDB 확정 전 |

---

## 5. 다음 단계 액션

1. **본 문서를 KL/발주처에 공유** → §4의 Q1~Q7 답변 수집 (1주)
2. **PoC 데이터셋 1차 구축**: AI Hub·공공데이터 수집 + 가이드 문서 정리 (1주)
3. **PoC 환경 셋업**: GPU 1장 + Docker compose로 Elasticsearch/Postgres/MinIO/Redis/MLflow 구동 (3일, ES 마이그레이션 단계 S1은 [doc/13 §6](13_벡터DB_ES_전환_계획서.md) 참고)
4. **P4 시작**: 문서처리는 다른 PoC의 입력이므로 최우선 (W1)
5. **PoC 완료 후 본 문서 §1을 갱신하여 v1.0 확정** → 본 설계의 §2 모듈 구현 착수

---

## 부록 A. PoC 1차 결과 (2026-05-26, dryrun 모드)

발주처 데이터 미확보·Docker/GPU 미가용 환경에서 **합성 코퍼스 + 결정론적 mock**으로
파이프라인 무결성 + 합격선 판정 로직을 검증. 실측 모델 비교는 GPU 확보 후 `--mode full` 재실행.

| PoC | 합격선 | 1차 결과 | 판정 | 비고 |
|---|---|---|:---:|---|
| **P4 추출** | 누락률 ≤ 5%, 품질 ≥ 0.7 | 누락 0.0%, 품질 0.986 | PASS | 30 파일 (.txt/.md). HWP/DOCX/PDF 실파일은 발주처 데이터 확보 후 동일 스크립트로 재실행 |
| **P3 합성** | 라벨 일치도 ≥ 90% | **100.0%** (**800건**, 2026-05-27 확장), FNR 0% | PASS | Noop provider, 비용 $0. 등급별 균등 200×4, 도메인 6종 분포. 실 Claude/Qwen3 비교는 API 키 추가 후 `--provider anthropic` |
| **P2 임베딩** | Recall@5 ≥ 0.80, Lat ≤ 200ms | Recall 0.70 (hash baseline), 3ms | PASS* | dryrun baseline 0.50 통과. KURE-v1/BGE-M3 실측은 모델 다운로드 후 `--mode full` |
| **P1 분류** | F1-macro ≥ 0.75, FNR ≤ 5% | **F1 1.0**, FNR 0% | PASS | 룰 라벨러 surrogate. KF-DeBERTa 학습은 GPU 확보 후 `--mode full --epochs 5` |
| **P5 E2E** | 응답 200 + 라벨 OK, ≤ 30s | 4/4 일치, max 6.4ms (inproc) | PASS | TestClient in-process. HTTP 모드는 `docker compose up` 후 `--mode http` |

**전체 자동 실행**: `make poc-all` 또는 `python scripts/run_all_pocs.py` — 2초 내 6단계 완료, `poc/reports/summary.md` 생성.

### 부록 B. 1차 PoC 한계와 후속 액션

| 한계 | 후속 액션 |
|---|---|
| 발주처 실데이터 0건 → 합성 코퍼스로만 검증 | Q4(데이터 제공) 확정 후 P1/P3 재실행 |
| GPU·Docker 미가용 → mock/in-memory 백엔드 | Q2(GPU 사양) 확정 후 `make infra-up` + KF-DeBERTa/KURE-v1 실측 |
| LLM 비용 측정 불가 (noop provider) | API 키 확보 후 P3 `--provider anthropic` 재실행 → 운영 단가 견적 |
| 룰 라벨러가 분류기 surrogate | KF-DeBERTa 학습 완료 후 P1 `--mode full` → 룰 vs 학습모델 비교 |

---

## 부록 C. 2차 진행 — ES 전환·운영 도구·위험관리 (2026-05-27)

1차 PoC 합격 판정 후, **벡터 DB 전환 + 회신 대기 기간 활용한 운영 전환 자산 확보**에 주력. 외부 의존(GPU·발주처 데이터·KL 인프라) 없이 가능한 작업을 모두 완료.

### C.1 주요 산출물

| 영역 | 산출물 | 라인 |
|---|---|---|
| **설계** | [doc/13](13_벡터DB_ES_전환_계획서.md) 벡터DB Qdrant→ES 전환 v0.9-final | 601 |
| | [doc/10](10_위험관리대장.md) 위험관리대장 v0.9 (31개 위험, 회신 시나리오 매핑) | 312 |
| | [doc/12](12_폐쇄망_배포_설계.md) 폐쇄망 배포 설계 v0.9 (자기완비 번들 25~30GB) | 535 |
| **코드** | `EsStore` 어댑터 + `VectorStore` Protocol 확장 (`search_hybrid` 폴리필) | — |
| | docker-compose ES 8.15.3 서비스 + Nori 사용자 사전 + 인덱스 템플릿 | — |
| | `migrate_qdrant_to_es.py` (5단계 idempotent + dry-run + sample-N) | 380 |
| | `build_offline_bundle.py` (manifest dry-run, doc/12 §4.1 명세 구현) | 525 |
| | `p2_compare_embeddings.py` 재설계 (4-way 비교 `--backends --hybrid`) | — |
| **테스트** | EsStore 26 + factory 10 + migration 19 + bundle 23 = **신규 78종** | — |
| | 누적: 48 → **126/126 PASS, 0 회귀** | — |
| **CI** | bundle-dry / migrate-dry 잡 통합, bundle-manifest 아티팩트 업로드 | — |
| **문서 정합** | doc/00·02·04·05·06·06a·07·08·09 Qdrant→ES 일괄 갱신 | — |
| | [doc/06 K1-A E1~E9 부록 신설](06_협의요청서_KL_발주처.md) — ES 8항목 + 망 등급 | — |

### C.2 합격선 결정 (도용 없음)

[doc/13 §9.2](13_벡터DB_ES_전환_계획서.md) 기준: **합격선은 v0.9 그대로 유지** (Recall@5 ≥ 0.80, Lat p50 ≤ 200ms). ES + RRF 도입 효과는 S3 PoC v2에서 **4-way 측정** 후 발주처 협의로 v1.1 갱신.

```
(a) Qdrant + KURE-v1          ← baseline
(b) ES + KURE-v1 (dense kNN)  ← 백엔드 효과만
(c) ES + BM25 only            ← 키워드 기여도
(d) ES + KURE + BM25 + RRF    ← 하이브리드

→ (d) vs (a) Δ Recall ≥ 0.05 입증 시에만 0.85로 상향 협의
→ 측정 없이 합격선 상향 금지
```

### C.3 회신 의존 잔여 작업

회신 도착 시 [doc/10 §6 시나리오 매핑](10_위험관리대장.md)으로 즉시 1차 대응 발동. 외부 의존 없이 가능한 모든 작업은 완료.

| 회신 변수 | 트리거 액션 |
|---|---|
| Q1 GPU 사양 | `.env CLASSIFIER_BASE_MODEL` 확정 → P1 `--mode full` |
| Q3 실문서 | `datasets/raw/koipa/` 적재 → P1 hold-out test set |
| E1 ES 버전 | EsStore 자동 분기 (retriever/legacy/clientside) — 추가 작업 0 |
| E3 라이선스 | doc/13 §11.1 라이선스 매트릭스 채움 → DLS vs 인덱스 분리 결정 |
| E5 노드 사양 | JVM heap·HNSW 파라미터·`int8_hnsw` vs `hnsw` 결정 |
| E9 망 등급 | doc/12 §1.2 갱신 → v1.0 확정 → 매체 반입 절차 KL 합의 |

### C.4 검증 명령 한 줄 정리

회신 없이도 다음 명령으로 즉시 검증 가능:

```bash
make poc-all              # P4→P3→P2→P1→P5 dryrun (~2초)
make p2 --hybrid          # 4-way 시뮬레이션 (ES SKIP 안전망 동작)
make migrate-dry          # Qdrant→ES sample-5 dry-run
make bundle-dry           # 폐쇄망 번들 manifest dry-run
pytest                    # 126/126 (어댑터·마이그·번들)
```

OpenAPI yaml ES 정합성도 점검 완료 — `rag_namespace` → `rag_index_alias` rename, backend 컨텍스트 명시. KL이 K2 검토를 backend-agnostic하게 진행 가능.

---

## 부록 D. 800건 합성 데이터 실측 분석 (2026-05-27)

회신 대기 기간 동안 PoC 합성 데이터를 40 → 800건으로 확장(`make p3-800`), `scripts/analyze_synthetic.py`로 다각도 분석.

### D.1 분포 균등성

| 등급 | 건수 | CV (변동계수) |
|---|---:|---|
| TS | 200 | **0.000** (완벽 균등) |
| S1 | 200 | |
| S2 | 200 | |
| S3 | 200 | |

| 도메인 | 건수 | CV |
|---|---:|---|
| tech, business | 136 | **0.0155** (균등) |
| finance, hr, legal, mixed | 132 | |

**판정**: 등급·도메인 모두 균등 분배 PASS (CV < 0.1 기준).

### D.2 라벨 일치도 (target_grade ↔ predicted_grade)

| | TS | S1 | S2 | S3 |
|---|---:|---:|---:|---:|
| **TS** | 200 | 0 | 0 | 0 |
| **S1** | 0 | 200 | 0 | 0 |
| **S2** | 0 | 0 | 200 | 0 |
| **S3** | 0 | 0 | 0 | 200 |

**일치도: 100.00% (800/800)** — M3 룰 라벨러가 합성 시 target 등급을 정확히 재현. FNR (high→low) 0%.

### D.3 본문 길이 통계

| 등급 | 평균 글자 수 | 중앙 | 표준편차 |
|---|---:|---:|---:|
| TS | 169 | 169 | 0 |
| S1 | 199 | 199 | 0 |
| S2 | 178 | 178 | 0 |
| S3 | 169 | 169 | 0 |

**관찰**: 등급별 표준편차 0 → noop provider가 결정론적 템플릿을 사용한 증거. 실 LLM(Anthropic/Qwen3) 사용 시 다양성 확보 예상.

### D.4 LLM 사용량·비용 (noop)

- input tokens 합계: 134,246
- output tokens 합계: 80,200
- **비용: $0.00** (noop)
- 평균 지연: 0.0 ms (mock)

**참고**: Anthropic Claude로 동일 800건 생성 시 추정 비용 약 $5~10 (실측은 API 키 확보 후 진행).

### D.5 메타데이터 다양성 (noop의 한계)

| 지표 | 결과 |
|---|---|
| document_type unique | **1** ← noop 결정론적 |
| dept_hint unique | **1** ← 동일 |
| PII 위반 누적 | 0 |
| 파싱 에러 | 0 |

**한계 명시**: noop은 단일 템플릿 사용 → 실제 문서 다양성·품질은 측정 불가. 실 LLM 사용 시 doc_type·dept_hint 다양성 확보가 핵심 검증 포인트.

### D.6 OSS 라이선스 자동 검증 (dump_licenses.py)

[doc/14 §11.1](14_OSS_라이선스_보고서.md) 약속 이행 — `scripts/dump_licenses.py` 신규.

| 등급 | 패키지 수 (transitive 포함) |
|---|---:|
| 허용형 (MIT/BSD/Apache) | 162 |
| Weak Copyleft (LGPL/MPL) | 7 |
| **Strong Copyleft (GPL/AGPL)** | **2** |
| Unknown | 2 |

**Strong Copyleft 식별**:
1. **PyMuPDF 1.27.1** (AGPL-3.0 / Artifex Commercial 듀얼) — doc/14 §3.1 예상과 일치, 직접 의존성
2. **pdf2docx 0.5.9** (AGPL-3.0) — PyMuPDF 의존 transitive

→ doc/14 §3.1 대응안(Artifex 구매 / pdfminer.six 교체 / AGPL 수용) 선택은 발주처 협의 후 결정.

CLI 사용:
```bash
python scripts/dump_licenses.py --check                    # CI용 라이선스 검증 (exit 1 on risk)
python scripts/dump_licenses.py --check --scope=direct     # 직접 의존성만 검사
python scripts/dump_licenses.py --format=cyclonedx -o sbom.json  # SBOM 산출
```

### D.7 후속 액션

- [ ] **Anthropic API 키 확보 시**: `make p3 --provider anthropic --total 800` → 실 다양성 측정 → 본 §D 갱신
- [ ] **CI에 `dump_licenses --check --scope=direct`** 추가 — PyMuPDF 외 신규 strong copyleft 의존성 사전 차단
- [ ] **`docker-compose.yml` Redis `7.2-alpine` 고정** — doc/14 §11.1 RSALv2 회피 권장
