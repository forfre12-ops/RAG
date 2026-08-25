#!/usr/bin/env bash
# ============================================================================
# 설치 전 호스트 점검 — install.sh 를 돌리기 전에 먼저 실행한다.
# ----------------------------------------------------------------------------
# 왜 필요한가. 설치는 발주처 담당자가 우리 없이 수행한다. 그 자리에서 처음 만나는 실패는
# 우리가 고칠 수 없다. 컨테이너 배포라 호스트 OS 자체는 대체로 무관하지만, RHEL·Rocky
# 계열에서 확실히 걸리는 지점이 넷 있다(SELinux · 컨테이너 런타임 · uid · 방화벽).
# 이 스크립트는 그 넷을 포함해 설치 전에 확인 가능한 것을 전부 미리 드러낸다.
#
# 성격 : 읽기 전용. 아무것도 설치하거나 변경하지 않는다.
# 사용 : bash preflight_host.sh            (번들 루트에서)
# 종료 : 0=진행 가능(경고 있을 수 있음) · 1=차단 항목 있음
# ============================================================================
set -uo pipefail

API_PORT="${API_PORT:-8000}"
NEED_DISK_GB="${NEED_DISK_GB:-40}"     # 번들 12GB + 적재 이미지 + 데이터 여유
CONTAINER_UID="${CONTAINER_UID:-1000}" # 운영 이미지가 비-root 로 도는 uid

fail=0; warn=0
ok()   { printf '  [ OK ] %s\n' "$*"; }
note() { printf '         %s\n' "$*"; }
warning() { printf '  [WARN] %s\n' "$*"; warn=$((warn+1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; fail=$((fail+1)); }
head_() { printf '\n%s\n' "$*"; }

echo "============================================================"
echo " 설치 전 호스트 점검"
echo " 실행 시각: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ── 1. 운영체제 ──────────────────────────────────────────────────────────
head_ "1. 운영체제"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  ok "${PRETTY_NAME:-$NAME $VERSION_ID}"
  OS_ID="${ID:-unknown}"; OS_LIKE="${ID_LIKE:-}"
else
  warning "/etc/os-release 를 읽을 수 없다 — OS 확인 불가"
  OS_ID="unknown"; OS_LIKE=""
fi
case "$OS_ID $OS_LIKE" in
  *rhel*|*fedora*|*rocky*|*centos*)
     note "RHEL 계열이다. 아래 SELinux·런타임 항목을 반드시 확인한다." ;;
esac
ok "커널 $(uname -r) · 아키텍처 $(uname -m)"
[ "$(uname -m)" = "x86_64" ] || bad "x86_64 가 아니다 — 동봉 이미지는 amd64 로 빌드되어 있다"

# ── 2. 컨테이너 런타임 ───────────────────────────────────────────────────
head_ "2. 컨테이너 런타임"
RUNTIME=""
if command -v docker >/dev/null 2>&1; then
  RUNTIME=docker
  ok "docker $(docker --version 2>/dev/null | sed 's/,.*//')"
  if docker info >/dev/null 2>&1; then
    ok "docker 데몬에 접근 가능"
  else
    bad "docker 데몬에 접근할 수 없다 — 서비스 기동 또는 사용자 권한(docker 그룹)을 확인한다"
  fi
  if docker compose version >/dev/null 2>&1; then
    ok "compose v2 사용 가능 ($(docker compose version --short 2>/dev/null))"
  elif command -v docker-compose >/dev/null 2>&1; then
    warning "compose v1(docker-compose)만 있다 — 배포 스크립트는 v2(docker compose) 문법을 쓴다"
    note "조치: docker-compose-plugin 설치"
  else
    bad "compose 를 찾을 수 없다 — docker-compose-plugin 이 필요하다"
  fi
elif command -v podman >/dev/null 2>&1; then
  RUNTIME=podman
  warning "docker 가 없고 podman 만 있다 ($(podman --version 2>/dev/null))"
  note "배포 스크립트는 docker 명령을 직접 호출한다. 둘 중 하나가 필요하다:"
  note "  · docker-ce 설치 (권장 — 스크립트 수정 불필요)"
  note "  · podman-docker + podman-compose 설치 후 연동 시험"
else
  bad "컨테이너 런타임이 없다 — docker 또는 podman 이 필요하다"
fi

# ── 3. SELinux ──────────────────────────────────────────────────────────
head_ "3. SELinux"
if command -v getenforce >/dev/null 2>&1; then
  MODE="$(getenforce 2>/dev/null)"
  case "$MODE" in
    Enforcing)
      warning "SELinux 가 Enforcing 이다"
      note "bind mount 경로에 라벨이 필요하다. 동봉 compose 는 ../models 와 mtls 경로에"
      note "이미 ':z' 라벨을 붙여 두었으므로 그대로 사용하면 된다."
      note "그래도 접근 거부가 나면 확인: sudo ausearch -m avc -ts recent" ;;
    Permissive) ok "SELinux Permissive — 라벨 문제로 막히지 않는다" ;;
    Disabled)   ok "SELinux 비활성" ;;
    *)          warning "SELinux 상태를 판별하지 못했다: ${MODE:-없음}" ;;
  esac
else
  ok "SELinux 도구 없음 (비-RHEL 계열로 보인다)"
fi

# ── 4. 방화벽 · 포트 ────────────────────────────────────────────────────
head_ "4. 방화벽 · 포트"
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  warning "firewalld 가 동작 중이다"
  note "API 포트 개방 필요: sudo firewall-cmd --add-port=${API_PORT}/tcp --permanent && sudo firewall-cmd --reload"
  note "컨테이너 간 통신이 막히면 docker 존 확인: sudo firewall-cmd --get-active-zones"
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi active; then
  warning "ufw 가 활성이다 — ${API_PORT}/tcp 개방이 필요할 수 있다"
else
  ok "호스트 방화벽이 활성이 아니거나 확인 대상 아님"
fi
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${API_PORT} "; then
  bad "포트 ${API_PORT} 이 이미 사용 중이다 — .env 의 API_PORT 를 바꾸거나 기존 프로세스를 정리한다"
else
  ok "포트 ${API_PORT} 사용 가능"
fi

# ── 5. 사용자 uid · gid ─────────────────────────────────────────────────
head_ "5. 사용자 uid · gid"
MYUID="$(id -u)"; MYGID="$(id -g)"
ok "설치 사용자 $(id -un) — uid ${MYUID} · gid ${MYGID}"
if [ "$MYUID" != "0" ] && [ "$MYUID" != "$CONTAINER_UID" ]; then
  warning "호스트 uid(${MYUID}) 와 컨테이너 실행 uid(${CONTAINER_UID}) 가 다르다"
  note "호스트에서 만든 디렉터리를 컨테이너가 못 쓰는 경우가 있다(운영 이미지는 비-root)."
  note "동봉 모델 디렉터리는 읽기 전용이라 대개 문제되지 않으나, 접근 거부가 나면 확인:"
  note "  ls -ln ./models    (숫자 uid 로 확인 — 이름이 아니라 숫자를 본다)"
fi

# ── 6. cgroup ───────────────────────────────────────────────────────────
head_ "6. cgroup"
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then
  ok "cgroup v2"
elif [ -d /sys/fs/cgroup/memory ]; then
  ok "cgroup v1"
else
  warning "cgroup 구성을 판별하지 못했다 — 컨테이너 메모리 제한이 적용되지 않을 수 있다"
fi

# ── 7. 자원 ────────────────────────────────────────────────────────────
head_ "7. 자원"
CPUS="$(nproc 2>/dev/null || echo '?')"
ok "CPU ${CPUS} 코어"
[ "$CPUS" != "?" ] && [ "$CPUS" -lt 4 ] 2>/dev/null && warning "4 코어 미만이다 — 추론 지연이 커진다"
if command -v free >/dev/null 2>&1; then
  MEMG="$(free -g | awk '/^Mem:/{print $2}')"
  ok "메모리 ${MEMG} GB"
  [ "${MEMG:-0}" -lt 16 ] 2>/dev/null && warning "16GB 미만이다 — 분류기 적재에 부족할 수 있다"
fi
AVAIL_GB="$(df -BG . 2>/dev/null | awk 'NR==2{gsub("G","",$4); print $4}')"
if [ -n "${AVAIL_GB:-}" ]; then
  if [ "$AVAIL_GB" -lt "$NEED_DISK_GB" ] 2>/dev/null; then
    bad "여유 디스크 ${AVAIL_GB}GB — 최소 ${NEED_DISK_GB}GB 필요(번들 12GB + 적재 이미지 + 데이터)"
  else
    ok "여유 디스크 ${AVAIL_GB}GB"
  fi
fi

# ── 8. 필수 명령 ────────────────────────────────────────────────────────
head_ "8. 필수 명령"
for c in tar curl sha256sum; do
  if command -v "$c" >/dev/null 2>&1; then ok "$c"; else bad "$c 없음"; fi
done

# ── 9. 시간 동기 ────────────────────────────────────────────────────────
head_ "9. 시간 동기"
if command -v timedatectl >/dev/null 2>&1; then
  if timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes; then
    ok "시간 동기됨 · $(timedatectl show -p Timezone --value 2>/dev/null)"
  else
    warning "시간이 동기되지 않았다 — 감사 로그와 토큰 유효기간에 영향을 준다"
  fi
else
  note "timedatectl 없음 — 시간 동기 상태를 확인하지 못했다"
fi

# ── 결과 ────────────────────────────────────────────────────────────────
echo
echo "============================================================"
if [ "$fail" -gt 0 ]; then
  echo " 결과: 차단 ${fail}건 · 경고 ${warn}건 — 차단 항목을 해소한 뒤 설치한다."
  exit 1
fi
if [ "$warn" -gt 0 ]; then
  echo " 결과: 차단 없음 · 경고 ${warn}건 — 경고 내용을 확인하고 진행한다."
else
  echo " 결과: 모든 항목 통과 — install.sh 로 진행한다."
fi
echo "============================================================"
exit 0
