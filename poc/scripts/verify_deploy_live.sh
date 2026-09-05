#!/usr/bin/env bash
# 재배포 검증 — 배포한 판이 라이브에 실제로 반영됐는지 확인한다.
#   사용: bash scripts/verify_deploy_live.sh 8000 .env.jjw 지재원
#         bash scripts/verify_deploy_live.sh 8001 .env.cust 고객사
#   poc 디렉터리에서 실행할 것. 검사할 문서를 직접 주려면 DOCPATH=<파일>.
set -u
PORT=${1:-8000}; ENVF=${2:-.env.jjw}; NAME=${3:-지재원}
B="http://127.0.0.1:$PORT/api/v1"
PGC="${PGC:-koipa-jjw-postgres-1}"   # 뒷정리 안내문에 쓸 DB 컨테이너 이름
K=$(grep '^API_KEY=' "$ENVF" | cut -d= -f2)

# 업로드 검사에 쓸 문서.
# ⚠ demo_formats/ 는 **배포 tar 에 안 들어간다.** 서버에서 돌리면 없다(실측 2026-08-29, 223).
#   그때 스크립트가 "업로드 실패"만 찍어서 진짜 고장과 구분이 안 됐다.
#   static/demo_docs 는 이미지에 함께 나가므로 예비 경로로 쓴다.
DOC="${DOCPATH:-}"
if [ -z "$DOC" ]; then
  for cand in "demo_formats/내부 시스템 구성·장애 대응 분기 보고.docx" \
              "src/koipa/api/static/demo_docs/01_TS_semiconductor_euv.docx"; do
    [ -f "$cand" ] && { DOC="$cand"; break; }
  done
fi

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
# 파일이 없으면 **고장이 아니라 건너뜀**이다. 둘을 구분해서 말한다.
up=""; did=""
if [ -z "$DOC" ] || [ ! -f "$DOC" ]; then
  printf '2b) doc_id 경로             : 건너뜀 — 검사용 문서 없음 (DOCPATH=<파일> 로 지정)\n'
else
  up=$(curl -s -X POST "$B/documents" -H "X-API-Key: $K" -H "X-Actor-Role: admin" \
       -F 'actor={"user_id":"deploy-verify","role":"admin"}' -F "file=@$DOC")
  did=$(printf '%s' "$up" | grep -oE '"doc_id" *: *"[^"]*"' | head -1 | sed -E 's/.*"([^"]*)"$/\1/')
fi
if [ -n "$up" ] && [ -z "$did" ]; then
  printf '2b) doc_id 경로             : 업로드 실패 → %s\n' "$(printf '%s' "$up" | head -c 150)"
elif [ -n "$did" ]; then
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
  # 이 검사는 문서 1건을 **실제로 적재한다**. 시연 서버라면 지워 두는 편이 낫다.
  # 자동으로 지우지 않는다 - created_by 범위로 지우면 남의 시연 데이터까지 지운다.
  echo "    남긴 검사 문서 지우기(시연 서버라면):"
  echo "      docker exec -i $PGC $DB_CLIENT \"delete from tb_classification_evidence where classification_id in (select classification_id from tb_classifications where doc_id='$did'); delete from tb_classifications where doc_id='$did'; delete from tb_chunks where doc_id='$did'; delete from tb_documents where doc_id='$did';\""
fi

# 3) 관리자 콘솔 화면이 이 판에 들어 있나
# ⚠ 화면 이름이 바뀌면 여기도 바꿔야 한다. 검사기가 낡으면 **멀쩡한 화면을 고장으로 읽는다** —
#   실측 2026-08-29: "합성 샘플 생성 · 검수" 로 찾고 있었는데 화면은 그때 이미
#   "학습 후보 생성 · 검토 → 학습셋 편입" 으로 바뀌어 있었다.
html=$(curl -s "http://127.0.0.1:$PORT/demo/admin.html")
for kw in "등급체계 관리 — 추가" "라벨링 · 태깅 규칙 관리" "학습 후보 생성 · 검토" "정밀도 Precision" "golden_jobs.js"; do
  [ "$(printf '%s' "$html" | grep -c "$kw")" -gt 0 ] && s="있음" || s="없음"
  printf '3) 콘솔 [%-26s]: %s\n' "$kw" "$s"
done

# 4) 골든 잡 목록 (f079b24)
printf '4) GET /golden/jobs         : HTTP %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' -H "X-API-Key: $K" -H "X-Actor-Role: admin" "$B/golden/jobs?limit=3")"
echo
