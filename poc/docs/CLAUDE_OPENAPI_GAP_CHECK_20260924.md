# OpenAPI(03_openapi_koipa_kl.yaml) ↔ 실 라우터 대조 — 2026-09-24

읽기 전용 조사. 코드·문서는 고치지 않았다. 배경: KL정보통신이 "분류 모델 API 통신 방식 및
인터페이스 명세서"를 요청했고, 그 정본으로 추정된 `doc/03_openapi_koipa_kl.yaml` 이 실제
구현(`poc/src/koipa/api/`)과 어긋난 곳이 있는지 확인한다.

저장 위치는 작업 지시에 적힌 경로(`poc/docs/CLAUDE_OPENAPI_GAP_CHECK_20260924.md`)를 그대로
따랐다. 다만 메모리 `feedback-collab-with-codex-read-only-folder-2026-09-15`에는 이런 종류의
1회성 조사 산출물을 `poc/reports/CLAUDE_*`에 두는 선례가 있고(`poc/reports/CLAUDE_serving_path_eval_comparison_v1.md`
등), `poc/docs/`에는 아직 `CLAUDE_*` 접두 파일이 없었다(2026-09-24 확인). 지시된 경로가
더 구체적이라 그대로 따랐지만, 위치를 옮기고 싶으면 알려달라.

## 0. 결론 요약

| 항목 | 상태 | 근거파일줄 |
|---|---|---|
| (1) yaml 엔드포인트 전체 목록화 | 완료 — 69 경로 · 72 오퍼레이션(operation) | `doc/03_openapi_koipa_kl.yaml` paths: 79~2013 (아래 2절 표) |
| (2) 코드 실제 엔드포인트 전체 목록화 | 완료 — 라우터 파일 17개 · 72 오퍼레이션 | `poc/src/koipa/api/*.py` (아래 2절 표) |
| (3-a) yaml에 있는데 코드에 없음 | **0건** | 2절 표 전량 일치(코드 근거 열 전부 채워짐) |
| (3-b) 코드에 있는데 yaml에 없음 | **0건** | 동일 |
| (3-c) golden_reviewer_access(배정·블라인드) API 3건이 yaml 반영됐는가 | **반영됨** — 코드와 같은 커밋 | `git log`: 커밋 `dadbb5542`(2026-09-22 10:30:09 +0900)가 `doc/03_openapi_koipa_kl.yaml`에 133줄 추가하며 `golden.py`의 배정 API 3개와 동시에 들어감 |
| (4) yaml 최종 수정 시각 | 커밋 `dadbb5542`, 2026-09-22 10:30:09 +0900(author forfre12-ops). 이후 HEAD(`eac60757`, 2026-09-24)까지 이 파일은 **무변경** | `git log --follow -- doc/03_openapi_koipa_kl.yaml`, `git diff dadbb5542..HEAD -- doc/03_openapi_koipa_kl.yaml`(출력 없음) |
| (4) 작업 지시의 "git status에 M으로 떠 있었다"는 전제 | **현재는 재현 안 됨** — `git status`·`git diff` 모두 이 파일에 대해 무출력(정정 사항으로 보고, 비난 아님) | `git status --porcelain=v1 -z` 전체 출력에 `openapi` 문자열 0건(2026-09-24 조사 시점) |
| (4) 409 등급변경 응답(`PUT /schema/grades`) 반영 | **반영됨** | `doc/03_openapi_koipa_kl.yaml:1024-1025`(커밋 `f74968bfc` 2026-09-11 최초, `1b937fbc` 2026-09-13 401/403/422 보강) |
| (4) `metadata-management-underclass` 게이트명 반영 | **미반영(이름 기준)** — 관련 입력 필드(`access_scope`)의 일반적 효과 서술은 있으나 게이트 고유명은 문서 어디에도 없음 | `warnings` 필드 3곳(`:2043,2190,2255`) 전부 `type: array, items: string`로 2026-05~07 원시 작성 이후 무변경. `access_scope` 서술은 `:2106-2111`, 2026-07-29 커밋 `c9671ca11`(게이트 구현 2026-09-10보다 이전) |

## 1~3. yaml ↔ 코드 전체 대조표 (72 오퍼레이션 전량)

방법: `doc/03_openapi_koipa_kl.yaml`의 `paths:` 블록을 들여쓰기 기준으로 파싱(경로 69개,
메서드 72개 — `/schema/grades`·`/admin/keywords`·`/golden/assignments` 3개 경로만 메서드 2개).
코드 쪽은 `poc/src/koipa/api/` 안의 `.py` 18개 파일 전부에서 `@router.*`/`@html_router.*`
데코레이터를 두 가지 정규식(줄 안 경로 포함형, 줄바꿈 후 경로형)으로 교차 추출하고, `app.py`의
`include_router` 접두(`/api/v1`, `admin.py`·`keyword_admin.py`는 추가로 `/admin`)를 반영해
최종 경로를 만들었다. `_rbac.py`의 `@router.post("/train", ...)`는 docstring 안 사용례일 뿐
실제 라우트가 아니라 제외했다(직접 열어 확인). 두 목록을 집합으로 비교한 결과 **72 = 72,
완전 일치**.

| yaml줄 | 메서드 | 경로 | 코드 근거 | audience | 비고 |
|---|---|---|---|---|---|
| 80 | GET | `/healthz` | `health.py:370` | kl (IF-01) | |
| 98 | GET | `/healthz/live` | `health.py:314` | internal | |
| 111 | GET | `/healthz/ready` | `health.py:320` | internal | |
| 126 | GET | `/healthz/deep` | `health.py:344` | internal | |
| 139 | POST | `/classify` | `classify.py:16` | internal | |
| 168 | POST | `/classify/stream` | `classify_stream.py:113` | internal | |
| 193 | POST | `/classify/explain` | `explain.py:125` | internal | |
| 218 | POST | `/classify/async` | `async_classify.py:27` | kl (IF-03) | |
| 241 | POST | `/classify/batch` | `async_classify.py:34` | internal | |
| 296 | GET | `/classify/jobs/{job_id}` | `async_classify.py:46` | kl (IF-05) | |
| 315 | GET | `/classify/{doc_id}` | `async_classify.py:55` | kl (IF-06) | |
| 333 | POST | `/confirm` | `confirm.py:72` | internal | |
| 366 | GET | `/review-queue` | `confirm.py:84` | internal | |
| 410 | POST | `/relabel` | `confirm.py:151` | internal | |
| 444 | POST | `/train` | `training.py:24` | internal | 조건부: `enable_training or enable_incremental_retrain` 일 때만 라우터 등록(`app.py:381-385`) |
| 483 | GET | `/train/jobs/{train_job_id}` | `training.py:32` | internal | 위와 동일 조건 |
| 515 | GET | `/train/jobs` | `training.py:40` | internal | 위와 동일 조건 |
| 543 | POST | `/synth/generate` | `synthesis.py:30` | internal | 조건부: `enable_synthetic_generation`(`app.py:394-395`) |
| 601 | GET | `/synth/jobs/{synth_job_id}` | `synthesis.py:80` | internal | 위와 동일 조건 |
| 636 | GET | `/synth/queue` | `synthesis.py:46` | internal | 위와 동일 조건 |
| 664 | GET | `/synth/coverage` | `synthesis.py:56` | internal | 위와 동일 조건 |
| 704 | POST | `/synth/{synth_id}/review` | `synthesis.py:90` | internal | 위와 동일 조건 |
| 748 | POST | `/documents` | `documents.py:61` | kl (IF-02) | |
| 792 | GET | `/documents/{doc_id}/similar` | `documents.py:561` | internal | |
| 839 | POST | `/documents/analyze` | `documents.py:259` | internal | |
| 875 | POST | `/guide/documents` | `guide.py:26` | internal | |
| 924 | GET | `/guide/documents/{guide_id}` | `guide.py:60` | internal | |
| 956 | GET | `/schema/grades` | `schema_admin.py:18` | internal | |
| 975 | PUT | `/schema/grades` | `schema_admin.py:23` | internal | 409 응답 문서화됨(6절 참조) |
| 1030 | GET | `/metrics/latest` | `metrics.py:14` | internal | |
| 1043 | GET | `/metrics/history` | `metrics.py:22` | internal | |
| 1064 | GET | `/metrics/confusion-matrix/{model_version}` | `metrics.py:27` | internal | |
| 1096 | POST | `/golden/build` | `golden.py:237` | internal | |
| 1115 | GET | `/golden/jobs/{job_id}` | `golden.py:305` | internal | |
| 1133 | GET | `/golden/jobs/{job_id}/review.html` | `golden.py:1428`(html_router) | internal | |
| 1151 | GET | `/golden/builds` | `golden.py:254` | internal | |
| 1188 | POST | `/golden/jobs/register` | `golden.py:273` | internal | |
| 1216 | GET | `/golden/jobs/{job_id}/signoff.html` | `golden.py:1454`(html_router) | internal | |
| 1243 | GET | `/golden/jobs/{job_id}/signoff/preflight` | `golden.py:1483` | internal | |
| 1266 | POST | `/golden/jobs/{job_id}/signoff` | `golden.py:1509` | internal | |
| 1305 | GET | `/promotions/pending` | `promotion.py:23` | internal | |
| 1317 | POST | `/promotions/promote` | `promotion.py:34` | internal | |
| 1334 | POST | `/admin/model/reload` | `admin.py:32`(prefix `/admin`) | internal | |
| 1347 | POST | `/admin/model/activate` | `admin.py:102`(prefix `/admin`) | internal | |
| 1370 | GET | `/admin/escalation-held` | `admin.py:326`(prefix `/admin`) | internal | |
| 1383 | GET | `/admin/locked-readiness` | `admin.py:360`(prefix `/admin`) | internal | |
| 1396 | GET | `/admin/dashboard` | `admin.py:433`(prefix `/admin`) | internal | |
| 1409 | GET | `/admin/keywords` | `keyword_admin.py:26`(prefix `/admin`) | internal | |
| 1426 | POST | `/admin/keywords` | `keyword_admin.py:44`(prefix `/admin`) | internal | |
| 1455 | PATCH | `/admin/keywords/{keyword_id}` | `keyword_admin.py:58`(prefix `/admin`) | internal | |
| 1486 | GET | `/admin/audit-log` | `admin.py:241`(prefix `/admin`) | internal | |
| 1508 | POST | `/admin/model/rollback` | `admin.py:174`(prefix `/admin`) | internal | |
| 1534 | GET | `/review-queue/{classification_id}/evidence` | `confirm.py:133` | internal | |
| 1557 | GET | `/golden/summary` | `golden.py:322` | internal | |
| 1576 | GET | `/golden/jobs` | `golden.py:731` | internal | |
| 1593 | GET | `/golden/candidates` | `golden.py:352` | internal | |
| 1615 | GET | `/golden/candidates/summary` | `golden.py:390` | internal | |
| 1628 | GET | `/golden/candidates/decisions` | `golden.py:404` | internal | |
| 1644 | GET | `/golden/candidates/session` | `golden.py:420` | internal | |
| 1657 | POST | `/golden/candidates/upload` | `golden.py:444` | internal | |
| 1687 | GET | `/golden/candidates/{doc_id}` | `golden.py:476` | internal | |
| 1706 | POST | `/golden/candidates/{doc_id}/decision` | `golden.py:535` | internal | |
| 1740 | POST | `/golden/candidates/promote` | `golden.py:587` | internal | |
| 1772 | POST | `/golden/candidates/{doc_id}/provenance` | `golden.py:493` | internal | |
| 1803 | GET | `/golden/candidates/login.html` | `golden.py:1327`(html_router) | internal | |
| 1819 | GET | `/golden/candidates/manage.html` | `golden.py:1407`(html_router) | internal | |
| **1832** | **GET** | **`/golden/assignments`** | `golden.py:696` | internal | **9/22 신설(커밋 `dadbb5542`)** |
| **1900** | **POST** | **`/golden/assignments`** | `golden.py:664` | internal | **9/22 신설(커밋 `dadbb5542`)** |
| **1933** | **POST** | **`/golden/assignments/revoke`** | `golden.py:680` | internal | **9/22 신설(커밋 `dadbb5542`)** |
| 1965 | GET | `/dashboard/summary` | `metrics.py:35` | internal | |
| 1981 | GET | `/metrics-prom` | `prom_metrics.py:852` | internal | FastAPI 자동 스펙에서는 `include_in_schema=False`로 빠지지만, 이 수기 yaml에는 수록돼 있음 |
| 1998 | POST | `/admin/demo/purge` | `admin.py:500`(prefix `/admin`) | internal | |

행 수 검증: 위 표 72행 = yaml에서 파싱한 72 오퍼레이션 = 코드에서 데코레이터로 추출한 72
오퍼레이션(파일별 개수: health 4·classify 1·classify_stream 1·explain 1·async_classify 4·
confirm 4·documents 3·guide 2·schema_admin 2·metrics 4·prom_metrics 1·golden(router) 20·
golden(html_router) 4·promotion 2·admin 8·keyword_admin 3·training 3·synthesis 5 = 72,
`rg`로 2가지 방식(수동 목록 vs `^@\w*router\w*\.(get|post|...)\(` 단일 정규식) 교차 실행해
둘 다 72로 일치 확인).

## 4. `x-audience` 태깅 — KL이 실제로 부르는 경로는 5개뿐

문서 서두(공식 6~28줄)에 "KL 웹시스템이 호출하는 것은 아래 5개 경로(IF-01,02,03,05,06)"라고
적혀 있고, 실제로 `x-audience: kl` 태그가 붙은 오퍼레이션도 정확히 5개였다(`grep -c`로 셈:
kl 5건·internal 67건, 합 72건 = 전체 오퍼레이션 수와 일치, 태깅 누락 0건).

| IF 번호 | 메서드/경로 | 코드 근거 | 조건부 마운트 여부 |
|---|---|---|---|
| IF-01 | GET `/healthz` | `health.py:370` | 아니오(항상 등록) |
| IF-02 | POST `/documents` | `documents.py:61` | 아니오 |
| IF-03 | POST `/classify/async` | `async_classify.py:27` | 아니오 |
| IF-05 | GET `/classify/jobs/{job_id}` | `async_classify.py:46` | 아니오 |
| IF-06 | GET `/classify/{doc_id}` | `async_classify.py:55` | 아니오 |

IF-04(엔진→KL 콜백)는 문서 자신이 "본 명세 대상 아님"이라고 명시했고(`:19`), KL 쪽 수신
엔드포인트라 이 yaml의 `paths`에 없는 게 맞다(콜백 스펙 부재를 결함으로 세지 않았다).
**결론: KL이 실제로 호출하는 5개 경로는 코드와 100% 일치하고, 전부 배포 프로파일과 무관하게
항상 등록된다.** golden·admin·train·synth 계열(67개, 이번 대조에서 어긋난 곳 0건이었던 바로
그 목록)은 문서 스스로 "KL 연동 대상이 아니다"(`:28`)라고 선을 그은 내부/운영 화면용이다.

## 5. yaml 수정 이력 (git log)

```
$ git log --follow -- doc/03_openapi_koipa_kl.yaml   (최근 3건)
dadbb5542  2026-09-22 10:30:09 +0900  feat(골든셋): 전문가별 검수 배정 + 제안 등급 숨김 + 별칭 ID + 관리 화면
                                       → doc/03_openapi_koipa_kl.yaml 133줄 추가(전량 신규, 삭제 0)
1b937fbc5  2026-09-13 21:00:19 +0900  docs(계약·도구): OpenAPI 가 실제로 나오는 오류 상태를 안 적고 있었다
                                       → /schema/grades 에 401/403/422 보강
f74968bfc  2026-09-11 02:50:11 +0900  docs(DB·계약): 정의서를 PostgreSQL 정본으로 되돌리고, 벡터 표·표준명·409 응답을 반영한다
                                       → /schema/grades PUT 에 409 최초 추가
```

`git diff dadbb5542..HEAD -- doc/03_openapi_koipa_kl.yaml` 출력 없음(무변경) — HEAD는 `eac60757`
(2026-09-24 15:34:29 +0900, "사실 우선 문서 생성 1~9차…"). `git status --porcelain=v1 -z`
전체를 뒤져도 이 파일 경로가 한 번도 안 나온다(2026-09-24 확인 시점). 작업 지시문의 "git
status에 M으로 떠 있었다"는 전제는 **현재 시점 기준 재현되지 않는다** — 조사 시작 전 다른
세션이 같은 리포에서 동시에 파일을 만졌다가 이미 커밋했거나, 확인 시점이 달랐을 가능성이
있지만 원인은 확인하지 못했다(추정하지 않는다).

golden.py 자체도 9/20 이후 커밋이 `dadbb5542` 단 1건뿐이고, 그 커밋과 HEAD 사이에는 라우트
데코레이터(`@router.`/`@html_router.`) 추가·삭제가 0건이었다(`git diff` 결과 직접 확인). 같은
구간의 `poc/src/koipa/api/` 변경은 정적 프런트엔드 자산 3개(`static/admin.html`,
`static/app.js`, `static/review_reason_ko.js`)뿐이며 이는 `review_reason_ko.js`의 게이트
표시문구 수정(9/22 후속, 아래 6절)과 일치하고 새 API 경로는 아니다. **즉 9/22~24 사이 golden.py에
yaml 미반영 신규 라우트는 없다.**

## 6. 최신 게이트 반영 여부

### 6-1. 409 등급변경 응답 (`PUT /schema/grades`) — 반영됨

`doc/03_openapi_koipa_kl.yaml:1005-1027`에 200/401/403/409/422 다섯 응답이 모두 있고, 409
설명이 "서빙 분류기를 멈추게 하는 변경이라 저장하지 않음(force 와 사유 없음)"으로 실제 동작
(`schema_admin_service.py`의 force/force_reason 우회)과 일치한다. `git blame` 기준 409는
`f74968bfc`(2026-09-11), 401/403/422는 `1b937fbc5`(2026-09-13)가 추가했다.

### 6-2. `metadata-management-underclass` 게이트 — 이름 기준 미반영

이 문자열은 `doc/03_openapi_koipa_kl.yaml` 전체에서 0건이다(대소문자 무시 검색). 관련이
있을 수 있는 자리 두 곳을 직접 열어 확인했다.

- `ClassifyResponse.warnings`(`:2190`) 및 동형 필드 2곳(`:2043` DocumentAnalysisResponse,
  `:2255` ReviewQueueResponse) — 전부 `type: array, items: {type: string}`, 열거값 없음.
  `git blame`: 세 곳 모두 2026-05~07(원시 작성) 이후 무변경. 즉 이 필드는 애초에 게이트
  이름을 나열하는 방식으로 설계돼 있지 않다 — `metadata-management-underclass` 뿐 아니라
  현재 `review_reasons.py`의 다른 게이트 태그(9/9 기준 15개 안팎, 예: `metadata-management-conflict`·
  `no-auto-confirm-grade` 등)도 전부 마찬가지로 이름이 안 나온다. **이 게이트만 빠뜨린 게
  아니라, 애초에 어떤 게이트도 이름으로 문서화하지 않는 구조다.**
- `DocumentInput.metadata.access_scope`(`:2106-2111`) — "접근범위 → 비밀관리성(M) 신호.
  표기 없고 approved_only/designated/department이면 예측이 낮을 때 검수 라우팅"이라고
  적혀 있다. `git blame`: `c9671ca11`, 2026-07-29. 이 서술은 게이트 구현일(2026-09-10,
  메모리 `metadata-management-underclass-gate-2026-09-10`)보다 **한 달 반 이상 이르다** —
  즉 이 게이트를 겨냥해 나중에 쓴 문장이 아니라, 우연히 방향이 맞아떨어지는 사전 서술이다.
  2026-09-22에 있었던 검수 사유 표 누락 수정(리뷰 사유 UI 문구 `review_reason_ko.js`
  보강, 메모리 `metadata-management-underclass-gate-2026-09-10`의 9/22 후속 절)은 이
  yaml에 반영되지 않았다(위 5절에서 확인한 대로 `static/*.js`만 바뀌었다).

**요약: 게이트가 만드는 '효과'(access_scope 기반 검수 라우팅)는 게이트 구현보다 먼저부터
일반론으로 서술돼 있었고, 게이트 '고유명'과 최신 표시 문구는 애초 설계상 이 문서에 안 실리는
정보다.** 이걸 결함으로 볼지는 이 yaml이 게이트 이름까지 노출할 계약인지에 달려 있는데,
현재 구조(자유 문자열 배열)로는 판단할 근거가 없다 — 결정 필요 사항으로만 남긴다.

## 7. 교차 검증 — 리포 자체 점검 도구 실행 결과

이 리포에는 이미 같은 목적의 도구 `poc/scripts/openapi_consistency.py`(B5)가 있다. 손으로 만든
위 대조표와 별도로 실행해 교차 확인했다(`poc/.venv`, `TESTING=1 PYTHONIOENCODING=utf-8`).

```
YAML 정의   : 69 엔드포인트     Router 구현 : 61 엔드포인트     일치 : 61
미구현(yaml only)      : 8      스펙 누락(router only) : 0      매칭률 88.4%
```

8건이 "yaml에만 있다"고 나오지만, **전부 도구 자체의 낡은 예외목록·프로파일 처리 때문에
생기는 오탐이었다** (직접 코드를 열어 8건 전부 실존 확인):

| 경로 | 도구가 오탐하는 이유 | 실제 상태 |
|---|---|---|
| `/api/v1/dashboard/summary`·`/api/v1/admin/demo/purge` | `openapi_consistency.py:133-134`의 `_ROUTER_IGNORE_EXACT`가 "yaml 범위 밖"이라 주석해 두고 라우터 쪽에서 제외하는데, 정작 yaml에는 이미 수록돼 있다(`:1964`,`:1997`) — 예외목록이 낡았다 | 코드·yaml 둘 다 있음(`metrics.py:35`, `admin.py:500`) |
| `/api/v1/metrics-prom` | 같은 파일 `:129` 예외목록. FastAPI 자동 스펙에서 숨겨진 것과 이 수기 yaml에 수록된 것을 혼동 | 코드·yaml 둘 다 있음(`prom_metrics.py:852`) |
| `/api/v1/synth/*` 5건 | `enable_synthetic_generation=False`인 로컬 프로파일에서 라우터 미등록. 스크립트가 `/train/*`엔 이 조건부 제외 로직(`:259-270`)을 넣어놨지만 `/synth/*`엔 똑같이 안 넣었다 | 코드(`synthesis.py`)에 5개 라우트 전부 존재, yaml에도 있음 |

부수 발견(경미, 이번 대조 결과에 영향 없음): 같은 예외목록의 `/api/v1/rag/search`(`:135`,
"LLM-free 내부 검색")는 `poc/src` 전체에서 0건 검색된다 — yaml에도 코드에도 없는 죽은 참조로
보인다. 읽기 전용 조사라 `openapi_consistency.py` 자체는 고치지 않았다.

이 스크립트 실행은 `poc/reports/openapi_consistency.json`을 남기는데 `poc/reports/`는
gitignore 대상이라(`poc/.gitignore:30`) 커밋 상태에 영향이 없다.

## 8. 범위 밖 참고사항 — 이번에 대조하지 않은 관련 문서

`doc/docs_registry.yaml`을 보면 `status: current`·`audience: customer`로 등록된 문서가
이 yaml 말고도 더 있다. 이번 작업은 지시받은 대로 yaml ↔ 코드만 봤고 아래 문서들의 내용은
열지 않았다 — 존재만 확인했다.

| ID | 제목 | 경로 | 마지막 커밋 |
|---|---|---|---|
| DOC-OPENAPI-001 | KL OpenAPI YAML | `doc/03_openapi_koipa_kl.yaml` | `dadbb5542` 2026-09-22(이번 조사 대상) |
| DOC-API-001 | KL 연동 인터페이스 규약서 ICD | `doc/result/감리정본/KL연동_인터페이스_규약서_ICD.html` | `e56730c7` 2026-08-29(미확인) |
| DOC-GAMRI-010 | API 통신 방안 검토 회신 | `doc/감리문서/KL_API_통신방안_검토회신.html` | `0016afac` 2026-09-05(미확인) |

제목만 보면 "인터페이스 명세서"라는 요청과 가장 가깝게 읽히는 쪽은 이 yaml보다 오히려
DOC-API-001(ICD)일 수 있다. ICD는 이 yaml보다 먼저 멈췄고(8/29 vs 9/22), 9/10(메타데이터
게이트)·9/11·9/13(409·오류코드)·9/22(검수 배정) 변경이 반영됐는지는 **확인하지 않았다** —
KL에 실제로 내보낼 문서를 이 yaml로 확정하기 전에 ICD와 회신 문서도 같은 방식으로 한 번 더
대조해 보는 걸 권한다(이번 조사 범위에는 없었다).

## 결론

`doc/03_openapi_koipa_kl.yaml`(69 경로/72 오퍼레이션)과 `poc/src/koipa/api/` 실 라우터
(17개 파일/72 오퍼레이션)는 **경로+메서드 기준으로 완전히 일치한다 — 어긋나는 항목 0건**
(양방향 모두, 리포 자체 도구 `openapi_consistency.py`가 보고한 8건은 도구의 낡은 예외목록·
프로파일 처리 누락에 의한 오탐으로 직접 코드 대조 후 확인). 9/22 golden_reviewer_access
배정·블라인드 API 3개도 코드와 같은 커밋(`dadbb5542`)으로 yaml에 반영돼 있다. 다만
`metadata-management-underclass` 같은 게이트 고유명은 이 yaml의 어떤 응답 스키마에도
열거되지 않으며(구조적으로 전 게이트가 다 그렇다), 이 문서 말고도 "인터페이스 명세서"에
더 가까울 수 있는 문서(ICD, API 통신 방안 검토 회신)가 리포에 별도로 있고 이번엔 대조하지
않았다.
