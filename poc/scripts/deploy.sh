#!/usr/bin/env bash
# ============================================================================
# 통합 배포 진입점 — 환경 자동 감지 후 오픈망/폐쇄망 배포로 디스패치
# ----------------------------------------------------------------------------
# "스크립트 하나 실행하면 자동 진행" 요구의 단일 진입점. 실제 배포는
#   deploy_cloud.sh  (오픈망 / lite-cloud — VM 에서 빌드)
#   deploy_airgap.sh (폐쇄망 / onprem-local — 번들에서 image load)
# 로 위임하며, 각 스크립트가 fail-fast·헬시대기·모델적재·스모크까지 자동 수행한다.
#
# 사용:
#   bash deploy.sh                 # 자동 감지(번들=airgap, 아니면 cloud)
#   bash deploy.sh cloud           # 강제 오픈망
#   bash deploy.sh airgap          # 강제 폐쇄망
# 각 배포의 세부 override(ENV_FILE·SKIP_BUILD·WITH_MTLS…)는 환경변수로 그대로 전달된다.
# ============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-auto}"

_norm() {
  case "$1" in
    cloud|open|openmang|open-net|lite-cloud|lite)                 echo cloud ;;
    airgap|closed|onprem|onprem-local|full-train|폐쇄망|offline)  echo airgap ;;
    *)                                                            echo "" ;;
  esac
}

target="$(_norm "$MODE")"
reason="명시 인자"
if [ -z "$target" ]; then
  # 1) 번들 레이아웃(airgap compose 존재) → 폐쇄망
  if [ -f "$HERE/infra-config/docker-compose.airgap.yml" ] \
  || [ -f "$HERE/../infra-config/docker-compose.airgap.yml" ] \
  || [ -f "$PWD/infra-config/docker-compose.airgap.yml" ]; then
    target=airgap; reason="번들 레이아웃 감지"
  else
    # 2) DEPLOY_PROFILE 힌트
    prof="${KOIPA_DEPLOY_PROFILE:-${DEPLOY_PROFILE:-}}"
    case "$(_norm "$prof")" in
      airgap) target=airgap; reason="DEPLOY_PROFILE=$prof" ;;
      cloud)  target=cloud;  reason="DEPLOY_PROFILE=$prof" ;;
      *)      target=cloud;  reason="기본값(오픈망)" ;;
    esac
  fi
fi

printf '\033[1m[deploy] target=%s (%s)\033[0m\n' "$target" "$reason"

if [ "$target" = "airgap" ]; then
  [ -f "$HERE/deploy_airgap.sh" ] || { echo "[deploy][ERROR] deploy_airgap.sh 없음" >&2; exit 1; }
  exec bash "$HERE/deploy_airgap.sh"
else
  [ -f "$HERE/deploy_cloud.sh" ] || { echo "[deploy][ERROR] deploy_cloud.sh 없음" >&2; exit 1; }
  exec bash "$HERE/deploy_cloud.sh"
fi
