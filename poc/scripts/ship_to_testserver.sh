#!/usr/bin/env bash
# ============================================================================
# ship_to_testserver.sh — 소스+모델을 한 패키지로 묶어 테스트서버로 보내고 설치까지 한 방
# ----------------------------------------------------------------------------
# Windows Git Bash 에서 이 한 줄이면 끝: 패키징 → 전송 → 서버 설치(지재원+고객사 이중배포).
#   bash poc/scripts/ship_to_testserver.sh
#
# 대상 서버 기본값 = 현재 kip-ai 테스트서버. 바꾸려면 환경변수로:
#   TS_HOST=1.2.3.4 TS_PORT=22 TS_USER=aisadm bash poc/scripts/ship_to_testserver.sh
# 모드:
#   PACKAGE_ONLY=1 …  패키지만 생성(전송·설치 안 함)
#   SHIP_ONLY=1    …  전송까지만(서버 설치는 직접)
# 비밀번호는 scp/ssh 프롬프트에 입력(명령·파일에 안 박음).
# ============================================================================
set -euo pipefail

TS_HOST="${TS_HOST:-182.212.163.182}"
TS_PORT="${TS_PORT:-56320}"
TS_USER="${TS_USER:-aisadm}"
PKG="lloydk-testserver.tar.gz"

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
MODEL="poc/artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9"

b(){ printf '\033[1m%s\033[0m\n' "$*"; }
[ -f "$MODEL/model.safetensors" ] || { echo "✗ 모델 없음: $ROOT/$MODEL" >&2; exit 1; }

# ── 1) 패키지 생성 (추적 소스 + 모델) ───────────────────────────────────────
b "[1/3] 패키지 생성 (소스 + 모델)…"
rm -rf .ship && mkdir -p ".ship/poc"
git archive --format=tar HEAD:poc | tar -x -C ".ship/poc"          # 추적 소스(artifacts·datasets 제외)
# git archive 는 커밋본만 담는다 → 미커밋 배포 파일은 working tree 에서 보강(누락 방지).
for f in scripts/deploy_testserver_dual.sh docker-compose.dual.yml scripts/deploy_cloud.sh scripts/deploy_airgap.sh; do
  [ -f "poc/$f" ] && { mkdir -p ".ship/poc/$(dirname "$f")"; cp -f "poc/$f" ".ship/poc/$f"; }
done
mkdir -p ".ship/poc/artifacts/classifier_p1_retrain_v4_clean"
cp -r "$MODEL" ".ship/poc/artifacts/classifier_p1_retrain_v4_clean/"   # 모델만 얹기
tar czf "$PKG" -C .ship poc
# [#3] 패키징 정합성 게이트 — run 커맨드(deploy_testserver_dual.sh)가 요구하는 파일이 실제
# tar 에 들어갔는지 전송 전에 확인. 과거 stale tar(피처 커밋 이전 빌드)가 이 두 파일 없이
# 실려 서버에서 'No such file' 로 죽던 사고 재발 방지 — 누락이면 fail-fast(전송 안 함).
_need=("poc/scripts/deploy_testserver_dual.sh" "poc/docker-compose.dual.yml")
_manifest="$(tar tzf "$PKG")"
_missing=()
for _f in "${_need[@]}"; do
  printf '%s\n' "$_manifest" | grep -qx "$_f" || _missing+=("$_f")
done
rm -rf .ship
if [ "${#_missing[@]}" -gt 0 ]; then
  echo "✗ 패키지 무결성 실패 — tar 에 필수 배포 파일 누락(서버 설치가 반드시 실패):" >&2
  printf '   - %s\n' "${_missing[@]}" >&2
  echo "   git ls-files 로 HEAD 포함 여부 / working tree 존재를 확인 후 재실행하세요." >&2
  exit 1
fi
b "      → $PKG ($(du -h "$PKG" | cut -f1))"
[ "${PACKAGE_ONLY:-0}" = "1" ] && { echo "패키지만 생성 완료: $ROOT/$PKG"; exit 0; }

# ── 2) 전송 ─────────────────────────────────────────────────────────────────
b "[2/3] 전송 → $TS_USER@$TS_HOST:$TS_PORT  (서버 비밀번호 입력)…"
scp -P "$TS_PORT" "$PKG" "$TS_USER@$TS_HOST:~/"
if [ "${SHIP_ONLY:-0}" = "1" ]; then
  echo; b "전송 완료. 이제 서버 터미널에서 아래 한 줄:"
  echo "  tar xzf ~/$PKG -C ~ && cd ~/poc && bash scripts/deploy_testserver_dual.sh"
  exit 0
fi

# ── 3) 원격 설치 (서버에서 압축해제 + 이중배포) ─────────────────────────────
b "[3/3] 서버에서 설치 시작 (비밀번호 한 번 더 · 10~20분 · 창을 닫지 마세요)…"
ssh -p "$TS_PORT" "$TS_USER@$TS_HOST" "tar xzf ~/$PKG -C ~ && cd ~/poc && bash scripts/deploy_testserver_dual.sh"

echo; b "✓ 완료 — 지재원 :8000 · 고객사 :8001"
echo "원격 접속(터널): ssh -p $TS_PORT -L 8000:127.0.0.1:8000 -L 8001:127.0.0.1:8001 $TS_USER@$TS_HOST"
echo "그 뒤 브라우저: http://localhost:8000  ·  http://localhost:8001"
