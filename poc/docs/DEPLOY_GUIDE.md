# Koipa AI 배포 가이드 (통합) — 고객사 · 지재원

> **이 문서 하나로 따라 배포**할 수 있게 정리한 절차서입니다. 고객사(운영 런타임)와 지재원(모델공장)
> 두 대상을 다루되, **설치 절차는 공통**이고 `.env` 프로파일 몇 줄만 다릅니다.
> 짝 문서(설계·R&R): `폐쇄망_설치_배포_설계서`, 운영 상세: `운영_런북`, 장애: `docs/TROUBLESHOOTING.md`.

---

## 가장 쉬운 경로 (권장 · TL;DR)

**아티팩트 1개 + 명령 1개 + `.env` 몇 줄.** 고객사·지재원 모두 같은 `dist/koipa-airgap-bundle/`
(약 12GB — 도커 이미지·**분류 모델(v-dd3abab9+temperature)**·wheel·DB 마이그레이션·인수팩까지 자립)을 쓰고,
`bash deploy.sh`(폐쇄망/연결망 **자동감지**) 한 명령으로 마이그레이션+기동+스모크까지 끝난다.
**대상별로 다른 건 `.env` 프로파일뿐이다.**

```bash
# 공통 3단계 (반입/전송 후) — 고객사·지재원 동일
cd ~/koipa-airgap-bundle
bash verify.sh && bash install.sh                  # 무결성 확인 → 이미지 적재(docker load)
cp infra-config/.env.template .env && nano .env    # ↓ 표의 대상별 값만 다름
bash deploy.sh                                      # 자동감지 → 배포, 이어서 bash verify_install.sh
```

| | 고객사 (폐쇄망) | 지재원 (연결망) |
|---|---|---|
| `.env` | `DEPLOY_PROFILE=onprem-local`<br>(CPU면 `LLM_PROVIDER=noop` + A5 GPU블록 주석) | `DEPLOY_PROFILE=full-train` · `ANTHROPIC_API_KEY=…`<br>(GPU블록 유지) |
| 학습 | ✅ **야간 CPU 증분재학습**<br>(`enable_incremental_retrain` — 번들에 포함) | ✅ **전체 재학습·합성·골든**<br>(`enable_training`, GPU) + 증분 |
| 번들 밖(별도) | — | 전체학습용 **CUDA torch·대형 LLM**은 인터넷 조달(배포와 분리된 운영 워크플로) |

> **핵심 원리**: 코드·스키마·모델·설치절차는 **100% 공통**이고, 위 표의 세 줄만 대상별로 다르다.
> DB는 빈 상태로 시작하며 스키마(DDL, Alembic 13개)는 `deploy.sh`의 `alembic upgrade head`가 생성한다
> — 사람 검수·교정 데이터(`tb_corrections`·`tb_document_labels`·`tb_audit_log`)는 배포 후 서버에서 쌓인다.
>
> **왜 지재원도 번들?** "테스트한 것 = 고객사가 돌리는 것 = 지재원이 돌리는 것"이 비트단위로 동일해져
> 의존성 드리프트가 없다. 전송이 부담일 때만, 지재원은 인터넷이 되므로 **32MB 소스만 보내 서버가
> 직접 빌드**하는 경량 경로도 가능하다(단 두 번째 배포 방법을 유지하는 비용이 생긴다).

세부 단계·필수값·트러블슈팅은 아래 PART A~E 참조.

---

## 0. 대상별 차이 (먼저 읽기)

| 구분 | 고객사 (onprem-local) | 지재원 (full-train) |
|---|---|---|
| 역할 | 운영 런타임 (분류·검색·API) | 모델공장 (학습·합성·골든·인덱싱) + 운영 |
| 네트워크 | **폐쇄망(에어갭)** | 연결망(상용 LLM/API 접근) |
| GPU | 대개 **없음(CPU)** | **있음** (학습용) |
| LLM | `ollama` (로컬·GPU 시) | `anthropic` (상용) |
| 전체 재학습 | ❌ (`enable_training=False`) | ✅ (`enable_training=True`) |
| 야간 CPU 증분재학습 | ✅ (`enable_incremental_retrain`) | (전체학습 경로 사용) |
| 관리자 콘솔 | ✅ | ✅ |
| **안전 하드닝** (온도보정 T=3·안전게이트·원본암호화·실모델강제) | **✅ 동일** | **✅ 동일** |

**핵심**: 두 프로파일 모두 `DEPLOY_PROFILE`이 안전장치를 자동 활성화한다. 이 값을 `lite-*`로 바꾸거나
지우면 안전장치가 꺼진 채 부팅되며 `deploy_airgap.sh`가 **거부**한다.

---

## 1. 사전 요건

| 항목 | 기준 | 확인 |
|---|---|---|
| OS | Ubuntu 22.04 LTS | `cat /etc/os-release` |
| Docker | Engine 24+, compose v2 | `docker version && docker compose version` |
| GPU (지재원/GPU고객) | nvidia-smi + Container Toolkit | `docker run --rm --gpus all nvidia/cuda:12.4.0-base nvidia-smi` |
| 디스크 | 여유 ≥ 70GB (번들 ~12GB + 볼륨/적재) | `df -h /` |
| RAM | ≥ 16GB (권장 32GB+) | `free -h` |
| 포트(내부) | 5432·6379·8000 (+ 관측성 9090·3000) | — |

> **GPU 없음(CPU 고객사)**: 이 문서의 🅲 표시 단계를 따르면 된다. 추론이 40~60% 느려지지만 동작한다.

---

## PART A — 공통 설치 (고객·지재원 동일)

### A0. Docker 설치 (미설치 시)
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER        # 재로그인 후 sudo 없이 docker
docker version && docker compose version
```
🅶 GPU 서버는 추가로 nvidia-container-toolkit 설치 (발주처/인프라 담당 범위):
```bash
# (요약) NVIDIA Container Toolkit repo 추가 후:
sudo apt-get install -y nvidia-container-toolkit && sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker
```

### A1. 번들 반입 · 무결성 검증
번들: `dist/koipa-airgap-bundle/` (약 12GB — docker 이미지 11GB + 모델 0.7GB).
```bash
# (로컬 PC → 서버) 전체:
scp -P <PORT> -r dist/koipa-airgap-bundle <user>@<host>:~/
# 또는 최소(분류 스모크만, ~4GB): api.tar + 모델 + 설정 + 스크립트
#   docker-images/api.tar docker-images/nginx-mtls.tar models infra-config acceptance install.sh verify.sh deploy_airgap.sh manifest.json CHECKSUMS.sha256
# 대용량은 rsync 이어받기 권장: rsync -avP -e "ssh -p <PORT>" dist/koipa-airgap-bundle <user>@<host>:~/

cd ~/koipa-airgap-bundle
bash verify.sh                       # "Checksums OK"
```

### A2. 이미지 적재
```bash
bash install.sh                      # docker-images/*.tar 를 docker load
docker images | grep -E 'koipa|pgvector|redis|nginx'
```
🅲🌐 **번들에 postgres/redis 이미지가 없다** (공개 이미지). 연결망이면 온라인 pull:
```bash
docker pull pgvector/pgvector:pg16
docker pull redis:7.2-alpine
```
> 순수 에어갭이면 이 두 이미지 tar를 매체로 별도 반입해 `docker load` 한다.

### A3. 모델 배치
번들 `models/classifier-trained/`에 현행 배포 모델(가중치+`temperature.json`)이 포함돼 있다. 그대로 사용 가능.
**v5_clean(v-fe4b386b)로 교체 배포**하려면:
```bash
scp -P <PORT> -r artifacts/classifier_p1_v5_clean/v-fe4b386b/* \
    <user>@<host>:~/koipa-airgap-bundle/models/classifier-trained/
```
> ⚠️ `temperature.json`이 반드시 함께 있어야 보정(T)이 적용된다(없으면 T=1.0 과신 위험).
> 🌐 임베딩(KURE-v1) 캐시가 번들 `models/hf/`에 없으면: 연결망은 `.env`에 `HF_HUB_OFFLINE=0`(첫 기동 시
> ~2GB 다운로드), 에어갭은 빌드호스트에서 `huggingface-cli download nlpai-lab/KURE-v1` 후 `models/hf/`로 반입.

### A4. `.env` 작성 (공통 필수값)
```bash
cp infra-config/.env.template .env
python3 -c "import secrets;print('STORAGE_ENCRYPTION_KEY='+secrets.token_hex(32))"   # 이 값 사용
nano .env
```
**공통 필수** (`replace_me_*` 남으면 배포 스크립트가 거부):
```ini
IMAGE_TAG=1.0.0-rc1
API_KEY=<강한 임의값>
POSTGRES_USER=koipa
POSTGRES_PASSWORD=<강한 임의값>
DATABASE_URL=postgresql+psycopg://koipa:<위와 동일 비번>@postgres:5432/koipa
REDIS_URL=redis://redis:6379/0
VECTOR_BACKEND=pg
STORAGE_BACKEND=local
STORAGE_ENCRYPTION_KEY=<64hex 랜덤>
POC_MODE=full
EMBEDDING_MODEL=nlpai-lab/KURE-v1
CLASSIFIER_MODEL_DIR=/models/classifier-trained
```
> **프로파일·LLM은 PART B에서** 대상별로 채운다.

### A5. (🅲 CPU 서버만) compose GPU 블록 주석
GPU 없는 호스트에서 `nvidia` 예약 블록이 있으면 기동이 **실패**한다.
```bash
grep -n "nvidia" infra-config/docker-compose.airgap.yml
# api·worker 서비스의 deploy.resources.reservations.devices(driver: nvidia) 블록을 # 로 주석
```

### A6. 인프라 기동 → 마이그레이션
```bash
export COMPOSE="docker compose --env-file .env -f infra-config/docker-compose.airgap.yml"
$COMPOSE up -d postgres redis
$COMPOSE ps                                   # postgres healthy(~30s)
$COMPOSE run --rm api alembic upgrade head    # 19테이블 + 파티션 백필
```

### A7. 애플리케이션 기동
```bash
$COMPOSE up -d api worker beat                # 분류 스모크만이면: up -d api
$COMPOSE ps
```
> **worker**는 전큐(`classify,index,synthesis,learning,celery`) 구독, **beat**는 단일 인스턴스(자동화 발행기).
> **또는** A5까지 끝냈으면 `bash deploy_airgap.sh` 한 방으로 .env검증+기동+마이그레이션 자동.

---

## PART B — 대상별 `.env` (딱 이 부분만 다름)

### B-고객사 (onprem-local)
```ini
DEPLOY_PROFILE=onprem-local          # 안전 하드닝 자동 활성 (지우지 말 것)
LLM_PROVIDER=ollama
LOCAL_LLM_BASE_URL=http://host.docker.internal:11434/v1
LOCAL_LLM_MODEL=Qwen/Qwen3-14B
LOCAL_LLM_API_KEY=EMPTY
# enable_incremental_retrain=True 는 프로파일이 자동 설정(야간 CPU 증분재학습)
```
🅲 **CPU·LLM 없음(로컬 LLM 미기동)**: 분류·검색은 LLM 없이 동작. `/answer`(2차의견)만 비활성:
```ini
LLM_PROVIDER=noop                    # 로컬 LLM 생략 (분류는 LLM-free 핫패스)
HF_HUB_OFFLINE=0                     # 임베딩 캐시 없으면 온라인 다운로드
```
- GPU 고객사면 위 ollama/vLLM 사용 + A5 건너뜀(GPU 블록 유지).
- 로컬 LLM을 쓰려면 별도 기동: `ollama serve && ollama run qwen3:14b` (또는 vLLM, `운영_런북` §8).

### B-지재원 (full-train)
```ini
DEPLOY_PROFILE=full-train            # 학습 활성 + 안전 하드닝 자동
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<키>               # 합성·골든 라벨링용 상용 LLM
# enable_training=True 는 프로파일이 자동 설정 (URGENT 자동재학습 경로 포함)
```
- 지재원은 GPU 서버 → A5 건너뜀(nvidia 블록 유지), Container Toolkit 필수(A0 🅶).
- 학습 실행은 별도 절차(`scripts/p1_train_classifier.py` 등 모델공장 워크플로) — 배포와 분리.

---

## PART C — 설치 검증 (공통)
```bash
API_KEY=$(grep '^API_KEY=' .env | cut -d= -f2)
curl -s http://localhost:8000/api/v1/healthz/ready ; echo         # 200 (503이면 미준비)
$COMPOSE exec api python scripts/verify_infra.py                   # 인프라 일괄 점검

# 스모크 분류
curl -s -X POST http://localhost:8000/api/v1/classify \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"doc_id":"smoke-1","content":"본 문서는 당사의 반도체 공정 영업비밀을 포함한다"}' ; echo
# 기대: 등급(TS/S1/S2/S3) + confidence

# (권장) 인수 샘플팩 — 전 포맷 파싱+분류+안전게이트 일괄
API_KEY="$API_KEY" BASE_URL=http://localhost:8000 bash acceptance/run_acceptance.sh
# 기대: [acceptance] PASS: N docs, 0 veto   (UNDER!/파싱실패 1건이라도 있으면 FAIL)
```
> **외부(원격 PC)에서 테스트**: 서버 8000 포트 개방 또는 SSH 터널
> `ssh -p <PORT> -L 8000:localhost:8000 <user>@<host>` 후 로컬 `localhost:8000` 사용.

### (권장) 관측성 스택 — 안전 알림 소비자
안전 신호(FNR 급증·감사체인 파손·킬게이트 등)는 Prometheus/Grafana가 떠야 소비된다.
```bash
export OBS="docker compose --env-file .env -f observability/docker-compose.observability.airgap.yml"
$OBS up -d ; curl -s http://localhost:9090/-/ready
# Grafana http://<host>:3000 (.env GRAFANA_PASSWORD 필수)
```

---

## PART D — 배포 후 (운영 루프)

- **모델 교체(스왑)**: 새 후보를 `models/classifier-trained/`에 배치(또는 `CLASSIFIER_MODEL_DIR` 변경) →
  `$COMPOSE up -d api` 재기동. **자동 활성 아님** — GA 활성은 사람서명 locked-eval readiness 또는
  감사된 force가 필요(하드닝 게이트). 후보 판정은 `python scripts/gate_p1_candidate.py --candidate <dir>`.
- **고객사 운영 학습 루프**: 현장 검수 교정 → `tb_corrections` → 야간 CPU 증분재학습(무인 배치) →
  후보 등록(자동 서빙 안 됨). 활성화는 관리자 콘솔 + 사람 확인.
- **지재원 학습**: 전체 재학습·합성·골든 라벨링(GPU) → 후보 산출 → 게이트 → 배포본 갱신.
- **롤백**: `bash deploy_rollback.sh` (직전 이미지/모델로 복귀).

---

## PART E — 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `/ready` 503 | 의존성 미기동 | `$COMPOSE ps`로 postgres healthy 확인 |
| 기동 시 모델 다운로드 실패 | `HF_HUB_OFFLINE=1` + 캐시 없음 | `models/hf/` 반입 또는 `.env HF_HUB_OFFLINE=0`(연결망) |
| compose up `could not select device driver "nvidia"` | CPU 호스트인데 nvidia 블록 존재 | A5(🅲) nvidia 블록 주석 |
| 분류 confidence 비정상 | `temperature.json` 부재 | 보정값 동봉 후 모델 재배치 |
| 큐 작업 미소비 | worker `-Q` 누락/beat 미기동 | A7대로 worker 전큐 + beat 단일 |
| `deploy_airgap.sh` 거부 | `.env`에 `replace_me_*`/`lite-*` 프로파일 | 실값 입력 + `DEPLOY_PROFILE` 하드닝 유지 |
| pgvector/redis `no such image` | 번들에 없음(공개 이미지) | A2 온라인 pull 또는 tar 반입 |
| startup fail (암호화 키) | `STORAGE_ENCRYPTION_KEY` 미설정 | 64hex 키 설정(하드닝 프로파일 필수) |

---

## 부록 — 책임 경계 (R&R)
- **발주처/KL 준비**: 서버·폐쇄망·(GPU 시)드라이버/CUDA/Container Toolkit·Docker·매체 반입 승인
- **한국지식재산보호원 수행**: PART A~C 전 과정 (반입·적재·구성·마이그레이션·기동·검증)

## 부록 — CPU·연결망 테스트 서버 빠른 참고 (예: kip-ai)
CPU 전용 + 인터넷 되는 테스트 서버(고객사 프로파일 검증용): A0(도커 설치) → A1(scp) → A2(load+online
pull) → A3(모델) → A4+B-고객사(🅲: `LLM_PROVIDER=noop`·`HF_HUB_OFFLINE=0`) → A5(nvidia 주석) →
A6~A7 → PART C. 외부 테스트는 SSH 터널(8000).
