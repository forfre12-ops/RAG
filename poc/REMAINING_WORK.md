# 남은 일 — 실행 백로그

갱신 2026-08-15. **이 문서는 백로그다.** 완료 이력이 아니다. 이력은 git log 에 있다.

이전 판은 6월 상태와 최근 완료가 섞여 실행에 못 썼다. 특히 "Celery 미발사" ·
"synth review 미구현" · "prod beat 미배선" 은 **소스와 반대**였다(아래 §5 참조).
그래서 규칙을 하나 둔다.

> **항목을 적을 때 근거 파일:줄을 함께 적는다. 근거를 못 대면 백로그에 넣지 않는다.**

분류는 넷이다.

```
🔴 실버그      우리가 고칠 수 있고 고쳐야 하는 것
🟠 외부의존    KL·발주처·인프라가 움직여야 하는 것
🟡 결정대기    사람이 정해야 코드화되는 것
⚪ 측정공백    코드 문제가 아니라 증거가 없는 것
```

---

## 1. 🔴 실버그 · 미완 (우리가 고친다)

| ID | 항목 | 근거 | 규모 |
|---|---|---|---|
| ~~B-1~~ | 서버가 빌드 sha 를 노출하지 않음 | ✅ 8/15 배포·확인 (`healthz.build.git_sha=ab780792b2f5`) | 완료 |
| ~~B-2~~ | ICD §3.1 enum 을 배포본이 인식 못함 | ✅ 8/15 배포·라이브 확인 (`source_type=public` → S3 · source-prior) | 완료 |
| ~~B-2b~~ | ICD 메타데이터가 세 경로 중 두 곳에서 끊김 | ✅ 8/15 배포·라이브 확인 (아래 상세) | 완료 |
| **B-3** | `review_batch` 필터 — 검수자가 306건 중 120건 못 고름 | 코드는 8/15 배포됨. **검증은 223 에서만 가능**(182 콘솔 DB 는 0건, 306건은 223 에 있음) | 223 |
| **B-4** | 223 서버 `api_key_role=system` → 서명 불가 | `_jwt_auth.py:258` `_resolve_api_key_roles` | 설정 |
| ~~B-5~~ | 서명 잡 미등록 | ✅ 8/15 등록·재배포 후에도 생존 확인 (120건 · `signoff.html` 200 · 736KB) | 완료 |
| **B-6** | 저장 암호화 키 미주입 | `storage_encryption_enabled` — 하드닝 프로파일은 True, 키는 별도 | 운영 |
| **B-7** | JWT issuer/audience/JWKS 미설정 | 현재 `auth_mode=api_key`. 검수 권한 분리의 정석 경로 | 운영 |
| **B-8** | 임베딩 모델 오프라인 미동봉 | 로드 실패 시 HashEmbedding 무음 폴백 | 번들 |

### B-2b 상세 — 사용자가 잡은 것

> "현재 문서만 업로드되지, 메타값은 주게 되어 있지 않잖아?"

맞았다. 확인하니 **끊긴 곳이 세 군데**였다.

```
POST /classify           metadata dict 통째로              ✅ 인수 팩이 쓴 유일한 길
POST /documents          source_type 만                    ❌ 관리성 2필드 자리 없음
POST /documents/analyze  아무것도 없음                      ❌ 콘솔·시연·E2E 하니스가 타는 길
_effective_metadata      source_type 있으면 조기 반환        ❌ DB 의 관리성 2필드를 영영 안 읽음
```

인수 실행이 PASS 였던 이유는 팩이 `/classify` 로만 돌기 때문이다. KL 이 실제로 쓸 두
경로는 검증된 적이 없었다.

실측(같은 본문 · `METADATA_FLOOR_ENABLED=true`):

```
메타 없음                      S3  needs_review
access_scope=approved_only    S3  needs_review   metadata-access-conflict · metadata-management
security_marking=secret       S1  staging        metadata-floor · metadata-management
source_type=public            S3  needs_review
```

⚠ **`metadata_floor_enabled` 의 코드 기본값은 False 다.** 켜는 것은 프로파일이고
  `onprem-local` · `full-train` 둘 다 True 이며 182 의 두 스택이 각각 그것이다
  (`:8000` full-train · `:8001` onprem-local — 8/15 서버에서 확인). **223 배포 시
  프로파일을 확인할 것.** 기본값으로 뜨면 메타데이터를 보내도 아무 일도 안 일어난다.

---

## 2. 🟠 외부의존 (KL·발주처)

| ID | 항목 | 막고 있는 것 |
|---|---|---|
| **X-1** | **EDMS 가 ICD §2 3필드 전송** (`source_type`·`security_marking`·`access_scope`) | **품질 개선의 유일한 레버**. 실측 4등급 +7.7pp · S1 재현율 +33pp |
| **X-2** | 메타데이터 **오류율 5% 계약 경계** 합의 (ICD §6) | 20% 면 미탐 상한 0.0177 → 0.0624 로 사전등록 조건 붕괴 |
| **X-3** | ICD §3.1 enum 불일치 **통지** | 지금까지 `public` 이 안 걸리고 있었다는 사실 |
| **X-4** | 검수자 배정 | 아래 M-1 의 전제 |
| **X-5** | 실 회원사 문서 확보 | TS 실문서 0건 — 구조적 한계 |

## 3. 🟡 결정대기 (사람이 정해야 코드화)

| ID | 항목 | 누가 |
|---|---|---|
| **D-1** | 5요소 100점 → 등급 환산표 | 업무 소유자. **요구사항 포함 시에만 구현** |
| **D-2** | v8 요소 모델 운영 전환 | 현재 설계는 **기각**(§4). 새 설계·데이터 생기면 **미열람 잠금셋에 사전등록 후 1회 평가** |
| **D-3** | 검수 권한 방식 | 1순위 사용자 JWT 역할 · 2순위 기간 한정 별도 admin 키 · 최후 공유키 변경 |
| **D-4** | 지원 범위 명시 | 스캔 PDF OCR · 구형 `.doc`/`.ppt` · 일부 `.xls` — 명시할지 번들에 넣을지 |

## 4. ⚪ 측정공백 (코드 문제 아님)

| ID | 항목 | 현재 | 필요 표본 |
|---|---|---|---|
| **M-1** | `locked_gold_eval` 0건 | GA 릴리스 차단 | **20건**(등급별 5) = 구조적 게이트만 해소 |
| **M-2** | 실문서 정확도 주장 불가 | 전부 기계 라벨 | **120건** = 파일럿·방향성 |
| **M-3** | "무음 미탐 2% 이하" 주장 | 합성면에서만 확보 | **자동확정 189건 이상** (Wilson 0/189 = 0.0199) |
| **M-4** | 형태별 2% 주장 | 합성면 8형태 통과 | **형태마다 189건** |
| **M-5** | `P1 classifier` FAIL | readiness 게이트 차단 | M-1 과 **별개**. 분류기 성능 게이트를 따로 통과해야 함 |
| **M-6** | 재학습 라이브 완주 미확인 | 코드는 있음 | 입력 마운트 → 산출 쓰기 → 등록 → 서명 → 활성화/롤백 1회 |
| **M-7** | PG·Redis·beat 실환경 발화 미확인 | compose 에 배선됨 | 드리프트·감사체인·자동롤백이 컨테이너에서 실제로 뜨는지 |

⚠ **M-1(20건)은 GA 조건이 아니다.** locked 게이트만 열고 M-5 는 따로 남는다.

---

## 5. 이전 판에서 **틀렸던** 항목 (소스로 확인)

같은 실수를 막기 위해 남긴다. 백로그에 다시 넣지 말 것.

| 이전 주장 | 실제 | 근거 |
|---|---|---|
| A-2 Celery `.delay()` 미발사 | **발사됨** | `training_service.py:562` · `synthesis_service.py:82` |
| A-3 synth 큐 승인/반려 미구현 | **구현됨** | `api/synthesis.py:45,56` (`pending\|approved\|rejected`) |
| prod beat 미배선 | **배선됨** | `docker-compose.prod.yml:116` |
| #6 모델 버전 드리프트 | **정합** | `.env.example:93` · `.env.prod.example:107` 둘 다 v-fe4b386b |
| C-4 온도 보정 파이프라인 끊김 | **연결됨** | `v-fe4b386b/temperature.json` `source=trainer-auto` · `val_logits.jsonl` 존재 |
| 연동 API 4종을 우리가 구현 | **KL 소유·코드 발자국 0** | 우리 경로는 classify 요청 `metadata`. 2026-08-14 구현 완료 |
| KL 서버 콘솔 미배포 | **배포됨** | 경로가 `/api/v1/golden/...` — 접두사 오판이었다 |

---

## 6. 실행 순서

```
이번 주   B-1~B-3 재배포 · B-4 권한 · B-5 등록
          X-3·X-2 KL 통지·협의
          X-4 검수자 배정 → M-1 서명 20건
다음 주   지재원 서버 배포 · M-5 재판정 · M-6 재학습 리허설
          D-2·D-3 결정
이후      X-1 메타데이터 수신 시 재측정
```

## 7. 도구

```
scripts/deploy_checklist.sh              배포 6단계 확인(기본은 배포 안 함)
scripts/check_release_gate.py --require-fresh   증거 최신성·동일성
scripts/register_review_signoff_job.py   검수 서명 잡 등록(멱등·HTTP)
scripts/verify_signoff_path.py           서명 편입조건·readiness 문턱
docs/GOLDEN_SIGNOFF_RUNBOOK_2026-08-15.md  검수 절차와 함정 4개
```
