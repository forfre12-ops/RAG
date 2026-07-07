#!/usr/bin/env bash
# ============================================================================
# 오픈망(인터넷 가용) 테스트/스테이징 배포 — lite-cloud tier
# ----------------------------------------------------------------------------
# docs/CLOUD_DEPLOY_RUNBOOK.md 를 한 번에 실행. 인터넷 되는 클라우드 VM 에서
# 소스로부터 이미지 빌드 → 인프라 → 마이그레이션 → (모델 자동적재) → 앱 → 스모크.
#
#   전제 : Docker + Docker Compose v2, 인터넷(이미지 빌드·외부 LLM API), GPU 불요
#   위치 : poc/scripts/deploy_cloud.sh  (리포 루트=poc/ 기준으로 동작)
#   대상 : lite-cloud (안전게이트 warn-only·저장암호화 OFF·외부 LLM). 폐쇄망 하드닝
#          운영 배포는 deploy_airgap.sh 를 쓴다. 통합 진입점은 deploy.sh.
#
# 사용:
#   cp .env.lite-cloud .env.cloud && vi .env.cloud   # 실값(§04 fail-fast)
#   bash scripts/deploy_cloud.sh
#
# 환경변수 override:
#   ENV_FILE=.env.cloud   API_HOST=127.0.0.1   API_PORT=8000
#   SKIP_BUILD=1  이미지 재빌드 생략        SKIP_SMOKE=1  스모크 생략
#   AUTO_LOAD_MODEL=0  모델 자동적재 끔     READY_TIMEOUT=180  ready 대기 초
# ============================================================================
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env.cloud}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
SKIP_BUILD="${SKIP_BUILD:-0}"
SKIP_SMOKE="${SKIP_SMOKE:-0}"
AUTO_LOAD_MODEL="${AUTO_LOAD_MODEL:-1}"
READY_TIMEOUT="${READY_TIMEOUT:-180}"
ARTIFACT_VOLUME="${ARTIFACT_VOLUME:-lloydk_prod_artifacts}"
PG_USER="${PG_USER:-lloydk}"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

# prod overlay 강제 + dev override(.override.yml=GPU·Ollama 강제) 자동머지 차단.
# ENV_FILE 과 --env-file 을 같은 파일로 줘 dev .env 누수를 끊는다(런북 A4).
BASE=(-f docker-compose.yml -f docker-compose.prod.yml)
dc() { ENV_FILE="$ENV_FILE" docker compose --env-file "$ENV_FILE" "${BASE[@]}" "$@"; }

c_bold=$'\033[1m'; c_red=$'\033[31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
log()  { printf '\n%s[deploy-cloud] %s%s\n' "$c_bold" "$*" "$c_off"; }
info() { printf '%s  %s%s\n' "$c_dim" "$*" "$c_off"; }
warn() { printf '  [!] %s\n' "$*"; }
die()  { printf '\n%s[deploy-cloud][ERROR] %s%s\n' "$c_red" "$*" "$c_off" >&2; exit 1; }

# .env 값 추출(주석행 제외·따옴표/공백 제거). env_file 은 소스하지 않는다(복잡값 안전).
_env_val() { grep -E "^${1}=" "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' '; }

# ── 0. 사전 요건 + fail-fast 예방점검 ──────────────────────
log "0/7  사전 요건 확인"
command -v docker >/dev/null 2>&1 || die "docker 미탑재"
docker compose version >/dev/null 2>&1 || die "docker compose v2 미탑재"
# .env 없으면 테스트서버용으로 자동 생성(무설정 배포). NO_AUTO_ENV=1 로 끔.
if [ ! -f "$ENV_FILE" ]; then
  [ "${NO_AUTO_ENV:-0}" = "1" ] && die "env 없음: $ENV_FILE (NO_AUTO_ENV=1) — cp .env.lite-cloud $ENV_FILE 후 실값 입력"
  log "env 자동 생성 (테스트서버용 · $ENV_FILE)"
  _gen() { openssl rand -hex "$1" 2>/dev/null || { tr -dc 'a-f0-9' </dev/urandom | head -c "$(( $1 * 2 ))"; echo; }; }
  _api="$(_gen 24)"; _audit="$(_gen 32)"
  _model_line='# CLASSIFIER_MODEL_DIR 미설정 → 룰 기반 분류(모델 없이도 파싱·등급 E2E 동작)'
  [ -d "artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9" ] \
    && _model_line='CLASSIFIER_MODEL_DIR=/app/artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9'
  cat > "$ENV_FILE" <<ENVEOF
# 테스트서버 자동생성(lite-cloud) — 실운영 아님. 재생성: 이 파일 삭제 후 재실행.
DEPLOY_PROFILE=lite-cloud
POC_MODE=full
API_KEY=$_api
LLOYDK_AUDIT_CHAIN_SECRET=$_audit
# 저장소=로컬FS → minio 불요(무설정). 업로드 문서는 컨테이너 볼륨에 저장.
STORAGE_BACKEND=local
VECTOR_BACKEND=pg
# CORS: 데모 콘솔은 same-origin이라 무관. 타 머신 브라우저 직접 접근 시 실 origin으로.
ALLOWED_ORIGIN=http://localhost:$API_PORT
EMBEDDING_MODEL=nlpai-lab/KURE-v1
CLASSIFIER_TEMPERATURE=3.0
$_model_line
# LLM: 분류 핫패스는 LLM-free라 불요. /answer·2차판정 쓰려면 키 채우기.
# ANTHROPIC_API_KEY=sk-ant-...
ENVEOF
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  info "생성됨 · API_KEY=$_api  (업로드·E2E 에 사용 — 보관)"
fi
[ -f "$ENV_FILE" ] || die "env 파일 없음: $ENV_FILE"

# 앱도 startup 에서 재검증하나(fail-fast), 부팅 전에 친절히 잡는다.
_ph_re='change[_-]?me|replace[_-]?me|placeholder|xxx|your[_-]'
for k in API_KEY LLOYDK_AUDIT_CHAIN_SECRET; do
  v="$(_env_val "$k" || true)"
  { [ -z "$v" ] || printf '%s' "$v" | grep -qiE "$_ph_re"; } \
    && die "필수값 미설정/placeholder: $k  ($ENV_FILE 실값 입력)"
done
# LLM 키는 경고(분류 핫패스는 LLM-free — 파싱·분류 E2E 는 무관)
{ [ -n "$(_env_val ANTHROPIC_API_KEY || true)" ] || [ -n "$(_env_val OPENAI_API_KEY || true)" ]; } \
  || warn "LLM 키 미설정 — /answer·2차판정 비활성(분류·파싱 E2E 는 정상)"
# storage=minio 일 때만 minio 시크릿 필요(local 이면 minio 자체 불요)
STORAGE="$(_env_val STORAGE_BACKEND || true)"; STORAGE="${STORAGE:-minio}"
if [ "$STORAGE" = "minio" ]; then
  { mk="$(_env_val MINIO_SECRET_KEY || true)"; [ -n "$mk" ] && ! printf '%s' "$mk" | grep -qiE "$_ph_re"; } \
    || warn "MINIO_SECRET_KEY 미설정/placeholder — storage_backend=minio 는 startup 거부될 수 있음(테스트는 STORAGE_BACKEND=local 권장)"
fi

# CORS: prod overlay 는 CORS_ALLOW_ORIGINS 를 ["${ALLOWED_ORIGIN:-…}"] 로 강제(environment>env_file)
# → 배포 경로의 실효 노브는 ALLOWED_ORIGIN. 사용자가 어느 쪽에 적었든 동작하도록 브리지+검증.
ORIGIN="$(_env_val ALLOWED_ORIGIN || true)"
[ -n "$ORIGIN" ] || ORIGIN="$(_env_val CORS_ALLOW_ORIGINS | sed -E 's/[][]//g' | cut -d, -f1)"
[ -n "$ORIGIN" ] || die "CORS origin 미설정 — $ENV_FILE 에 ALLOWED_ORIGIN(또는 CORS_ALLOW_ORIGINS) 실 origin 설정"
printf '%s' "$ORIGIN" | grep -qiE '\*|replace_me|example\.com|your[_-]?host' \
  && die "CORS origin 이 placeholder/와일드카드: '$ORIGIN' — 실 포털/프런트 origin 으로(오픈망 노출 위험)"
export ALLOWED_ORIGIN="$ORIGIN"
info "env=$ENV_FILE · 자격증명 OK · CORS origin=$ORIGIN"

# ── 1. 이미지 빌드 ─────────────────────────────────────────
if [ "$SKIP_BUILD" = "1" ]; then
  log "1/7  이미지 빌드 (SKIP_BUILD=1 → 생략)"
else
  log "1/7  이미지 빌드 (Dockerfile.api.prod: non-root gunicorn)"
  dc build
fi

# ── 2. 인프라 기동 (postgres·redis·minio) ──────────────────
log "2/7  인프라 기동 + postgres 헬시 대기"
infra=(postgres redis); [ "$STORAGE" = "minio" ] && infra+=(minio)
info "인프라: ${infra[*]}  (storage=$STORAGE)"
dc up -d "${infra[@]}"
for i in $(seq 1 60); do
  if dc exec -T postgres pg_isready -U "$PG_USER" -d "$PG_USER" >/dev/null 2>&1; then
    info "postgres ready (${i}s)"; break
  fi
  [ "$i" = 60 ] && die "postgres 헬시 실패(60s). 'ps'/'logs postgres' 확인"
  sleep 1
done

# ── 3. 분류 모델 external 볼륨 (alembic 前 — api 컨테이너가 ro 마운트하므로 반드시 선존재) ──
# prod overlay 의 api/worker 는 prod_artifacts(external:true)를 마운트한다. 이 볼륨이 없으면
# 바로 다음 `dc run api`(alembic)부터 'external volume not found' 로 실패한다 → 먼저 생성.
log "3/7  분류 모델 external 볼륨 준비"
had_vol=0
docker volume inspect "$ARTIFACT_VOLUME" >/dev/null 2>&1 && had_vol=1
docker volume create "$ARTIFACT_VOLUME" >/dev/null    # 멱등 — external 볼륨 존재 보장
if [ "$had_vol" = 1 ]; then
  info "볼륨 $ARTIFACT_VOLUME 기존 재사용 — 재적재 생략(강제: docker volume rm 후 재실행)"
elif [ "$AUTO_LOAD_MODEL" != "1" ]; then
  warn "AUTO_LOAD_MODEL=0 → 빈 볼륨 생성. 모델 미적재+CLASSIFIER_MODEL_DIR 설정 시 부팅 거부(수동 적재 필요)"
else
  cmdir="$(_env_val CLASSIFIER_MODEL_DIR || true)"   # /app/artifacts/<rel>
  rel="${cmdir#/app/artifacts/}"
  if [ -z "$cmdir" ]; then
    warn "CLASSIFIER_MODEL_DIR 미설정 → 모델 없이 룰 폴백 테스트(빈 볼륨 OK · ServingRuleFallbackActive 경보)"
  elif [ "$rel" = "$cmdir" ] || [ ! -d "artifacts/$rel" ]; then
    warn "모델 소스 없음: artifacts/${rel:-?}  (CLASSIFIER_MODEL_DIR=$cmdir) → 빈 볼륨. 설정 유지 시 부팅 거부 — 수동 적재:"
    warn "  docker run --rm -v $ARTIFACT_VOLUME:/dst -v \"\$PWD/artifacts:/src:ro\" alpine cp -r /src/<model> /dst/<model>"
  else
    info "모델 적재: artifacts/$rel → $ARTIFACT_VOLUME"
    docker run --rm -v "$ARTIFACT_VOLUME:/dst" -v "$PWD/artifacts:/src:ro" alpine \
      sh -c "mkdir -p '/dst/$(dirname "$rel")' && cp -r '/src/$rel' '/dst/$(dirname "$rel")/'"
    info "적재 완료"
  fi
fi

# ── 4. DB 마이그레이션 (수동 · 단일 head) ──────────────────
log "4/7  alembic 마이그레이션 (run --rm api)"
dc run --rm api alembic upgrade head

# ── 5. 애플리케이션 기동 (api·worker·beat) ─────────────────
log "5/7  애플리케이션 기동 (api·worker·beat)"
dc up -d api worker beat

# ── 6. /healthz/ready 대기 ─────────────────────────────────
log "6/7  /healthz/ready 대기 (최대 ${READY_TIMEOUT}s)"
READY_URL="http://${API_HOST}:${API_PORT}/api/v1/healthz/ready"
ok=0
for i in $(seq 1 "$READY_TIMEOUT"); do
  curl -fsS "$READY_URL" >/dev/null 2>&1 && { ok=1; info "ready 200 (${i}s)"; break; }
  sleep 1
done
[ "$ok" = 1 ] || die "ready 미도달(${READY_TIMEOUT}s). 'logs api' 확인(모델 미공급·자격증명·HF캐시 재다운로드)"

# ── 7. 스모크 (분류 + 인수 severity floor) ─────────────────
if [ "$SKIP_SMOKE" = "1" ]; then
  log "7/7  스모크 (SKIP_SMOKE=1 → 생략)"
else
  log "7/7  스모크 — 분류 1건 + 인수 샘플팩(고등급 미탐 0)"
  API_KEY="$(_env_val API_KEY || true)"
  if [ -n "$API_KEY" ]; then
    code="$(curl -s -o /dev/null -w '%{http_code}' -X POST \
      "http://${API_HOST}:${API_PORT}/api/v1/classify" \
      -H "X-API-Key: $API_KEY" -H 'Content-Type: application/json' \
      -d '{"doc_id":"smoke-1","content":"본 문서는 당사의 반도체 공정 영업비밀을 포함한다"}' || true)"
    [ "$code" = 200 ] || die "classify 스모크 실패(HTTP $code)"
    info "classify 200 OK"
  else
    warn "API_KEY 미확보 → classify 스모크 생략"
  fi
  # 인수 러너(prod 이미지에 scripts/ 포함): 전 포맷 파싱 + severity floor(고등급 미탐=veto)
  if [ -f scripts/run_acceptance.py ]; then
    dc exec -T api python scripts/run_acceptance.py --mode http \
       --base-url "http://127.0.0.1:${API_PORT}" --api-key "${API_KEY:-}" \
      || die "인수 스모크 FAIL — 파싱실패/고등급 미탐(UNDER!). /healthz/deep 로 원인 확인"
  fi
fi

APIKEY_OUT="$(_env_val API_KEY || true)"
log "완료 ✓  오픈망 배포 성공 (lite-cloud)"
cat <<EOF
${c_bold}━━ 설치 확인 & 문서 E2E (파싱→분류) ━━━━━━━━━━━━━━━━━━━━━${c_off}
${c_dim}  API_KEY = ${APIKEY_OUT}${c_off}

  [한 방] bash scripts/verify_install.sh     # 헬스 + 문서 업로드 파싱·분류 스모크

  1) 헬스:   curl -fsS http://${API_HOST}:${API_PORT}/api/v1/healthz/ready   # 200
             curl -s   http://${API_HOST}:${API_PORT}/api/v1/healthz/deep    # 컴포넌트 상태
  2) 데모 콘솔(브라우저): http://<서버>:${API_PORT}/demo/parse_demo.html
             → 문서 드래그하면 파싱 텍스트·표·등급이 바로 보임
  3) 커맨드 E2E: 문서 하나 올려 파싱+분류 한 번에
     curl -s -X POST http://${API_HOST}:${API_PORT}/api/v1/documents/analyze \\
          -H "X-API-Key: ${APIKEY_OUT}" -F "file=@/경로/문서.pdf" | (jq . 2>/dev/null || cat)

${c_dim}  ⚠ 외부/브라우저 접근: api 는 127.0.0.1:${API_PORT} 바인딩(하드닝). 원격 브라우저는
    SSH 터널 권장:  ssh -L ${API_PORT}:127.0.0.1:${API_PORT} <user>@<서버>  → http://localhost:${API_PORT}/demo/parse_demo.html
  상태:  docker compose --env-file $ENV_FILE ${BASE[*]} ps
  로그:  docker compose --env-file $ENV_FILE ${BASE[*]} logs -f api${c_off}
EOF
