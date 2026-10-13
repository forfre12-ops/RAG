#!/usr/bin/env bash
# 배포 스크립트 공용 — 이 배포가 쓰는 DB 서비스와 헬시 프로브를 정한다.
#
# 왜(2026-09-05). 배포 스크립트가 `up -d postgres` 후 `pg_isready` 만 기다렸다.
# 앱이 다른 DB 를 보게 되면 **엉뚱한 DB 를 확인하고 성공을 보고한다** — 오류가 아니라
# 조용한 오판이라 더 나쁘다. DATABASE_URL 에서 엔진을 읽어 서비스명과 프로브를 맞춘다.
#
# [2026-09-09] MariaDB 분기를 걷었다 — PostgreSQL + pgvector 로 되돌렸다(고객사 요청).
# 파일은 남긴다: 배포 스크립트 셋이 이 함수를 부르고 있고, "앱이 붙는 DB 와 기다리는 DB 가
# 어긋나면 조용히 오판한다"는 교훈은 엔진이 하나여도 그대로 유효하다. 엔진이 다시 늘면
# db_service 에 한 줄만 붙이면 된다.
#
# 사용:
#   . "$(dirname "$0")/db_probe.sh"
#   svc=$(db_service "$DATABASE_URL")            # postgres
#   db_wait 60 "$svc" "$user" "$db" dc_air       # dc 실행 함수를 마지막 인자로

db_service() {
  # 인자는 DATABASE_URL. 지금은 지원 엔진이 PostgreSQL 하나뿐이라 항상 postgres 다.
  case "$1" in
    *) echo "postgres" ;;
  esac
}

# db_wait <초> <서비스> <유저> <DB> <dc함수명> [비밀번호]
# 서비스가 접속을 받을 때까지 기다린다. 성공 0 / 시간초과 1.
# 비밀번호 인자는 호출부 계약 유지를 위해 받되 PostgreSQL 프로브에는 쓰이지 않는다.
db_wait() {
  local secs="$1" svc="$2" user="$3" db="$4" dcfn="$5" i
  for i in $(seq 1 "$secs"); do
    if "$dcfn" exec -T postgres pg_isready -U "$user" -d "$db" >/dev/null 2>&1; then
      echo "$svc ready (${i}s)"; return 0
    fi
    sleep 1
  done
  return 1
}
