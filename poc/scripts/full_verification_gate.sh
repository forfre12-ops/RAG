#!/usr/bin/env bash
# 커밋/푸시 전 전수 검증 게이트 — 2026-10-03 사용자 지시로 추가.
#
# 왜. 오늘 하루 동안 여러 세션이 같은 체크아웃에 동시에 커밋하면서: 배포 번들이
# 구버전 코드를 담은 채 출하 대기 상태였고(S/V/M 역산 수정 누락), 검수 배치 설정이
# 하루 안에 두 번 바뀌었고, 관리자 콘솔이 2~3개월 된 폐기 실험본을 "지금 고를 수
# 있는 것"처럼 보여주고 있었다. 전부 "커밋은 됐는데 실제로 돌아가는지, 최신 상태를
# 반영하는지"를 아무도 끝까지 확인 안 해서 생긴 문제다. 이 스크립트는 그 확인을
# 사람이 기억해서 손으로 하는 대신 강제한다.
#
# [2026-10-03] Makefile 의 동일 타겟을 그대로 쓰지 않는다 — 이 Windows/Git-Bash
# 환경엔 `make` 자체가 없고(winget 설치도 실패), Makefile의 ACTIVATE 는 Linux venv
# 레이아웃(.venv/bin/activate)을 전제해 이 저장소의 Windows venv(.venv/Scripts/)와
# 안 맞는다. 그래서 각 타겟의 **실제 커맨드**를 Makefile에서 그대로 옮겨와 Windows
# venv 파이썬으로 직접 부른다 — 타겟 내용이 바뀌면 이 스크립트도 같이 고칠 것.
#
# 무엇을 확인하는가(순서대로, 하나라도 실패하면 즉시 중단):
#   0. uv.lock·OpenAPI 정합성 — 의존성/계약 드리프트
#   1. 임시 PostgreSQL 기동 + 전체 pytest(fullstack 포함) — DB 없이 돌리면
#      fullstack ~100여 건이 조용히 skip된다(실측 2026-08-29, Makefile 주석) — 이 게이트는
#      그 침묵을 허용하지 않는다.
#   2. 데이터 위생 게이트(누출·특허프록시 과라벨·gold 3층 분리)
#   3. 콘솔 e2e 시나리오(jsdom)
#   4. api·worker 도커 이미지 실제 빌드 + 이미지 안에서 앱 모듈 import 스모크
#      (기존에 이 저장소 어디에도 없던 검사 — 이미지가 빌드는 되는데 import가 깨진
#      상태로 배포되는 사고를 여기서 잡는다)
#   5. 인수 샘플팩 채점(parse→classify→gate in-proc)
#   6. release-gate-pilot(readiness + adversarial + metamorphic, 데이터천장 BLOCKED만 감사waiver)
#
# 무엇을 확인하지 않는가(의도적 제외 — 이유 포함):
#   - GPU 테스트(`-m gpu`)·model_download — 이 PC/CI 환경에 전제조건이 없다.
#   - 실제 배포(deploy.sh --deploy) — 이 게이트는 "배포해도 안전한가"를 검증하지
#     "배포 자체"는 하지 않는다. 배포는 여전히 사람이 명시적으로 지시할 때만.
#
# 실행 시간: 전부 콜드로 돌면 10~20분 걸릴 수 있다(도커 이미지 캐시가 없을 때 특히).
# 그래도 끝까지 돈다 — 빠르게 하려고 단계를 빼면 바로 오늘 겪은 문제로 되돌아간다.
#
# 사용: bash scripts/full_verification_gate.sh   (poc/ 에서 실행, 또는 .pre-commit-config.yaml 가 호출)

set -u
cd "$(dirname "${BASH_SOURCE[0]}")/.."   # poc/ 로 이동(스크립트 위치 기준, cwd 무관하게 동작)

PY=".venv/Scripts/python.exe"
TEST_DB_URL="postgresql+psycopg://koipa:koipa_dev@localhost:15432/koipa"
P1_MODEL="artifacts/classifier_p1_v5_clean/v-fe4b386b"
DEPLOYED_MODEL="${CLASSIFIER_MODEL_DIR:-$P1_MODEL}"

STAGE=""
FAILED=0
_start=$(date +%s)

fail() {
    echo ""
    echo "============================================================"
    echo "[전수검증 실패] 단계: $STAGE"
    echo "============================================================"
    FAILED=1
}

cleanup() {
    # 성공/실패/중단 어느 경우에도 임시 자원은 반드시 정리한다.
    echo "" ; echo "== 정리 — 임시 DB·게이트용 도커 이미지 =="
    docker rm -f koipa-testdb >/dev/null 2>&1 || true
    docker rmi -f koipa-gate-api:check koipa-gate-worker:check >/dev/null 2>&1 || true
    local _elapsed=$(( $(date +%s) - _start ))
    if [ "$FAILED" -eq 0 ]; then
        echo "[전수검증 완료] 전부 통과 — 경과 ${_elapsed}초. 커밋/푸시해도 좋다."
    else
        echo "[전수검증 중단] '$STAGE' 단계 실패 — 경과 ${_elapsed}초. 고치기 전엔 커밋/푸시하지 말 것."
    fi
    exit "$FAILED"
}
trap cleanup EXIT

run() {  # run "표시용 단계이름" cmd...
    STAGE="$1"; shift
    echo "" ; echo "==[ $STAGE ]=="
    if ! "$@"; then
        fail
        exit 1   # trap cleanup 이 처리
    fi
}

echo "전수 검증 게이트 시작 — $(date)"

run "uv.lock 정합성" "$PY" -m uv lock --locked
run "OpenAPI 계약 정합성" "$PY" scripts/openapi_consistency.py --strict

run "임시 PostgreSQL 기동" bash -c '
    docker rm -f koipa-testdb >/dev/null 2>&1 || true
    docker run -d --rm --name koipa-testdb -p 15432:5432 \
        -e POSTGRES_USER=koipa -e POSTGRES_PASSWORD=koipa_dev -e POSTGRES_DB=koipa \
        pgvector/pgvector:pg16 >/dev/null
    echo "  기동 대기..." && sleep 8
    DATABASE_URL="'"$TEST_DB_URL"'" "'"$PY"'" -m alembic upgrade head
'
run "전체 pytest(fullstack 포함, DB 연결)" bash -c \
    "TESTING=1 DATABASE_URL='$TEST_DB_URL' '$PY' -m pytest -q -m 'not gpu and not model_download'"

run "데이터 누출 게이트" "$PY" scripts/check_data_quality.py \
    --train-hash-manifest datasets/gold_real/train_subset.hashes.txt \
    --train-pool datasets/gold_real/train_subset.jsonl \
    --holdout datasets/gold_real/holdout_eval.clean.jsonl,datasets/gold_real/holdout_business.clean.jsonl \
    --strict-coverage \
    --report reports/leakage_report.json

if [ -f datasets/labeled_p1_v5_clean/train.jsonl ]; then
    run "공개특허 과라벨 가드(train)" "$PY" scripts/check_no_patent_proxy.py --path datasets/labeled_p1_v5_clean/train.jsonl
    run "공개특허 과라벨 가드(val)" "$PY" scripts/check_no_patent_proxy.py --path datasets/labeled_p1_v5_clean/val.jsonl
    run "공개특허 과라벨 가드(test)" "$PY" scripts/check_no_patent_proxy.py --path datasets/labeled_p1_v5_clean/test.jsonl
else
    echo "" ; echo "==[ 공개특허 과라벨 가드 ]== 건너뜀 — datasets/labeled_p1_v5_clean/ 없음"
fi

if [ -f datasets/gold_real/classification_gold.jsonl ]; then
    run "gold_real 3층 분리 검사" "$PY" scripts/check_data_quality.py \
        --train datasets/labeled_v2_balanced/train.jsonl \
        --gold  datasets/gold_real/classification_gold.jsonl \
        --gold-train-overlap-policy warn \
        --report reports/gold_real_quality_report.json
else
    echo "" ; echo "==[ gold_real 3층 분리 검사 ]== 건너뜀 — datasets/gold_real/classification_gold.jsonl 없음"
fi

run "콘솔 e2e 시나리오" "$PY" -m pytest -q tests/test_e2e_console_scenarios.py tests/test_e2e_console_api_contract.py

_sha=$(git rev-parse HEAD 2>/dev/null || echo unknown)
_now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
run "api 이미지 빌드" docker build -f Dockerfile.api.prod \
    --build-arg KOIPA_BUILD_SHA="$_sha" --build-arg KOIPA_BUILD_AT="$_now" \
    -t koipa-gate-api:check .
run "api 이미지 import 스모크" docker run --rm koipa-gate-api:check \
    python -c "import koipa.api.app; print('koipa.api.app import OK')"
run "worker 이미지 빌드" docker build -f Dockerfile.worker \
    --build-arg KOIPA_BUILD_SHA="$_sha" --build-arg KOIPA_BUILD_AT="$_now" \
    -t koipa-gate-worker:check .
run "worker 이미지 import 스모크" docker run --rm koipa-gate-worker:check \
    python -c "import koipa.workers.tasks; print('koipa.workers.tasks import OK')"

run "인수 샘플팩 채점" env CLASSIFIER_MODEL_DIR="$DEPLOYED_MODEL" "$PY" scripts/run_acceptance.py --mode inproc --require-model

run "readiness 리포트" "$PY" scripts/build_operational_readiness.py \
    --model-dir "$P1_MODEL" --deployed-model "$DEPLOYED_MODEL" --out reports/operational_readiness.md
run "적대 회귀 게이트" "$PY" scripts/eval_adversarial.py --model-dir "$DEPLOYED_MODEL"
run "메타모픽 재분류" "$PY" scripts/gen_metamorphic_pairs.py \
    --from-samples datasets/metamorphic/anchor_pairs.fixture.jsonl \
    --model-dir "$DEPLOYED_MODEL" \
    --report reports/metamorphic_pairs.md
run "메타모픽 게이트" "$PY" scripts/check_metamorphic_gate.py --report reports/metamorphic_pairs.json
run "release-gate 최종판정(파일럿, 데이터천장만 waiver)" "$PY" scripts/check_release_gate.py \
    --readiness reports/operational_readiness.json --require-fresh --allow-conditional

echo "" ; echo "모든 단계 통과."
