⛔⛔ **2026-09-20 배포 취소** — golden100을 재구성(등급당 25→50건, `datasets/gold/
golden100_labeled_v3.jsonl`)해 재평가한 결과, 이 모델이 golden100_v2에서는 안 보이던
심각한 결함(S1 재현율 80%→28~40% 붕괴, S1 문서의 절반이 S3로 직행 오분류)이 있음이
드러났다. 아래 내용은 그 발견 이전의 "배포 후보" 판단이며 **더 이상 유효하지 않다.**
상세: 메모리 `pilot-public-s3-success-plus-expansion-failed-2026-09-19`,
`golden100-v3-rebuild-2026-09-20`.

# v5_clean + S3 파일럿 28건 — 배포 취소됨(아래는 취소 전 기록)

`datasets/labeled_p1_v5_clean`(현재 배포본 v-fe4b386b의 실제 학습셋, 2,042건)에
`datasets/synth_public_with_numbers_s3_pilot`(28건)를 더한 학습셋. golden100 S3
재현율 16.0%→56~72.0%(2시드 검증)를 내면서 다른 3개 평가면(hardened42·clean42·
holdout109)엔 부작용이 없는, 이번 세션에서 유일하게 성공한 개선안이다.

이미 학습까지 마친 두 후보 모델이 있다(재학습 불필요, 그대로 존재):

- `reports/pilot_public_s3_experiment/model_seed42/v-9567c863`
- `reports/pilot_public_s3_experiment/model_seed43/v-9bcbf05d`

전체 근거·시행착오·기각된 84건 확장 이야기는
`datasets/synth_public_with_numbers_s3_pilot/README.md`와
메모리 `pilot-public-s3-success-plus-expansion-failed-2026-09-19`에 있다.

## 현재 상태 — CANDIDATE, 미배포

이 폴더와 위 두 모델은 **배포 후보로 확정된 것**이지, 실제 운영 서버(211)에 올라간
것이 아니다. 이 프로젝트 규칙상 실제 배포는 별도의 명시적 지시가 있을 때만
진행한다(`no-223-deploy-during-audit-2026-08-29`: 211에도 적용).

## 알려진 대가

golden100 S1 재현율이 56.0%(배포본)→44~52.0%(두 시드 다)로 소폭 하락한다. 이걸
메우려던 84건 확장 시도는 TS 재현율을 배포본보다 더 나쁘게 만드는 부작용이 있어
기각했다 — 상세 원인은 위 README/메모리 참조.
