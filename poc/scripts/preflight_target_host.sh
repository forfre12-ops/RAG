#!/usr/bin/env bash
# ============================================================================
# KL 대상 호스트 최종 사전점검 (읽기 전용)
# ----------------------------------------------------------------------------
# 설치 전 "이 PC에서 스크립트가 실행된다"와 실제 Rocky/폐쇄망 대상 적합성은 다르다.
# 이 스크립트는 이미지 적재, compose up, 네트워크 연결을 하지 않는다. 그래서 이미지 내부
# 추출기와 실제 방화벽 차단은 PASS라고 추정하지 않고 NOT_READY로 남긴다.
#
# 사용:
#   NODE=customer bash preflight_target_host.sh
#   NODE=jjw REQUIRE_GPU=1 bash preflight_target_host.sh
#
# 종료: 0=호스트 사전요건 통과(후속 런타임 점검은 별도)
#       1=차단 결함, 2=대상 호스트/외부통신/이미지 추출기 증거가 아직 없음
# ============================================================================
set -uo pipefail

NODE="${NODE:-customer}"
API_PORT="${API_PORT:-8000}"
PG_PORT="${PG_PORT:-5432}"
REDIS_PORT="${REDIS_PORT:-6379}"
NEED_DISK_GB="${NEED_DISK_GB:-80}"
REQUIRE_GPU="${REQUIRE_GPU:-}"
if [ -z "${BUNDLE_ROOT:-}" ]; then
  # ``bash -s`` (used by the Windows focused test) has no BASH_SOURCE[0].
  # A real bundle execution has it; `$0` is the safe stdin fallback.
  _script_source="${BASH_SOURCE:-$0}"
  BUNDLE_ROOT="$(cd "$(dirname "$_script_source")" && pwd)"
fi
BASE_PREFLIGHT="$BUNDLE_ROOT/preflight_host.sh"
COMPOSE_FILE="${COMPOSE_FILE:-$BUNDLE_ROOT/infra-config/docker-compose.airgap.yml}"
ENV_FILE="${ENV_FILE:-$BUNDLE_ROOT/.env}"

case "$NODE" in
  customer|jjw) ;;
  *) printf '[FAIL] NODE must be customer or jjw (got %s)\n' "$NODE" >&2; exit 1 ;;
esac
if [ -z "$REQUIRE_GPU" ]; then
  [ "$NODE" = "jjw" ] && REQUIRE_GPU=1 || REQUIRE_GPU=0
fi

fail=0
not_ready=0
pass() { printf '  [PASS] %s\n' "$*"; }
na() { printf '  [NOT_APPLICABLE] %s\n' "$*"; }
pending() { printf '  [NOT_READY] %s\n' "$*"; not_ready=$((not_ready + 1)); }
bad() { printf '  [FAIL] %s\n' "$*"; fail=$((fail + 1)); }
section() { printf '\n%s\n' "$*"; }

is_wsl() {
  [ -n "${WSL_INTEROP:-}" ] || [ -n "${WSL_DISTRO_NAME:-}" ] || \
    grep -qiE '(microsoft|wsl)' /proc/sys/kernel/osrelease 2>/dev/null
}

port_is_listening() {
  local port="$1"
  command -v ss >/dev/null 2>&1 && ss -ltnH 2>/dev/null | awk '{print $4}' | \
    grep -Eq "[:.]${port}$"
}

env_value() {
  local key="$1"
  [ -r "$ENV_FILE" ] || return 0
  sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r' | sed 's/^"//;s/"$//'
}

is_local_or_private_url() {
  local url="$1" host
  host="$(printf '%s' "$url" | sed -E 's#^[a-zA-Z]+://([^/:]+).*#\1#')"
  case "$host" in
    localhost|host.docker.internal|127.*|::1|10.*|192.168.*|172.1[6-9].*|172.2[0-9].*|172.3[0-1].*) return 0 ;;
    *) return 1 ;;
  esac
}

section '1. 대상 호스트 판별'
if [ "$(uname -s 2>/dev/null || true)" != 'Linux' ]; then
  pending 'Linux target host is required; local non-Linux execution is not installation proof'
elif is_wsl; then
  pending 'WSL is not the Rocky/customer target host; do not treat its Docker, ports, GPU, SELinux, or disk as target PASS'
else
  pass "Linux target host ($(uname -r))"
fi

# WSL/local에서는 아래의 값이 고객사 서버 사실을 말해 주지 않는다. base preflight도
# 실행하지 않아 local PASS가 최종 승인처럼 보이는 것을 막는다.
if is_wsl || [ "$(uname -s 2>/dev/null || true)" != 'Linux' ]; then
  na 'Rocky/SELinux, host ports, disk/memory, and NVIDIA checks require direct execution on the target Linux host'
  section '결과'
  printf 'NOT_READY: target host evidence is required (fail=%s, not_ready=%s)\n' "$fail" "$not_ready"
  exit 2
fi

section '2. 기본 Rocky/Docker/SELinux 점검'
if [ -x "$BASE_PREFLIGHT" ] || [ -f "$BASE_PREFLIGHT" ]; then
  # Base script is also read-only. 80 GB is the current bundle/install floor;
  # do not silently fall back to its historical 40 GB default.
  if API_PORT="$API_PORT" NEED_DISK_GB="$NEED_DISK_GB" bash "$BASE_PREFLIGHT"; then
    pass 'base host preflight passed at the 80 GB installation floor'
  else
    bad 'base host preflight failed'
  fi
else
  bad "base preflight missing: $BASE_PREFLIGHT"
fi

section '3. Compose 포트'
for item in "API:$API_PORT" "PostgreSQL:$PG_PORT" "Redis:$REDIS_PORT"; do
  label="${item%%:*}"; port="${item##*:}"
  if port_is_listening "$port"; then
    bad "$label port $port is already listening; choose an unused mapped port before first install"
  else
    pass "$label port $port is not listening"
  fi
done

section '4. GPU (opt-in)'
if [ "$REQUIRE_GPU" = '1' ]; then
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    pass 'NVIDIA driver reports at least one GPU'
  else
    bad 'GPU profile requested, but nvidia-smi cannot enumerate a GPU'
  fi
  if command -v docker >/dev/null 2>&1 && docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q 'nvidia'; then
    pass 'Docker reports the nvidia runtime (no image was pulled or run)'
  else
    bad 'GPU profile requested, but Docker nvidia runtime is unavailable; install/configure NVIDIA Container Toolkit'
  fi
else
  na 'CPU customer profile does not require NVIDIA GPU or Container Toolkit'
fi

section '5. 폐쇄망 설정 증거'
if [ -r "$COMPOSE_FILE" ] \
  && grep -q 'HF_HUB_OFFLINE: "1"' "$COMPOSE_FILE" \
  && grep -q 'TRANSFORMERS_OFFLINE: "1"' "$COMPOSE_FILE"; then
  pass 'compose statically forces Hugging Face/Transformers offline mode'
else
  bad "offline model flags are absent from compose file: $COMPOSE_FILE"
fi

llm_provider="$(env_value LLM_PROVIDER)"
llm_url="$(env_value LOCAL_LLM_BASE_URL)"
if [ "$llm_provider" = 'noop' ] || [ -z "$llm_provider" ]; then
  pass 'LLM provider is disabled or not overridden in the target env file'
elif [ -n "$llm_url" ] && is_local_or_private_url "$llm_url"; then
  pass 'configured LLM endpoint is local/private by address policy'
else
  pending 'LLM endpoint is not locally/private proven; record an approved private endpoint or disable the provider'
fi
pending 'host firewall/ACL egress policy is not inspectable without an operations-owned rule export; no external connection was attempted'

section '6. 필수 추출기'
# rhwp/unhwp/pdfplumber/xlrd are installed inside the API/worker images, not on
# this host.  Declaring them healthy before the exact loaded image runs would
# repeat the prior "container healthy, extractor degraded" release mistake.
pending 'image-internal HWP/HWPX/PDF/XLS extractors require the post-install /healthz/deep and format smoke evidence; this read-only host preflight does not start containers'

section '결과'
if [ "$fail" -gt 0 ]; then
  printf 'FAIL: blocking target-host defects=%s; pending evidence=%s\n' "$fail" "$not_ready"
  exit 1
fi
if [ "$not_ready" -gt 0 ]; then
  printf 'NOT_READY: host prerequisites passed, but target runtime/egress evidence is pending=%s\n' "$not_ready"
  exit 2
fi
printf 'PASS: target-host pre-install requirements passed\n'
