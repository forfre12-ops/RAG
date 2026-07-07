#!/usr/bin/env bash
# ============================================================================
# 설치 확인 + 문서 E2E(파싱→분류) 스모크 — 배포 후 언제든 실행
# ----------------------------------------------------------------------------
#   1) /healthz/ready   200 = 모델·DB·스토어 준비
#   2) /healthz/deep    컴포넌트별 상태(모델/DB/벡터스토어/추출기)
#   3) POST /documents/analyze  문서 업로드 → 파싱 단계 + 분류 등급 (핵심 E2E)
#
# 사용:
#   bash scripts/verify_install.sh                              # .env.cloud 에서 키 추출·임시샘플
#   API_KEY=<키> BASE_URL=http://호스트:8000 bash scripts/verify_install.sh
#   FILE=문서.pdf bash scripts/verify_install.sh                # 내 문서로 파싱·분류 확인
# ============================================================================
set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
ENV_FILE="${ENV_FILE:-.env.cloud}"
FILE="${FILE:-}"
API_KEY="${API_KEY:-}"

# API_KEY 없으면 env 파일에서 추출
if [ -z "$API_KEY" ] && [ -f "$ENV_FILE" ]; then
  API_KEY="$(grep -E '^API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
fi

c_bold=$'\033[1m'; c_red=$'\033[31m'; c_grn=$'\033[32m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
hdr()  { printf '\n%s== %s ==%s\n' "$c_bold" "$*" "$c_off"; }
pass() { printf '  %s✓%s %s\n' "$c_grn" "$c_off" "$*"; }
fail() { printf '  %s✗ %s%s\n' "$c_red" "$*" "$c_off"; FAILED=1; }
jqp()  { jq "$@" 2>/dev/null || cat; }
FAILED=0

command -v curl >/dev/null 2>&1 || { echo "curl 필요"; exit 1; }
printf '%s대상: %s%s\n' "$c_dim" "$BASE_URL" "$c_off"

# ── 1. ready ────────────────────────────────────────────────
hdr "1) /healthz/ready"
if curl -fsS "$BASE_URL/api/v1/healthz/ready" >/dev/null 2>&1; then
  pass "ready 200 (모델·DB·스토어 준비)"
else
  fail "ready 실패 — 서버/모델/DB 미준비. 'logs api' 확인"
fi

# ── 2. deep(컴포넌트 상태) ──────────────────────────────────
hdr "2) /healthz/deep (컴포넌트 상태)"
curl -s "$BASE_URL/api/v1/healthz/deep" | jqp '.'

# ── 3. 문서 E2E: 파싱 + 분류 ────────────────────────────────
hdr "3) 문서 E2E — POST /documents/analyze (파싱→분류)"
if [ -z "$API_KEY" ]; then
  fail "API_KEY 미확보 — env=$ENV_FILE 없음/키 없음. API_KEY=<키> 로 지정"
else
  tmp=""
  if [ -z "$FILE" ]; then
    tmp="$(mktemp --suffix=.txt 2>/dev/null || mktemp)"; FILE="$tmp"
    printf '본 문서는 당사의 반도체 공정 영업비밀과 핵심 매출 데이터를 포함한다.\n2026년 신규 공정 수율(대외비)은 92%%이며 경쟁사 대비 우위이다.\n' > "$FILE"
    printf '%s  (샘플 자동생성: %s)%s\n' "$c_dim" "$FILE" "$c_off"
  fi
  [ -f "$FILE" ] || { fail "파일 없음: $FILE"; FILE=""; }
  if [ -n "$FILE" ]; then
    resp="$(curl -s -X POST "$BASE_URL/api/v1/documents/analyze" \
              -H "X-API-Key: $API_KEY" -F "file=@$FILE" || true)"
    [ -n "$tmp" ] && rm -f "$tmp"
    echo "$resp" | jqp '.'
    # 판정: 파싱(char_count) + 분류(등급 토큰) 존재 여부 = 두 질문에 직접 대응
    if printf '%s' "$resp" | grep -q 'char_count'; then pass "파싱 OK (텍스트 추출됨)"; else fail "파싱 실패 — 응답에 char_count 없음"; fi
    if printf '%s' "$resp" | grep -qE '(TS|S1|S2|S3)'; then pass "분류 OK (등급 산출됨)"; else fail "분류 실패 — 등급(TS/S1/S2/S3) 없음"; fi
  fi
fi

echo
if [ "$FAILED" = 0 ]; then
  printf '%s설치 확인 PASS ✓  — 파싱·분류 E2E 정상%s\n' "$c_grn" "$c_off"
else
  printf '%s설치 확인 FAIL ✗  — 위 항목 확인%s\n' "$c_red" "$c_off"; exit 1
fi
