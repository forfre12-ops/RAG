#!/usr/bin/env bash
# ============================================================================
# 배포 롤백 — 활성 분류 모델을 이전(known-good) 버전으로 무중단 원복
# ----------------------------------------------------------------------------
# 새 모델이 문제면 즉시 되돌린다. POST /admin/model/activate 로 대상 버전을 활성화하면
# deploy gate(고등급 미탐 FNR·F1 회귀) 적용 + 무중단 hot reload 까지 한 번에 처리된다
# (컨테이너 재기동 불요). known-good 이전 버전으로의 롤백은 보통 게이트를 통과한다.
#
#   전제 : 스택이 떠 있고 대상 ModelVersion 이 등록돼 있음(이미지·모델 배치 완료)
#   권한 : admin (X-Actor-Role 또는 API_KEY_ROLE=admin)
#
# 사용:
#   bash scripts/deploy_rollback.sh <version_label> [--force] [--reason "사유"]
#   API_KEY=<키> BASE_URL=http://host:8000 bash scripts/deploy_rollback.sh v-dd3abab9
#   (ENV_FILE=.env.cloud 지정 시 거기서 API_KEY 추출)
#
#   version_label = artifacts/ 디렉토리명(예: v-dd3abab9) 또는 등록된 ModelVersion 라벨.
#   현재 활성/이전 버전은 metrics/dashboard 또는 artifacts/ 목록에서 확인.
# ============================================================================
set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
ENV_FILE="${ENV_FILE:-.env.cloud}"
API_KEY="${API_KEY:-}"
ACTOR_ROLE="${ACTOR_ROLE:-admin}"
FORCE=false
REASON=""
VERSION=""

while [ $# -gt 0 ]; do
  case "$1" in
    --force)      FORCE=true ;;
    --reason)     shift; REASON="${1:-}" ;;
    --reason=*)   REASON="${1#--reason=}" ;;
    -h|--help)    grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)           echo "unknown flag: $1" >&2; exit 2 ;;
    *)            VERSION="$1" ;;
  esac
  shift
done

c_bold=$'\033[1m'; c_red=$'\033[31m'; c_grn=$'\033[32m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
log()  { printf '\n%s[rollback] %s%s\n' "$c_bold" "$*" "$c_off"; }
jqp()  { jq "$@" 2>/dev/null || cat; }

[ -n "$VERSION" ] || { echo "사용: deploy_rollback.sh <version_label> [--force] [--reason \"사유\"]"; exit 2; }
# version_label 은 안전 문자셋만(주입·JSON깨짐 차단). 실제 버전은 v-<hex>/영숫자 형태.
case "$VERSION" in *[!A-Za-z0-9._/-]*) echo "version_label 은 [A-Za-z0-9._/-] 만 허용: $VERSION" >&2; exit 2;; esac
command -v curl >/dev/null 2>&1 || { echo "curl 필요"; exit 1; }
if [ -z "$API_KEY" ] && [ -f "$ENV_FILE" ]; then
  API_KEY="$(grep -E '^API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
fi
[ -n "$API_KEY" ] || { echo "API_KEY 미확보 — env=$ENV_FILE 없음/키 없음. API_KEY=<키> 로 지정"; exit 2; }

AUTH=(-H "X-API-Key: $API_KEY" -H "X-Actor-Role: $ACTOR_ROLE")

# 1) 현재 활성 모델 — 무엇으로부터 롤백하는지
log "현재 활성 모델"
curl -s "$BASE_URL/api/v1/metrics/latest" "${AUTH[@]}" \
  | jqp '{active: .model_version, f1: .f1, fnr: .fnr}' 2>/dev/null || echo "  (조회 실패 — 계속)"

# 2) 대상 버전 활성화 (deploy gate + 무중단 reload)
log "→ $VERSION 활성화 (force=$FORCE, reason='${REASON:-rollback}')"
# reason 은 감사 노트라 JSON 깨는 문자만 제거(lossy 허용). version 은 위에서 안전 문자셋 검증됨.
reason_safe="$(printf '%s' "${REASON:-rollback}" | tr -d '\\"' | tr '\n\r\t' '   ')"
body="$(printf '{"version_label":"%s","force":%s,"reason":"%s"}' "$VERSION" "$FORCE" "$reason_safe")"
resp="$(curl -s -X POST "$BASE_URL/api/v1/admin/model/activate" \
  "${AUTH[@]}" -H 'Content-Type: application/json' -d "$body" || true)"
echo "$resp" | jqp '{activated, blocked, forced, model_version, reloaded, reason}'

# 3) 판정
if printf '%s' "$resp" | grep -qE '"activated":[[:space:]]*true'; then
  printf '%s롤백 성공 ✓  활성=%s (무중단 reload)%s\n' "$c_grn" "$VERSION" "$c_off"
elif printf '%s' "$resp" | grep -qE '"blocked":[[:space:]]*true'; then
  printf '%sdeploy gate 차단 — 이 버전이 현 활성 대비 회귀(FNR·F1)로 판정됨.%s\n' "$c_red" "$c_off"
  echo "$resp" | jqp '.gate'
  printf '  확실히 되돌리려면: --force --reason "사유"  (강제활성은 감사에 forced=true 로 기록)\n'
  exit 1
else
  printf '%s활성 실패 — 권한(admin)·버전 등록·응답 확인.%s\n' "$c_red" "$c_off"
  printf '  403 이면: API_KEY_ROLE=admin 설정 또는 X-Actor-Role 신뢰(개발) 필요.\n'
  printf '  버전 미등록이면: 대상 모델을 볼륨/artifacts 에 두고 seed/등록 후 재시도.\n'
  exit 1
fi

# 4) ready 확인
if curl -fsS "$BASE_URL/api/v1/healthz/ready" >/dev/null 2>&1; then
  printf '  /healthz/ready 200 ✓\n'
else
  printf '  %s/healthz/ready 실패 — logs 확인%s\n' "$c_red" "$c_off"
fi

printf '%s\n  멀티워커(WEB_CONCURRENCY>1)면 reload 는 로컬 프로세스만 갱신 — 재기동 권장(NFR-OPS-01).%s\n' "$c_dim" "$c_off"
