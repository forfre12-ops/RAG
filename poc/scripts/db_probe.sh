#!/usr/bin/env bash
# 배포 스크립트 공용 — 이 배포가 쓰는 DB 서비스와 헬시 프로브를 정한다.
#
# 왜(2026-09-05). 배포 스크립트가 `up -d postgres` 후 `pg_isready` 만 기다렸다.
# 앱이 MariaDB 를 보게 되면 **엉뚱한 DB 를 확인하고 성공을 보고한다** — 오류가 아니라
# 조용한 오판이라 더 나쁘다. DATABASE_URL 에서 엔진을 읽어 서비스명과 프로브를 맞춘다.
#
# 사용:
#   . "$(dirname "$0")/db_probe.sh"
#   svc=$(db_service "$DATABASE_URL")            # postgres | mariadb
#   db_wait 60 "$svc" "$user" "$db" dc_air       # dc 실행 함수를 마지막 인자로
#
# 판정은 DATABASE_URL 하나만 본다 — 배포 스크립트는 .env 를 이미 읽고 있고, 그것이
# 앱이 실제로 붙는 곳이다. 못 읽으면 postgres 로 본다(현행 배포가 전부 PostgreSQL).

db_service() {
  case "$1" in
    mariadb*|mysql*) echo "mariadb" ;;
    *)               echo "postgres" ;;
  esac
}

# db_wait <초> <서비스> <유저> <DB> <dc함수명> [비밀번호]
# 서비스가 접속을 받을 때까지 기다린다. 성공 0 / 시간초과 1.
db_wait() {
  local secs="$1" svc="$2" user="$3" db="$4" dcfn="$5" pw="${6:-}" i
  for i in $(seq 1 "$secs"); do
    if [ "$svc" = "mariadb" ]; then
      # 비밀번호는 argv 가 아니라 환경변수로 — ps 로 읽히면 안 된다.
      if MYSQL_PWD="$pw" "$dcfn" exec -T -e MYSQL_PWD mariadb \
           mariadb-admin ping -u "$user" --silent >/dev/null 2>&1; then
        echo "$svc ready (${i}s)"; return 0
      fi
    else
      if "$dcfn" exec -T postgres pg_isready -U "$user" -d "$db" >/dev/null 2>&1; then
        echo "$svc ready (${i}s)"; return 0
      fi
    fi
    sleep 1
  done
  return 1
}
