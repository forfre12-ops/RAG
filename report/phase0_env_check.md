# Phase 0 — 사전 검증 결과

5070 Ti 풀가동 PoC 계획(옵션 C)의 Phase 0 환경 점검 결과. **모든 사전 검증 통과**.

- 일자: 2026-05-30 (Phase 0 완료)
- 대상 GPU: NVIDIA GeForce RTX 5070 Ti (Blackwell, sm_120, 16GB VRAM)

---

## 0.1 PyTorch + CUDA 호환성 (Blackwell sm_120)

### 시도 1 — 안정판 cu124 (실패)
- `torch==2.5.1+cu124` 설치
- 결과: `sm_120 not compatible. The current PyTorch install supports sm_50 sm_60 ... sm_90`
- 원인: 안정판은 Hopper(sm_90)까지만 공식 지원

### 시도 2 — nightly cu124 2025-03-10 (실패)
- `torch==2.7.0.dev20250310+cu124` 설치
- 결과: 동일한 sm_120 미지원 경고. `CUDA error: no kernel image is available for execution on the device`
- 원인: 해당 nightly 버전 시점에 Blackwell sm_120 커널 미빌드

### 시도 3 — nightly cu128 2026-04-08 ✅ 성공
- `torch==2.12.0.dev20260408+cu128` 설치
- 결과:
  ```
  torch: 2.12.0.dev20260408+cu128
  cuda: True 12.8
  device: NVIDIA GeForce RTX 5070 Ti
  cap: (12, 0)
  mm 1024x1024 OK, sum= 1015547.625
  autograd OK, grad_norm= 11324.4404296875
  mem_allocated MB: 74.0
  ```
- 검증: 행렬 곱 + autograd 역전파 정상 동작

### 추가 검증 — transformers + BERT GPU 추론
- `klue/bert-base` 로드 + `.to("cuda")` + 4 문서 추론
- 결과: `logits shape: (4, 4) on cuda:0` + `mem MB: 454.74`
- 결론: **KF-DeBERTa-base(184M) 학습 5070 Ti에서 안전**

### 정착 명령
```powershell
poc\.venv\Scripts\python.exe -m pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu128
```

### 주의
- nightly는 안정성 보장 X. Blackwell 정식 안정판 출시 시 안정판으로 전환 권장
- 다운로드 크기: 약 2.8GB (cu128 wheel)

---

## 0.2 Elasticsearch Nori plugin (한국어 분석기)

### 결과: ✅ 통합
- 신설: `poc/infra/es/Dockerfile.es`
  ```dockerfile
  FROM docker.elastic.co/elasticsearch/elasticsearch:8.15.3
  RUN bin/elasticsearch-plugin install --batch analysis-nori
  ```
- 수정: `poc/docker-compose.yml`의 elasticsearch 서비스
  - `image:` 단독 → `build: ./infra/es` + `image: lloydk/elasticsearch:8.15.3-nori` 태그
  - 기존 `userdict_ko.txt` 볼륨 마운트 그대로 유지

### Phase 1에서 실측 검증 예정
```powershell
docker compose build elasticsearch
docker compose up -d elasticsearch
curl http://localhost:9200/_cat/plugins  # 기대: analysis-nori 출력
```

---

## 0.3 pytest markers (회귀 분리)

### 결과: ✅ 통합
- 수정: `poc/pyproject.toml` `[tool.pytest.ini_options]`에 `markers` 항목 추가
  - `fullstack` — 풀스택 docker compose 필요
  - `gpu` — CUDA GPU 필요 (sm_120 호환)
  - `model_download` — HuggingFace 모델 다운 필요
  - `phase2` `phase3` `phase4` `phase5` — Phase별 측정 케이스

### 영향
- 기본 `pytest` = lite-noapi 빠른 회귀 그대로 (영향 0건)
- 새 케이스는 명시적 marker 부착으로 분리 실행

---

## 0.4 Ollama 로컬 LLM (Qwen3 + Solar)

### Ollama 사전 설치 확인
- `ollama version 0.24.0` 이미 설치 완료 (시스템)

### 모델 다운 결과
| 모델 | 사이즈 | 용도 |
|---|---|---|
| `qwen3:14b` | 9.3GB | LLM 1순위 후보 (Phase 5) |
| `solar:10.7b` | 6.1GB | LLM 비교 (Phase 5) |
| `bge-m3:latest` | 1.2GB | 임베딩 (Phase 2) — 이미 보유 |

### Qwen3 GPU 추론 검증
- 단순 프롬프트 응답 6.1초
- 처리량: **76.2 tokens/sec** (GPU 정상 동작 시 50~150 tps 범위)
- CPU 폴백이었다면 5~10 tps라 GPU 활용 확정
- thinking mode 활성 (`enable_thinking=True` 기본)

### 인지된 제약
- Qwen3 thinking mode가 짧은 프롬프트에선 응답 토큰 보다 thinking 토큰 비중 큼
- Phase 5 합성 시 `enable_thinking=True` 또는 `False` 옵션 분리 측정 예정 (V2 §6.2)

---

## 0.5 회귀 안정성

### 결과
- 단위 (demo): **9/9 PASS** (2.5s)
- 영향파일 5개: **36/36 PASS** (3분 21초)
  - test_api.py
  - test_deploy_profile.py
  - test_api_answer.py
  - test_demo_page.py
  - test_classify_stream.py
- 회귀 깨짐: **0건**

### 의미
- torch 2.10 → torch 2.12.0.dev nightly로 메이저 점프했음에도 회귀 안정
- 기존 추론 코드(rule-fallback)가 GPU/CPU 둘 다 추상화돼 있어 모델 교체 영향 격리

---

## Phase 1 진입 준비도

| 사전 요건 | 상태 |
|---|---|
| PyTorch CUDA 12.8 + sm_120 | ✅ |
| transformers BERT 학습 가능 | ✅ |
| ES Nori plugin Dockerfile | ✅ (빌드는 Phase 1) |
| pytest marker 회귀 분리 | ✅ |
| Ollama Qwen3 14B GPU 추론 | ✅ (76 tps) |
| Solar 10.7B 가용 | ✅ |
| BGE-M3 Ollama 가용 | ✅ |
| 기존 회귀 영향 0건 | ✅ |

---

## 다음 단계 — Phase 1

1. `docker compose build elasticsearch` (Nori 통합 ES 빌드)
2. `docker compose up -d` (6 컨테이너 풀 부팅)
3. alembic migration + MinIO 버킷 초기화
4. `.env.dev` 작성 (`DEPLOY_PROFILE=onprem-local`)
5. uvicorn 풀스택 부팅 + `/answer` Qwen3 첫 실호출
6. 데모 콘솔 nav 배지 `onprem-local` 표시 확인

예상 소요: 1일
