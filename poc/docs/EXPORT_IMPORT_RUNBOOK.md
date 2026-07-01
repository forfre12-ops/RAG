# Lloydk 릴리스 · Export/Import 런북 (지재원 내부)

대상: **지재원(Lloydk) 릴리스 담당자** — 학습된 분류기를 폐쇄망 고객사(KL/KOIPA)로
안전하게 내보내고(export), 활성화하고, 운영 검수 결과를 통제된 절차로 되받는(import) 절차.

짝 문서
- **고객사 설치 단계**: [`INSTALL.md`](./INSTALL.md) (반입→적재→마이그레이션→기동, §0–§11) — **여기서 재작성하지 않고 참조만** 한다.
- **일상 운영**: `doc/result/open/운영_런북.html` (일일 점검·모니터링·장애대응·큐).
- **설계·책임경계**: `doc/result/open/폐쇄망_설치_배포_설계서.html`, [[deploy-db-topology]].

> **이 런북의 존재 이유.** 죽음의 나선(고객사 교정 → 자동 재학습 → 러버스탬프 악화) 경로는
> *코드 가드*로 이미 닫혀 있다(§E). 남은 약한 고리는 **코드가 아니라 절차** — 사람이 반출/활성/
> 재학습을 어떤 순서로, 무엇을 지키며, 무엇을 절대 하지 않으며 수행하는가다. 이 문서가 그 절차를
> 코드 가드 위에 얹어 고정한다.

---

## 0. 핵심 안전 원칙 (읽고 시작)

| 원칙 | 근거(코드) | 의미 |
|---|---|---|
| **자동 활성 금지가 기본** | `retrain_auto_activate=False` (config.py) | 등록(register)은 되지만 **활성(activate)은 사람이 의도적으로** 한다. |
| **실(locked) 평가 없인 자동 활성 차단** | `deploy_gate_require_locked_eval=True`·`golden_tiers.eval_readiness` | 사람서명 locked_gold_eval이 등급별 ≥5 없으면 자동 활성 불가. 무실데이터 단계=영구 수동. |
| **고객사에서 학습 비활성** | `enable_training=False` (onprem-local 프로파일) | 학습 라우터(`/api/v1/train`·`/api/v1/train/jobs`) 자체가 미등록 → 고객사에서 재학습 트리거 불가. |
| **미탐 우선 게이트** | `deploy_gate.evaluate_deploy_gate` | `fnr_high`가 baseline+0.02 초과면 배포 거부(성능보다 고등급 미탐 차단 우선). |
| **서명 무결성** | `import_review_corrections._is_machine_reviewer` | `human_review` 승격은 **실계정 reviewer_id**만. 머신/빈값 거부. |

이 다섯을 우회하는 어떤 절차도 **금지**다(§C.5 MUST-NOT).

---

## A. 지재원 릴리스 준비 (학습 모델 → 반출 번들)

### A1. 모델 확정 + 캘리브레이션 (미보정 반출 금지)
```bash
cd poc
# 학습이 끝나면 trainer 가 <model-dir>/temperature.json 을 이미 자동 산출한다. 아래는 *재보정*이
# 필요할 때만 실행. ⚠️ --val 은 반드시 per-row {"logits":[...], "label_idx":int} 포맷 jsonl 이어야 한다.
# 평범한 text+label val 세트를 주면 dummy 분기 → exit 1(배포금지)로 실패한다. --val 을 생략하면
# 학습이 남긴 <model-dir>/val_logits.jsonl(실 logits)을 자동 사용하므로 그게 안전하다.
.venv/Scripts/python.exe scripts/calibrate_classifier.py --model-dir artifacts/<VERSION>
ls artifacts/<VERSION>/temperature.json                     # 존재 필수(목표 T≈3)
```
- 학습 디렉토리에 **함께 있어야** 반출되는 파일: 가중치(`model.safetensors`/`pytorch_model.bin`), `config.json`, `label_encoder`(있으면), **`temperature.json`**, 등급 스냅샷.
- 더미/미보정 반출은 절대 금지. (근거: [[model-serving-needs-calibration]])

### A2. 서빙경로 평가 + 배포 게이트 (반출 전 자체 판정)
- 홀드아웃(누출 제거본)으로 서빙경로 평가 → `report.json`(f1_macro·fnr_by_grade).
- 배포 게이트 기준(`deploy_gate.py`)을 **반출 전에 미리** 통과하는지 확인:
  - `fnr_high ≤ baseline + 0.02` (고등급 미탐 악화 차단 — 최우선)
  - `f1_macro ≥ baseline − 0.05`
  - degenerate(한 클래스 >99%) 아님
- baseline이 없는 **최초 배포**는 degenerate만 아니면 통과다(그래서 A1 보정·A2 평가가 더 중요).

### A3. 번들 빌드
```bash
# 먼저 dry-run 으로 manifest 만 검증(다운로드 없음)
.venv/Scripts/python.exe scripts/build_offline_bundle.py \
    --version 1.0.0-rc1 --dry-run \
    --classifier-model-dir artifacts/<VERSION>

# 실제 빌드(docker save + pip download + 가중치/temperature.json 동봉)
.venv/Scripts/python.exe scripts/build_offline_bundle.py \
    --version 1.0.0-rc1 \
    --classifier-model-dir artifacts/<VERSION>
```
- **`--version` 값 = 고객사 `.env`의 `IMAGE_TAG`.** 빌더는 이미지를 `lloydk-api:<version>`으로 태깅하고 airgap compose 기본값은 `${IMAGE_TAG:-1.0.0-rc1}`이다. `--version`과 INSTALL.md §4의 `IMAGE_TAG`가 **다르면** compose가 없는 태그를 참조해 `image not found`로 기동 실패한다. 한 문자열로 고정할 것(여기선 `1.0.0-rc1`).
- `--classifier-model-dir` **미지정 시** env `CLASSIFIER_MODEL_DIR`/`LLOYDK_CLASSIFIER_MODEL_DIR` 사용. **셋 다 없으면 베이스 모델만 번들 → 고객사에서 rule-fallback(분류기 미탑재)**. 반드시 지정·확인.
- 출력: `dist/lloydk-airgap-bundle/` (`docker-images/*.tar`, `python-deps/wheels/`, `models/classifier-trained/`(+temperature.json), `models/hf/`(KURE-v1 등), `db-migrations/alembic/`, `infra-config/docker-compose.airgap.yml`, `.env.template`, `install.sh`, `verify.sh`, `CHECKSUMS.sha256`).
- 파싱 기준 compose는 `docker-compose.airgap.yml`(기본) — dev compose(build:/minio/mlflow 잔존)로 빌드하지 말 것.

### A4. 무결성 + 릴리스 사인오프
```bash
cd dist/lloydk-airgap-bundle && bash verify.sh     # CHECKSUMS.sha256 대조 → "Checksums OK"
# manifest 는 version/target_env 를 .bundle 아래에, 이미지 집합을 .components 로 담는다(최상위 아님).
cat manifest.json | jq '{version: .bundle.version, target_env: .bundle.target_env, git_commit: .bundle.git_commit, components: (.components|keys), models: [.models[].name]}'
```
- **[결정 D1] 릴리스 서명 권한**: 이 번들(버전·model_uri·metrics·게이트 결과)을 누가 서명·승인하는가?
  릴리스 로그에 `version_label`·`f1_macro`·`fnr_by_grade`·서명자·일자를 기록. *(현재 미정 — §F)*

### A5. 반출
- **[결정 D2] 반출 매체·승인 절차**: 오프라인 매체 반입/반출은 **KL(발주처) R&R**. 매체·승인 체계·해시 대조 절차를 KL과 확정. ([[deploy-db-topology]]: 고객사=KL 폐쇄망, 오프라인 매체.)
- 반출물은 **번들뿐**. 지재원 원본 학습데이터/합성 코퍼스/내부 문서는 반출하지 않는다.

---

## B. 고객사 반입 · 활성화 · 검증

### B1. 설치 (INSTALL.md 기반 — 아래 정합 주의 반영)
[`INSTALL.md`](./INSTALL.md) §1–§10을 따른다: `verify.sh` → `install.sh`(docker load) → 모델 배치
(`/models/classifier-trained` = `CLASSIFIER_MODEL_DIR`) → `.env` → 인프라 기동 → **`alembic upgrade head`**
(등급 TS/S1/S2/S3는 baseline 마이그레이션이 `ON CONFLICT DO NOTHING`으로 자동 시드) → 앱(api·worker·beat) 기동.

> **INSTALL.md 정합 주의(에어갭 compose) — 그대로 따르면 막히는 지점**:
> - **§5 인프라 기동**: airgap compose에는 `minio`·`mlflow` 서비스가 **없다**(docker-compose.airgap.yml에서 제거). INSTALL.md §5의 `up -d postgres minio redis mlflow` 대신 **`$COMPOSE up -d postgres redis`만** 실행한다(없는 서비스를 지정하면 `no such service`로 postgres까지 기동 실패). 스토리지=로컬FS([[onprem-storage-local-fs]]).
> - **§7(a) MinIO 버킷 생성**(`mc mb m/mlflow`)은 **통째 건너뛴다**. §4의 `MINIO_ROOT_USER/PASSWORD`도 설정 불요.
> - **스모크 예시(§10)**: 필드는 `"text"`가 아니라 **`"content"`**, **`"doc_id"`는 필수**(누락 시 422), `"tenant_id"`는 **넣지 말 것**(tenant 전면 제거, [[tenant-removal-kl-portal]]). 올바른 스모크는 §B3 참조.

### B2. 모델 활성화 (⚠️ 자동 아님 — 의도적 수동)
`register_and_gate_model()`은 안전 기본값(`retrain_auto_activate=False`) 하에서 **등록만** 한다.
배포된 모델을 서빙에 태우려면 **버전을 명시적으로 활성**해야 한다.

> ⚠️ **`/api/v1/admin/*`는 admin 역할 필수.** 기본 인증(`auth_mode=api_key`)의 `X-API-Key`는 `system`
> 역할만 부여하므로(=`api_key_role` 기본값) 아래 curl을 `X-API-Key`만으로 치면 **403 forbidden**이다.
> 다음 중 하나로 admin 권한을 확보한다: **(a)[권장]** `auth_mode=jwt`(또는 `both`)에서 KL이 서명한, roles
> claim에 `"admin"`을 담은 JWT로 `Authorization: Bearer $ADMIN_JWT` 사용; **(b)** 운영 정책상 허용 시 admin
> 전용 키에 `LLOYDK_API_KEY_ROLE=admin` 분리 설정(공유 서비스 키에 admin 부여 금지); **(c)** 실험실 한정
> `LLOYDK_API_KEY_TRUST_ACTOR_ROLE_HEADER=1`(운영 poc_mode=full에선 startup 차단) + `-H "X-Actor-Role: admin"`.
> 아래에서 `$ADMIN`은 이렇게 확보한 admin 인증 헤더를 뜻한다.

- 활성 전 준비 상태 확인:
```bash
curl -s http://localhost:8000/api/v1/admin/locked-readiness $ADMIN | jq
# {ready, per_grade, missing, min_per_grade, require_locked_eval, deploy_locked_gate_passed, reason}
```
- 활성화(**G1 구현 엔드포인트**): 등록된 버전을 명시 활성 — 현재 활성본 대비 deploy gate(고등급 미탐 fnr·f1 회귀)를 적용하고, 회귀 시 `force=true`(감사됨)로만 우회. 활성+무중단 리로드까지 한 번에 수행:
```bash
curl -s -X POST http://localhost:8000/api/v1/admin/model/activate $ADMIN \
  -H 'Content-Type: application/json' -d '{"version_label":"<VERSION>"}' | jq
# {activated, blocked, forced, version_label, reason, gate, reloaded, model_version, model_loaded}
# 게이트 미통과로 blocked=true면: 회귀 확인 후 의도적일 때만 {"version_label":"<VERSION>","force":true}
```
> **[검증 게이트 — 통과 못하면 STOP]** reload 응답이 **`model_loaded: true`** 이고 **`model_version`이 방금
> 활성한 버전과 정확히 일치**해야 한다. `model_version == "rule-fallback"`(스모크에선 `rule-fallback-v0`)
> 이거나 `model_loaded: false`이면 **학습 가중치/temperature.json이 마운트되지 않은 것** — 서빙하지 말고 즉시
> 중단하고, 활성 ModelVersion.model_uri가 로컬 디렉토리인지·`/models/classifier-trained`(=`CLASSIFIER_MODEL_DIR`)가
> 실제 마운트됐는지 확인 후 재시도. (reload는 dir 미해석 시에도 `reloaded:true`를 반환하며 조용히
> rule-fallback으로 떨어질 수 있어 이 확인이 필수다.)

> **[G1 구현됨] `POST /api/v1/admin/model/activate`** (admin RBAC). 특정 등록 버전을 수동 활성한다. 현재
> 활성본 대비 deploy gate(고등급 미탐 fnr·f1 회귀)를 적용해 회귀 모델의 무심한 활성을 막고, 게이트 실패 시
> `force=true`(감사 미들웨어 기록)로만 우회한다. 활성+무중단 리로드를 한 번에 수행. (`/admin/model/reload`는
> 별개로 — 이미 활성인 버전을 라이브에 재적용하는 도구. `scripts/seed_active_model_version.py`는 데모 전용·하드코딩.)

### B3. 활성 검증
```bash
curl -s http://localhost:8000/api/v1/healthz/ready -H "X-API-Key: $API_KEY"     # 200 (미준비면 503)
# 스모크 — doc_id 필수(누락 시 422)·필드는 content·tenant_id 없이. (/classify·/healthz/ready는 X-API-Key로 200)
curl -s -X POST http://localhost:8000/api/v1/classify \
  -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
  -d '{"doc_id":"smoke-1","content":"본 문서는 당사 반도체 공정 영업비밀을 포함한다"}' | jq '{label,confidence,status,model_version}'
```
기대: 등급 + confidence + `model_version`이 방금 활성한 버전과 일치. **`model_version`이 `rule-fallback-v0`이면 FAIL(중단)** — 학습 가중치 미탑재(§B2 검증 게이트 참조).

---

## C. 운영 중 검수 결과 반입 (고객사 → 지재원) — 죽음의 나선 통제 루프

> 이 절은 고객사 검수 라벨을 지재원으로 **되받는** 절차다. 최약 고리이므로 정책·서명·금지사항을
> 엄격히 지킨다. 기본 방침은 **무반출**(집계·검수 라벨 최소반출), 문서 **원문 반출 금지**.

### C1. 반출 정책
- **[결정 D3] 반출 여부·범위**: 고객사 보정을 지재원으로 반출하는가? 반출 시 **집계(혼동행렬)·검수 라벨만**, 문서 원문은 반출 최소화하고 **KL 승인** 필수. 모델 자체는 고객사 IP가 아니며 반출 대상 아님. *(현재 미결 — 기본 무반출·집계만, §F.)*

### C2. 검수 CSV 포맷 (`import_review_corrections.py` 입력)
필수: `doc_id`, `model_label`, `human_label`(TS/S1/S2/S3). 권장/필수-정책: `reviewer_id`(**실계정**), `review_decision`, `reason_code`, `text`(gold 편입·DB기록엔 원문 필요).

### C3. 서명 무결성 (자동 거부)
- `reviewer_id`는 **실제 사람 계정**만 인정. `_is_machine_reviewer`가 빈값·`human`·`ai_assist`·`claude`·`llm_*`·머신 접두사를 **거부**한다. 머신/플레이스홀더 서명으로 `human_review` 승격은 불가. ([[human-review-signoff-integrity]])

### C4. 반입 실행
```bash
cd poc
# 1) 검증(적재/기록 없이 포맷·서명만 점검)
.venv/Scripts/python.exe scripts/import_review_corrections.py customer_review.csv --dry-run

# 2) 적용 — 고객사 검수 반입은 반드시 --as-candidate (gold_candidate=학습용, 평가정답 오염 방지)
.venv/Scripts/python.exe scripts/import_review_corrections.py customer_review.csv \
    --merge-gold --write-db --as-candidate
```
- `--merge-gold`: text 있는 검수를 gold_real에 편입, 기존 doc_id는 `llm_judge → human_review`로 **승격**(label_source 갱신).
- `--write-db`: `tb_document_labels` upsert (`labeled_by=human_review`, `is_verified=True`).
- (기본 out-dir `datasets/corrections/`.)
- ⚠️ **`--merge-gold`은 기본으로 `label_source=human_review`(=`golden_tiers`가 `locked_gold_eval` 평가정답으로 분류)로 쓴다.** 그 파일(`datasets/gold_real/classification_gold.jsonl`)은 eval 홀드아웃을 깎는 원천이라, 고객사 검수엔 **반드시 `--as-candidate`**를 붙여 `gold_candidate`(학습용)로 기록한다 — 안 붙이면 고객 라벨이 **말없이 평가 정답이 된다**(train-on-test, §C.5 #7). 플래그 없는 기본형은 **지재원 골든셋 검수 전용**.

### C5. 절대 금지 (MUST NOT) — 죽음의 나선 재개 차단
1. 고객사 corrections를 **locked_gold_eval(평가 정답)로 직접 학습** 금지 — train-on-test. locked는 학습에서 영구 제외([[golden-set-3split-decision]], `golden_tiers.train_records`).
2. 반입 데이터로 **자동 재학습·자동 활성** 금지 — `retrain_auto_activate`를 True로 켜 두고 반입/재학습 루프를 돌리지 말 것.
3. `eval_readiness` 미충족(locked 등급별 <`deploy_gate_min_locked_per_grade`=5)에서 **자동 활성** 금지.
4. **머신/빈 reviewer_id**로 human_review 승격 금지(§C.3).
5. 고객사 문서 **원문을 무단 반출** 금지(§C.1, KL 승인·최소반출).
6. 고객사와 지재원 홀드아웃(val/test)에 **겹치는 문서**를 train에 편입 금지(누출; `corrections_rebuild`가 holdout_paths로 차단하나 절차로도 확인).
7. **`--merge-gold`가 만든 `human_review` 레코드를 평가 정답으로 굳히지 말 것.** `--merge-gold`는 편입 검수를 `label_source=human_review`로 기록하고 `golden_tiers.tier_of`는 이를 실계정 reviewer와 함께 **`locked_gold_eval`(유일한 평가 정답)**으로 분류한다. 그런데 `datasets/gold_real/classification_gold.jsonl`은 eval 홀드아웃(`build_p1_holdout_split`)과 `build_operational_readiness`의 human_review 릴리스-블로커 카운트의 **원천**이다. 따라서 (a) 고객사 보정을 `--merge-gold`로 편입한 뒤 eval 홀드아웃/`locked_eval_jsonl`을 gold_real에서 재생성해 고객사 라벨을 평가 정답으로 굳히지 말 것; (b) `build_operational_readiness`의 human_review 게이트를 고객사 import로 채우지 말 것; (c) 재학습 학습셋을 gold_real에서 파생할 땐 **반드시 `build_p1_holdout_split`를 재실행**해 holdout(및 locked)을 재분리할 것. → **G2 해소(구현됨)**: 고객사 반입 시 `--as-candidate`를 붙이면 `label_source=customer_review`·`review_status=gold_candidate`로 기록돼 `golden_tiers.tier_of`가 TIER_CANDIDATE(학습용)로 분류, 평가 정답이 되지 않는다(§C.4). 플래그 없는 기본형은 지재원 골든셋 검수(human_review 승격)이므로 **고객사 반입엔 `--as-candidate` 필수**.

### C6. (선택) 지재원 재학습 반영 — 결정 시에만
> **[결정 D4] 고객사 보정의 재학습 사용 여부는 현재 미결**(기본 권고: 무반출·집계만).
반영을 결정하면: 반입 라벨을 **silver_train/gold_candidate로만** 편입(locked 아님 — `import_review_corrections … --as-candidate`) → 재학습 →
**locked eval에서 검증** → §A.2 배포 게이트 통과 → §A 번들 버전업 → §B **수동 활성**. 순서·게이트를 건너뛰지 않는다.

---

## D. 롤백

- **자동**(opt-in): `auto_rollback_enabled=True`이고 라이브 `fnr_high`가 baseline+tolerance 초과 회귀 시 `evaluate_rollback_need` → `rollback_active_model(reason)`.
- **수동**: 콘솔에서 `TrainingRepo.rollback_to_previous(reason=…)`(keyword-only 인자)로 직전 활성 버전 복귀 후 `POST /api/v1/admin/model/reload $ADMIN`(admin 권한 필요 — §B2). 롤백 사유·시각을 릴리스 로그에 기록.

---

## E. 안전 게이트 레퍼런스 (설정·기본값·강제지점)

| 설정 | 기본값 | 강제/의미 |
|---|---|---|
| `enable_training` | `False`(lite-*/onprem) · `True`(full-train) | False면 학습 라우터(`/api/v1/train`·`/train/jobs`) 미등록·태스크 skip. 고객사=False. |
| `retrain_auto_activate` | **`False`** | 활성엔 `passed ∧ auto ∧ ¬eval_block` 모두 필요. 기본은 등록만. |
| `deploy_gate_require_locked_eval` | **`True`** | `eval_block = require_locked ∧ (eval_ready≠True)`. locked 미충족 시 활성 차단. |
| `deploy_gate_min_locked_per_grade` | `5` | locked_gold_eval 등급별 최소 표본(readiness). |
| `require_safety_gates` | `False`(dev) · **`True`**(onprem-local·full-train) | True인데 안전게이트 OFF면 startup 차단(fail-clear). |
| `auto_rollback_enabled` | opt-in | 라이브 미탐 회귀 시 자동 롤백 여부. |

관련 코드: `services/training_service.py`(`register_and_gate_model`·`rollback`), `modules/m6_evaluation/deploy_gate.py`, `golden_tiers.py`(`eval_readiness`), `repositories/training_repo.py`(`activate_model_version`·`rollback_to_previous`), `api/admin.py`(`/model/reload`·`/locked-readiness`).

---

## F. 사람 판단이 필요한 결정 항목 (미결 — 확정 후 이 표 갱신)

| # | 결정 | 현재 상태 | 소유 |
|---|---|---|---|
| D1 | 릴리스 서명 권한(누가 번들을 승인·서명) | 미정 | 지재원 |
| D2 | 반출 매체·승인·해시대조 절차 | KL R&R 협의 | KL↔지재원 |
| D3 | 고객사 검수 결과 반출 범위(집계-only vs 라벨+원문) | 기본 무반출·집계 | KL 승인 |
| D4 | 고객사 보정의 지재원 재학습 사용 여부 | 미결(기본 미사용) | 지재원 |
| G1 | 수동-활성 엔드포인트 `POST /admin/model/activate`(deploy gate 적용·force 우회 감사) | ✅ 구현(§B.2) | 지재원 개발 |
| G2 | `--merge-gold`이 human_review(=locked_gold_eval) 생성 → 고객사 반입용 `--as-candidate`(gold_candidate·is_verified=False)로 eval 오염 차단 | ✅ 구현(§C.4) | 지재원 개발 |

---

## 부록 — 명령 요약 (cheat sheet)

```bash
# 지재원: (재)보정 → 번들 → 검증   (--version = 고객사 IMAGE_TAG 와 반드시 동일)
python scripts/calibrate_classifier.py --model-dir artifacts/<V>          # --val 생략=val_logits.jsonl 자동
python scripts/build_offline_bundle.py --version 1.0.0-rc1 --dry-run --classifier-model-dir artifacts/<V>
python scripts/build_offline_bundle.py --version 1.0.0-rc1 --classifier-model-dir artifacts/<V>
( cd dist/lloydk-airgap-bundle && bash verify.sh )

# 고객사: 설치는 INSTALL.md(§B1 주의 반영), 활성 상태·리로드 ($ADMIN=admin 인증 — §B2; X-API-Key만이면 403)
curl .../api/v1/admin/locked-readiness $ADMIN
curl -X POST .../api/v1/admin/model/reload $ADMIN

# 되받기(검수 반입): 검증 → 적용   (⚠️ --merge-gold = locked_gold_eval 생성, §C.5 #7)
python scripts/import_review_corrections.py customer_review.csv --dry-run
python scripts/import_review_corrections.py customer_review.csv --merge-gold --write-db --as-candidate
```
