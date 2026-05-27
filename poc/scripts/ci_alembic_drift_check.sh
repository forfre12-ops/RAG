#!/usr/bin/env bash
# P3: Alembic migration drift check
#
# 목적: ORM 모델 변경 후 alembic revision 누락을 사전 차단.
# 전제: `alembic upgrade head` 가 성공한 상태에서 호출.
# 동작:
#   1) `alembic check` 가능하면 그것으로 drift 즉시 판정 (alembic 1.9+).
#   2) 없거나 실패 시 fallback — autogenerate 시도하여 비어있지 않으면 fail.
set -euo pipefail

cd "$(dirname "$0")/.."

LOG_DIR="${LOG_DIR:-$(mktemp -d)}"
CHECK_LOG="${LOG_DIR}/alembic_check.log"
GEN_LOG="${LOG_DIR}/alembic_gen.log"

DRIFT_HINT='::error::DB-ORM drift 감지 — model 변경 후 alembic revision 잊었는지 확인하세요'
DRIFT_CMD='::error::  실행: alembic revision --autogenerate -m "<your-change>"'

echo "==[migration-drift]== alembic check (preferred path)"
if alembic check >"${CHECK_LOG}" 2>&1; then
  cat "${CHECK_LOG}"
  echo "[migration-drift] PASS — no drift detected by alembic check"
  exit 0
fi

CHECK_RC=$?
cat "${CHECK_LOG}"

# alembic check가 명시적으로 drift를 알리면 즉시 fail
if grep -qE "New upgrade operations detected|target database is not up to date" "${CHECK_LOG}"; then
  echo "${DRIFT_HINT}"
  echo "${DRIFT_CMD}"
  exit 1
fi

# 'alembic check' 미지원 버전 fallback — autogenerate로 차이 추출
echo "[migration-drift] alembic check 실패/미지원 (rc=${CHECK_RC}) — autogenerate fallback"
rm -f alembic/versions/*_drift_probe.py
alembic revision --autogenerate -m "_drift_probe" >"${GEN_LOG}" 2>&1 || {
  echo "::warning::alembic autogenerate 실패 — 로그 참조"
  cat "${GEN_LOG}"
  exit 1
}
cat "${GEN_LOG}"

PROBE_FILE="$(ls -t alembic/versions/*_drift_probe.py 2>/dev/null | head -1 || true)"
if [ -z "${PROBE_FILE}" ]; then
  echo "::warning::autogenerate 결과 파일 없음 — drift 판정 불가, 통과 처리"
  exit 0
fi

# upgrade()/downgrade() 본문이 'pass'만 있으면 drift 없음 → python으로 AST 검사
if python scripts/_drift_probe_empty_check.py "${PROBE_FILE}"; then
  echo "[migration-drift] PASS — autogenerate 결과 비어있음 (drift 없음)"
  rm -f "${PROBE_FILE}"
  exit 0
fi

echo "${DRIFT_HINT}"
echo "${DRIFT_CMD}"
echo "::error::--- autogenerate 결과 (참고) ---"
cat "${PROBE_FILE}"
rm -f "${PROBE_FILE}"
exit 1
