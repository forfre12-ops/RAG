#!/usr/bin/env bash
# 골든 데이터 볼륨(golden_data)을 로컬에서 묶어 새 서버에 채운다.
#
# 왜 이 파일이 있는가(2026-09-22). 골든 콘솔이 읽는 후보·학습셋·정본 구성은 git 밖(.gitignore)이고
# 이미지에도 없다(.dockerignore). 새 서버는 볼륨이 비어 있어 콘솔 화면이 비거나 "행이 없다/404"가 난다.
# 2026-09-08 에는 이 절차가 명령 몇 줄로 대화 기록에만 있었다 — 서버를 없애도 다시 세울 수 있게 리포에 남긴다.
#
# 담는 것(기본, poc/datasets 기준 상대경로 — 컨테이너의 /app/datasets 와 같은 배치):
#   proxy_gold/single_document_candidates   골든 후보 목록·검수 원장  (없으면 후보 목록이 빈다)
#   labeled_p1_v5_clean                     학습셋                     (없으면 전략 격자 "행이 없다")
#   gold_real                               정본 구성·builds           (없으면 정본 구성 404)
# 더 담으려면 EXTRA_DIRS="golden_review other" 처럼 준다. 기본에 golden_review 를 안 넣은 이유:
# 검수 서명 결과가 들어 있어, 무엇을 새 서버로 넘길지는 사람이 정할 일이다.
#
# 사용:
#   (로컬, poc/ 에서)  bash scripts/golden_data_volume.sh pack  [출력.tar.gz]
#   (새 서버)          bash scripts/golden_data_volume.sh load  golden_data_20260922.tar.gz
#   환경변수: VOLUME=koipa-airgap_golden_data(기본) · FORCE=1(이미 후보가 있는 볼륨에 덮어쓴다)
#
# ⛔ load 는 볼륨에 후보(*.metadata.json)가 이미 있으면 멈춘다 — 그 볼륨의 검수 원장을 덮어쓰지 않기 위해서다.
set -euo pipefail

VOLUME="${VOLUME:-koipa-airgap_golden_data}"
DIRS="proxy_gold/single_document_candidates labeled_p1_v5_clean gold_real ${EXTRA_DIRS:-}"
CAND="proxy_gold/single_document_candidates"

mode="${1:-}"
case "$mode" in
  pack)
    out="${2:-golden_data_$(date +%Y%m%d).tar.gz}"
    [ -d datasets ] || { echo "⛔ poc/ 에서 실행해야 한다(datasets/ 없음)"; exit 1; }
    for d in $DIRS; do
      [ -d "datasets/$d" ] || { echo "⛔ datasets/$d 없음"; exit 1; }
    done
    # shellcheck disable=SC2046
    tar czf "$out" -C datasets $DIRS
    sha256sum "$out" > "$out.sha256"
    echo "== 묶음: $out ($(du -h "$out" | cut -f1))"
    echo "   후보 메타 $(ls datasets/$CAND/*.metadata.json | wc -l)건 · 원장 $(wc -l < datasets/$CAND/candidate_decisions.jsonl 2>/dev/null || echo 0)줄"
    cat "$out.sha256"
    ;;
  load)
    tar_path="${2:-}"
    [ -f "$tar_path" ] || { echo "사용: $0 load <tar.gz>"; exit 1; }
    # Git Bash(Windows)에서 시험할 때 경로가 바뀌지 않게 한다. 리눅스 서버에서는 무해하다.
    export MSYS_NO_PATHCONV=1
    host_dir="$(cd "$(dirname "$tar_path")" && (pwd -W 2>/dev/null || pwd))"
    name="$(basename "$tar_path")"
    if [ -f "$tar_path.sha256" ]; then
      (cd "$(dirname "$tar_path")" && sha256sum -c "$name.sha256") || { echo "⛔ 묶음 해시 불일치"; exit 1; }
    fi
    docker volume inspect "$VOLUME" >/dev/null 2>&1 || docker volume create "$VOLUME" >/dev/null
    have=$(docker run --rm -v "$VOLUME":/data alpine sh -c "ls /data/$CAND/*.metadata.json 2>/dev/null | wc -l")
    if [ "$have" != "0" ] && [ "${FORCE:-0}" != "1" ]; then
      echo "⛔ $VOLUME 에 후보가 이미 $have 건 있다 — 검수 원장을 덮어쓰지 않으려고 멈춘다(덮어쓰려면 FORCE=1)"
      exit 1
    fi
    # 컨테이너 프로세스가 uid 1000 이라 소유권을 맞춰야 쓸 수 있다(안 맞추면 서명 제출이 500).
    docker run --rm -v "$VOLUME":/data -v "$host_dir":/xfer:ro alpine \
      sh -c "tar xzf /xfer/$name -C /data && chown -R 1000:1000 /data"
    docker run --rm -v "$VOLUME":/data alpine sh -c "
      echo '== 적재 결과 ($VOLUME)'
      echo -n '   후보 메타 '; ls /data/$CAND/*.metadata.json | wc -l
      echo -n '   원장 줄수 '; wc -l < /data/$CAND/candidate_decisions.jsonl
      echo -n '   학습셋 파일 '; ls /data/labeled_p1_v5_clean | wc -l
      echo -n '   gold_real 항목 '; ls /data/gold_real | wc -l"
    ;;
  *)
    sed -n 2,22p "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
