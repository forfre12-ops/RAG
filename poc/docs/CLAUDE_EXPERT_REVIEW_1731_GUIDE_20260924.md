# 전문가 검수 1,731건 — 진행 안내 (2026-09-24)

목적: KL 요청 항목3(전문가 검수 사이트) 대응. 사실 우선 모의문서 1,731건(`datasets/expert_review_all_1731_20260924/`)을
검수 콘솔(`ProxyGoldCandidateService` · `/api/v1/golden/candidates/manage.html`)에 적재하고, 블라인드 독립 검수
흐름을 로컬에서 end-to-end 로 직접 실행해 확인했다. 이 문서는 **그 결과와, 지재원/보호원이 실제로 검수를
시작하기 전에 반드시 해야 할 설정**을 정리한다.

## 1. 오늘 로컬에서 확인한 것 (근거 포함)

| 확인 항목 | 결과 | 근거 |
|---|---|---|
| 1,731건 전량 적재 | metadata.json 1,731 / 본문 md 1,731 — 일치 | `grep -l review_batch=expert_review_1731_20260924 datasets/proxy_gold/single_document_candidates/*.metadata.json \| wc -l` = 1731 |
| 목록 화면(블라인드) | 제안 등급·근거 노출 없음, doc_id 가 `RV-` 별칭으로 대체 | `GET /api/v1/golden/candidates?review_batch=...` 응답 직접 확인 |
| 상세 화면 | 본문 전문 노출, 제안 등급·라운드·학습이력 미노출 | 상동 |
| 승인(approve) 차단 | 블라인드 모드에서 `action=approve` 요청 시 403 | golden.py:555 의 명시적 가드, 실제 호출로 재확인 |
| 등급 직접 지정(change) | 성공 · 원장에 append-only 로 기록 | `POST .../decision {"action":"change",...}` → `candidate_decisions.jsonl` 9번째 줄 |
| 배치 초기화 상태 | 현재 1,731건 전부 `proposed`(미확정) — 확정 0건 | 아래 §4 참고 |

## 2. 실 배포 전 **반드시** 켜야 하는 설정 (기본값이 꺼짐 — 안 켜면 블라인드가 아니다)

`src/koipa/config.py` 의 두 손잡이는 **기본 `False`** 다. `docs/INSTALL.md` 의 표준 `.env` 체크리스트에는
이 둘이 없다 — 일반 설치 절차만 따르면 검수자에게 제안 등급이 그대로 보인다.

```
GOLDEN_REVIEW_BLIND_ENFORCED=1          # 필수 — 이게 없으면 제안 등급·근거가 검수자에게 노출된다
GOLDEN_REVIEWER_ASSIGNMENT_ENFORCED=1   # 선택 — 검수자별로 문서를 배정해 나눠 볼 때만. 안 켜면 전원이 전체 1,711건을 본다
```

값은 필드명 그대로 env 매핑(`env_prefix` 없음, `config.py:186,1124` 확인). 두 값 다 서버 기동 시점에 읽으므로
`.env` 반영 후 api 컨테이너 재기동이 필요하다.

## 3. 검수자 계정 발급

콘솔은 공유 API 키를 거부하고 **포털 JWT 로그인만** 받는다(golden.py 로그인 화면 문구: "공유 API 키로는
열 수 없습니다. 토큰의 계정 이름이 검수 이력에 그대로 기록됩니다"). 별도 아이디/비번 로그인 화면은
없고, **발급된 토큰을 붙여넣는 방식**이다.

```bash
python scripts/setup_console_test_login.py --sub "<검수자 실명 또는 사번>" --roles reviewer --days 45
```

- `--sub` 값이 검수 결정마다 `actor_id` 로 그대로 남는다(감사 대상) — 이름을 그대로 쓴다.
  스크립트 기본값(`kl-admin-test`)이나 파일명의 "test" 는 무시해도 된다 — `--sub`/`--roles`/`--days` 는
  실제 운영 발급에 그대로 쓸 수 있는 정식 인자다(코드 확인, `scripts/setup_console_test_login.py:66-72`).
- 검수자 수만큼 반복 실행 → 각자 다른 토큰 발급 → 로그인 화면(`/api/v1/golden/candidates/login.html`)에
  1회 붙여넣기 → 토큰 만료까지 쿠키 유지.
- **결정(2026-09-25, 사용자 지시 반영)**: 검수자는 1명이고, 토큰을 사람이 입력하는 절차는 두지 않는다. 지재원 노드 설치
  (`setup.sh` 7단계)가 검수자 토큰(계정 `expert-01`, reviewer 전용, 365일)을 만들어 로그인 화면에 미리 채운다
  (`CONSOLE_LOGIN_PREFILL_TOKEN` + `CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE=1` — 사용자 결정 2026-08-20 "로그인은 항상 되어야
  한다"). 전문가는 검수 화면 주소(`/api/v1/golden/candidates/manage.html`)만 열면 자동으로 로그인된다. 대가: 그 주소에
  닿는 누구나 같은 권한으로 들어가므로 폐쇄망에서만 쓴다. 끄려면 `CONSOLE_AUTOLOGIN=0 bash setup.sh`. 관리자 토큰은
  번들 폴더의 `console_admin_token.txt`. 상세는 `INSTALL.md` §10.2.
  (이 문서 §1·§2·§5 의 건수는 작성 당시(2026-09-24) 값이다. 검수 요청 범위는 품질 통과 1,711건으로 바뀌었다 —
  학습 1,101 · 검증 67 · 개발 344 · 봉인 199.)

## 4. 검수자 사용법

1. 로그인(자동 — §3) → `manage.html` 자동 이동.
2. 「검수 배치」 필터에서 **`expert_review_1731_20260924`** 선택 → 1,711건만 좁혀 보기.
3. 문서 클릭 → 본문 전문만 보임(등급·근거·라운드·학습이력 없음).
4. 「결정」에서 **`change`**(등급 지정/변경) 선택 — 블라인드에서는 `approve`(제안대로 확정) 항목 자체가 없다.
5. TS/S1/S2/S3 중 확정 등급 선택 + 사유 입력(필수) → 저장.
6. 비밀관리성(보안표시·접근범위) 칸은 등급 결정과 **별개**(선택 입력) — 모르면 "확인 안 됨"으로 둔다.
7. 모든 결정은 `candidate_decisions.jsonl` 에 append-only 로 쌓인다 — 콘솔 「보류·폐기 이력」에서 전체 열람 가능.

## 5. 결과를 나중에 쓸 때 반드시 지킬 것

`datasets/expert_review_all_1731_20260924/internal_manifest.jsonl` 의 `training_use` 값(검수자에게는 비공개)에
따라 결과의 쓰임이 갈린다 — README.md(같은 폴더)에 이미 정리되어 있다. 요약:

- `fs_train_loss`(1,110건)/`fs_val_holdout`(67건) — 모델이 학습에 쓴 문서. 검수 결과는 **라벨 품질 검증용**만.
  이 결과로 재현율·미탐율을 다시 계산하면 train-on-test 오염이다.
- `never_trained`(354건)/`never_trained_sealed`(200건) — 모델이 전혀 안 본 문서. 검수 결과를 그대로
  **골든셋(평가 정답)** 으로 승격할 수 있다.

## 6. 기존 문서와의 관계 — 갱신 필요 지점

`doc/result/감리정본/사용자매뉴얼_검수관리자.html`(9/5)은 이 콘솔이 아니라 **구 방식**
(`signoff.html`/`review.html`, "화면 서명" 절)을 기준으로 쓰여 있었다 — "블라인드"·"배치"·"review_batch"
어느 키워드도 없었다(전문 검색 결과 0건, "alias" 1건만). → 2026-09-24 에 §07(블라인드 검수 화면·배치 필터)이
추가됐고, 2026-09-25 에 §07 을 1,711건·자동 로그인 기준으로 정정했다.

## 7. 오늘 정리한 것 — 로컬 테스트 결정 원복

로컬 검증 중 만든 시험 결정(문서 `MD-0473`, 별칭 `RV-00DRNRTSMQ59NMSK`, actor `reviewer-test-1`,
`change`→S2, 2026-09-24T08:00:57Z)을 같은 API 의 `reopen` 액션으로 되돌렸다(2026-09-24T08:25:44Z,
`candidate_decisions.jsonl` 최종 줄). **삭제가 아니라 이력에 되돌림 이벤트를 추가**하는 방식이다(append-only
원장이라 손으로 줄을 지우지 않았다). 재확인: `review_batch=expert_review_1731_20260924` 기준
`{"total":1731,"terminal":0,"pending":1731,"by_status":{"proposed":1731}}` — 1,731건 전부 미확정 상태로
정상 초기화됨.
