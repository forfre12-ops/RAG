#!/usr/bin/env bash
# 재배포 검증 — 오늘 커밋 3건이 라이브에 실제로 반영됐는지 확인.
#   사용: bash verify_deploy.sh 8000 .env.jjw 지재원
#         bash verify_deploy.sh 8001 .env.cust 고객사
# poc 디렉터리에서 실행할 것.
set -u
PORT=${1:-8000}; ENVF=${2:-.env.jjw}; NAME=${3:-지재원}
B="http://127.0.0.1:$PORT/api/v1"
K=$(grep '^API_KEY=' "$ENVF" | cut -d= -f2)
DOC=${DOCPATH:-"demo_formats/내부 시스템 구성·장애 대응 분기 보고.docx"}

echo "══════ $NAME (:$PORT) ══════"

# 1) ready
printf '1) healthz/ready            : HTTP %s\n' "$(curl -s -o /dev/null -w '%{http_code}' "$B/healthz/ready")"

# 2) 저장소 read-back P0 (a6cde3a) — content 경로 vs doc_id 경로 대조.
#    본문을 못 읽으면 fail-secure 로 TS + confidence 0.0 이 된다(수정 전 증상).
BODY='당사 반도체 공정의 수율 파라미터와 장비 레시피 원본이다. 노광 조건과 식각 선택비는 미공개이며 공정팀장 외 열람 금지.'
# 한글 본문은 --data-binary + 파일로 넘긴다(-d 인라인은 셸/curl 인코딩에서 body 파싱 실패).
tmpj=$(mktemp); printf '{"doc_id":"verify-content-1","content":"%s"}' "$BODY" > "$tmpj"
cr=$(curl -s -X POST "$B/classify" -H "X-API-Key: $K" -H "X-Actor-Role: admin" \
     -H 'Content-Type: application/json; charset=utf-8' --data-binary "@$tmpj")
rm -f "$tmpj"
# 응답 JSON 은 공백이 섞일 수 있어 ' *' 를 허용한다.
lbl() { printf '%s' "$1" | grep -oE '"label" *: *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/'; }
cnf() { printf '%s' "$1" | grep -oE '"confidence" *: *[0-9.]+' | head -1 | grep -oE '[0-9.]+$'; }
printf '2a) content 경로            : label=%s conf=%s\n' "$(lbl "$cr")" "$(cnf "$cr")"

# POST /documents 는 multipart 라 actor 를 JSON 문자열 Form 필드로 받는다(코드 계약).
up=$(curl -s -X POST "$B/documents" -H "X-API-Key: $K" -H "X-Actor-Role: admin" \
     -F 'actor={"user_id":"verify","role":"admin"}' -F "file=@$DOC")
did=$(printf '%s' "$up" | grep -oE '"doc_id" *: *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')
if [ -z "$did" ]; then
  printf '2b) doc_id 경로             : 업로드 실패 → %s\n' "$(printf '%s' "$up" | head -c 150)"
else
  # 파싱·정규화가 끝나야 normalized_text_uri 가 생긴다 — 잠시 대기 후 분류.
  sleep 8
  dr=$(curl -s -X POST "$B/classify" -H "X-API-Key: $K" -H "X-Actor-Role: admin" \
       -H 'Content-Type: application/json' -d "{\"doc_id\":\"$did\"}")
  dl=$(lbl "$dr"); dc=$(cnf "$dr")
  printf '2b) doc_id 경로             : label=%s conf=%s (doc=%s)\n' "$dl" "$dc" "$did"
  case "$dl:$dc" in
    TS:0|TS:0.0) echo '    ⛔ read-back 실패 재현 — P0 미반영(이미지가 옛 버전)' ;;
    *)           echo '    ✅ read-back 정상 — 본문 기반 분류' ;;
  esac
fi

# 3) 관리자 콘솔 신규 화면 (7bf46a7)
html=$(curl -s "http://127.0.0.1:$PORT/demo/admin.html")
for kw in "등급체계 관리 — 추가" "라벨링 · 태깅 규칙 관리" "합성 샘플 생성 · 검수" "정밀도 Precision" "golden_jobs.js"; do
  [ "$(printf '%s' "$html" | grep -c "$kw")" -gt 0 ] && s="있음" || s="없음"
  printf '3) 콘솔 [%-26s]: %s\n' "$kw" "$s"
done

# 4) 골든 잡 목록 (f079b24)
printf '4) GET /golden/jobs         : HTTP %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $K" -H "X-Actor-Role: admin" "$B/golden/jobs?limit=3")"
echo
