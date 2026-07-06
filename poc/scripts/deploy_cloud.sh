#!/usr/bin/env bash
# ============================================================================
# 오픈망(인터넷 가용) 테스트/스테이징 배포 — lite-cloud tier
# ----------------------------------------------------------------------------
# docs/CLOUD_DEPLOY_RUNBOOK.md 를 한 번에 실행한다. 인터넷이 되는 클라우드 VM
# 에서 소스로부터 이미지를 빌드 → 인프라 기동 → 마이그레이션 → 앱 기동 → 스모크.
#
#   전제 : Docker + Docker Compose v2, 인터넷(이미지 빌드·외부 LLM API), GPU 불요
#   위치 : poc/scripts/deploy_cloud.sh  (리포 루트=poc/ 기준으로 동작)
#   대상 : lite-cloud (안전게이트 warn-only·저장암호화 OFF·외부 LLM). 하드닝
#          폐쇄망 운영 배포는 deploy_airgap.sh 를 쓴다.
#
# 사용:
#   cp .env.lite-cloud .env.cloud && vi .env.cloud   # 실값 채우기(§04 fail-fast)
#   bash scripts/deploy_cloud.sh
#
# 환경변수 override:
#   ENV_FILE=.env.cloud   API_HOST=127.0.0.1   API_PORT=8000
#   SKIP_BUILD=1  이미지 재빌드 생략        SKIP_SMOKE=1  스모크 생략
#   READY_TIMEOUT=180  /healthz/ready 대기 초
# ============================================================================
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.cloud}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"
READY_TIMEOUT="${READY_TIMEOUT:-180}"
ARTIFACT_VOLUME="${ARTIFACT_VOLUME:-lloydk_prod_artifacts}"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

BASE=(-f docker-compose.yml -f docker-compose.prod.yml)
# prod overlay 를 강제하고 dev override(.override.yml=GPU·Ollama 강제) 자동머지를 차단.
# ENV_FILE 과 --env-file 을 같은 파일로 줘 dev .env 누수를 끊는다(런북 A4).
dc() { ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" "${BASE[@]}" "$@"; }

c_bold=$'\033[1m'; c_red=$'\033[31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
log()  { printf '\n%s[deploy-cloud] %s%s\n' "$c_bold" "$*" "$c_off"; }
info() { printf '%s  %s%s\n' "$c_dim" "$*" "$c_off"; }
die()  { printf '\n%s[deploy-cloud][ERROR] %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }

# ── 0. 사전 요건 ────────────────────────────────────────────
log "0/7  사전 요건 확인"
command -v docker >/dev/null 2>&1 || die "docker 미탑재"
docker compose version >/dev/null 2>&1 || die "docker compose v2 미탑재"
[ -f "$ENV_FILE" ] || die "env 파일 없음: $ENV_FILE  (cp .env.lite-cloud $ENV_FILE 후 실값 입력)"

# fail-fast 예방점검(부팅 거부 전에 친절히 알림). 앱도 startup 에서 재검증한다.
_env_val() { grep -E "^${1}=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' '; }
_missing=()
for k in API_KEY LLOYDK_AUDIT_CHAIN_SECRET CORS_ALLOW_ORIGINS; do
  v="$(_env_val "$k" || true)"
  { [ -z "$v" ] || printf '%s' "$v" | grep -qiE 'change[_-]?me|placeholder|xxx|your[_-]'; } && _missing+=("$k")
done
printf '%s' "$(_env_val CORS_ALLOW_ORIGINS || true)" | grep -q '\*' \
  && die "CORS_ALLOW_ORIGINS 에 '*' 포함 — 오픈망 노출 위험. 실제 origin 으로 잠글 것(부팅도 거부됨)."
[ "${#_missing[@]}" -eq 0 ] || die "미설정/placeholder 필수값: ${_missing[*]}  ($ENV_FILE 실값 입력)"
info "env=$ENV_FILE · 필수 자격증명 채워짐 · CORS 잠김"

# ── 1. 이미지 빌드 ─────────────────────────────────────────
if [ "$SKIP_BUILD" = "1" ]; then
  log "1/7  이미지 빌드 (SKIP_BUILD=1 → 생략)"
else
  log "1/7  이미지 빌드 (Dockerfile.api.prod: non-root gunicorn)"
  dc build
fi

# ── 2. 인프라 기동 (postgres·redis·minio) ──────────────────
log "2/7  인프라 기동 + postgres 헬시 대기"
dc up -d postgres redis minio
for i in $(seq 1 60); do
  if dc exec -T postgres pg_isready -U "${POSTGRES_USER:-lloydk}" >/dev/null 2>&1; then
    info "postgres ready (${i}s)"; break
  fi
  [ "$i" = 60 ] && die "postgres 헬시 실패(60s). 'dc ps'/'dc logs postgres' 확인"
  sleep 1
done

# ── 3. DB 마이그레이션 (수동 · 단일 head) ──────────────────
log "3/7  alembic 마이그레이션 (run --rm api)"
dc run --rm api alembic upgrade head

# ── 4. 분류 모델 볼륨 확인 ─────────────────────────────────
log "4/7  분류 모델 external 볼륨 확인"
if docker volume inspect "$ARTIFACT_VOLUME" >/dev/null 2>&1; then
  info "볼륨 $ARTIFACT_VOLUME 존재 — CLASSIFIER_MODEL_DIR 경로와 일치하는지 확인"
else
  info "[주의] 볼륨 $ARTIFACT_VOLUME 부재. 모델 없이 룰만 테스트가 아니라면 아래로 적재:"
  info "  docker volume create $ARTIFACT_VOLUME"
  info "  docker run --rm -v $ARTIFACT_VOLUME:/dst -v \"\$(pwd)/artifacts:/src:ro\" alpine \\"
  info "    sh -c 'mkdir -p /dst/classifier_p1_retrain_v4_clean && cp -r /src/classifier_p1_retrain_v4_clean/v-dd3abab9 /dst/classifier_p1_retrain_v4_clean/'"
  info "  (CLASSIFIER_MODEL_DIR 설정 후 경로 부재면 앱이 부팅 거부한다 — 의도된 fail-loud)"
fi

# ── 5. 애플리케이션 기동 (api·worker·beat) ─────────────────
log "5/7  애플리케이션 기동 (api·worker·beat)"
dc up -d

# ── 6. /healthz/ready 대기 ─────────────────────────────────
log "6/7  /healthz/ready 대기 (최대 ${READY_TIMEOUT}s)"
READY_URL="http://${API_HOST}:${API_PORT}/api/v1/healthz/ready"
ok=0
for i in $(seq 1 "$READY_TIMEOUT"); do
  if curl -fsS "$READY_URL" >/dev/null 2>&1; then ok=1; info "ready 200 (${i}s)"; break; fi
  sleep 1
done
[ "$ok" = 1 ] || die "ready 미도달(${READY_TIMEOUT}s). 'dc logs api' 확인(모델 미공급·자격증명·HF캐시 재다운로드 등)"

# ── 7. 스모크 (분류 + 인수 severity floor) ─────────────────
if [ "$SKIP_SMOKE" = "1" ]; then
  log "7/7  스모크 (SKIP_SMOKE=1 → 생략)"
else
  log "7/7  스모크 — 분류 1건 + 인수 샘플팩(고등급 미탐 0)"
  API_KEY="$(_env_val API_KEY || true)"
  [ -n "$API_KEY" ] || info "[skip] API_KEY 미확보 → classify 스모크 생략"
  if [ -n "$API_KEY" ]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      "http://${API_HOST}:${API_PORT}/api/v1/classify" \
      -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
      -d '{"doc_id":"smoke-1","content":"본 문서는 당사의 반도체 공정 영업비밀을 포함한다"}' || true)"
    [ "$code" = 200 ] || die "classify 스모크 실패(HTTP $code)"
    info "classify 200 OK"
  fi
  # 인수 러너(있으면): 전 포맷 파싱 + severity floor(고등급 미탐=veto)
  if [ -f scripts/run_acceptance.py ]; then
    dc exec -T api python scripts/run_acceptance.py --mode http \
       --base-url "http://127.0.0.1:${API_PORT}" --api-key "${API_KEY:-}" \
      || die "인수 스모크 FAIL — 파싱실패/고등급 미탐(UNDER!) 발생. /healthz/deep 로 원인 확인"
  fi
fi

log "완료 ✓  오픈망 배포 성공 (lite-cloud)"
cat <<EOF
${c_dim}
  다음 확인:
    - 외부 접속: prod overlay 는 api 를 127.0.0.1:${API_PORT} 에만 바인딩한다.
      오픈망 외부 노출은 반드시 앞단 리버스 프록시(nginx)로. 직노출 금지.
    - 상태:   docker compose --env-file $ENV_FILE ${BASE[*]} ps
    - 로그:   docker compose --env-file $ENV_FILE ${BASE[*]} logs -f api
    - 관측성: infra/observability/docker-compose.observability.yml (선택)
${c_off}
EOF
