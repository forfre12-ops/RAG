# 빠른 211 UI/API 배포 경로

211은 빌드 머신이 아니다. 로컬 또는 CI가 정확한 커밋에서 이미지를 만들고 검증한 뒤, 211은 그 이미지를 받아 선택된 서비스만 전환한다. 이 경로는 화면·정적 파일·API 코드의 변경을 빠르게 반영하면서 워커 재기동과 전체 회귀를 매번 반복하지 않기 위한 운영 규약이다.

## 작업과 GitLab 반영

작업 중간 커밋은 로컬 브랜치에만 둔다. 여러 UI 수정을 하나의 검증 가능한 배치로 묶은 뒤에만 GitLab에 push한다. 따라서 GitLab push는 소스 변경의 중간 전달 수단이 아니라, CI 전수 회귀와 변경 이력을 남기는 최종 배치다.

기본 pre-push 훅은 빠른 계약만 확인한다. 전수 검증은 그 최종 SHA의 CI가 맡고, 필요하면 다음으로 수동 실행한다.

```bash
pre-commit run full-verification-gate --hook-stage manual
```

## UI/API 전용 릴리스

다음 모두에 해당할 때만 빠른 경로를 쓴다.

- `src/koipa/api/static/` 또는 API/UI 코드만 바뀌었다.
- `alembic/`, `pyproject.toml`, `uv.lock`, compose/환경 계약, 워커 태스크·큐, 모델/데이터셋은 바뀌지 않았다.
- DB 마이그레이션·워커 코드 전환·모델 재학습이 필요하지 않다.

먼저 빌드 호스트에서 변경 범위의 정적 계약과 브라우저 E2E만 확인한다. 전체 콘솔 E2E와 전체 회귀는 최종 배치의 CI가 수행한다.

```bash
cd poc
bash scripts/verify_ui_fast_path.sh
```

정확한 커밋을 만든 뒤 API 이미지만 빌드·import smoke·아카이브한다. 스크립트는 dirty checkout을 거부하고 `KOIPA_BUILD_SHA`와 이미지 ID·tar SHA-256을 manifest에 남긴다.

```bash
cd poc
bash scripts/build_release_images.sh \
  --services api \
  --output ../release/<full-commit-sha>
```

연결된 레지스트리를 쓰는 CI라면 `--image-prefix <registry/namespace> --push`를 추가할 수 있다. air-gap 전송이라면 위 출력 디렉터리 전체를 211로 복사한다. 둘 다 211에서 소스를 빌드하지 않는다.

211에서는 기존 compose/.env를 유지한 채 이미지 전용 overlay만 바꾼다. `api`만 지정하면 worker와 beat는 재빌드·재기동되지 않는다.

```bash
cd /home/koipa/releases/koipa-<full-commit-sha>/poc
bash scripts/deploy_prebuilt_images.sh \
  --bundle-dir /home/koipa/incoming/<full-commit-sha> \
  --compose-file /home/koipa/infra-config/docker-compose.airgap.yml \
  --env-file /home/koipa/infra-config/.env \
  --overlay /home/koipa/runtime-overlays/<full-commit-sha>.yml \
  --services api
```

이 스크립트는 tar hash, Docker image ID, 이미지 안 `KOIPA_BUILD_SHA`를 먼저 검증한다. 그 다음 `docker compose ... up -d --no-deps api`만 수행하고 `/api/v1/healthz`가 manifest SHA와 일치할 때까지 기다린다. 빌드와 마이그레이션은 실행하지 않는다.

실패하면 새 overlay의 `.previous` 파일을 사용해 이전 검증 bundle로 같은 스크립트를 다시 실행한다. 이 경로의 복구 단위는 소스가 아니라 이전의 검증 이미지다.

## 빠른 경로를 쓰면 안 되는 경우

아래는 API 전용 전환으로 처리하지 않는다. API·worker의 사전 빌드 이미지를 모두 만들고, 계획된 마이그레이션/운영 절차와 전체 UAT를 거친다.

- 마이그레이션 또는 저장 데이터 계약 변경
- `uv.lock`·OS 패키지·Dockerfile 설치 규약 변경
- Celery 태스크/큐, worker/beat 동작 변경
- 모델, 데이터셋, 학습·승격 또는 보안/권한 정책 변경
- compose, 볼륨, 네트워크, 환경변수 변경

이 구분은 빠르게 가기 위해 검증을 없애는 방식이 아니다. 빠른 경로는 변경한 API만 짧게 검증·전환하고, 전체 회귀는 CI에서 같은 최종 SHA에 대해 남기는 방식이다.
