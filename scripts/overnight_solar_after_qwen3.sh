#!/bin/bash
# 야간 무중단: Qwen3 합성 완료 직후 Solar로 자동 전환 + 합성 200건.
#
# 가정:
# - Qwen3 합성 PID 또는 출력 파일이 완성되면 종료 신호
# - 본 스크립트가 폴링하다 종료 감지 후 .env 모델 변경 → Solar 합성 시작
#
# 사용:
#   bash scripts/overnight_solar_after_qwen3.sh

set -e

# 2026-06-18: 드라이브 레터(/e/) 하드코딩 제거 — 스크립트 위치 기준 자동 탐지.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QWEN_DIR="$REPO_ROOT/poc/datasets/synthetic_qwen3"
SOLAR_DIR="$REPO_ROOT/poc/datasets/synthetic_solar"
QWEN_TARGET=200

# 안전장치: 스크립트 시작 시점의 LOCAL_LLM_MODEL 백업 → 모든 종료 경로에서 복원.
# 야간 무중단 실패 시(SIGTERM·SIGINT·script crash) .env가 solar로 남는 사고 차단.
ENV_FILE="$REPO_ROOT/poc/.env"
ORIGINAL_MODEL=$(grep -E '^LOCAL_LLM_MODEL=' "$ENV_FILE" | head -1 | cut -d'=' -f2)
echo "[overnight] 시작 시 LOCAL_LLM_MODEL=${ORIGINAL_MODEL} 백업"

restore_env() {
  if [ -n "$ORIGINAL_MODEL" ] && [ -f "$ENV_FILE" ]; then
    current=$(grep -E '^LOCAL_LLM_MODEL=' "$ENV_FILE" | head -1 | cut -d'=' -f2)
    if [ "$current" != "$ORIGINAL_MODEL" ]; then
      sed -i "s|^LOCAL_LLM_MODEL=.*$|LOCAL_LLM_MODEL=${ORIGINAL_MODEL}|" "$ENV_FILE"
      echo "[overnight] EXIT trap — LOCAL_LLM_MODEL 복원: ${current} → ${ORIGINAL_MODEL}"
    fi
  fi
}
trap restore_env EXIT INT TERM

cd "$REPO_ROOT"

echo "[overnight] $(date '+%H:%M:%S') Qwen3 진행 폴링 시작 (목표 ${QWEN_TARGET} 건)"

# Qwen3 완료 폴링 (최대 6시간)
deadline=$((SECONDS + 21600))
while [ $SECONDS -lt $deadline ]; do
  count=$(ls "$QWEN_DIR" 2>/dev/null | wc -l)
  if [ "$count" -ge "$QWEN_TARGET" ]; then
    echo "[overnight] $(date '+%H:%M:%S') Qwen3 완료 (${count}건)"
    break
  fi
  # 30초 미진척 5회 = 정체 종료로 간주
  prev_count="${prev_count:-0}"
  if [ "$count" -gt "$prev_count" ]; then
    prev_count=$count
    stale=0
  else
    stale=$((stale + 1))
    if [ "$stale" -ge 60 ]; then
      echo "[overnight] $(date '+%H:%M:%S') Qwen3 정체 감지 (${count}건). 더 이상 대기 안 함."
      break
    fi
  fi
  sleep 30
done

count=$(ls "$QWEN_DIR" 2>/dev/null | wc -l)
echo "[overnight] $(date '+%H:%M:%S') Qwen3 최종 ${count} 건. Solar 합성 진입."

# .env LOCAL_LLM_MODEL 변경: qwen3:14b → solar:10.7b
sed -i 's|^LOCAL_LLM_MODEL=qwen3:14b$|LOCAL_LLM_MODEL=solar:10.7b|' poc/.env
echo "[overnight] .env LOCAL_LLM_MODEL → solar:10.7b 변경"
grep "^LOCAL_LLM_MODEL=" poc/.env

# Solar 합성 200건
cd poc
export PYTHONPATH=src
export PYTHONIOENCODING=utf-8
export MLFLOW_TRACKING_URI=http://localhost:5000
echo "[overnight] $(date '+%H:%M:%S') Solar 합성 시작 (목표 200건)"
./.venv/Scripts/python.exe scripts/p3_generate_synthetic.py \
  --total 200 \
  --out datasets/synthetic_solar \
  --provider local_openai \
  --report ../report/phase5_p3_solar_2026-05-30.md \
  >> ../report/phase5_p3_solar_run.log 2>&1

echo "[overnight] $(date '+%H:%M:%S') Solar 완료"

# .env 복원: solar → qwen3 (default 유지, Phase 5.3에서 1순위 확정 시 별도 변경)
cd ..
sed -i 's|^LOCAL_LLM_MODEL=solar:10.7b$|LOCAL_LLM_MODEL=qwen3:14b|' poc/.env
echo "[overnight] .env LOCAL_LLM_MODEL → qwen3:14b 복원"

# Solar 결과 카운트
solar_count=$(ls "$SOLAR_DIR" 2>/dev/null | wc -l)
echo "[overnight] 최종 상태:"
echo "  Qwen3: $(ls $QWEN_DIR 2>/dev/null | wc -l) 건"
echo "  Solar: $solar_count 건"
echo "[overnight] $(date '+%H:%M:%S') 종료"
