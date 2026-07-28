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
rm -rf .ship
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
