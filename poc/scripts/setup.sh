#!/usr/bin/env bash
# ============================================================================
# setup.sh — 원커맨드 설치 (Rocky Linux 8.10)
# ----------------------------------------------------------------------------
#   bash setup.sh                 # 대상 자동 판별(GPU 있으면 학습 노드)
#   NODE=jjw       bash setup.sh  # 지재원 학습·골든 노드로 강제
#   NODE=customer  bash setup.sh  # 고객사 운영 노드로 강제
#
# 이 스크립트 하나로 끝난다. 설치자는 .env 를 손으로 편집하지 않는다.
#
# 설치 대상이 둘이고 성격이 다르다:
#   지재원 서버   공공망 · KL 이 원격 접속해 설치 · 학습·골든 노드(GPU) · full-train
#   고객사 서버   폐쇄망 · USB 등 매체로 반입해 직접 설치 · 운영 추론(CPU) · onprem-local
# 둘 다 인터넷 저장소를 쓸 수 없다 — 컨테이너 런타임이 없으면 번들의 rpms/ 로 설치한다.
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
#   NODE=jjw|customer    설치 대상. 미지정이면 GPU 유무로 판별한다
#   API_PORT=8000        노출 포트
#   DRY_RUN=1            무엇을 할지만 출력하고 아무것도 바꾸지 않는다
#   OPEN_FIREWALL=1      firewalld 에 API 포트를 연다(기본 0 — 안내만)
#   ENV_FILE=.env        생성/사용할 env 파일
#   FORCE_ENV=1          이미 있는 .env 를 덮어쓴다(기본 0 — 보존)
#   REVIEWER_ID=expert-01  검수자 토큰의 계정 이름(지재원 노드만). 검수 기록에 이 이름이 남는다
#   CONSOLE_AUTOLOGIN=1  콘솔 주소를 열면 토큰 입력 없이 자동 로그인(기본 1). 0 이면 끄고 토큰을 붙여넣게 한다
#   CONSOLE_TOKEN_DAYS=365  콘솔 토큰 유효기간(일). 자동 로그인 토큰이 만료되면 로그인이 풀린다
# ============================================================================
set -euo pipefail

BUNDLE="$(cd "$(dirname "$0")" && pwd)"
cd "$BUNDLE"

API_PORT="${API_PORT:-8000}"
ENV_FILE="${ENV_FILE:-.env}"
DRY_RUN="${DRY_RUN:-0}"
OPEN_FIREWALL="${OPEN_FIREWALL:-0}"
FORCE_ENV="${FORCE_ENV:-0}"
REVIEWER_ID="${REVIEWER_ID:-expert-01}"
CONSOLE_AUTOLOGIN="${CONSOLE_AUTOLOGIN:-1}"
CONSOLE_TOKEN_DAYS="${CONSOLE_TOKEN_DAYS:-365}"

# 설치 대상 판별. 지재원 학습 노드는 GPU 가 있고 학습 라우터가 필요하다(full-train).
# 고객사 운영 노드는 CPU 전용이고 학습 경로가 아예 등록되지 않는다(onprem-local).
# 자동 판별은 GPU 유무로 한다 — 틀리면 NODE 로 강제한다.
if [ -z "${NODE:-}" ]; then
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    NODE=jjw
  else
    NODE=customer
  fi
  NODE_AUTO=1
else
  NODE_AUTO=0
fi
case "$NODE" in
  jjw)      PROFILE=full-train;   NODE_LABEL="지재원 학습·골든 노드" ;;
  customer) PROFILE=onprem-local; NODE_LABEL="고객사 운영 노드" ;;
  *) printf "NODE 는 jjw 또는 customer 여야 한다 (받은 값: %s)\n" "$NODE" >&2; exit 1 ;;
esac

b()   { printf '\n\033[1m>> %s\033[0m\n' "$*"; }
ok()  { printf '   \033[32m[ok]\033[0m %s\n' "$*"; }
inf() { printf '   \033[2m%s\033[0m\n' "$*"; }
die() { printf '\n\033[31m[setup][중단] %s\033[0m\n' "$*" >&2; exit 1; }
run() { if [ "$DRY_RUN" = "1" ]; then inf "(dry-run) $*"; else eval "$@"; fi; }

printf '\033[1m====== Koipa 설치 — %s ======\033[0m\n' "$NODE_LABEL"
[ "$NODE_AUTO" = "1" ] && inf "대상 자동 판별(GPU 유무). 다르면 NODE=jjw 또는 NODE=customer 로 재실행"
inf "프로파일 $PROFILE"

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
# manifest.yaml 이 없으면(예: 소스체크아웃에서 직접 실행) sed 가 rc=2 로 실패하고, pipefail 하에서
# 그 실패가 파이프 전체 종료코드로 올라와 set -e 가 스크립트를 여기서 죽인다 — 바로 다음 줄의
# ":-1.0.0-rc1" 폴백이 그래서 한 번도 실행되지 못했다(실측 2026-09-27: Rocky8 실치설치 리허설에서
# manifest.yaml 없이 실행하니 트레이스 없이 조용히 멈춤). "|| true" 로 파이프 실패를 흡수한다.
IMAGE_TAG_DEFAULT="$(sed -n 's/^ *version: *//p' "$BUNDLE/manifest.yaml" 2>/dev/null | head -1 || true)"
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
DEPLOY_PROFILE=${PROFILE}
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

# 외부 전문가 블라인드 검수 — 기본 꺼짐인 채로 두면 제안 등급이 검수자에게 그대로 보여
# 독립 판정이 깨진다(오류 없이 조용히 그렇게 됨). 안 쓰는 노드에서도 켜 둬서 해될 것이 없어
# 기본으로 켠다. 검수자별 배정까지 나누려면 GOLDEN_REVIEWER_ASSIGNMENT_ENFORCED=1 을 따로 켠다.
GOLDEN_REVIEW_BLIND_ENFORCED=1

# 후보 관리 화면의 기본 검수 배치 — 번들 golden_review_batch/ 의 회차. 옛 회차가 후보 풀에 쌓여도
# 첫 화면이 이번 회차만 보여 준다(그 배치가 서버에 없으면 자동으로 전체).
GOLDEN_DEFAULT_REVIEW_BATCH=expert_review_1731_20260924

# 골든 검수 콘솔 로그인 — 결정 저장(POST .../decision)은 공유 API_KEY 를 거부하고 포털 JWT
# 쿠키만 받는다("golden console requires a portal JWT login"). 이걸 안 채우면 AUTH_MODE
# 기본값(api_key)이 남아 **아무도 콘솔에 로그인하지 못한다**(로그인 화면은 "토큰이 잘못됐다"고
# 잘못 알린다 — 2026-09-24 로컬 재현 확인). JWT_JWKS_PATH 가 datasets/ 밑인 이유: 컨테이너는
# secrets/ 를 마운트하지 않고 golden_data 볼륨(/app/datasets)만 영속된다 — 아래 7단계가
# 여기 실제 키를 채운다.
AUTH_MODE=both
JWT_JWKS_PATH=datasets/_console_jwt/jwks.json
JWT_ISSUER=koipa-console
JWT_AUDIENCE=koipa-api

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

# >>> console-login helpers (tests/test_setup_console_login.py 가 이 구간을 떼어 시험한다)
# 스택을 올린다 — 6단계와 7단계(자동 로그인 적용 뒤 재기동)가 같은 명령을 쓴다.
_run_deploy() {
  SKIP_VERIFY=1 API_PORT="$API_PORT" ENV_FILE="$ENV_FILE" GPU_OVERLAY="${_gpu_overlay:-}" \
    bash "$BUNDLE/deploy_airgap.sh"
}
# ENV_FILE 의 KEY=값 줄을 지우고(_env_unset) 필요하면 끝에 다시 쓴다(_env_set). 권한 600 유지.
_env_unset() {
  local _t; _t="$(mktemp "${ENV_FILE}.XXXXXX")"
  { grep -v "^$1=" "$ENV_FILE" || true; } > "$_t"
  chmod 600 "$_t"; mv "$_t" "$ENV_FILE"
}
_env_set() { _env_unset "$1"; printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; }
# 토큰 발급기가 파일 이름으로 쓰는 형태(영숫자 . _ - 만 남긴다)
_tok_name() { printf '%s' "$1" | sed 's/[^A-Za-z0-9._-]/_/g'; }
# 컨테이너 안에서 토큰을 발급한다. $1=계정 $2=역할. 결과 JSON 은 표준출력(토큰 자체는 없다).
_issue_token() {
  ${CRT_COMPOSE} --env-file "$ENV_FILE" -f infra-config/docker-compose.airgap.yml \
    exec -T api python scripts/setup_console_test_login.py \
    --sub "$1" --roles "$2" --days "$CONSOLE_TOKEN_DAYS" \
    --also-copy-jwks datasets/_console_jwt/jwks.json 2>&1
}
# 발급된 토큰을 호스트 파일($2)로 꺼낸다 — 컨테이너를 다시 만들면 안의 토큰 파일이 사라진다. 권한 600.
_save_token() {
  local _f="$2.tmp"
  ${CRT_COMPOSE} --env-file "$ENV_FILE" -f infra-config/docker-compose.airgap.yml \
    exec -T api cat "secrets/console_jwt/tokens/$(_tok_name "$1").txt" 2>/dev/null | tr -d '\r\n' > "$_f" || true
  if [ -s "$_f" ]; then chmod 600 "$_f"; mv "$_f" "$2"; return 0; fi
  rm -f "$_f"; return 1
}
# <<< console-login helpers

# >>> install-data helpers (tests/test_setup_install_data.py 가 이 구간을 떼어 시험한다)
# 검수 문서를 golden_data 볼륨에 복사하고 그 볼륨의 후보 수를 표준출력 한 줄로 돌려준다.
# 이미지에는 검수 문서가 안 들어간다(.dockerignore 가 datasets/ 를 뺀다). 임시 컨테이너(api 이미지·볼륨
# 정의 그대로, 비-root uid 1000)로 복사하므로 볼륨 경로를 몰라도 되고 파일 소유자도 맞다 — 종전 수동
# 절차(cp)는 볼륨 경로를 모르면 못 했고, 소유자가 uid 1000 이 아니면 서명 제출이 500 이었다.
# cp -n 이라 이미 있는 후보와 검수 원장은 덮어쓰지 않는다. 건너뛴 파일이 있으면 coreutils 버전에 따라
# 종료코드가 1 일 수 있어 `|| true` 로 받고, 성공 여부는 건수로 판단한다.
_review_batch_into_volume() {
  ${CRT_COMPOSE} --env-file "$ENV_FILE" -f infra-config/docker-compose.airgap.yml \
    run --rm --no-deps -T -v "$1":/incoming:ro,z --entrypoint sh api -c '
      d=/app/datasets/proxy_gold/single_document_candidates
      mkdir -p "$d" || exit 1
      cp -n /incoming/* "$d"/ || true
      ls "$d"/MD-*.metadata.json 2>/dev/null | wc -l' 2>/dev/null | tail -1 | tr -d ' \r'
}
_load_review_batch() {
  local _src="$BUNDLE/golden_review_batch" _want _got
  if [ ! -d "$_src" ]; then
    inf "golden_review_batch/ 없음 — 검수 문서 적재를 건너뛴다(전문가 검수를 안 하는 번들이면 정상)"
    return 0
  fi
  _want="$(ls "$_src"/MD-*.metadata.json 2>/dev/null | wc -l | tr -d ' ')"
  _got="$(_review_batch_into_volume "$_src")" || _got=""
  if [ -n "$_got" ] && [ "$_got" -ge "$_want" ] 2>/dev/null; then
    ok "검수 문서 적재 — 볼륨에 후보 ${_got}건(번들 ${_want}건). 이미 있던 후보·검수 원장은 덮어쓰지 않았다"
  else
    inf "검수 문서 적재를 확인하지 못했다(볼륨 ${_got:-?}건 / 번들 ${_want}건) — bash setup.sh 를 다시 돌리거나 INSTALL.md §10.3 의 수동 절차를 쓴다"
  fi
}
# 배포된 분류기를 모델 버전 표에 '등록만' 한다(활성화 안 함 — 관리자 콘솔의 게이트를 거쳐서만). 이 단계가
# 없으면 GET /metrics/latest 가 404 "no active model" 이고 모델 활성화가 '미등록'으로 실패해 관제 화면이
# 빈다(서빙 자체는 CLASSIFIER_MODEL_DIR 로 정상). 멱등이다.
_register_model() {
  if ${CRT_COMPOSE} --env-file "$ENV_FILE" -f infra-config/docker-compose.airgap.yml \
      exec -T api python scripts/register_deployed_model.py >/dev/null 2>&1; then
    ok "배포 분류기를 모델 버전 표에 등록했다(활성화는 안 한다)"
  else
    inf "모델 등록에 실패했다 — 서빙은 정상이고 지표 카드·모델 활성화 화면만 비어 있다. bash setup.sh 를 다시 돌리면 된다(멱등)"
  fi
}
# <<< install-data helpers

# ── 6. 기동 ───────────────────────────────────────────────────
b "6. 스택 기동"
[ -f "$BUNDLE/deploy_airgap.sh" ] || die "deploy_airgap.sh 가 없다 — 번들이 불완전하다"
if [ "$DRY_RUN" = "1" ]; then
  inf "(dry-run) deploy_airgap.sh 실행 — 무결성→이미지→모델→인프라→마이그레이션→앱→스모크"
else
  # 학습 노드에서 GPU 가 실제로 잡히면 GPU 오버레이를 얹는다. compose 기본값에는 GPU 예약이
  # 없다 — GPU 없는 호스트에서 기동 자체가 막히던 문제 때문에 기본에서 뺐다.
  #
  # [2026-09-25] gpu.yml 은 **장치 예약만** 한다 — 자기 머리말이 스스로 적고 있듯, 이것만
  # 얹고 CPU 휠 이미지를 쓰면 "GPU 는 보이는데 연산은 CPU 로 도는" 상태가 되고 setup.sh 는
  # 그걸 알려줄 방법이 없었다(경고 0줄). gpu-train.yml 은 이 함정까지 같은 파일에 묶어
  # CUDA 이미지(koipa-gpu:*)를 함께 지정하므로, 그 이미지가 미리 빌드돼 있으면 이쪽을
  # 우선한다. 없으면 gpu.yml 로 내려가되 — 이번에 함정을 실제로 경고한다.
  _gpu_overlay=""
  if [ "$NODE" = "jjw" ] && command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    # nvidia-smi 는 드라이버 유무만 본다 — 컨테이너 런타임에 nvidia 런타임이 등록돼 있는지는
    # 별개다(nvidia-container-toolkit 설치 필요). 실측 2026-09-28: 드라이버는 있는데 런타임이
    # 없는 호스트에서 오버레이를 그대로 적용하니 "could not select device driver nvidia" 로
    # 마이그레이션 단계째 배포가 멈췄다 — 장치예약 실패는 "느리게 CPU 로 돈다"가 아니라 하드 크래시다.
    if ! "$CRT" info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"nvidia":'; then
      inf "[주의] GPU 는 보이는데(nvidia-smi) 이 호스트의 컨테이너 런타임에 nvidia 런타임이 등록돼 있지 않다(nvidia-container-toolkit 미설치로 추정) — CPU 로 기동한다. 오버레이를 적용했다면 이 자리에서 배포 전체가 멈췄을 것이다."
    else
      if "$CRT" image inspect koipa-gpu:cu130 >/dev/null 2>&1 || "$CRT" images -q "koipa-gpu" 2>/dev/null | grep -q .; then
        for _c in "$BUNDLE/infra-config/docker-compose.gpu-train.yml" "$BUNDLE/docker-compose.gpu-train.yml"; do
          [ -f "$_c" ] && { _gpu_overlay="$_c"; break; }
        done
      fi
      if [ -z "$_gpu_overlay" ]; then
        for _c in "$BUNDLE/infra-config/docker-compose.gpu.yml" "$BUNDLE/docker-compose.gpu.yml"; do
          [ -f "$_c" ] && { _gpu_overlay="$_c"; break; }
        done
        [ -n "$_gpu_overlay" ] && inf "[주의] koipa-gpu:cu130 이미지가 안 보인다 — 장치만 예약되고 학습은 CPU 휠로 돈다(느림, 실패는 아님). CUDA 이미지 빌드 후 재기동하면 gpu-train.yml 로 올라간다 — $(basename "$_gpu_overlay") 자체 주석 참고."
      fi
      if [ -n "$_gpu_overlay" ]; then
        inf "GPU 감지 — 오버레이 적용: $(basename "$_gpu_overlay")"
      else
        inf "[주의] GPU 는 있는데 docker-compose.gpu.yml 이 번들에 없다 — CPU 로 기동한다"
      fi
    fi
  fi
  _run_deploy
fi

# >>> install-data steps
# ── 6-1. 전문가 검수 문서 적재 (지재원 노드) — 이 단계가 없으면 콘솔은 뜨는데 검수할 문서가 0건이다 ──
if [ "$NODE" = "jjw" ]; then
  b "6-1. 전문가 검수 문서 적재"
  if [ "$DRY_RUN" = "1" ]; then
    inf "(dry-run) golden_review_batch/ 를 golden_data 볼륨에 복사(cp -n)하고 건수를 확인한다"
  else
    _load_review_batch
  fi
fi
# ── 6-2. 배포 모델 등록 ────────────────────────────────────────
b "6-2. 배포 모델 등록"
if [ "$DRY_RUN" = "1" ]; then
  inf "(dry-run) register_deployed_model.py — 배포 분류기를 모델 버전 표에 등록만 한다"
else
  _register_model
fi
# <<< install-data steps

# ── 7. 검수 콘솔 로그인 ───────────────────────────────────────
# 고객사·전문가는 "토큰"이 무엇인지 모른다. 로그인 화면이 토큰을 붙여넣으라고 하면 설치가 끝난 것이
# 아니다(2026-08-24 사용자 지시 — 사람이 토큰을 입력하는 안은 답이 아니다). 그래서 설치가
#   (1) 서명키와 토큰을 만들고  (2) 그 토큰을 .env 에 적어 로그인 화면에 미리 채워 둔다.
# 미리 채워진 로그인 화면은 버튼 없이 스스로 로그인한다(211·223 시험서버가 쓰던 방식) — 주소만 열면 된다.
#   · 지재원 노드 : 전문가용 reviewer 전용 토큰을 채운다(제안 등급이 가려진 채로 검수한다)
#   · 고객사 노드 : 관리자 토큰(admin,reviewer)을 채운다
# 대가: 그 주소에 닿는 누구나 그 토큰의 권한으로 들어간다 → 폐쇄망 안에서만 쓴다. 하드닝 프로파일은
# 이 값이 있으면 기동을 거부하므로 CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE=1 을 함께 적는다(명시적 허용).
# 끄려면 CONSOLE_AUTOLOGIN=0 bash setup.sh.
b "7. 검수 콘솔 로그인"
_autologin_on=0; _expiry=""; _prefill_who="admin"
_admin_file="$BUNDLE/console_admin_token.txt"
_rev_file="$BUNDLE/console_reviewer_token.txt"
_prefill_file="$_admin_file"
if [ "$DRY_RUN" = "1" ]; then
  inf "(dry-run) 서명키·관리자 토큰 발급(유효 ${CONSOLE_TOKEN_DAYS}일) → ${_admin_file##*/} 에 보관"
  if [ "$NODE" = "jjw" ]; then
    inf "(dry-run) 검수자 토큰(계정 ${REVIEWER_ID}, reviewer 전용) 발급 → ${_rev_file##*/}"
  fi
  if [ "$CONSOLE_AUTOLOGIN" = "1" ]; then
    inf "(dry-run) 로그인 화면에 토큰을 미리 채워 자동 로그인을 켠다 — .env 에 CONSOLE_LOGIN_PREFILL_* 기록 후 스택 재기동"
  else
    inf "(dry-run) 자동 로그인 끔 — 로그인 화면에 토큰을 붙여넣어야 한다"
  fi
else
  if _login_out="$(_issue_token admin admin,reviewer)"; then
    _expiry="$(printf '%s' "$_login_out" | sed -n 's/.*"만료": "\([0-9-]\{10\}\).*/\1/p' | head -1)"
    ok "관리자 토큰 발급 — 계정 admin${_expiry:+ · 만료 $_expiry}"
  else
    printf '%s\n' "$_login_out" >&2
    inf "관리자 토큰 발급 실패 — 위 오류 확인. bash setup.sh 를 다시 돌리면 이어서 한다(멱등)"
  fi
  if _save_token admin "$_admin_file"; then
    ok "관리자 토큰을 ${_admin_file##*/} 에 보관(권한 600) — 관리 기능이 필요할 때만 쓴다"
  else
    inf "관리자 토큰을 꺼내지 못했다 — 발급이 실패했으면 위 안내대로 다시 돌린다"
  fi
  # 검수자 토큰 — 지재원 노드에서만. 블라인드 검수(제안 등급 숨김)는 admin 역할이면 우회되므로 전문가는
  # reviewer 전용 토큰으로 들어가야 한다. 키쌍은 위에서 만든 것을 그대로 쓴다("기존 키 사용").
  if [ "$NODE" = "jjw" ]; then
    if _issue_token "$REVIEWER_ID" reviewer >/dev/null && _save_token "$REVIEWER_ID" "$_rev_file"; then
      ok "검수자 토큰 발급 — 계정 ${REVIEWER_ID} (reviewer 전용) → ${_rev_file##*/}"
      _prefill_file="$_rev_file"; _prefill_who="$REVIEWER_ID"
    else
      inf "검수자 토큰 발급 실패 — bash setup.sh 를 다시 돌리면 이어서 한다(멱등)"
    fi
  fi
  # 자동 로그인 — 토큰을 .env 에 적고 스택을 다시 올려야 로그인 화면이 그 값을 읽는다.
  if [ "$CONSOLE_AUTOLOGIN" = "1" ]; then
    if [ -s "$_prefill_file" ]; then
      _env_set CONSOLE_LOGIN_PREFILL_TOKEN "$(cat "$_prefill_file")"
      _env_set CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE 1
      inf "자동 로그인을 적용하려고 스택을 다시 올린다(1~2분)"
      if _run_deploy; then
        _autologin_on=1
        ok "자동 로그인 켜짐 — 콘솔 주소를 열면 토큰 입력 없이 들어간다"
      else
        inf "재기동이 경고를 남겼다 — 위 출력 확인. bash setup.sh 를 다시 돌리면 이어서 적용된다"
      fi
    else
      inf "자동 로그인 토큰이 없어 켜지 못했다 — 위 토큰 발급 결과를 확인한다"
    fi
  elif grep -q '^CONSOLE_LOGIN_PREFILL_TOKEN=' "$ENV_FILE"; then
    _env_unset CONSOLE_LOGIN_PREFILL_TOKEN
    _env_unset CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE
    inf "자동 로그인을 끈다(CONSOLE_AUTOLOGIN=0) — 스택을 다시 올린다"
    _run_deploy || inf "재기동이 경고를 남겼다 — 위 출력 확인"
  else
    inf "자동 로그인 끔(CONSOLE_AUTOLOGIN=0) — 로그인 화면에 토큰을 붙여넣어야 한다"
  fi
fi

# ── 8. 설치 검증 ───────────────────────────────────────────────
b "8. 설치 검증"
if [ -f "$BUNDLE/verify_install.sh" ]; then
  run "API_PORT='$API_PORT' bash '$BUNDLE/verify_install.sh'" || inf "검증 스크립트가 경고를 남겼다 — 위 출력을 확인한다"
else
  inf "verify_install.sh 없음 — 생략"
fi

# ── 완료 ──────────────────────────────────────────────────────
b "완료"
ok "설치가 끝났다"
# 안내문에 적을 .env 의 절대 경로(ENV_FILE 이 상대 경로면 번들 루트 기준)
case "$ENV_FILE" in /*) _env_abs="$ENV_FILE" ;; *) _env_abs="$BUNDLE/$ENV_FILE" ;; esac
cat <<DONE

  접속 확인 :  curl -s http://127.0.0.1:${API_PORT}/api/v1/healthz
  관리 콘솔 :  http://<서버주소>:${API_PORT}/console/admin.html
DONE
if [ "$NODE" = "jjw" ]; then
  printf '  검수 화면 :  http://<서버주소>:%s/api/v1/golden/candidates/manage.html   (전문가용)\n' "$API_PORT"
fi
cat <<DONE
  상태 보기 :  ${CRT_COMPOSE} --env-file ${ENV_FILE} -f infra-config/docker-compose.airgap.yml ps
  로그 보기 :  ${CRT_COMPOSE} --env-file ${ENV_FILE} -f infra-config/docker-compose.airgap.yml logs -f api

  설치 대상 : ${NODE_LABEL} (프로파일 ${PROFILE})

  ── KL 포털 연동에 필요한 값 ──
  호출 주소   :  http://<서버주소>:${API_PORT}/api/v1
  인증 헤더   :  X-API-Key  (KL 포털이 호출할 때 이 헤더에 API 키를 넣는다)
  API 키 위치 :  ${_env_abs} 의 API_KEY= 줄 (권한 600 — root 만 읽을 수 있다)
  API 키 확인 :  sudo grep '^API_KEY=' ${_env_abs}
  키는 설치기가 만든다. 손으로 입력하거나 화면에 붙여넣을 곳은 없고, 위 설치 확인 PASS 가 키의 적용까지 확인했다.
  ${ENV_FILE} 에는 이 밖에도 자동 생성된 비밀값이 들어 있다. 백업하고 외부에 공유하지 않는다.
DONE
if [ "$_autologin_on" = "1" ]; then
  cat <<LOGIN

  콘솔 로그인 :  위 주소를 열면 **토큰 입력 없이 자동으로 로그인**된다(로그인되는 계정: ${_prefill_who}).
    ⚠ 이 서버는 그 주소에 닿는 누구나 같은 권한으로 들어간다 — 폐쇄망 안에서만 쓴다.
      끄려면:  CONSOLE_AUTOLOGIN=0 bash setup.sh
    토큰 만료 :  ${_expiry:-확인 못 함} — 그 전에 bash setup.sh 를 다시 실행하면 갱신된다(데이터는 그대로).
    관리자 토큰(관리 기능이 필요할 때만) :  ${_admin_file} 의 내용을 로그인 화면에 붙여넣는다.
LOGIN
else
  cat <<LOGIN

  콘솔 로그인 :  자동 로그인이 꺼져 있다. 콘솔 주소를 열면 나오는 로그인 화면에
    ${_admin_file} 의 내용을 붙여넣는다(관리자 토큰).
LOGIN
  if [ "$NODE" = "jjw" ]; then
    printf '    전문가에게 줄 토큰(reviewer 전용, 계정 %s) :  %s\n' "$REVIEWER_ID" "$_rev_file"
  fi
  printf '    자동 로그인을 켜려면:  CONSOLE_AUTOLOGIN=1 bash setup.sh\n'
fi
