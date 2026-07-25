#!/usr/bin/env bash
# 무인 오케스트레이터: GPU가 비면 v5_clean 재학습 → 게이트 평가(신규 vs 배포본) → 비교 리포트.
# 안전: 새 아티팩트 디렉토리에만 기록. 배포본(v-dd3abab9) 불변. 자동 배포/스왑 절대 안 함(사람 결정).
# 실패해도 무해(후보 모델만 생성). 모든 단계 로그. 결과는 reports/v5_gate/ 에서 아침에 확인.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 3
mkdir -p reports/v5_gate
# 단일-인스턴스 락(mkdir 은 원자적). 이전 세션에서 이중 프로세스로 STATUS 덮어쓰기·중복 재학습이
# 발생했던 문제 차단. stale 락(24h+)은 자동 회수.
LOCK=reports/v5_gate/.orchestrator.lock
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +1440 2>/dev/null)" ]; then
    echo "[lock] stale 락 회수" >&2; rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || { echo "[lock] 획득 실패, 중단" >&2; exit 4; }
  else
    echo "[lock] 다른 오케스트레이터가 실행 중 — 중단(중복 방지)" >&2; exit 4
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
LOG=reports/v5_gate/overnight.log
MARK=reports/v5_gate/STATUS
BASE=artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9
HARD=datasets/gold_real/holdout_eval.hardened.jsonl
export PYTHONIOENCODING=utf-8
log(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }
mark(){ echo "$1" > "$MARK"; log "STATUS=$1"; }

mark "WAITING_FOR_GPU"
log "=== overnight v5 retrain armed ==="

# 1) GPU가 충분히 빌 때까지 폴링(연속 2회 free≥threshold). 최대 대기 180분.
THRESH_FREE_MIB=6000   # v5는 작아 6GB면 충분
MAX_WAIT_MIN=180
ok=0
for i in $(seq 1 $((MAX_WAIT_MIN)) ); do
  used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  total=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  if [ -n "$used" ] && [ -n "$total" ]; then
    free=$((total - used))
    log "poll #$i: used=${used} free=${free} MiB (need free>=${THRESH_FREE_MIB})"
    if [ "$free" -ge "$THRESH_FREE_MIB" ]; then
      ok=$((ok+1))
    else
      ok=0
    fi
    if [ "$ok" -ge 2 ]; then log "GPU free confirmed (2 consecutive)"; break; fi
  else
    log "poll #$i: nvidia-smi 조회 실패"
  fi
  sleep 60
done
if [ "$ok" -lt 2 ]; then mark "GPU_NEVER_FREED"; log "최대 대기 초과 — 중단(무해). 아침에 수동 실행 권장."; exit 0; fi

# 2) 재학습(vanilla, 입증된 커맨드). 새 디렉토리에만 기록.
mark "RETRAINING"
OUT=artifacts/classifier_p1_v5_clean
log "retrain 시작 → $OUT"
python scripts/p1_train_classifier.py --mode full \
  --train-path datasets/labeled_p1_v5_clean/train.jsonl \
  --val-path datasets/labeled_p1_v5_clean/val.jsonl \
  --test-path datasets/labeled_p1_v5_clean/test.jsonl \
  --epochs 5 --max-seq-len 512 --no-mlflow \
  --output-dir "$OUT" >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then mark "RETRAIN_FAILED"; log "재학습 실패 rc=$rc (배포본 불변·무해). 로그 확인."; exit 0; fi

# 새로 만들어진 모델 버전 디렉토리(최신) 탐색
MODEL=$(ls -dt "$OUT"/v-* 2>/dev/null | head -1)
if [ -z "$MODEL" ]; then mark "MODEL_DIR_NOT_FOUND"; log "모델 디렉토리 못 찾음"; exit 0; fi
log "학습 완료 → $MODEL"

# 3) 게이트 평가: 신규 후보 vs 배포본 (동일 셋)
mark "EVALUATING"
run_eval(){ log "eval: $*"; "$@" >> "$LOG" 2>&1 || log "  (eval 실패, 계속)"; }
run_eval python scripts/eval_p1_holdout.py --model-dir "$MODEL" --holdout "$HARD" --report reports/v5_gate/holdout_new.json
run_eval python scripts/eval_p1_holdout.py --model-dir "$BASE"  --holdout "$HARD" --report reports/v5_gate/holdout_base.json
run_eval python scripts/eval_real_public_fpr.py --model-dir "$MODEL" --report reports/v5_gate/fpr_new
run_eval python scripts/eval_real_public_fpr.py --model-dir "$BASE"  --report reports/v5_gate/fpr_base

# 4) 비교 요약 + adversarial veto(고→S3=0) 체크
python - "$MODEL" "$BASE" <<'PY' >> "$LOG" 2>&1
import json, sys
from pathlib import Path
def load(p):
    p=Path(p)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
hn,hb=load('reports/v5_gate/holdout_new.json'),load('reports/v5_gate/holdout_base.json')
def allblk(d): return {k:(d.get('ALL',{}) or {}).get(k) for k in ('f1_macro','fnr_underclass','high_risk_to_s3','per_class_recall','n')}
new,base=allblk(hn),allblk(hb)
def verdict():
    if not hn or new['high_risk_to_s3'] is None: return "INCOMPLETE(신규 eval 없음)"
    safe = new['high_risk_to_s3'] <= (base['high_risk_to_s3'] or 0)      # 고→S3 위험미탐 증가 금지
    noreg = (new['f1_macro'] or 0) >= (base['f1_macro'] or 0) - 0.02     # f1 회귀 ~없음
    return ("PASS(사람확인용)" if (safe and noreg) else "REVIEW(안전 or 회귀 플래그)")
summary={
 "candidate_model": sys.argv[1],
 "baseline_model": sys.argv[2],
 "holdout_hardened": {"new": new, "base": base},
 "auto_verdict_hint": verdict(),
 "note": "자동 배포 절대 없음. 사람이 판정: (1) high_risk_to_s3(고→S3 위험미탐) base 대비 증가 금지 (2) 공개문서 FPR(reports/v5_gate/fpr_*)이 개선/유지 (3) f1_macro 회귀 없는가. 3개 통과 시에만 배포 후보. auto_verdict_hint는 (1)(3)만 본 참고치.",
}
Path('reports/v5_gate/COMPARISON.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
PY

mark "DONE"
log "=== 완료. reports/v5_gate/COMPARISON.json + fpr_*/holdout_* 확인. 배포는 사람 결정. ==="
exit 0
