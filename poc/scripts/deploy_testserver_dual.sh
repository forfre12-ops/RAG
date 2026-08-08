#!/usr/bin/env bash
# ============================================================================
# deploy_testserver_dual.sh — 테스트서버 1대에 지재원 + 고객사 두 스택 동시 배포
# ----------------------------------------------------------------------------
# "그냥 실행만" 목표: 필수 시크릿(API 키·감사키·암호화키)·DB·모델·마이그레이션·스모크를
# 전부 자동 처리한다. 인터넷 되는 테스트서버(연결망) 기준 — 이미지는 소스에서 직접 빌드한다.
#
#   지재원 스택  : profile=full-train   · 포트 8000 · project=lloydk-jjw
#   고객사 스택  : profile=onprem-local · 포트 8001 · project=lloydk-cust
# 두 스택은 컨테이너·네트워크·DB 볼륨이 완전 격리되고, HF 임베더 캐시만 공유한다.
#
# 사용:
#   bash scripts/deploy_testserver_dual.sh            # 둘 다 배포(기본)
#   bash scripts/deploy_testserver_dual.sh status     # 상태·URL·API키 출력
#   bash scripts/deploy_testserver_dual.sh down        # 둘 다 중지(데이터 볼륨 보존)
#   bash scripts/deploy_testserver_dual.sh down --wipe # 중지 + DB 볼륨까지 삭제(초기화)
#
# 옵션(환경변수):
#   JJW_PORT=8000  CUST_PORT=8001        # 호스트 노출 포트 변경
#   ANTHROPIC_API_KEY=sk-ant-...         # 주면 지재원 LLM=anthropic, 없으면 noop(분류는 LLM-free라 무관)
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

JJW_PORT="${JJW_PORT:-8000}"
CUST_PORT="${CUST_PORT:-8001}"
MODEL_REL="artifacts/classifier_p1_v5_clean/v-fe4b386b"   # v5 승격(gate_p1_candidate 3축 PASS 2026-07-29); 롤백=v-dd3abab9
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.dual.yml"
HF_VOLUME="lloydk_hf_cache"

c_b(){ printf '\033[1m%s\033[0m\n' "$*"; }
c_ok(){ printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
c_err(){ printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }

# ── 사전 점검 ───────────────────────────────────────────────────────────────
preflight() {
  command -v docker >/dev/null || { c_err "docker 미설치 — DEPLOY_GUIDE A0 참조"; exit 1; }
  docker compose version >/dev/null 2>&1 || { c_err "docker compose v2 필요"; exit 1; }
  docker info >/dev/null 2>&1 || { c_err "docker 데몬 접근 불가 — 'sudo usermod -aG docker \$USER' 후 재로그인"; exit 1; }
  if [ ! -f "$MODEL_REL/config.json" ] || [ ! -f "$MODEL_REL/model.safetensors" ]; then
    c_err "분류 모델 없음: $MODEL_REL (config.json+model.safetensors 필요)"
    echo "  → 모델을 이 경로에 두거나, CLASSIFIER_MODEL_DIR 을 비워 rule-fallback 으로 갈 수 있게 .env 를 편집하세요." >&2
    exit 1
  fi
  # non-root(uid1000) 컨테이너가 ro 바인드된 모델을 읽을 수 있게 보정.
  chmod -R a+rX "$MODEL_REL" 2>/dev/null || true
}

rand(){ openssl rand -hex "${1:-32}"; }

# ── 스택별 .env 생성(이미 있으면 유지 — 재실행 시 키 회전 방지) ──────────────
gen_env() {
  local file=$1 profile=$2 port=$3 llm=$4
  if [ -f "$file" ]; then
    # 기존 파일 유지(시크릿 회전 방지)하되, 배포 모델 포인터는 MODEL_REL 로 강제 동기화 —
    # 과거 MODEL_REL(v4)로 만든 .env 가 v5 승격 후에도 낡은 모델을 서빙하던 드리프트를 자동 치유.
    if grep -q '^CLASSIFIER_MODEL_DIR=' "$file"; then
      local _cur; _cur=$(grep '^CLASSIFIER_MODEL_DIR=' "$file" | head -1 | cut -d= -f2-)
      if [ "$_cur" != "$MODEL_REL" ]; then
        sed -i "s|^CLASSIFIER_MODEL_DIR=.*|CLASSIFIER_MODEL_DIR=$MODEL_REL|" "$file"
        c_ok "$file 유지 · 모델 포인터 $_cur → $MODEL_REL 동기화(드리프트 치유)"
      else
        c_ok "$file 유지(기존 · 모델 $MODEL_REL)"
      fi
    else
      echo "CLASSIFIER_MODEL_DIR=$MODEL_REL" >> "$file"
      c_ok "$file 유지 · CLASSIFIER_MODEL_DIR=$MODEL_REL 추가"
    fi
    return
  fi
  local pw api sek acs ghs
  pw=$(rand 16); api=$(rand 24); sek=$(rand 32); acs=$(rand 32); ghs=$(rand 32)
  {
    echo "# 자동생성 — $profile (deploy_testserver_dual.sh). 재생성하려면 이 파일을 지우고 재실행."
    echo "DEPLOY_PROFILE=$profile"
    echo "POC_MODE=full"
    echo "API_KEY=$api"
    # 테스트 편의: 단일 키로 관리자 콘솔(/demo/admin.html)까지 사용 가능하게 admin 역할 부여.
    # (실배포 deploy.sh/.env 템플릿은 기본 system 유지 — 최소권한. 콘솔용 admin 자격은 별도 발급 권장.)
    echo "API_KEY_ROLE=admin"
    echo "LLOYDK_AUDIT_CHAIN_SECRET=$acs"
    # 골든 검수/서명 HTML 서명 URL 강제 — 미설정이면 job_id 만 알면 후보 원문(TS 포함)이
    # 무인증으로 열린다. 배포가 항상 켜서 내보내도록 시크릿 대열에 편입(2026-08-02).
    echo "GOLDEN_HTML_URL_SECRET=$ghs"
    echo "STORAGE_BACKEND=local"
    echo "STORAGE_ENCRYPTION_ENABLED=1"
    echo "STORAGE_ENCRYPTION_KEY=$sek"
    echo "VECTOR_BACKEND=pg"
    echo "POSTGRES_PASSWORD=$pw"
    echo "DATABASE_URL=postgresql+psycopg://lloydk:$pw@postgres:5432/lloydk"
    echo "REDIS_URL=redis://redis:6379/0"
    echo "EMBEDDING_MODEL=nlpai-lab/KURE-v1"
    echo "CLASSIFIER_MODEL_DIR=$MODEL_REL"
    echo "ALLOWED_ORIGIN=http://localhost:$port"
    echo "WEB_CONCURRENCY=1"
    if [ "$llm" = "anthropic" ]; then
      echo "LLM_PROVIDER=anthropic"
      echo "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}"
    else
      echo "LLM_PROVIDER=noop   # 분류 핫패스는 LLM-free. /answer 만 비활성. 로컬 LLM 쓰려면 ollama+LLM_PROVIDER 변경"
    fi
  } > "$file"
  chmod 600 "$file"
  c_ok "$file 생성(시크릿 랜덤)"
}

dc() { local proj=$1 envf=$2; shift 2; ENV_FILE="$envf" docker compose -p "$proj" --env-file "$envf" $COMPOSE_FILES "$@"; }

wait_pg() {
  local proj=$1 envf=$2 i
  for i in $(seq 1 30); do
    if dc "$proj" "$envf" exec -T postgres pg_isready -U lloydk -q 2>/dev/null; then return 0; fi
    sleep 2
  done
  c_err "postgres 헬시 대기 초과 ($proj)"; return 1
}

# ── 스택 하나 배포 ──────────────────────────────────────────────────────────
deploy_stack() {
  local proj=$1 envf=$2 port=$3
  c_b "── $proj (port $port) 배포 ──"
  export API_HOST_PORT="$port"
  dc "$proj" "$envf" up -d postgres redis
  wait_pg "$proj" "$envf"
  # 빌드를 마이그레이션보다 **먼저** 돌린다. compose 는 이미지가 있으면 재사용하므로, 빌드를
  # 생략하면 소스를 새로 올려도 예전 코드가 뜬다(2026-08-01 실측: 소스는 최신인데 3일 전
  # 이미지가 기동 — unhwp 미설치·제외한 OCR 잔존). 그 상태로 `run --rm api alembic upgrade`
  # 를 돌리면 신규 마이그레이션이 통째로 누락된다. 레이어 캐시는 살아 있어 변경분만 재설치된다.
  c_b "  이미지 빌드(소스 반영)…"
  dc "$proj" "$envf" build api worker beat
  c_b "  마이그레이션(alembic upgrade head)…"
  dc "$proj" "$envf" run --rm api alembic upgrade head
  c_b "  앱 기동(api·worker·beat)…"
  dc "$proj" "$envf" up -d api worker beat
  c_ok "$proj 기동 완료"
}

smoke() {
  local name=$1 envf=$2 port=$3 key
  key=$(grep '^API_KEY=' "$envf" | cut -d= -f2)
  printf '  %-8s ' "$name"
  # 임베더 최초 다운로드로 첫 ready 가 늦을 수 있어 넉넉히 대기.
  local i ok=0
  for i in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$port/api/v1/healthz/ready" >/dev/null 2>&1; then ok=1; break; fi
    sleep 3
  done
  if [ "$ok" != 1 ]; then c_err "healthz/ready 미도달(:$port) — 'docker compose -p ... logs api' 확인"; return; fi
  local resp grade mv
  resp=$(curl -fsS -X POST "http://127.0.0.1:$port/api/v1/classify" \
      -H "X-API-Key: $key" -H 'Content-Type: application/json' \
      -d '{"doc_id":"smoke-1","content":"본 문서는 당사의 반도체 공정 영업비밀을 포함한다"}' 2>/dev/null || true)
  grade=$(printf '%s' "$resp" | tr ',' '\n' | grep -iE '"(grade|label|level)"' | head -1)
  # [#7] 배포 모델이 실제 로드됐는지 검증 — preflight 에서 모델 파일은 확인했으므로 rule-fallback 이면
  # 로드 실패(가중치·임베더 오류)다. 과거 smoke 는 등급 응답만 보고 rule-fallback 을 무음 통과시켜
  # '모델 미로드 상태로 배포 성공'처럼 보였다(#11 인수 blind spot). 서빙 model_version 을 노출하고
  # rule-fallback 이면 loud 경고(비차단 — rule-only 는 지원모드라 배포는 계속하되 반드시 보이게).
  mv=$(printf '%s' "$resp" | grep -oE '"model_version" *: *"[^"]*"' | head -1 | sed -E 's/.*: *"([^"]*)"/\1/')
  case "$mv" in
    rule-fallback*|none|"")
      c_err "$name: 모델 미로드(model_version=${mv:-없음}=rule-fallback) — 파일은 있으나 로드 실패. 'docker compose -p lloydk-<jjw|cust> $COMPOSE_FILES logs api' 로 원인 확인. 분류: ${grade:-?}" ;;
    *)
      c_ok "ready 200 · model=$mv · 분류: ${grade:-(응답 확인)}" ;;
  esac
}

print_summary() {
  echo
  c_b "══════════ 배포 요약 ══════════"
  local kj kc
  kj=$(grep '^API_KEY=' .env.jjw 2>/dev/null | cut -d= -f2)
  kc=$(grep '^API_KEY=' .env.cust 2>/dev/null | cut -d= -f2)
  printf '  %-8s  %-28s  API_KEY=%s\n' "지재원"  "http://127.0.0.1:$JJW_PORT"  "${kj:-?}"
  printf '  %-8s  %-28s  API_KEY=%s\n' "고객사"  "http://127.0.0.1:$CUST_PORT" "${kc:-?}"
  echo
  echo "  · 원격 접근(터널): ssh -L $JJW_PORT:127.0.0.1:$JJW_PORT -L $CUST_PORT:127.0.0.1:$CUST_PORT <user>@<host>"
  # 정적 콘솔은 app.py 에서 "/demo" 로 mount 된다 — 루트(/admin.html)는 404 다(2026-08-01 실측).
  echo "  · 관리 콘솔: http://127.0.0.1:$JJW_PORT/demo/admin.html · http://127.0.0.1:$CUST_PORT/demo/admin.html"
  echo "               (하드닝 프로파일이라 파괴적 /demo purge 는 비활성)"
  echo "  · 헬스     : http://127.0.0.1:$JJW_PORT/api/v1/healthz/ready"
  echo "  · 로그: docker compose -p lloydk-jjw $COMPOSE_FILES logs -f api"
  echo "  · 중지: bash scripts/deploy_testserver_dual.sh down"
}

# ── 서브커맨드 ──────────────────────────────────────────────────────────────
cmd="${1:-up}"
case "$cmd" in
  up)
    preflight
    docker volume inspect "$HF_VOLUME" >/dev/null 2>&1 || docker volume create "$HF_VOLUME" >/dev/null
    # external 볼륨은 root 소유로 생성됨 → 비-root(uid1000) 컨테이너가 HF 캐시에 못 써
    # 임베더 warmup 이 Permission denied 로 fail-clear(require_real_embedder). 소유권을 컨테이너 uid 로 보정.
    docker run --rm -v "$HF_VOLUME":/c alpine sh -c 'chown -R 1000:1000 /c' >/dev/null 2>&1 || true
    # [2026-08-06] datasets/ 는 호스트 bind(rw) 라 **호스트 계정 uid 가 1000 이 아니면** 컨테이너
    # (uid1000)가 골든 산출물을 못 쓴다 → 검수 서명이 500(PermissionError)으로 죽는다.
    # 실측: KL 제공 서버의 계정 kopia=uid1001 에서 재현(우리 테스트서버는 aisadm=1000 이라 우연히 통과).
    # HF 캐시와 같은 이유·같은 처방이며, 여기서 함께 보정한다(호스트 계정도 그룹 쓰기 유지).
    if [ -d "$ROOT/datasets" ] && [ "$(stat -c %u "$ROOT/datasets" 2>/dev/null || echo 1000)" != "1000" ]; then
      c_b "datasets/ 소유권 보정(uid1000) — 컨테이너 쓰기 권한"
      sudo chown -R 1000:1000 "$ROOT/datasets" 2>/dev/null || chown -R 1000:1000 "$ROOT/datasets" 2>/dev/null || true
      sudo chmod -R g+w "$ROOT/datasets" 2>/dev/null || chmod -R g+w "$ROOT/datasets" 2>/dev/null || true
    fi
    gen_env .env.jjw  full-train   "$JJW_PORT"  "$([ -n "${ANTHROPIC_API_KEY:-}" ] && echo anthropic || echo noop)"
    gen_env .env.cust onprem-local "$CUST_PORT" noop
    deploy_stack lloydk-jjw  .env.jjw  "$JJW_PORT"
    deploy_stack lloydk-cust .env.cust "$CUST_PORT"
    c_b "── 스모크 검증 ──"
    smoke 지재원 .env.jjw  "$JJW_PORT"
    smoke 고객사 .env.cust "$CUST_PORT"
    print_summary
    ;;
  status)
    for p in lloydk-jjw lloydk-cust; do c_b "[$p]"; docker compose -p "$p" $COMPOSE_FILES ps 2>/dev/null || true; done
    print_summary
    ;;
  down)
    extra=""; [ "${2:-}" = "--wipe" ] && extra="--volumes"
    for pe in "lloydk-jjw .env.jjw" "lloydk-cust .env.cust"; do
      set -- $pe
      ENV_FILE="$2" docker compose -p "$1" --env-file "$2" $COMPOSE_FILES down $extra 2>/dev/null || true
      c_ok "$1 중지 ${extra:+(볼륨 삭제)}"
    done
    ;;
  *)
    c_err "알 수 없는 명령: $cmd (up|status|down)"; exit 1;;
esac
