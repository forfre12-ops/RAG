#!/usr/bin/env bash
# ============================================================================
# setup.sh — 폐쇄망 원커맨드 설치 (Rocky Linux 8.10 대상)
# ----------------------------------------------------------------------------
#   bash setup.sh
#
# 이 스크립트 하나로 끝난다. 설치자는 .env 를 손으로 편집하지 않는다.
#
# 왜 원커맨드인가. 설치는 발주처가 우리 없이 수행한다. 종전 절차는 5 단계였고
# 그중 '.env 의 replace_me 를 손으로 채우기' 에서 두 번 막혔다(2026-08-26 리허설 실측):
#   · KOIPA_AUDIT_CHAIN_SECRET 미설정 → api 가 startup 에서 죽음
#   · STORAGE_ENCRYPTION_ENABLED=0    → 하드닝 게이트가 기동 거부
# 둘 다 마이그레이션까지 다 돌고 ready 300초를 기다린 뒤에야 드러났다.
# 비밀값은 사람이 채울 이유가 없다 — 여기서 생성한다.
#
# Rocky 8.10 전제(실측 2026-08-26, rockylinux:8 기준):
#   · python3 가 없을 수 있다 → /usr/libexec/platform-python · openssl · /dev/urandom 순 폴백
#   · openssl · ss · free · getenforce · firewall-cmd 도 최소 설치엔 없을 수 있다
#   · 기본 런타임이 podman 이다 → docker 우선, 없으면 podman 으로 진행
#   · SELinux enforcing → compose 의 bind mount 에 :z 라벨이 이미 붙어 있다
#   · cgroup v1 기본 → 컨테이너 동작에 지장 없음
#
# 옵션(환경변수):
#   API_PORT=8000        노출 포트
#   DRY_RUN=1            무엇을 할지만 출력하고 아무것도 바꾸지 않는다
#   OPEN_FIREWALL=1      firewalld 에 API 포트를 연다(기본 0 — 안내만)
#   ENV_FILE=.env        생성/사용할 env 파일
#   FORCE_ENV=1          이미 있는 .env 를 덮어쓴다(기본 0 — 보존)
# ============================================================================
set -euo pipefail

BUNDLE="$(cd "$(dirname "$0")" && pwd)"
cd "$BUNDLE"

API_PORT="${API_PORT:-8000}"
ENV_FILE="${ENV_FILE:-.env}"
DRY_RUN="${DRY_RUN:-0}"
OPEN_FIREWALL="${OPEN_FIREWALL:-0}"
FORCE_ENV="${FORCE_ENV:-0}"

b()   { printf '\n\033[1m>> %s\033[0m\n' "$*"; }
ok()  { printf '   \033[32m[ok]\033[0m %s\n' "$*"; }
inf() { printf '   \033[2m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31m[setup][중단] %s\033[0m\n' "$*" >&2; exit 1; }
run() { if [ "$DRY_RUN" = "1" ]; then inf "(dry-run) $*"; else eval "$@"; fi; }

printf '\033[1m====== Koipa 폐쇄망 설치 ======\033[0m\n'
[ "$DRY_RUN" = "1" ] && inf "DRY_RUN=1 — 아무것도 바꾸지 않는다"

# ── 0. 호스트 점검 ─────────────────────────────────────────────
b "0. 호스트 점검"
if [ -f "$BUNDLE/preflight_host.sh" ]; then
  # 포트 점유는 재설치 시 정상이므로 여기서는 차단하지 않는다(아래 5단계가 판단).
  if API_PORT="$API_PORT" bash "$BUNDLE/preflight_host.sh"; then
    ok "점검 통과"
  else
    printf '\n'
    die "호스트 점검에서 차단 항목이 나왔다. 위 [FAIL] 을 해소한 뒤 다시 실행한다."
  fi
else
  inf "preflight_host.sh 없음 — 점검 생략"
fi

# ── 1. 무결성 ─────────────────────────────────────────────────
b "1. 번들 무결성"
if [ -f "$BUNDLE/verify.sh" ] && [ -f "$BUNDLE/CHECKSUMS.sha256" ]; then
  run "bash '$BUNDLE/verify.sh'" && ok "체크섬 일치"
else
  inf "verify.sh/CHECKSUMS 없음 — 검증 생략(리포에서 실행 중일 수 있다)"
fi

# ── 2. 컨테이너 런타임 ─────────────────────────────────────────
b "2. 컨테이너 런타임"
CRT=""
if command -v docker >/dev/null 2>&1; then CRT=docker
elif command -v podman >/dev/null 2>&1; then CRT=podman
fi
if [ -z "$CRT" ] && [ -d "$BUNDLE/rpms" ]; then
  inf "런타임이 없다 — 번들의 rpms/ 로 오프라인 설치를 시도한다"
  run "sudo dnf -y --disablerepo='*' localinstall '$BUNDLE'/rpms/*.rpm"
  run "sudo systemctl enable --now docker" || true
  command -v docker >/dev/null 2>&1 && CRT=docker
fi
[ -n "$CRT" ] || die "컨테이너 런타임이 없다.
  이 번들에 rpms/ 가 없으면 오프라인 설치를 할 수 없다. 둘 중 하나를 먼저 한다:
    · 인터넷 되는 곳에서 docker-ce 를 설치한다
    · Rocky 8.10 에서 'dnf download --resolve docker-ce docker-ce-cli containerd.io' 로
      받은 RPM 을 번들의 rpms/ 에 넣고 다시 실행한다
  podman 이 이미 있다면 그대로 진행되므로 이 메시지는 나오지 않는다."
if $CRT compose version >/dev/null 2>&1; then CRT_COMPOSE="$CRT compose"
elif [ "$CRT" = podman ] && command -v podman-compose >/dev/null 2>&1; then CRT_COMPOSE="podman-compose"
elif command -v docker-compose >/dev/null 2>&1; then CRT_COMPOSE="docker-compose"
else die "compose 가 없다 — '$CRT compose'(v2) 또는 podman-compose/docker-compose 필요"; fi
ok "런타임 $CRT · compose '$CRT_COMPOSE'"

# ── 3. 이미지 적재 ─────────────────────────────────────────────
b "3. 이미지 적재"
if [ -d "$BUNDLE/docker-images" ]; then
  for tar in "$BUNDLE"/docker-images/*.tar; do
    [ -e "$tar" ] || continue
    inf "load $(basename "$tar")"
    run "'$CRT' load -i '$tar' >/dev/null"
  done
  ok "적재 완료"
else
  inf "docker-images/ 없음 — 이미 적재돼 있다고 본다"
fi

# ── 4. 설정 생성 (비밀값 자동) ──────────────────────────────────
b "4. 설정 생성"
# 난수 32바이트 hex. Rocky 8 최소설치는 python3·openssl 이 없을 수 있어 세 단계로 폴백한다.
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  elif [ -x /usr/libexec/platform-python ]; then
    /usr/libexec/platform-python -c 'import secrets;print(secrets.token_hex(32))'
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import secrets;print(secrets.token_hex(32))'
  else
    od -An -tx1 -N32 /dev/urandom | tr -d ' \n'
  fi
}
IMAGE_TAG_DEFAULT="$(sed -n 's/^ *version: *//p' "$BUNDLE/manifest.yaml" 2>/dev/null | head -1)"
IMAGE_TAG_DEFAULT="${IMAGE_TAG_DEFAULT:-1.0.0-rc1}"

if [ -f "$ENV_FILE" ] && [ "$FORCE_ENV" != "1" ]; then
  ok "$ENV_FILE 이미 있음 — 보존한다(덮어쓰려면 FORCE_ENV=1)"
else
  if [ "$DRY_RUN" = "1" ]; then
    inf "(dry-run) $ENV_FILE 생성 — 비밀값 3종 자동 생성"
  else
    umask 077
    cat > "$ENV_FILE" <<ENVEOF
# setup.sh 가 생성했다 — 비밀값은 자동 생성된 것이다. 외부에 공유하지 않는다.
DEPLOY_PROFILE=onprem-local
POC_MODE=full
IMAGE_TAG=${IMAGE_TAG_DEFAULT}
API_PORT=${API_PORT}

POSTGRES_USER=koipa
POSTGRES_DB=koipa
POSTGRES_PASSWORD=$(gen_secret)
API_KEY=$(gen_secret)

# 감사체인 HMAC — 없으면 api 가 기동하지 않는다
KOIPA_AUDIT_CHAIN_SECRET=$(gen_secret)
# 골든 검수·서명 화면 URL 서명키 — 없으면 그 화면이 무인증으로 열린다
GOLDEN_HTML_URL_SECRET=$(gen_secret)

# 하드닝 프로파일 필수 — 원본 저장 암호화
STORAGE_ENCRYPTION_ENABLED=1
STORAGE_ENCRYPTION_KEY=$(gen_secret)

STORAGE_BACKEND=local
VECTOR_BACKEND=pg
LLM_PROVIDER=noop
CORS_ALLOW_ORIGINS=["http://127.0.0.1:${API_PORT}"]
ENVEOF
    chmod 600 "$ENV_FILE"
    ok "$ENV_FILE 생성 (권한 600 · 비밀값 5종 자동 생성)"
    inf "CORS_ALLOW_ORIGINS 는 콘솔을 여는 실제 주소로 바꿔야 브라우저에서 열린다"
  fi
fi
[ -d "$BUNDLE/infra-config" ] && run "cp -f '$ENV_FILE' '$BUNDLE/infra-config/.env'"

# ── 5. 방화벽 ─────────────────────────────────────────────────
b "5. 방화벽"
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  if [ "$OPEN_FIREWALL" = "1" ]; then
    run "sudo firewall-cmd --add-port=${API_PORT}/tcp --permanent"
    run "sudo firewall-cmd --reload"
    ok "firewalld 에 ${API_PORT}/tcp 개방"
  else
    inf "firewalld 가 켜져 있다. 외부에서 접속하려면 아래를 실행한다(또는 OPEN_FIREWALL=1 로 재실행):"
    inf "  sudo firewall-cmd --add-port=${API_PORT}/tcp --permanent && sudo firewall-cmd --reload"
  fi
else
  ok "firewalld 비활성 — 조치 불요"
fi

# ── 6. 기동 ───────────────────────────────────────────────────
b "6. 스택 기동"
[ -f "$BUNDLE/deploy_airgap.sh" ] || die "deploy_airgap.sh 가 없다 — 번들이 불완전하다"
if [ "$DRY_RUN" = "1" ]; then
  inf "(dry-run) deploy_airgap.sh 실행 — 무결성→이미지→모델→인프라→마이그레이션→앱→스모크"
else
  SKIP_VERIFY=1 API_PORT="$API_PORT" ENV_FILE="$ENV_FILE" bash "$BUNDLE/deploy_airgap.sh"
fi

# ── 7. 설치 검증 ───────────────────────────────────────────────
b "7. 설치 검증"
if [ -f "$BUNDLE/verify_install.sh" ]; then
  run "API_PORT='$API_PORT' bash '$BUNDLE/verify_install.sh'" || inf "검증 스크립트가 경고를 남겼다 — 위 출력을 확인한다"
else
  inf "verify_install.sh 없음 — 생략"
fi

# ── 완료 ──────────────────────────────────────────────────────
b "완료"
ok "설치가 끝났다"
cat <<DONE

  접속 확인 :  curl -s http://127.0.0.1:${API_PORT}/api/v1/healthz
  검수 콘솔 :  http://<서버주소>:${API_PORT}/console/admin.html
  상태 보기 :  ${CRT_COMPOSE} --env-file ${ENV_FILE} -f infra-config/docker-compose.airgap.yml ps
  로그 보기 :  ${CRT_COMPOSE} --env-file ${ENV_FILE} -f infra-config/docker-compose.airgap.yml logs -f api

  ${ENV_FILE} 에 자동 생성된 비밀값이 들어 있다(권한 600). 백업하고 외부에 공유하지 않는다.
  API_KEY 는 KL 포털이 호출할 때 X-API-Key 헤더에 넣는 값이다.
DONE
