# Lloydk AI Engine — 영업비밀 등급분류 엔진

한국지식재산보호원(KOIPA) AI 영업비밀 관리시스템 / 로이드케이 담당 파트.
문서를 읽어 **TS · S1 · S2 · S3** 4등급으로 분류하고, 사람이 검수한 결과로 다시 학습한다.

> **처음 이 저장소를 여는 분께** — 소스는 4만여 줄이지만, 전부 읽을 필요는 없습니다.
> 아래 **요건별 진입 경로** 표에서 관심 있는 요건 한 줄만 따라가면 4~5개 파일로 끝납니다.
> 더 자세한 호출 경로는 [docs/CODE_MAP.md](docs/CODE_MAP.md)에 있습니다.

---

## 1. 이 엔진이 도는 방식 — 루프가 두 개다

이것만 알면 나머지는 세부사항입니다.

```
루프 A · 일상 운영 (매일 · 현업)
  문서 업로드 → 분류 → 사람 검수·확정          ← 교정 결과가 쌓인다

루프 B · 모델 갱신 (주기적 · 관리자)
  골든셋 후보 생성 → 검수·서명 → 재학습 → 배포 → 메트릭 확인
```

관리자 콘솔(`/demo/admin.html`)의 탭도 이 두 루프로 나뉘어 있습니다.

**설계의 중심 개념 하나** — 이 엔진은 정확도 극대화가 아니라 **미탐 최소화**를 목표로 합니다.
애매하면 높은 등급으로 올립니다(veto). 낮게 잡아 놓치는 쪽이 높게 잡아 과분류하는 쪽보다
훨씬 위험하기 때문입니다. 지표에서 `high_grade_fnr`(고등급 미탐률)을 F1보다 먼저 보는 이유입니다.

---

## 2. 요건별 진입 경로

| 요건 | 무엇 | API 진입점 | 서비스 | 코어 모듈 |
|---|---|---|---|---|
| **FUN-002** | 가이드 문서 업로드·버전관리 | [api/guide.py](src/lloydk/api/guide.py) | `guide_service.py` | — |
| **FUN-003** | 합성 샘플 · 골든셋 구축 | [api/synthesis.py](src/lloydk/api/synthesis.py)<br>[api/golden.py](src/lloydk/api/golden.py) | `synthesis_service.py`<br>`golden_build_service.py` | [m1_synthesis/](src/lloydk/modules/m1_synthesis/)<br>`golden_tiers.py` · `golden_signoff.py` |
| **FUN-004** | 학습 · 재학습 | [api/training.py](src/lloydk/api/training.py) | `training_service.py` | [m4_training/](src/lloydk/modules/m4_training/) |
| **FUN-005** | 등급 분류 · 등급체계 | [api/classify.py](src/lloydk/api/classify.py)<br>[api/schema_admin.py](src/lloydk/api/schema_admin.py) | `classify_service.py`<br>`schema_admin_service.py` | [m5_inference/](src/lloydk/modules/m5_inference/) |
| **FUN-022** | 문서 텍스트 추출·전처리 | [api/documents.py](src/lloydk/api/documents.py) | `document_ingestion_service.py` | [m2_preprocess/](src/lloydk/modules/m2_preprocess/) |
| **FUN-023** | 라벨링 규칙 · 태깅 키워드 | [api/keyword_admin.py](src/lloydk/api/keyword_admin.py) | `keyword_admin_service.py` | [m3_labeling/](src/lloydk/modules/m3_labeling/) |
| **FUN-024** | 검수 · 평가 · 배포 게이트 | [api/confirm.py](src/lloydk/api/confirm.py) | `confirm_service.py` | [m6_evaluation/](src/lloydk/modules/m6_evaluation/) |

읽는 순서는 언제나 같습니다 — **API 라우터 → 서비스 → 코어 모듈**.
라우터는 계약(요청·응답·권한)만, 서비스는 조율만, 실제 알고리즘은 모듈에 있습니다.

---

## 3. 디렉터리 구조

```
src/lloydk/
  api/            HTTP 라우터 · 인증 · 미들웨어           (계약)
  services/       유스케이스 조율 · 트랜잭션 경계         (조율)
  modules/        m1~m6 — 실제 AI 파이프라인             (알고리즘)
    m1_synthesis    합성 문서 생성
    m2_preprocess   추출 · 정규화 · 청킹 · 마스킹
    m3_labeling     룰 엔진 + LLM 라벨링 + 합의
    m4_training     학습 · chunk 확장 · RAG 색인
    m5_inference    서빙 추론 파이프라인
    m6_evaluation   지표 · 배포 게이트 · 능동학습
  adapters/       임베딩 · 벡터스토어 · LLM · 스토리지    (외부 경계)
  repositories/   DB 접근
  db/             SQLAlchemy 모델 · 마이그레이션
  schemas/        Pydantic 요청·응답 계약
  workers/        Celery 비동기 작업
  *.py            루트 도메인 모듈 — 계층에 속하지 않는 순수 로직
                  golden_tiers · golden_signoff · golden_builder · config 등
```

> 루트의 `*.py`는 계층 밖입니다. HTTP·DB에 의존하지 않는 순수 함수 묶음이라
> 어느 계층에서든 import 합니다(`golden_signoff.py`가 대표적 — 시계·DB가 없어 감사에서 재현 가능).
> **새 파일을 여기 두려면 그 조건을 만족해야 합니다.** 그렇지 않으면 `services/` 또는 `modules/`입니다.

`modules/`의 **m1~m6 번호가 곧 파이프라인 순서**이고, 기능분해도·DFD의 프로세스와 1:1로 대응합니다.

---

## 4. 시연 — 시나리오 두 개

두 루프에 각각 하나씩. 터미널 판과 화면 판이 **같은 대본**이라, 스크립트 출력에
대응하는 콘솔 카드가 함께 찍힙니다.

```bash
# 시나리오 A — 문서 업로드 → 분류 → 검수 (루프 A)
.venv/Scripts/python.exe scripts/demo_e2e_8010.py

# 시나리오 B — 골든셋 → 검수·서명 → 재학습 → 배포 → 메트릭 (루프 B)
.venv/Scripts/python.exe scripts/demo_e2e_golden.py --register         # LLM 없는 환경(권장)
.venv/Scripts/python.exe scripts/demo_e2e_golden.py                    # LLM 있는 서버
.venv/Scripts/python.exe scripts/demo_e2e_golden.py --register --train --activate
.venv/Scripts/python.exe scripts/demo_e2e_golden.py --register --manual  # 서명은 화면에서 사람이
```

> **`--register`가 필요한 이유** — 기본 provider `noop`은 등급 라벨러가 아니라 합성 문서
> 생성기여서 룰과 합의가 되지 않고 **gold 후보가 0건**으로 끝납니다(실측). LLM이 없는
> 환경에서는 `--register`(재라벨링 없는 슬레이트 등록)가 유일하게 도는 경로이고,
> G2 이후(서명 → 재학습 → 배포)는 두 경로가 동일합니다.

대상 서버는 환경변수로 바꿉니다 — 로컬과 실서버 리허설에 같은 스크립트를 씁니다.

```bash
DEMO_BASE_URL=http://<서버>:8000  DEMO_API_KEY=<키>  python scripts/demo_e2e_golden.py
```

**화면 판**: `/demo/admin.html` (관리자 콘솔) · `/demo/parse_demo.html` (업로드·파싱)
사용법은 [docs/관리자콘솔_사용설명서.html](docs/관리자콘솔_사용설명서.html).

**시연 되돌리기** — 배포 프로파일에 따라 다릅니다.

| 프로파일 | `POST /admin/demo/purge` |
|---|---|
| 데모·파일럿 (`lite-*`) | 동작 — 콘솔 [운영] 탭 `데모 데이터 초기화` |
| **지재원 (`full-train`) · 고객사 (`onprem-local`)** | **404 (의도된 비활성)** |

하드닝 프로파일은 `demo_console_enabled=False`로 두어 **운영에서 파괴적 물리삭제 표면을
없앱니다**(관리 UI는 `serve_admin_console`로 따로 켜지므로 버튼은 보이지만 호출은 404).
그래서 실서버 리허설 데이터는 자동으로 지워지지 않습니다 — 다만
`created_by='demo-console'` + RAG 컬렉션 `demo`로 식별되므로 필요하면 DB 측에서 정리합니다.
시연 스크립트는 두 마커를 모두 붙여 보냅니다(화면 경로 `parse_demo.html`과 동일).

---

## 5. 빠른 시작

```bash
# 1) Python 3.11 venv + 의존성
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 2) .env 준비 (기본은 noop provider — 외부 LLM API 키 불필요)
cp .env.example .env

# 3) 인프라(Postgres+pgvector / Redis) 기동
make infra-up
python scripts/verify_infra.py

# 4) API + Worker
make api          # uvicorn lloydk.api.app:app --reload
make worker       # celery -A lloydk.workers.celery_app worker -l info
```

> **주의** — 테스트·로컬 기동에는 `TESTING=1`이 필요합니다. 없으면 uvicorn 기동에 실패합니다.

```bash
TESTING=1 python -m pytest -q          # 전체 테스트
```

**권한** — 관리자 콘솔은 `confirm`/`relabel`(admin·reviewer)과
`train`/`model/reload`/`keywords`(admin)를 호출합니다. 서버 `.env`의 `API_KEY_ROLE`이
`system`이면 이 호출들이 **403**입니다. 개발·시연 기본값은 `admin`이고,
테스트서버는 `scripts/deploy_testserver_dual.sh`가 `admin`을 자동 주입합니다.
`X-Actor-Role` 헤더는 역할 위조 차단을 위해 **무시**됩니다(`api_key_trust_actor_role_header=False`).

---

## 6. 운영 문서

| 영역 | 문서 |
|---|---|
| **코드 맵 — 요건→파일 상세** | [docs/CODE_MAP.md](docs/CODE_MAP.md) |
| 설치 (폐쇄망 포함) | [docs/INSTALL.md](docs/INSTALL.md) |
| 배포 가이드 | [docs/DEPLOY_GUIDE.md](docs/DEPLOY_GUIDE.md) · [docs/CLOUD_DEPLOY_RUNBOOK.md](docs/CLOUD_DEPLOY_RUNBOOK.md) |
| 운영 | [docs/OPERATION.md](docs/OPERATION.md) · [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) |
| 반출·반입 | [docs/EXPORT_IMPORT_RUNBOOK.md](docs/EXPORT_IMPORT_RUNBOOK.md) |
| KL 프록시 골든·학습 코퍼스 장기 상태 | [docs/KL_PROXY_GOLD_RUNBOOK.md](docs/KL_PROXY_GOLD_RUNBOOK.md) |
| 관리자 콘솔 사용법 | [docs/관리자콘솔_사용설명서.html](docs/관리자콘솔_사용설명서.html) |
| 파서 지원 범위 | [docs/parser_support_matrix.json](docs/parser_support_matrix.json) |
| 릴리스 점검 | [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) |

발주처 제출 문서(설계도·백서·RTM 등)는 이 저장소 밖의 `doc/result/KL_AI자료_2026-08/`에서
관리합니다.

---

## 7. 스크립트

`scripts/`는 공식 진입점과 실험용이 섞여 있습니다. **`_` 로 시작하는 파일과 `_lab/`
하위는 실험·일회성**이니 무시하십시오. 공식 진입점은 [scripts/README.md](scripts/README.md)
에 정리돼 있습니다.
