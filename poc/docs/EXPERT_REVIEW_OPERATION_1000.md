# 전문가 검수 운영 절차 — 1,000건 배치

대상 배치: `expert_review_product_1000_20261010_rf`
범위: 모의문서 1,000건을 활성 검수자 20명에게 각 50건씩 한 번만 배정하는 지재원 검수 운영

이 문서는 현재 회차의 운영 기준이다. `CLAUDE_EXPERT_REVIEW_1731_GUIDE_20260924.md`는 2026-09의 1,711건 회차를 설명하는 보관 자료이므로 현재 설치·배정에 사용하지 않는다.

## 시작 전

1. 대상 Rocky 서버에서 `NODE=jjw bash preflight_target_host.sh`를 실행해 `READY`를 확인한다. docker/podman이 없다면 반입 번들의 `rpms/`에 대상 Rocky 버전용 런타임 RPM과 의존성이 실제로 있어야 한다. 둘 다 없으면 설치를 시작하지 않는다.
2. 번들 루트에서 `bash verify.sh`로 체크섬을 확인하고, `sudo NODE=jjw bash setup.sh`로 설치한다.
3. `.env`에 다음 세 값이 모두 `1`인지 확인한 뒤 API를 재기동한다.

   ```bash
   GOLDEN_REVIEWER_ASSIGNMENT_ENFORCED=1
   GOLDEN_REVIEW_BLIND_ENFORCED=1
   GOLDEN_DEFAULT_REVIEW_BATCH=expert_review_product_1000_20261010_rf
   ```

4. 설치 후 아래 읽기 전용 점검을 실행한다. 결과는 `READY`, 1,000 문서, 20 활성 검수자, assignment/blind `True`여야 한다.

   ```bash
   docker compose --env-file .env -f infra-config/docker-compose.airgap.yml \
     exec api python scripts/check_expert_review_readiness.py \
       --expected-documents 1000 --expected-reviewers 20 --require-enforcement \
       --require-doc-format "응시자 지원서" --require-doc-format "고용계약서" \
       --require-doc-format "매뉴얼" --require-doc-format "각종 계약서"
   ```

## 배정과 확인

1. 인증된 관리자 계정으로 `.../api/v1/golden/candidates/manage.html`에 로그인한다.
2. **검수 배정**에서 활성 검수자 20명을 중복 없이 선택하고, 현재 배치
   `expert_review_product_1000_20261010_rf`에 대해 **전문가 검수 계획 실행**을 누른다.
3. 화면/`GET /api/v1/golden/assignments`에서 다음을 확인한다.

   - 문서 1,000건이 모두 배정됨
   - 검수자 20명 각각 50건
   - 중복 배정 0건
   - 배정 이벤트에는 실제 관리자의 로그인 식별자와 시각이 남음

4. 각 검수자는 자신의 계정으로 로그인하여 50건만 보이는지 확인한다. 제안 등급·근거·실제 문서 ID는 검수자 화면에 보이면 안 된다. 등급은 유지·변경·보류·폐기 중 검수자가 직접 지정하고, 자신의 이력만 볼 수 있어야 한다.

## 설치 E2E

운영 배치의 원장을 바꾸지 않는 읽기 전용 점검은 아래와 같다. 토큰은 명령행 기록에 남기지 말고 안전한 셸 변수나 단기 비밀관리 경로로 제공한다.

```bash
python scripts/e2e_expert_review_flow.py \
  --base-url http://127.0.0.1:8000 \
  --reviewer-token "$REVIEWER_JWT" --admin-token "$ADMIN_JWT" \
  --review-batch expert_review_product_1000_20261010_rf \
  --expect-reviewer-total 50 --expect-batch-total 1000
```

`--write-probe`는 검수 결정 한 건을 append-only 원장에 실제로 기록한다. 이 옵션은 운영 1,000건이 아닌 별도 UAT 배치에서만 사용한다.

## 완료 기준과 경계

- 1,000건 적재·20×50 배정·블라인드·개인 로그인·이력은 검수 운영의 완료 기준이다.
- 검수자의 실제 결정과 로그인 신원은 현장 운영 증빙이다. 로컬 코드 검증이나 모의 원장으로 대체할 수 없다.
- 콘솔의 `결정`은 검수 이력을 남기지만 모델을 자동 학습·자동 배포하지 않는다. 검수 완료 후 평가정답 승격과 재학습은 독립적인 승인·독립성 검증·배포 게이트를 거쳐야 한다.
