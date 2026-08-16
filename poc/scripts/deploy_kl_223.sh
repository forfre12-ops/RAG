#!/usr/bin/env bash
# ============================================================================
# deploy_kl_223.sh — KL 서버(223.130.156.134)에 현재 브랜치를 배포한다.
# ----------------------------------------------------------------------------
# 서버에서 직접 실행한다:   bash ~/poc/scripts/deploy_kl_223.sh
#
# 왜 전용 스크립트인가. 8/15 에 182 를 배포하면서 **네 개의 지뢰**를 밟았고 넷 다
# 배포를 실제로 실패시켰다. 223 에서도 같은 순서로 터진다. 하나씩 손으로 고치면
# 그 사이 서비스가 내려가 있는다 — 실제로 182 에서 그랬다.
#
#   1) uv.lock 이 리네임(2026-08-12) 전 것    빌드가 "Missing workspace member koipa-ai" 로 죽는다
#   2) 외부 볼륨 이름이 lloydk_hf_cache       compose 는 koipa_hf_cache 를 요구한다
#   3) .env 가 LLOYDK_ 접두                   코드는 KOIPA_AUDIT_CHAIN_SECRET 을 읽는다 → startup 사망
#   4) compose 프로젝트명이 lloydk-*          빌드는 기본 프로젝트명으로 이미지를 만든다 → 6개 태그 필요
#   5) compose 파일이 리네임 전               airgap compose 가 lloydk-api 를 참조해 pull 실패
#                                            (실측 2026-08-15, 182 에 13곳 잔존)
#
# 이 스크립트는 넷 다 **자동 탐지**한다. 이름을 하드코딩하지 않는다 — 223 의 실제
# 이름을 모르기 때문이고, 모르는 것을 안다고 가정하면 그것이 다섯 번째 지뢰가 된다.
# ============================================================================
set -euo pipefail

BRANCH="${BRANCH:-fix/design-review-hardening}"
REPO="${REPO:-https://github.com/forfre12-ops/RAG.git}"
CF="-f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.dual.yml"
# ⚠ docker-compose.expose.yml 은 **저장소에 없고 서버에만 있는 파일**이다(223 에서 직접
#   만든 것). prod 는 api 를 127.0.0.1:8000 에 묶는데(리버스 프록시 뒤 전제) 223 은 콘솔을
#   외부에 열어야 해서 0.0.0.0 으로 덮는 파일을 따로 두고 있다.
#   실측 2026-08-16: 이 파일을 빼고 기동했더니 **외부에서 접속되던 검수 화면이 끊겼다.**
#   있으면 자동으로 포함한다 - 서버가 정한 노출 방식을 배포가 되돌리지 않게.
if [ -f docker-compose.expose.yml ]; then
  CF="$CF -f docker-compose.expose.yml"
fi
STAMP="$(date -u +%Y%m%d%H%M)"
# 실측 2026-08-16: 7단계가 `WORKER_MEM_LIMIT: unbound variable` 로 죽었다.
# 4b 는 `${WORKER_MEM_LIMIT:-16G}` 를 지역 변수 WANT 에만 담고 정작 변수 자체는 설정하지
# 않는데, 7단계가 그것을 맨이름으로 참조한다. `set -u` 라 그 순간 종료다.
# 빌드·태그까지 끝난 뒤 기동 직전에 죽어서 **옛 이미지가 계속 도는 채로** 끝났다.
# 여기서 한 번 정해 4b·7단계가 같은 값을 쓴다.
WORKER_MEM_LIMIT="${WORKER_MEM_LIMIT:-16G}"
# 1단계가 소스를 덮기 **전** 자기 자신의 해시. 덮은 뒤와 다르면 새 판으로 다시 시작한다.
SELF_SHA_BEFORE="$(sha256sum "$(cd "$(dirname "$0")" && pwd)/$(basename "$0")" 2>/dev/null | cut -d' ' -f1 || true)"

b(){ printf '\n\033[1m>> %s\033[0m\n' "$*"; }
ok(){ printf '\033[1;32m  [ok] %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m  [!]  %s\033[0m\n' "$*"; }
die(){ printf '\033[1;31m  [X]  %s\033[0m\n' "$*" >&2; exit 1; }

cd "$(dirname "$0")/.." 2>/dev/null || cd ~/poc

# --- 0. 현재 상태를 먼저 기록한다 - 되돌릴 곳을 모르면 되돌릴 수 없다 --------
b "0. 현재 상태"
docker ps --format '{{.Names}}' | sort | sed 's/^/    /' || warn "도는 컨테이너 없음"

# compose 프로젝트명 자동 탐지 (지뢰 4)
PROJECTS="$(docker ps --format '{{.Label "com.docker.compose.project"}}' | sort -u | grep -v '^$' || true)"
if [ -z "$PROJECTS" ]; then
  die "compose 프로젝트를 못 찾았다. 스택이 안 도는 상태면 배포가 아니라 최초 기동이다 - deploy_testserver_dual.sh 를 쓸 것."
fi
echo "  프로젝트: $(echo "$PROJECTS" | tr '\n' ' ')"

# 프로젝트별 env 파일·포트를 되읽는다
# ⚠ **api 서비스가 있는 프로젝트만** 대상으로 삼는다. 실측 2026-08-16: 223 에는 우리 스택
#   말고 `lloydk-proxy-gold`(ollama) 가 따로 돈다. 그것까지 대상에 넣으면 6단계에서 엉뚱한
#   이미지 태그를 만들고 7단계에서 남의 스택을 재기동해 **끊어놓는다.**
declare -A P_ENV P_PORT
TARGETS=""
for P in $PROJECTS; do
  API="$(docker ps --filter "label=com.docker.compose.project=$P" --filter "label=com.docker.compose.service=api" --format '{{.Names}}' | head -1)"
  if [ -z "$API" ]; then warn "$P: api 서비스 없음 - 우리 스택이 아니므로 건드리지 않는다"; continue; fi
  TARGETS="$TARGETS $P"
  P_PORT[$P]="$(docker port "$API" 2>/dev/null | head -1 | sed 's/.*://')"
  SUF="${P##*-}"
  for CAND in ".env.$SUF" ".env"; do
    if [ -f "$CAND" ]; then P_ENV[$P]="$CAND"; break; fi
  done
  if [ -z "${P_ENV[$P]:-}" ]; then die "$P 의 env 파일을 못 찾았다 (.env.$SUF · .env 둘 다 없음)"; fi
  echo "    $P  env=${P_ENV[$P]}  port=${P_PORT[$P]:-?}"
done
TARGETS="${TARGETS# }"
if [ -z "$TARGETS" ]; then die "api 서비스를 가진 프로젝트가 없다. 배포 대상이 없다."; fi
ok "배포 대상: $TARGETS"

# --- 1. 소스 갱신 -----------------------------------------------------------
b "1. 소스 갱신 -> $BRANCH"
tar czf ~/poc_src_backup_$STAMP.tgz src scripts 2>/dev/null && ok "백업 ~/poc_src_backup_$STAMP.tgz"
git rev-parse --git-dir >/dev/null 2>&1 || git init -q
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REPO"
# ⚠ `--depth 1` 은 이미 얕은 저장소에서 **조용히 옛 tip 을 남긴다.** 실측 2026-08-16:
#   서버가 3df1e1c7 를 받아놓고 원격 tip 은 2caf9330 이었다. 옵션을 빼면 정상 갱신된다.
git fetch origin "$BRANCH"
SHA="$(git rev-parse FETCH_HEAD)"; SHORT="${SHA:0:12}"
git archive FETCH_HEAD poc/src poc/scripts poc/tests poc/uv.lock poc/pyproject.toml \
    poc/Dockerfile.api.prod poc/Dockerfile.worker poc/docker-compose.yml \
    poc/docker-compose.prod.yml poc/docker-compose.dual.yml poc/docker-compose.airgap.yml poc/Makefile \
  | tar -x --strip-components=1 -C .
ok "소스 $SHORT"

# ⚠ 이 스크립트는 방금 **자기 자신도** 덮었다(poc/scripts 에 들어 있다). bash 는 실행 중인
#   파일을 조금씩 읽어가므로, 갱신된 내용은 **이 실행에는 안 먹는다.** 실측 2026-08-16:
#   7단계 버그를 고쳐 push 하고 배포를 다시 돌렸는데 같은 자리에서 또 죽었다 - 1단계가
#   고친 판을 받아 디스크에 썼지만 실행 중인 것은 옛 판이었다. 한 번 더 돌려서야 됐다.
#   갱신 전후 해시를 비교해 바뀌었으면 새 판으로 스스로 다시 시작한다.
#   KOIPA_DEPLOY_REEXEC 가드로 재시작은 한 번만 한다(무한 루프 방지).
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
NOW_SHA="$(sha256sum "$SELF" 2>/dev/null | cut -d' ' -f1 || true)"
if [ "${SELF_SHA_BEFORE:-}" != "$NOW_SHA" ] && [ -z "${KOIPA_DEPLOY_REEXEC:-}" ]; then
  warn "배포 스크립트 자체가 갱신됐다 - 새 판으로 다시 시작한다"
  export KOIPA_DEPLOY_REEXEC=1
  exec bash "$SELF" "$@"
fi

# --- 2. 지뢰 1: uv.lock -----------------------------------------------------
b "2. uv.lock 검사"
# 리네임 잔재는 uv.lock 만이 아니다 - compose 파일도 본다(지뢰 5, 실측 2026-08-15).
# ⚠ `set -euo pipefail` 아래서 grep 은 **못 찾으면 종료코드 1** 이다. || true 가 없으면
#   잔재가 없을 때(=정상일 때) 스크립트가 조용히 죽는다 - 223 에서 실제로 그랬다.
STALE="$(grep -l 'lloydk' docker-compose*.yml 2>/dev/null | xargs echo || true)"
if [ -n "$STALE" ]; then
  die "compose 에 리네임 전 이름이 남았다: $STALE - 위 git archive 가 덮었어야 한다. 브랜치를 확인할 것."
fi
ok "compose 리네임 정합"
if grep -q 'koipa' uv.lock 2>/dev/null; then
  ok "koipa 워크스페이스 있음"
else
  die "uv.lock 이 리네임 전 것이다. 위 git archive 가 poc/uv.lock 을 덮었어야 한다 - 브랜치를 확인할 것."
fi

# --- 3. 지뢰 2: 외부 볼륨 ---------------------------------------------------
b "3. 외부 볼륨"
NEED="$(grep -ohE '[a-z]+_hf_cache' docker-compose*.yml 2>/dev/null | sort -u | head -1 || true)"
NEED="${NEED:-koipa_hf_cache}"
if docker volume inspect "$NEED" >/dev/null 2>&1; then
  ok "$NEED 있음"
else
  OLD="$(docker volume ls --format '{{.Name}}' | grep -E '_hf_cache$' | head -1 || true)"
  docker volume create "$NEED" >/dev/null
  if [ -n "$OLD" ]; then
    warn "$NEED 없어 생성 · $OLD 에서 모델 캐시 복사(재다운로드 회피)"
    docker run --rm -v "$OLD":/from -v "$NEED":/to alpine sh -c 'cp -a /from/. /to/ 2>/dev/null || true'
    ok "복사 완료 $(docker run --rm -v "$NEED":/v alpine du -sh /v | cut -f1)"
  else
    warn "$NEED 생성(빈 볼륨 - 임베더 모델을 최초 1회 내려받는다)"
  fi
fi

# --- 4. 지뢰 3: 환경변수 접두 -----------------------------------------------
b "4. 환경변수 접두(KOIPA_)"
for P in $TARGETS; do
  E="${P_ENV[$P]:-}"
  if [ -z "$E" ]; then continue; fi
  cp -n "$E" "$E.bak_$STAMP" 2>/dev/null || true
  ADDED=0
  # LLOYDK_ 로만 있는 키를 KOIPA_ 로 복제한다. 기존 줄은 지우지 않는다(되돌림용).
  while IFS= read -r line; do
    K="${line%%=*}"; V="${line#*=}"
    NK="KOIPA_${K#LLOYDK_}"
    if ! grep -q "^${NK}=" "$E"; then
      printf '%s=%s\n' "$NK" "$V" >> "$E"
      ADDED=$((ADDED+1))
    fi
  done < <(grep -E '^LLOYDK_[A-Z_]+=' "$E" 2>/dev/null || true)
  if [ "$ADDED" -gt 0 ]; then ok "$E: KOIPA_ 키 $ADDED 개 추가(LLOYDK_ 은 보존)"; else ok "$E: 이미 정합"; fi
  grep -q '^KOIPA_AUDIT_CHAIN_SECRET=' "$E" \
    || warn "$E 에 KOIPA_AUDIT_CHAIN_SECRET 이 없다 - production 자격증명 누락으로 startup 이 죽는다"
done

# --- 4b. 워커 메모리 한도 ----------------------------------------------------
# 실측 2026-08-16: 재학습이 **4GB 상한에서 SIGKILL** 되고 celery 가 다시 집어드는 순환에
# 빠졌다(3.98GiB/4GiB 도달 시점 사망 · 새 모델 산출 0건 · 8/8 이후 계속). 원인은
# docker-compose.prod.yml 의 기본값 `${WORKER_MEM_LIMIT:-4G}` 이고, 그 주석은 "워커는 배치
# 분류·인덱싱이라 CPU 바운드" 라고 적고 있다 - **학습을 염두에 둔 값이 아니다.**
#
# ⚠ 182 에서는 .env 에 16G 로 적혀 있는데도 컨테이너가 4GiB 로 떠 있었다. 값을 적는 것만으로
#   부족하고 **컨테이너 재생성**이 있어야 반영된다(이 스크립트 7단계가 한다).
b "4b. 워커 메모리 한도"
for P in $TARGETS; do
  E="${P_ENV[$P]:-}"; [ -z "$E" ] && continue
  CUR="$(grep -m1 '^WORKER_MEM_LIMIT=' "$E" 2>/dev/null | cut -d= -f2- || true)"
  WANT="$WORKER_MEM_LIMIT"
  if [ "$CUR" != "$WANT" ]; then
    sed -i '/^WORKER_MEM_LIMIT=/d' "$E"
    printf 'WORKER_MEM_LIMIT=%s
' "$WANT" >> "$E"
    ok "$E: WORKER_MEM_LIMIT ${CUR:-미설정} -> $WANT"
  else
    ok "$E: WORKER_MEM_LIMIT $CUR"
  fi
done
HOSTMEM="$(free -g 2>/dev/null | awk 'NR==2{print $2}')"
[ -n "$HOSTMEM" ] && echo "    (호스트 총 메모리 ${HOSTMEM}GB)"

# --- 4c. 학습 산출물 경로 소유권 ---------------------------------------------
# 실측 2026-08-16: 223 의 artifacts_out 이 root 소유(drwxr-xr-x)라 컨테이너(uid1000 koipa)가
# 하위 디렉터리를 못 만든다 -> 재학습이 산출물을 못 쓴다. 마운트는 rw 인데 권한에서 막힌다.
b "4c. 학습 산출물 경로"
mkdir -p artifacts_out 2>/dev/null || true
OWNER="$(stat -c '%u' artifacts_out 2>/dev/null || echo '')"
if [ "$OWNER" != "1000" ]; then
  # sudo 는 비밀번호를 요구할 수 있다(223 이 그랬다). 도커 컨테이너는 root 로 도니
  # 그것으로 고친다 - 배포자는 이미 docker 그룹이다.
  if docker run --rm -v "$PWD/artifacts_out:/x" alpine chown -R 1000:1000 /x 2>/dev/null; then
    ok "artifacts_out 소유권 -> uid1000(컨테이너 계정) · docker 로 정정"
  fi
  # ⚠ 컨테이너가 만든 model.safetensors 는 -rw------- 로 떨어진다. 호스트 계정(uid 다름)이
  #   못 읽어 학습 산출물을 회수할 수 없다(실측 2026-08-16). 읽기 권한을 열어둔다.
  if docker run --rm -v "$PWD/artifacts_out:/x" alpine chmod -R a+rX /x 2>/dev/null; then
    ok "artifacts_out 읽기 권한 개방(학습 산출물 회수용)"
  else
    warn "artifacts_out 이 uid${OWNER} 소유다 - 컨테이너(uid1000)가 학습 산출물을 못 쓴다."
    warn "  수동 조치: docker run --rm -v ~/poc/artifacts_out:/x alpine chown -R 1000:1000 /x"
  fi
else
  ok "artifacts_out 소유권 정합(uid1000)"
fi

# --- 5. 빌드 ----------------------------------------------------------------
b "5. 빌드"
DOCKER_BUILDKIT=1 docker compose $CF build \
  --build-arg KOIPA_BUILD_SHA="$SHORT" \
  --build-arg KOIPA_BUILD_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" 2>&1 | tail -6
# compose 기본 프로젝트명. docker-compose.yml 1행의 `name:` 이 디렉터리명을 이긴다 -
# 여기서 basename 을 쓰면 이미지 이름을 못 찾는다(실제 값 koipa-poc, 디렉터리는 poc).
BUILT="$(grep -m1 '^name:' docker-compose.yml 2>/dev/null | awk '{print $2}' || true)"
BUILT="${BUILT:-$(basename "$PWD")}"
for S in api worker beat; do
  docker image inspect "${BUILT}-${S}:latest" >/dev/null 2>&1 \
    || die "빌드 산출 이미지 ${BUILT}-${S}:latest 를 못 찾았다"
done
ok "빌드 산출 ${BUILT}-{api,worker,beat}"

# --- 6. 지뢰 4: 6개 태그(+ 되돌림 태그) -------------------------------------
b "6. 태그"
for P in $TARGETS; do
  for S in api worker beat; do
    docker image inspect "${P}-${S}:latest" >/dev/null 2>&1 \
      && docker tag "${P}-${S}:latest" "${P}-${S}:rollback-$STAMP"
    docker tag "${BUILT}-${S}:latest" "${P}-${S}:latest"
  done
done
ok "되돌림 태그 :rollback-$STAMP · 새 빌드 :latest"

# --- 7. 기동 ----------------------------------------------------------------
b "7. 기동"
for P in $TARGETS; do
  ENV_FILE="${P_ENV[$P]}" API_HOST_PORT="${P_PORT[$P]:-8000}" \
  WORKER_MEM_LIMIT="$WORKER_MEM_LIMIT" \
    docker compose -p "$P" $CF up -d --no-build 2>&1 | grep -Ei 'error|started' | sed 's/^/    /' || true
done

# --- 8. 검증: 배포한 커밋이 실제로 도는가 -----------------------------------
b "8. 검증"
FAIL=0
for P in $TARGETS; do
  PORT="${P_PORT[$P]:-8000}"
  OUT=""
  for i in $(seq 1 30); do
    OUT="$(curl -s --max-time 10 "http://localhost:${PORT}/api/v1/healthz" 2>/dev/null || true)"
    echo "$OUT" | grep -q '"status"' && break
    sleep 5
  done
  GOT="$(echo "$OUT" | python3 -c 'import json,sys
try:
    print((json.load(sys.stdin).get("build") or {}).get("git_sha",""))
except Exception:
    print("")' 2>/dev/null)"
  if [ "$GOT" = "$SHORT" ]; then
    ok "$P :$PORT  git_sha=$GOT"
  else
    warn "$P :$PORT  git_sha='$GOT' (기대 $SHORT)"; FAIL=1
  fi
done

# ICD 필드가 실제로 계약에 노출되는지 - 이 배포의 핵심 변경이다
P1="$(echo "$PROJECTS" | head -1)"
PORT1="${P_PORT[$P1]:-8000}"
N="$(curl -s --max-time 20 "http://localhost:${PORT1}/api/v1/openapi.json" 2>/dev/null | grep -c 'security_marking' || true)"
if [ "$N" -gt 0 ]; then
  ok "ICD 필드 계약 노출 확인(security_marking ${N}회)"
else
  warn "ICD 필드가 OpenAPI 에 없다 - 옛 이미지가 돈다"; FAIL=1
fi

# 외부 노출 확인 - 콘솔을 외부에서 여는 운영이면 여기서 끊긴 것을 잡는다.
if [ -f docker-compose.expose.yml ]; then
  HOSTIP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  BIND="$(docker ps --format '{{.Ports}}' --filter "name=api" | head -1)"
  echo "    포트 바인딩: ${BIND:-확인 실패}"
  case "$BIND" in
    *0.0.0.0*) ok "외부 노출 유지" ;;
    *) warn "api 가 0.0.0.0 에 안 붙었다 - 외부에서 콘솔에 못 들어온다"; FAIL=1 ;;
  esac
fi

b "완료"
if [ "$FAIL" -eq 0 ]; then
  ok "배포 $SHORT 반영 확인"
else
  warn "일부 확인 실패. 되돌리려면:"
  # ⚠ $PROJECTS 가 아니라 $TARGETS 다. PROJECTS 에는 우리 스택이 아닌 것도 들어 있어
  #   (223 의 koipa-proxy-gold=ollama) 그대로 실행하면 없는 이미지에 태그를 만들고
  #   남의 스택을 우리 compose 로 재기동한다 - 0단계가 일부러 걸러낸 것을 되돌리는 안내였다.
  echo "    for P in $TARGETS; do for S in api worker beat; do"
  echo "      docker tag \$P-\$S:rollback-$STAMP \$P-\$S:latest; done; done"
  echo "    그 뒤 위 7단계를 다시 실행"
  exit 1
fi
