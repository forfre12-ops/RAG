#!/usr/bin/env bash
# 구형 .hwp 실경로 검증 — 배포된 서버가 표를 회수하고 검수로 보내는가.
#
# 2026-08-02 기대값 확정 — 로컬·서버 양쪽에서 동일하게 재현했다:
#   본문 46,473자 · 표 47개 · 셀 1,815개 · processing_status=needs_review
#
# 실패 시 원인 두 가지를 구분할 것:
#   ① 표 미회수(본문 10,439자·표 0개) — rhwp-python 이 낡아 to_hwpx_bytes 가 없으면
#      검출기가 실패한다. 0.8.1 이상인지 먼저 확인(0.5.1 에서 실측 재현됨).
#      unhwp 설치 여부만 봐서는 안 된다 — unhwp 가 있어도 검출기가 죽으면 호출되지 않는다.
#   ② 타임아웃 — 표 47개 회수는 CPU 시간을 쓴다. 워커 타임아웃·입력 크기 상한을 볼 것.
#      이쪽이 원래 보고된 증상(gunicorn 워커 강제 재시작)에 해당한다.
#
# 사용:
#   BASE_URL=http://127.0.0.1:8000 API_KEY=<키> bash scripts/verify_hwp_path_remote.sh
#   (SSH 터널: ssh -p 56320 -L 8000:127.0.0.1:8000 -L 8001:127.0.0.1:8001 aisadm@<host>)
set -uo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
API_KEY="${API_KEY:-}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
DOC="${DOC:-$HERE/datasets/acceptance_pack/docs/real-S3-iprd-notice.hwp}"

# 로컬 실측 기대값(커밋 77f6c15 기준)
EXP_TABLES=47
EXP_CELLS=1815
MIN_CHARS=40000                       # 보강 성공 시 46,473자 · 미보강이면 10,439자
TOKENS=("1,000" "15,000" "2200" "2500" "3287")   # 전부 표 안에만 있는 숫자

command -v curl >/dev/null 2>&1 || { echo "FAIL: curl 필요"; exit 2; }
[ -f "$DOC" ] || { echo "FAIL: 검증 문서 없음 — $DOC"; exit 2; }

echo "=== 구형 .hwp 실경로 검증 ==="
echo "대상: $BASE_URL"
echo "문서: $(basename "$DOC")"
echo

AUTH=()
[ -n "$API_KEY" ] && AUTH=(-H "X-API-Key: $API_KEY")

# 1) 의존성 가시성 — health 가 파서 의존성을 어떻게 보고하는가
echo "[1] 파서 의존성 진단"
curl -s "${AUTH[@]}" "$BASE_URL/api/v1/healthz/deep" 2>/dev/null \
  | tr ',' '\n' | grep -iE "hwp|unhwp|rhwp" | head -6 || echo "  (진단 엔드포인트 응답 없음)"
echo

# 2) 실제 업로드 — 추출·게이트를 한 번에 본다
echo "[2] 업로드 → 추출·게이트"
T0=$(date +%s)
RESP=$(curl -s --max-time 300 "${AUTH[@]}" -F "file=@$DOC" "$BASE_URL/api/v1/documents/analyze" 2>&1)
T1=$(date +%s)
ELAPSED=$((T1-T0))

if [ -z "$RESP" ]; then
  echo "FAIL: 응답 없음(${ELAPSED}s) — 워커 타임아웃 가능성. gunicorn timeout·입력 상한 확인."
  exit 1
fi

py() { python3 -c "$1" 2>/dev/null || python -c "$1" 2>/dev/null; }
GET() { py "
import json,sys
d=json.loads('''$RESP''')
def dig(o,*ks):
    for k in ks:
        if isinstance(o,dict) and k in o: o=o[k]
        else: return ''
    return o
print(dig(d,*'$1'.split('.')))
"; }

CHARS=$(py "
import json,re
d=json.loads('''$RESP''')
t=json.dumps(d,ensure_ascii=False)
m=re.search(r'\"(?:text|content|extracted_text)\"\s*:\s*\"(.*?)(?<!\\\\)\"',t,re.S)
print(len(m.group(1)) if m else 0)")
STATUS=$(GET "processing_status")
[ -z "$STATUS" ] && STATUS=$(GET "status")

echo "  소요:        ${ELAPSED}s"
echo "  본문 길이:   ${CHARS:-?}자 (기대 ≥ $MIN_CHARS · 미보강이면 10,439)"
echo "  처리 상태:   ${STATUS:-?} (기대 needs_review)"
echo

# 3) 숫자 무손실 — 표 안에만 있는 토큰이 도달했는가(가장 결정적)
echo "[3] 표 내부 숫자 도달 여부"
MISS=0
for t in "${TOKENS[@]}"; do
  if printf '%s' "$RESP" | grep -qF "$t"; then
    echo "  OK    $t"
  else
    echo "  MISS  $t   ← 표가 회수되지 않았다"
    MISS=$((MISS+1))
  fi
done
echo

# 4) 판정
echo "=== 판정 ==="
FAIL=0
[ "${CHARS:-0}" -ge "$MIN_CHARS" ] || { echo "FAIL: 본문이 짧다 — 표 미회수(무음 유실)"; FAIL=1; }
[ "$MISS" -eq 0 ] || { echo "FAIL: 표 내부 숫자 ${MISS}종 유실"; FAIL=1; }
case "$STATUS" in
  needs_review) echo "OK:   검수 라우팅됨(FNR-safe 정책대로)";;
  "")           echo "WARN: 상태 필드를 못 읽음 — 응답 스키마 확인";;
  *)            echo "FAIL: 자동확정됨($STATUS) — 표 유실이 확인된 문서는 검수로 가야 한다"; FAIL=1;;
esac
[ "$ELAPSED" -lt 120 ] || echo "WARN: ${ELAPSED}s 소요 — 워커 타임아웃 여유 확인(표 47개 회수는 CPU 시간을 쓴다)"

if [ "$FAIL" -eq 0 ]; then
  echo; echo "→ PASS — 구형 .hwp 경로 정상(표 ${EXP_TABLES}개·셀 ${EXP_CELLS}개 기준)"
  exit 0
fi
echo; echo "→ FAIL — 위 ①②를 순서대로 확인할 것:"
echo "     ① docker exec <api> python -c 'import importlib.metadata as m; print(m.version(\"rhwp-python\"))'  # 0.8.1 이상인가"
echo "     ② 워커 타임아웃·입력 크기 상한 (표 47개 회수는 CPU 시간을 쓴다)"
exit 1
