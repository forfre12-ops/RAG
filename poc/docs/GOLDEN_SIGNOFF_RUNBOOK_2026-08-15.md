# 골든셋 검수 실행 절차 — 배포 전에 반드시 읽을 것

작성 2026-08-15. 검수자를 앉히기 전에 서명 경로를 끝까지 HTTP 로 돌려 확인한 결과다.
**함정이 네 개** 있고 하나라도 놓치면 검수 회차가 통째로 헛돈다.

---

## 0. 결론부터 — 검수는 품질을 올리지 않는다

```
골든 서명  -> locked_gold_eval -> **평가 정답**       측정용
운영 교정  -> promotion        -> 그 문서 1건만 확정   "폭발반경 1문서 · 재학습과 무관"
학습셋 빌더 -> human_review 를 읽지 않는다
```

검수가 여는 것은 **측정**이다. 지금 우리 수치가 전부 기계 라벨 기준이라 그 자체로 큰
값이지만, 모델이 좋아지지는 않는다. 품질 개선의 레버는 고객사 메타데이터다.

---

## 1. 함정 — 콘솔 버튼은 게이트를 안 움직인다

```
KL 배포 콘솔  /golden/candidates/{id}/decision  ->  approved_proxy
              proxy_gold_candidate_service 주석: "it never creates a locked evaluation record"
게이트가 세는 것  /golden/jobs/{job_id}/signoff  ->  locked_gold_eval
```

**콘솔에서 120건을 전부 눌러도 `locked_gold_eval` 은 0 건이다.**

---

## 2. 함정 — 등록은 반드시 HTTP 로

`GoldenBuildService.register_build()` 를 스크립트에서 직접 부르면 그 잡은 **스크립트
프로세스의 JobStore** 에 들어간다. API 서버는 자기 프로세스의 store 를 보므로 영영 못
본다(JobStore 가 in-memory 폴백일 때). 실측으로 확인했다.

```
scripts/register_review_signoff_job.py --base-url <서버> --actor <실계정> --api-key <키>
```

멱등이다 — 같은 건수의 등록 잡이 있으면 재사용한다. API 재시작 뒤 서명 화면이 404 면
다시 돌리면 된다.

---

## 3. 함정 — 역할 교집합은 admin · kl_backend 뿐

```
등록  /golden/jobs/register        admin · kl_backend · system
서명  /golden/jobs/{id}/signoff    admin · kl_backend · reviewer
```

`system` 으로는 서명이 안 되고 `reviewer` 로는 등록이 안 된다. **둘 다 하려면 계정이
`admin` 또는 `kl_backend`** 여야 한다.

그리고 역할은 **헤더로 못 바꾼다** — `X-Actor-Role` 은 위조 차단으로 무시되고
`settings.api_key_role`(env `API_KEY_ROLE`)이 정한다. 운영(poc_mode=full)에서는
헤더 신뢰 옵션 자체가 startup fail-fast 로 막힌다.

---

## 4. 함정 — publish=false 는 미리보기다

```
publish=false (기본)   run-스코프 locked_<id>.jsonl 만 기록. 정본·라이브 readiness 무변경
publish=true           settings.locked_eval_jsonl 에 dedup 병합 -> 배포 게이트가 소비
```

**서명 요청에 `publish=true` 를 넣지 않으면 게이트가 안 움직인다.**

---

## 5. 서명 무결성 — 누가 서명할 수 있나

```
jjw-admin-01 형태 실계정   인정
ai_assist · demo-console · system · codex · 빈값   전부 거부
   -> 응답에 rejected_reasons={'machine_reviewer': N}
```

편입 조건 5개가 **전부** 있어야 `locked_gold_eval` 이 된다. 하나라도 빠지면
`held_review` 로 떨어진다.

```
label_source=human_review · reviewer_id(실계정) · gate_version=human_signoff_v1
· signed_at · reviewer_ids
```

---

## 6. 필요한 양

```
게이트 열기   등급별 5건 = 20건   (DEFAULT_MIN_LOCKED_PER_GRADE)
정확도 주장   등급별 30건 = 120건
```

---

## 7. 실증 결과 (로컬 HTTP · publish=false)

```
등록      job_id 발급 · gold_count=120 · signoff.html -> 200
서명 20건  locked=20 · rejected=0 · locked_by_grade {TS 5·S1 5·S2 5·S3 5}
readiness ready=True
머신 reviewer  전건 거부(machine_reviewer)
```

⚠ **TS 실문서가 0 건이다.**

```
real_per_grade      TS 0 · S1 5 · S2 2 · S3 5
synthetic_per_grade TS 5 · S1 0 · S2 3 · S3 0
```

`ready=True` 는 나오지만 TS 서명분이 전부 합성이라 **"실문서 TS 정확도" 는 여전히
주장할 수 없다.** 구조적 한계이고 새 실문서 없이는 안 풀린다.

---

## 8. 배포 후 순서

```
1. 후보 파일 동기화        datasets/golden_review/ff5a822c/ 를 서버에 올린다
2. 등록                   register_review_signoff_job.py --base-url ... --actor ... --api-key ...
3. 화면 확인               signoff.html 이 200 인지 (스크립트가 자동 확인한다)
4. 검수 계정 발급          admin 또는 kl_backend · 실계정 이름
5. 서명                   publish=true · 등급별 5건 이상
6. readiness 확인          ready=True 와 real/synthetic 비율을 함께 본다
```
