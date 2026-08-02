#!/usr/bin/env bash
# .hwp 표 셀 회수가 **돌고 있는 배포 이미지**에 실제로 반영됐는지 검증한다.
#
# 왜 필요한가:
#   rhwp 는 .hwp 바이너리의 표 셀을 통째로 흘린다. 표에 등급·원가·담당이 들어있으면
#   본문만 분류기에 들어가 '조용한 미탐'(FNR)이 된다. unhwp(MIT)가 그 셀을 회수하는데,
#   pyproject·Dockerfile 에 적혀 있다는 것은 **돌고 있는 이미지에 있다는 뜻이 아니다**
#   (소스 존재 ≠ 배포본 존재 — 과거 OCR·PyMuPDF 과대주장이 이 착각에서 나왔다).
#   그래서 컨테이너 안에서 직접 측정한다.
#
#   또 하나: API 경로(api)와 배치 경로(worker)는 이미지가 갈릴 수 있다. 한쪽만 회수하면
#   같은 문서가 업로드/배치에서 다른 등급을 받는다. 그래서 컨테이너별로 전부 돌린다.
#
# 사용:
#   bash scripts/verify_hwp_tables_live.sh <표가있는.hwp> [포트] [envfile]
#   CONTAINERS="a b c" bash scripts/verify_hwp_tables_live.sh ~/hwp_test.hwp 8000 .env.jjw
#
#   ⚠ 넘기는 .hwp 는 반드시 **표가 있는 구형 .hwp(바이너리)** 여야 한다. .hwpx 는 rhwp 가
#     이미 정확하고 unhwp 보강 대상이 아니라 이 시험의 의미가 없다.
#
# poc 디렉터리에서 실행할 것. 종료코드 0=통과 / 1=실패.
set -u

HWP=${1:?"사용: verify_hwp_tables_live.sh <표가있는.hwp> [포트] [envfile]"}
PORT=${2:-8000}
ENVF=${3:-.env.jjw}
CONTAINERS=${CONTAINERS:-"lloydk-jjw-api-1 lloydk-jjw-worker-1"}
B="http://127.0.0.1:$PORT/api/v1"
FAIL=0

[ -f "$HWP" ] || { echo "⛔ 파일 없음: $HWP"; exit 1; }
[ -f "$ENVF" ] || { echo "⛔ env 파일 없음: $ENVF"; exit 1; }
K=$(grep '^API_KEY=' "$ENVF" | cut -d= -f2)

echo "══════ .hwp 표 셀 회수 배포본 검증 ══════"
echo "파일 : $HWP ($(stat -c%s "$HWP") bytes, md5 $(md5sum "$HWP" | cut -d' ' -f1))"
echo "대상 : $CONTAINERS  ·  API :$PORT ($ENVF)"
echo

# ── 컨테이너 안에서 돌릴 프로브 ───────────────────────────────────────────────
PROBE=$(mktemp /tmp/hwp_probe.XXXXXX.py)
cat > "$PROBE" <<'PYEOF'
import json
from pathlib import Path
from lloydk.modules.m2_preprocess.extractor import extract, _hwp_tables_via_unhwp

p = Path("/tmp/_hwp_verify.hwp")
o = {}

# A) rhwp 단독 = 보강 없는 baseline. .hwp 는 여기서 표가 통째로 빠진다.
import rhwp
base = rhwp.parse(str(p)).extract_text()
o["rhwp_only_chars"] = len(base)

# B) unhwp 표 회수 단독.
rec, tabs = _hwp_tables_via_unhwp(p)
tabs = tabs or []
o["unhwp_chars"] = len(rec or "")
o["tables"] = len(tabs)
o["rows"] = sum(len(t.rows) for t in tabs)
o["cells"] = sum(len(r) for t in tabs for r in t.rows)

# C) 배포 경로 extract() = 파이프라인이 실제로 타는 길.
r = extract(p)
o["final_chars"] = len(r.text)
o["gain"] = len(r.text) - len(base)
o["method"] = r.method
o["table_coverage"] = r.table_coverage
o["warnings"] = list(r.warnings or [])

# D) 회수된 셀이 분류기 입력에 실제로 들어갔는지 표본 대조.
#    in_text 전부 True + in_rhwp_only 전부 False 여야 '회수가 일어났다'가 성립한다.
seen, sample = set(), []
for t in r.tables:
    for row in t.rows:
        for c in row:
            c = (c or "").strip()
            if len(c) >= 4 and c not in seen:
                seen.add(c)
                sample.append({"in_text": c in r.text, "in_rhwp_only": c in base})
        if len(sample) >= 10:
            break
    if len(sample) >= 10:
        break
o["sample_n"] = len(sample)
o["all_cells_in_text"] = all(s["in_text"] for s in sample) if sample else False
o["none_in_rhwp_only"] = not any(s["in_rhwp_only"] for s in sample) if sample else False
print(json.dumps(o, ensure_ascii=False))
PYEOF

# ── 1) 컨테이너별 설치·추출 실측 ──────────────────────────────────────────────
for c in $CONTAINERS; do
  echo "── $c ──"
  if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
    echo "  ⛔ 컨테이너 없음"; FAIL=1; continue
  fi

  ver=$(docker exec "$c" python -c "
import importlib.metadata as m
def v(p):
    try: return m.version(p)
    except Exception: return 'MISSING'
ok=[]
for mod in ('rhwp','unhwp'):
    try:
        __import__(mod); ok.append(mod+':import OK')
    except Exception as e: ok.append(mod+':import FAIL '+type(e).__name__)
print('unhwp=%s rhwp-python=%s | %s' % (v('unhwp'), v('rhwp-python'), ' · '.join(ok)))
" 2>&1 | tail -1)
  echo "  $ver"
  case "$ver" in *MISSING*|*FAIL*) echo "  ⛔ 파서 미설치/임포트 실패"; FAIL=1; continue ;; esac

  docker cp "$HWP"  "$c":/tmp/_hwp_verify.hwp >/dev/null
  docker cp "$PROBE" "$c":/tmp/_hwp_probe.py  >/dev/null
  j=$(docker exec "$c" python /tmp/_hwp_probe.py 2>&1 | tail -1)
  docker exec "$c" rm -f /tmp/_hwp_verify.hwp /tmp/_hwp_probe.py >/dev/null 2>&1

  echo "$j" | python3 -c "
import sys, json
try: o = json.loads(sys.stdin.read())
except Exception:
    print('  ⛔ 프로브 실패'); sys.exit(1)
print('  rhwp단독 %d자 · 표0  →  unhwp 표%d·행%d·셀%d  →  최종 %d자 (+%d)'
      % (o['rhwp_only_chars'], o['tables'], o['rows'], o['cells'], o['final_chars'], o['gain']))
print('  coverage=%s  warnings=%s' % (o['table_coverage'], ','.join(o['warnings']) or '-'))
print('  표본셀 %d개: 최종본문 포함 %s · rhwp단독 부재 %s'
      % (o['sample_n'], o['all_cells_in_text'], o['none_in_rhwp_only']))
bad = []
if o['tables'] <= 0:                 bad.append('표 회수 0')
if o['cells'] <= 0:                  bad.append('셀 회수 0')
if o['gain'] <= 0:                   bad.append('보강 이득 없음')
if not o['all_cells_in_text']:       bad.append('회수 셀이 최종 본문에 없음')
if not o['none_in_rhwp_only']:       bad.append('표본이 rhwp단독에도 있음(표본 부적절)')
if 'hwp_tables_recovered_by_unhwp' not in o['warnings']: bad.append('회수 warning 누락')
print('  ' + ('✅ 통과' if not bad else '⛔ ' + ' / '.join(bad)))
sys.exit(1 if bad else 0)
" || FAIL=1
  echo
done
rm -f "$PROBE"

# ── 2) E2E: 적재 → 검수게이트 → 비동기 분류 ──────────────────────────────────
# 동기 /documents/analyze 는 청크 상한(LLOYDK_ANALYZE_SYNC_MAX_CHUNKS)에 걸려 거부되는 게
# 정상이다. 대용량 .hwp 의 정본 경로는 /documents + /classify/async 다.
echo "── E2E (:$PORT) ──"
up=$(curl -s -X POST "$B/documents" -H "X-API-Key: $K" -H "X-Actor-Role: admin" \
     -F 'actor={"user_id":"verify-hwp","role":"admin"}' -F "file=@$HWP")
eval "$(printf '%s' "$up" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for k in ('doc_id','char_count','chunk_count','requires_review'):
    print('%s=%r' % ('E_'+k, d.get(k)))
print('E_reasons=%r' % ','.join(d.get('review_reasons') or []))
" 2>/dev/null)" || { echo "  ⛔ 업로드 실패: $(printf '%s' "$up" | head -c 200)"; exit 1; }

echo "  적재: char_count=$E_char_count chunk=$E_chunk_count requires_review=$E_requires_review [$E_reasons]"
[ "$E_requires_review" = "True" ] || { echo "  ⛔ 표 불완전인데 검수 라우팅이 꺼졌다(FNR 위험)"; FAIL=1; }

jid=$(curl -s -X POST "$B/classify/async" -H "X-API-Key: $K" -H "X-Actor-Role: admin" \
      -H 'Content-Type: application/json' -d "{\"doc_id\":\"$E_doc_id\"}" \
      | python3 -c "import sys,json;print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
[ -n "$jid" ] || { echo "  ⛔ 분류 잡 생성 실패"; exit 1; }

for i in $(seq 1 60); do
  sleep 8
  st=$(curl -s -H "X-API-Key: $K" -H "X-Actor-Role: admin" "$B/classify/jobs/$jid")
  s=$(printf '%s' "$st" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status','?'))" 2>/dev/null)
  [ "$s" = "done" ] || [ "$s" = "failed" ] && break
done
echo "  분류: status=$s ($((i*8))s)"
printf '%s' "$st" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for r in d.get('results', []):
    print('  → label=%s conf=%.3f factors_source=%s' % (r['label'], r['confidence'], r.get('factors_source')))
" 2>/dev/null
[ "$s" = "done" ] || { echo "  ⛔ 분류 미완주"; FAIL=1; }

echo
[ "$FAIL" -eq 0 ] && echo "══════ ✅ 전체 통과 ══════" || echo "══════ ⛔ 실패 있음 ══════"
exit "$FAIL"
