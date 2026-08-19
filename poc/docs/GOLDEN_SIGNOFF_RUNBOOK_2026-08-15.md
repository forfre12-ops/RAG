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

## 7-1. 함정 — 검수자에게 준 링크가 안 열렸다 (2026-08-17 실측·수정)

```
GOLDEN_HTML_URL_SECRET 설정됨(223, 64자)
.../signoff.html            -> 403      ← 종전에 인쇄되던 주소
.../signoff.html?t=<토큰>   -> 200
```

`?t=` 는 **job_id 를 서명한 HMAC** 이다. 없으면 화면이 안 열린다. 종전
`register_review_signoff_job.py` 는 경로를 직접 조립해 토큰 없는 주소를 인쇄했다.
사람 검수가 시작되지 않던 진짜 이유였고, 원인이 화면이 아니라 링크라 콘솔을 봐도 안 보인다.

지금은 서버가 응답에 담아 주는 `signoff_url`·`review_url` 을 그대로 인쇄한다.
**주소를 직접 조립하지 말 것.** 검수자에게 전달할 때 `?t=` 뒤를 잘라내도 403 이다.

⚠ 이 토큰은 **신원이 아니다.** 링크를 가진 사람은 누구나 화면을 열 수 있다.
  누가 서명했는지는 아래 7-2 가 정한다.

---

## 7-2. 서명자는 로그인 쿠키(JWT sub)로만 정해진다 (2026-08-17 변경)

종전 서명 화면은 **검수자 이름·API Key·역할을 사람이 타이핑**하게 했다. 그리고 서버는
`auth_mode=both` 에서 공유 API Key 가 먼저 통과하면 클라이언트가 보낸 이름을 그대로 쓴다
(`confirm.py` `resolve_actor_user_id` — jwt sub 가 없으면 덮어쓰지 않는다). 두 사실이
만나면 원장에 남는 서명자는 자칭이다.

실제로 그렇게 됐다 — `locked_gold_eval` 20건이 전원 같은 이름, 그중 19건이 같은
마이크로초 서명이었다. 사람이 한 것이 아니다.

지금은 그 입력칸들이 없다. 화면은 `/golden/candidates/session` 이 준 신원을 **표시만** 하고,
본문 `actor` 에도 그 값을 싣는다. 로그인하지 않았으면 제출 버튼이 잠기고 로그인 링크가 뜬다.

**사람 검수자로 인정되지 않는 이름**(제출하면 403):

```
ai_assist · demo-console · system · codex · 빈값        머신·플레이스홀더
SIGNOFF_DEFAULT_REVIEWER 와 같은 이름                    화면이 채워 주던 이름
CONSOLE_LOGIN_PREFILL_TOKEN 의 sub (예: kl-admin-test)   로그인 화면이 나눠 주는 공용 신원
```

마지막 줄이 중요하다. 223 은 콘솔이 외부에 열려 있고 `login.html` 은 무인증이라, **접속
가능한 누구나 그 토큰을 받아 간다**(실측: HTTP 200 · roles=[admin]). 그 신원으로 한 서명은
검수 기록이 되지 않는다.

---

## 7-3. 검수자별 토큰 발급

```
python3 scripts/setup_console_test_login.py \
    --sub <실계정> --roles reviewer --until 2026-12-31
```

```
산출  secrets/console_jwt/tokens/<실계정>.txt      사람마다 따로 남는다
만료  --until 로 날짜를 못 박는다 — 노출 기한이 지나면 토큰이 스스로 죽는다
```

⛔ **`--regenerate-key` 를 쓰지 말 것.** 223 에 배포된 jwks 가 `kid=console-test-1` 이라
   키를 새로 만들면 **이전에 발급한 토큰이 전부 무효**가 된다.

⛔ **검수자 토큰을 `CONSOLE_LOGIN_PREFILL_TOKEN` 에 넣지 말 것.** 넣는 순간 그 이름이
   "로그인 화면이 나눠 주는 공용 신원" 으로 분류되어 **그 사람의 모든 서명이 403** 이 된다
   (§7-2 표의 세 번째 줄). 프리필은 둘러보기용 신원(`kl-admin-test`)으로만 두고, 검수자
   토큰은 파일로 따로 건넨다.

⛔ **한 토큰을 여러 명이 쓰지 말 것.** 원장에 같은 이름만 남아 검수 기록이 성립하지 않는다.

역할은 `reviewer` 로 충분하다(§3 — 등록은 admin 이 하고 서명만 검수자가 한다).
한 사람이 후보 관리(manage.html)까지 하면 `--roles admin,reviewer` 로 준다 — 후보
등급 결정은 admin 이어야 한다.

### 실서비스 전 검수 신원 (사용자 지정 2026-08-17)

```
--sub 지재원관리자 --roles admin,reviewer --until 2026-12-31
```

223 실측 확인:

```
/golden/candidates/session   {"actor_id":"지재원관리자","auth_mode":"jwt","actor_role":"admin"}
manage.html                  200
signoff.html?t=...           200
is_human_reviewer            True   (배포된 규칙으로 확인)
```

한글 이름은 JWT payload 에서 `\uXXXX` 로 인코딩되어 그대로 되돌아온다 — 원장에 제 이름이
남는다. 파일명도 그대로 쓸 수 있다(`secrets/console_jwt/tokens/지재원관리자.txt`).

⚠ **이것은 역할 이름이지 사람 이름이 아니다.** 여러 명이 나눠 쓰면 원장에 같은 이름만
남아, 오늘 막은 것(같은 이름 20건·같은 마이크로초 19건)과 같은 상태가 된다. 실서비스에서는
사람마다 실계정으로 발급할 것.

### 서명이 403 이면 사유를 읽는다

응답이 무엇 때문에 막혔는지 말해 준다(2026-08-17 추가). 거부 조건이 다섯 갈래인데
뒤 둘은 **이름이 아니라 설정** 때문에 막히는 것이라, 사유 없이는 이름만 계속 바꿔 보게 된다.

```
검수자 이름이 비어 있다
자리표시 이름이다('reviewer') — 실계정 이름을 쓸 것
기계 보조 계정 형식이다('ai_assist')
기계·시연 예약 접두사로 시작한다('demo-console' — 'demo')
서명 화면 기본값과 같은 이름이다(...) — 설정 SIGNOFF_DEFAULT_REVIEWER 가 이 이름이라 ...
로그인 화면이 나눠 주는 공용 신원이다(...) — 설정 CONSOLE_LOGIN_PREFILL_TOKEN 의 sub 가 ...
```

---

## 7-4. 프리필 신원 폐지 — 실서비스 전환 시 (2026-08-19 실측 추가)

§7-3 은 프리필을 "둘러보기용 신원" 으로 남겨 둔다. **그것은 폐쇄망 전제다.**
2026-08-19 에 223 을 실측해 보니 그 전제가 깨져 있었다.

```
내 PC(KL 망 밖·공인 IP)에서
  http://223.130.156.134:8000/api/v1/golden/candidates/login.html   HTTP 200
  http://223.130.156.134:8000/demo/admin.html                        HTTP 200
컨테이너 env
  CONSOLE_LOGIN_PREFILL_TOKEN = eyJhbGciOiJS…   (sub=kl-admin-test · roles=[admin] · exp 2026-12-31)
포트
  :80 닫힘 · :443 닫힘 · :8000 열림               ← 평문 HTTP
```

즉 **주소를 아는 누구나 admin 으로 들어온다.** 화면에는 경고가 떠 있다("외부 공개
환경에서는 `CONSOLE_LOGIN_PREFILL_TOKEN` 을 비워야 합니다") — 지금이 그 조건이다.

서명은 막힌다(§7-2 의 공용 신원 차단이 실제로 작동한다 — preflight 가 `reviewer_rejected`
로 거부하는 것을 확인했다). 그러나 **후보 원문 전량 열람과 관리 API 호출은 된다.**
후보에는 TS·S1 본문이 들어 있다.

### 끊는 순서 — 토큰 만료만으로는 안 끊긴다

```
1. CONSOLE_LOGIN_PREFILL_TOKEN 을 .env 에서 **제거**한다(빈 값이 아니라 키째로).
   ⚠ 만료일(2026-12-31)에 기대지 말 것. 설정이 남아 있으면 새 토큰을 넣어 얼마든지 연장된다.
2. AUTH_MODE 를 확인한다. 223 은 `both` 라 JWT 가 죽어도 **공유 API_KEY 경로가 남는다.**
   검수 화면만 잠그려면 골든 라우터가 JWT 만 받도록 하거나, API_KEY 를 회수해야 한다.
3. 앱을 외부에서 떼어 낸다. docker-compose.expose.yml 을 지우고
   docker-compose.console-proxy.yml 로 바꾼다(HTTPS·보안 헤더·스키마 차단은 그쪽에 있다).
   ⚠ deploy_kl_223.sh 는 expose.yml 이 있으면 자동 포함한다 — 지우지 않으면 배포가 되살린다.
4. 검수자별 토큰만 남긴다(§7-3). 사람마다 --sub, 만료는 --until 로 못 박는다.
```

### "URL 만 열면 되게" 와 신원 기록은 양립한다 — 단 조건이 있다

검수 기준으로 "검수자는 URL 만 열면 되고 키·JWT·역할을 입력할 일이 없어야 한다" 가 나올 수
있다. 그 자체는 지금 화면 설계와 일치한다 — 2026-08-17 에 입력칸을 없앤 이유가 그것이다.

다만 **그것을 프리필로 달성하면 누가 검수했는지가 사라진다.** 양립시키는 방법은 하나뿐이다:

> **사람마다 다른 URL.** 검수자별 토큰을 발급해 각자의 링크로 전달한다.
> "URL 만 열면 됨" 은 *개인화된 링크를 받는다*는 뜻이지 *공용 프리필*이 아니다.

이 구분을 문서에 못 박아 두지 않으면, 편의를 이유로 프리필이 되살아난다.

### 2027-01-01 자동 차단을 실제로 성립시키려면

토큰 exp 는 `2026-12-31T00:00:00Z` 라 날짜 자체는 맞다. 그러나 위 2·3번이 남아 있으면
그날이 지나도 접근 경로가 남는다. 자동 차단으로 삼으려면 **설정 부재를 배포 게이트에서
확인**해야 한다 — 예: 기동 시 `CONSOLE_LOGIN_PREFILL_TOKEN` 이 있으면 경고가 아니라
기동 실패로 다루거나, 배포 검증 단계에서 `env | grep -c PREFILL` 이 0 인지 본다.

---

## 8. 배포 후 순서

```
1. 후보 파일 동기화     datasets/golden_review/ff5a822c/ 를 서버에 올린다
2. 등록                register_review_signoff_job.py --base-url ... --actor ... --token ...
                       (등록은 admin·kl_backend 만 — §3)
3. 링크 확보            스크립트가 인쇄하는 ?t= 포함 주소 두 개(검토본·서명)
                       스크립트가 signoff.html 200 까지 스스로 확인한다
4. 검수자 토큰 발급      §7-3 — 사람마다 --sub 로 따로. 역할 reviewer 로 충분
5. 검수자에게 전달       ① 로그인 화면 ② 본인 토큰 파일 ③ ?t= 포함 링크 (셋 다)
6. 서명                publish=true · 등급별 5건 이상
7. readiness 확인       ready=True 와 real/synthetic 비율을 함께 본다
```

⚠ 5번에서 셋 중 하나만 빠져도 검수자가 막힌다 — 링크만 주면 열리기는 하나 제출이 잠기고,
  토큰만 주면 어디로 갈지 모른다.
