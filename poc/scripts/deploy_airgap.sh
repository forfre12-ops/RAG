#!/usr/bin/env bash
# ============================================================================
# 폐쇄망(에어갭) 스택 기동 — onprem-local tier
# ----------------------------------------------------------------------------
# 오프라인 번들 안에서 실행한다. install.sh(docker load) 이후 전체 스택을 한 번에
# 기동: docs/INSTALL.md §1·§5~§10 자동화 — 무결성검증 → 이미지 확인 → 인프라 기동
# → 마이그레이션 → 앱(api·worker·beat) 기동 → 스모크(인수 severity floor).
#
#   전제 : Docker + Compose v2, install.sh 로 이미지 적재 완료, 모델 배치 완료(§3)
#   위치 : 번들 루트(install.sh·verify.sh 옆). 리포에서 실행 시 poc/ 를 루트로 인식.
#   compose : infra-config/docker-compose.airgap.yml (image 참조·beat·named 볼륨)
#
# 사용(번들 루트에서):
#   bash verify.sh          # (선택) 체크섬
#   sudo bash install.sh    # docker load + 호스트 deps
#   cp infra-config/.env.template .env && vi .env   # IMAGE_TAG·POSTGRES_PASSWORD·API_KEY…
#   bash deploy_airgap.sh
#
# 환경변수 override:
#   ENV_FILE=.env   API_HOST=127.0.0.1   API_PORT=8000   READY_TIMEOUT=240
#   WITH_MTLS=1     nginx-mtls 프로파일 포함(인증서 ./mtls 사전배치 전제)
#   WITH_OBS=1      관측성 스택도 기동(observability/…airgap.yml 있으면)
#   SKIP_VERIFY=1   verify.sh(체크섬) 생략      SKIP_SMOKE=1  스모크 생략
# ============================================================================
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
READY_TIMEOUT="${READY_TIMEOUT:-240}"
WITH_MTLS="${WITH_MTLS:-0}"
WITH_OBS="${WITH_OBS:-0}"
SKIP_VERIFY="${SKIP_VERIFY:-0}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"

c_bold=$'\033[1m'; c_red=$'\033[31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
log()  { printf '\n%s[deploy-airgap] %s%s\n' "$c_bold" "$*" "$c_off"; }
info() { printf '%s  %s%s\n' "$c_dim" "$*" "$c_off"; }
die()  { printf '\n%s[deploy-airgap][ERROR] %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }

# ── 위치 탐지: 번들 루트(infra-config/) 우선, 리포(poc/) 폴백 ──
SELF="$(cd "$(dirname "$0")" && pwd)"
if   [ -f "$SELF/infra-config/docker-compose.airgap.yml" ]; then
  ROOT="$SELF";        COMPOSE="infra-config/docker-compose.airgap.yml"; LAYOUT="bundle"
elif [ -f "$SELF/../docker-compose.airgap.yml" ]; then
  ROOT="$(cd "$SELF/.." && pwd)"; COMPOSE="docker-compose.airgap.yml";   LAYOUT="repo"
elif [ -f "$PWD/infra-config/docker-compose.airgap.yml" ]; then
  ROOT="$PWD";         COMPOSE="infra-config/docker-compose.airgap.yml"; LAYOUT="bundle"
else
  die "airgap compose 를 못 찾음. 번들 루트(install.sh 옆)에서 실행하라."
fi
cd "$ROOT"
COMPOSE_DIR="$(cd "$(dirname "$COMPOSE")" && pwd)"
dc_air() { docker compose --env-file "$ENV_FILE" -f "$COMPOSE" "$@"; }

# ── 0. 사전 요건 ────────────────────────────────────────────
log "0/7  사전 요건 (layout=$LAYOUT · root=$ROOT)"
command -v docker >/dev/null 2>&1 || die "docker 미탑재"
docker compose version >/dev/null 2>&1 || die "docker compose v2 미탑재"

# .env: 없으면 템플릿 안내 후 중단. env_file: .env 는 compose 파일 기준으로도 해석될 수 있어
# infra-config/ 에도 동일 .env 를 보장(양쪽 resolution 안전) — CLI --env-file 과 서비스 env_file 불일치 예방.
if [ ! -f "$ENV_FILE" ]; then
  tmpl="$COMPOSE_DIR/.env.template"
  [ -f "$tmpl" ] && cp "$tmpl" "$ENV_FILE" && die "$ENV_FILE 생성함 — IMAGE_TAG·POSTGRES_PASSWORD·API_KEY 채운 뒤 재실행."
  die "$ENV_FILE 없음. 'cp infra-config/.env.template $ENV_FILE' 후 실값 입력."
fi
if [ "$LAYOUT" = "bundle" ]; then
  # 서비스 env_file: .env 는 compose 파일 기준(infra-config/)으로 해석될 수 있어 항상 미러(최신화).
  # cp 대상이 원본과 동일 파일이면 오류 → 무시.
  cp -f "$ENV_FILE" "$COMPOSE_DIR/.env" 2>/dev/null && info "infra-config/.env 미러(서비스 env_file 로드 보장)" || true
fi
_env_val() { grep -E "^${1}=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' '; }
# 하드닝 프로파일(onprem-local)은 저장암호화를 강제 → ENABLED 가 명시적 off 가 아니면 KEY 도 필수.
_req_keys="POSTGRES_PASSWORD API_KEY"
case "$(printf '%s' "$(_env_val STORAGE_ENCRYPTION_ENABLED || true)" | tr 'A-Z' 'a-z')" in
  0|false|no|off) : ;;
  *)              _req_keys="$_req_keys STORAGE_ENCRYPTION_KEY" ;;
esac
for k in $_req_keys; do
  v="$(_env_val "$k" || true)"
  { [ -z "$v" ] || printf '%s' "$v" | grep -qiE 'change[_-]?me|replace[_-]?me|placeholder|your[_-]'; } \
    && die "필수값 미설정/placeholder: $k  ($ENV_FILE 실값 입력)"
done
# DEPLOY_PROFILE 하드닝 검증 — lite-* 로 뜨면 온도보정·안전게이트·저장암호화가 OFF 라 고등급 무음 미탐 위험.
case "$(_env_val DEPLOY_PROFILE || true)" in
  onprem-local|full-train) : ;;
  "")  info "DEPLOY_PROFILE 미설정 → compose 가 onprem-local 로 기본 주입(하드닝 유지)" ;;
  *)   die "DEPLOY_PROFILE='$(_env_val DEPLOY_PROFILE)' 은 폐쇄망 하드닝 프로파일 아님 — onprem-local(또는 full-train)로 설정. lite-* 는 안전게이트·저장암호화 OFF." ;;
esac
info "env=$ENV_FILE · profile=$(_env_val DEPLOY_PROFILE || echo 'onprem-local(기본)') · IMAGE_TAG=$(_env_val IMAGE_TAG || echo '기본(1.0.0-rc1)')"

# compose 병합 검증(기동 전 조기 실패) — env 치환·상대경로·문법 오류를 up 전에 잡는다.
if ! cfgerr="$(dc_air config -q 2>&1)"; then
  printf '%s\n' "$cfgerr" >&2
  die "compose 병합 검증 실패 — .env(IMAGE_TAG·POSTGRES_PASSWORD 등)·경로 확인 후 재실행"
fi
info "compose 병합 검증 OK"

# ── 1. 무결성 검증 (체크섬) ────────────────────────────────
if [ "$SKIP_VERIFY" != "1" ] && [ -f "$ROOT/verify.sh" ]; then
  log "1/7  번들 무결성 검증 (verify.sh)"
  bash "$ROOT/verify.sh" || die "체크섬 불일치 — 매체 반입 손상. 재반입 필요."
else
  log "1/7  무결성 검증 (skip)"
fi

# ── 2. 이미지 적재 확인 (install.sh 선행) ──────────────────
log "2/7  적재 이미지 확인"
_tag="$(_env_val IMAGE_TAG || true)"; _tag="${_tag:-1.0.0-rc1}"
if ! docker image inspect "koipa-api:${_tag}" >/dev/null 2>&1; then
  die "koipa-api:${_tag} 이미지 없음. 먼저 'sudo bash install.sh' 로 docker load(§2). IMAGE_TAG 도 확인."
fi
info "koipa-api:${_tag} 적재됨"

# ── 3. 모델 배치 확인 (§3) ─────────────────────────────────
log "3/7  모델 배치 확인 (../models)"
mdir="$COMPOSE_DIR/../models"
[ -d "$mdir/classifier-trained" ] || info "[주의] models/classifier-trained 부재 → 룰 폴백(무음 미탐 위험). §3 대로 배치."
[ -f "$mdir/classifier-trained/temperature.json" ] || info "[주의] temperature.json 부재 → T=1.0 무보정 서빙(과신). CLASSIFIER_TEMPERATURE 대체 또는 재보정 권장."
[ -d "$mdir/hf" ] || info "[주의] models/hf 부재 → HF_HUB_OFFLINE=1 로 기동 실패 가능(§3)."

# ── 4. 인프라 기동 (postgres·redis) ────────────────────────
log "4/7  인프라 기동 + postgres 헬시 대기"
dc_air up -d postgres redis
for i in $(seq 1 60); do
  if dc_air exec -T postgres pg_isready -U "$(_env_val POSTGRES_USER || echo koipa)" >/dev/null 2>&1; then
    info "postgres ready (${i}s)"; break
  fi
  [ "$i" = 60 ] && die "postgres 헬시 실패(60s). 'dc_air ps'/'logs postgres' 확인(pgvector 이미지)"
  sleep 1
done

# ── 5. DB 마이그레이션 (19테이블 + 파티션 백필) ────────────
log "5/7  alembic 마이그레이션 (run --rm api)"
dc_air run --rm api alembic upgrade head

# ── 6. 애플리케이션 기동 (api·worker 전큐·beat 단일) ───────
log "6/7  애플리케이션 기동 (api·worker·beat)"
dc_air up -d api worker beat
if [ "$WITH_MTLS" = "1" ]; then
  info "mTLS 프로파일 기동 (인증서 ./mtls 사전배치 전제)"
  dc_air --profile mtls up -d nginx-mtls
fi
if [ "$WITH_OBS" = "1" ]; then
  obs="$COMPOSE_DIR/../observability/docker-compose.observability.airgap.yml"
  [ -f "$obs" ] && { info "관측성 스택 기동"; docker compose --env-file "$ENV_FILE" -f "$obs" up -d; } \
                 || info "[skip] 관측성 overlay 없음(빌드 시 --skip-observability?)"
fi

# ── 7. 준비도 + 스모크 ─────────────────────────────────────
log "7/7  /healthz/ready 대기 (최대 ${READY_TIMEOUT}s)"
READY_URL="http://${API_HOST}:${API_PORT}/api/v1/healthz/ready"
ok=0
for i in $(seq 1 "$READY_TIMEOUT"); do
  curl -fsS "$READY_URL" >/dev/null 2>&1 && { ok=1; info "ready 200 (${i}s)"; break; }
  sleep 1
done
[ "$ok" = 1 ] || die "ready 미도달(${READY_TIMEOUT}s). 'dc_air logs api' 확인(모델 미배치·HF offline·자격증명)"

if [ "$SKIP_SMOKE" != "1" ] && [ -f "$ROOT/acceptance/run_acceptance.sh" ]; then
  log "인수 스모크 — 전 포맷 파싱 + severity floor(고등급 미탐=veto)"
  API_KEY="$(_env_val API_KEY || true)" BASE_URL="http://${API_HOST}:${API_PORT}" \
    bash "$ROOT/acceptance/run_acceptance.sh" \
    || die "인수 스모크 FAIL — 파싱실패/고등급 미탐(UNDER!). /healthz/deep 로 원인 확인."
else
  log "인수 스모크 (skip 또는 러너 부재)"
fi

log "완료 ✓  폐쇄망 배포 성공 (onprem-local)"
cat <<EOF
${c_dim}
  상태:   docker compose --env-file $ENV_FILE -f $COMPOSE ps
  로그:   docker compose --env-file $ENV_FILE -f $COMPOSE logs -f api
  beat 는 단일 인스턴스만(drift·auto-rollback·outbox·파티션 발행기). 누락 시 자동화 정지.
  worker 는 -Q classify,index,synthesis,learning,celery 전큐 구독(compose 반영).
${c_off}
EOF
