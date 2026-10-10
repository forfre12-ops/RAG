set -uo pipefail
fail=0; warn=0
ok()   { printf 'OK:%s\n' "$*"; }
bad()  { printf 'FAIL:%s\n' "$*"; }
API_PORT=38123
ss() { printf 'LISTEN 0 4096 127.0.0.1:38123 0.0.0.0:*\n'; }
docker() { printf '%s' '127.0.0.1:38123->8000/tcp\n'; }
if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${API_PORT} "; then
  # 그 포트를 이미 설치된 우리 스택(compose 프로젝트 koipa-airgap)이 쓰고 있으면 재실행이다 — 막지 않는다.
  # 설치 완료 안내가 토큰 갱신·자동 로그인 전환을 "setup.sh 를 다시 실행하라"고 안내하는데, 여기서 막으면
  # 스택이 떠 있는 상태에서는 0단계에서 중단된다(2026-09-29 Rocky 8.10 리허설). 다른 프로세스면 그대로 막는다.
  if command -v docker >/dev/null 2>&1 \
     && docker ps --filter "label=com.docker.compose.project=koipa-airgap" --format '{{.Ports}}' 2>/dev/null \
        | grep -q ":${API_PORT}->"; then
    ok "포트 ${API_PORT} 은 이미 설치된 Koipa 스택이 쓰고 있다 — 재실행으로 본다"
  else
    bad "포트 ${API_PORT} 이 이미 사용 중이다 — .env 의 API_PORT 를 바꾸거나 기존 프로세스를 정리한다"
  fi
else
  ok "포트 ${API_PORT} 사용 가능"
fi


echo "RESULT fail=$fail"
