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
# [2026-08-11] rc 포착 정정. 종전엔 `if alembic check; then ... fi` 뒤에서 CHECK_RC=$? 를 읽었는데,
# bash 는 조건이 거짓이고 else 가 없는 if 문의 종료상태를 **0** 으로 준다 → CHECK_RC 가 항상 0 이라
# 진단 로그가 "rc=0" 으로 찍혔다(실측). 조건 실행 직후에 잡는다.
CHECK_RC=0
alembic check >"${CHECK_LOG}" 2>&1 || CHECK_RC=$?
cat "${CHECK_LOG}"
if [ "${CHECK_RC}" -eq 0 ]; then
  echo "[migration-drift] PASS — no drift detected by alembic check"
  exit 0
fi

# alembic check가 명시적으로 drift를 알리면 즉시 fail
# [2026-08-11] -i 추가. alembic 실제 출력은 "Target database is not up to date."(대문자 T)인데
# 패턴은 소문자 target 이라 매칭되지 않았다 → 원인을 알면서도 autogenerate 폴백으로 흘러가
# "alembic check 실패/미지원" 이라는 **틀린 진단**을 냈다(실측). 종료코드는 어차피 1 이라
# 결과는 같았지만, CI 에서 이 메시지를 보고 고칠 곳을 잘못 찾게 된다.
# [2026-09-07] **두 사실을 가른다.** 종전에는 아래 두 문구를 같은 것으로 보고 둘 다 drift 로
# 단정했다. 그런데 계열이 둘이 된 뒤(MariaDB 도입) 한 DB 는 **자기 계열의 head 에만** 있을
# 수 있어 `alembic check` 가 늘 "Target database is not up to date." 를 낸다 — 정상 상태다.
# 그 결과 이 검사기가 **영구히 빨간불**이 됐고, 늘 우는 경보는 곧 무시된다.
#
#   New upgrade operations detected   ORM 이 DB 보다 앞선다 = 진짜 drift
#   target database is not up to date DB 가 head 가 아니다 = 미적용(또는 계열이 둘)
if grep -qiE "New upgrade operations detected" "${CHECK_LOG}"; then
  echo "${DRIFT_HINT}"
  echo "${DRIFT_CMD}"
  exit 1
fi

if grep -qiE "target database is not up to date" "${CHECK_LOG}"; then
  CUR="$(alembic current 2>/dev/null | grep -oE '^[0-9a-f]{12}' | head -1 || true)"
  if [ -n "${CUR}" ] && alembic heads 2>/dev/null | grep -q "^${CUR}"; then
    # 자기 계열 head 에 있다 - 계열이 둘이라 나는 소리다.
    #
    # ⚠ 이 DB 에서는 **drift 를 잴 수 없다.** autogenerate 도 같은 이유로 막히고
    #   (--head <계열>@head 를 줘도 alembic 은 모든 head 를 요구한다 - 실측 확인),
    #   한 DB 는 두 계열 head 에 동시에 있을 수 없다.
    #
    #   그래서 **틀린 진단 대신 못 쟀다고 말한다.** 늘 우는 경보는 무시되고, 무시되는
    #   경보는 없는 것만 못하다. 실제 drift 검사는 **계열이 하나인 DB**에서 돌려야 한다
    #   (CI 의 PostgreSQL 서비스가 그 자리다).
    echo "::warning::migration-drift 를 재지 못했다 — 계열이 둘(postgres·mariadb)이라"
    echo "::warning::  이 DB(현재 ${CUR}, 자기 계열 head)에서는 alembic 이 drift 를 계산하지 못한다."
    echo "::warning::  실제 검사는 계열 하나만 있는 DB 에서: DATABASE_URL=postgresql://... 로 이 스크립트를 돌릴 것."
    echo "[migration-drift] SKIP — 잰 축 없음(위 경고 참조)"
    exit 0
  else
    # head 가 아니다 = 마이그레이션 미적용. drift 와 다른 사실이므로 지시도 달라야 한다.
    echo "::error::마이그레이션 미적용 — DB 가 head 가 아니다(현재: ${CUR:-알수없음})"
    echo "::error::  실행: alembic upgrade postgres@head  또는  alembic upgrade mariadb@head"
    exit 1
  fi
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
