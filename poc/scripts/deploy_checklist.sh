#!/usr/bin/env bash
# 배포 체크리스트 — 한 번에 실행하고, 실패하면 그 자리에서 멈춘다.
#
# 왜 스크립트인가. 배포 절차가 문서로만 있으면 단계가 빠진다. 실제로 빠졌다:
#   - 서버가 그 커밋을 실행 중인지 확인하는 단계가 없었다(healthz 에 git sha 0건)
#   - 검수 잡 등록이 절차에 없어 검수자에게 서명 대상이 없을 뻔했다
#   - 릴리스 게이트가 두 달 반 묵은 READY 를 재사용할 수 있었다
#
# 이 스크립트는 **확인만 하고 배포하지 않는다**(--deploy 를 주지 않는 한).
# 되돌리기 어려운 동작은 명시적으로 요구해야 실행된다.
#
# 사용:
#   bash scripts/deploy_checklist.sh --base-url http://223.130.156.134:8000 \
#        --api-key "$API_KEY" [--model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b] \
#        [--profile full-train] [--deploy]
set -uo pipefail

BASE_URL=""; API_KEY=""; MODEL_DIR=""; PROFILE=""; DO_DEPLOY=0
POOL="datasets/golden_review/ff5a822c/candidates.jsonl"
ACTOR="${DEPLOY_ACTOR:-unset}"

while [ $# -gt 0 ]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2;;
    --api-key) API_KEY="$2"; shift 2;;
    --model-dir) MODEL_DIR="$2"; shift 2;;
    --profile) PROFILE="$2"; shift 2;;
    --pool) POOL="$2"; shift 2;;
    --actor) ACTOR="$2"; shift 2;;
    --deploy) DO_DEPLOY=1; shift;;
    *) echo "unknown arg: $1"; exit 2;;
  esac
done

FAIL=0
step() { printf '\n=== %s\n' "$1"; }
ok()   { printf '  OK    %s\n' "$1"; }
bad()  { printf '  FAIL  %s\n' "$1"; FAIL=1; }
warn() { printf '  WARN  %s\n' "$1"; }

# ── 1. 배포 대상 커밋 확정 ────────────────────────────────────────────────────
step "1. 배포 대상 커밋"
HEAD_SHA=$(git rev-parse HEAD 2>/dev/null || echo "")
[ -n "$HEAD_SHA" ] && ok "HEAD = ${HEAD_SHA:0:12}" || bad "git HEAD 를 못 읽는다"
DIRTY=$(git status --porcelain 2>/dev/null | wc -l)
if [ "$DIRTY" -eq 0 ]; then ok "작업트리 깨끗"; else bad "미커밋 변경 ${DIRTY}건 - 배포 이미지와 소스가 갈린다"; fi

# ── 2. 서버가 지금 무엇을 실행 중인가 ─────────────────────────────────────────
step "2. 서버 현재 상태"
if [ -z "$BASE_URL" ]; then
  warn "--base-url 미지정 - 서버 확인 건너뜀"
else
  HZ=$(curl -s --max-time 20 "$BASE_URL/api/v1/healthz" 2>/dev/null || echo "")
  if [ -z "$HZ" ]; then
    bad "healthz 응답 없음: $BASE_URL"
  else
    ok "healthz 응답"
    SRV_SHA=$(printf '%s' "$HZ" | python -c "import json,sys; d=json.load(sys.stdin); print((d.get('build') or {}).get('git_sha','unknown'))" 2>/dev/null || echo unknown)
    SRV_PROF=$(printf '%s' "$HZ" | python -c "import json,sys; print(json.load(sys.stdin).get('deploy_profile','?'))" 2>/dev/null || echo '?')
    SRV_MODEL=$(printf '%s' "$HZ" | python -c "import json,sys; print(json.load(sys.stdin).get('classifier_model_dir','?'))" 2>/dev/null || echo '?')
    echo "        서버 sha=$SRV_SHA · profile=$SRV_PROF · model=$SRV_MODEL"
    if [ "$SRV_SHA" = "unknown" ]; then
      warn "서버가 빌드 sha 를 노출하지 않는다 - 이 배포부터 KOIPA_BUILD_SHA 를 구워야 한다"
    elif [ "${SRV_SHA:0:12}" = "${HEAD_SHA:0:12}" ]; then
      ok "서버가 HEAD 를 실행 중 - 재배포 불필요"
    else
      warn "서버 sha != HEAD - 미배포 커밋이 있다"
      git log --oneline "${SRV_SHA}..HEAD" 2>/dev/null | head -20 | sed 's/^/        /' || true
    fi
    [ -n "$PROFILE" ] && { [ "$SRV_PROF" = "$PROFILE" ] && ok "프로파일 일치" || bad "프로파일 $SRV_PROF != $PROFILE"; }
    [ -n "$MODEL_DIR" ] && { [ "$(basename "$SRV_MODEL")" = "$(basename "$MODEL_DIR")" ] && ok "모델 일치" || bad "모델 $SRV_MODEL != $MODEL_DIR"; }
  fi
fi

# ── 3. 릴리스 게이트 (최신성 강제) ────────────────────────────────────────────
step "3. 릴리스 게이트"
GATE_ARGS="--require-fresh"
[ -n "$MODEL_DIR" ] && GATE_ARGS="$GATE_ARGS --expect-model-dir $MODEL_DIR"
[ -n "$PROFILE" ] && GATE_ARGS="$GATE_ARGS --expect-profile $PROFILE"
if python scripts/check_release_gate.py $GATE_ARGS; then ok "게이트 통과"; else bad "게이트 차단 - readiness 재생성 필요"; fi

# ── 4. 검수 전달본 ────────────────────────────────────────────────────────────
step "4. 검수 전달본"
if [ -f "$POOL" ]; then
  N=$(grep -c . "$POOL" 2>/dev/null || echo 0)
  ok "$POOL — ${N}건"
else
  bad "$POOL 없음"
fi

# ── 5. 배포 (명시 요구 시에만) ────────────────────────────────────────────────
step "5. 배포"
if [ "$DO_DEPLOY" -ne 1 ]; then
  echo "  건너뜀 (--deploy 미지정). 실행할 명령:"
  cat <<CMD
        docker compose build \\
          --build-arg KOIPA_BUILD_SHA=$HEAD_SHA \\
          --build-arg KOIPA_BUILD_AT=\$(date -u +%Y-%m-%dT%H:%M:%SZ)
        docker compose up -d --no-build
CMD
elif [ "$FAIL" -ne 0 ]; then
  bad "앞 단계가 실패해 배포하지 않는다"
else
  docker compose build \
    --build-arg KOIPA_BUILD_SHA="$HEAD_SHA" \
    --build-arg KOIPA_BUILD_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    && docker compose up -d --no-build \
    && ok "배포 실행됨" || bad "배포 실패"
fi

# ── 6. 배포 후 해야 할 것 ─────────────────────────────────────────────────────
step "6. 배포 후 (수동)"
cat <<'NEXT'
  1) 서명 잡 등록 - API 재시작마다 다시
       python scripts/register_review_signoff_job.py \
         --base-url <서버> --actor <실계정> --api-key <키>
  2) signoff.html 이 200 이고 120건·등급 분포가 보이는지 확인
  3) 검수 권한 - 실사용자 JWT 에 admin/reviewer 부여가 1순위.
       공유 system 키를 admin 으로 바꾸는 것은 최후 수단(그 키의 전 권한이 넓어진다)
  4) 서명은 publish=true. 기본 false 는 미리보기라 게이트가 안 움직인다
  5) 서명 후 readiness 재생성 -> 이 스크립트 3단계 재실행
NEXT

printf '\n=== 판정: %s\n' "$([ $FAIL -eq 0 ] && echo PASS || echo FAIL)"
exit $FAIL
