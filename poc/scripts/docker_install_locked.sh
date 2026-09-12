#!/usr/bin/env bash
# uv.lock 기준 고정 설치 — Docker 이미지 3종(api·api.prod·worker) 공용.
#
#   ./scripts/docker_install_locked.sh psh otel jwt hwp hwp-tables pdf-tables xls
#
# 왜 필요한가. 종전에는 `pip install -e ".[extras]"` 로 pyproject 의 **범위**를 빌드 시점에
# 해석했다. 그러면 같은 커밋을 언제 빌드하느냐에 따라 torch·transformers 버전이 달라지고,
# 로컬과 KL 서버가 다른 의존성으로 돌아 결과 차이의 원인을 특정할 수 없다. 폐쇄망 반출
# 번들은 이미 uv.lock 해시핀으로 고정돼 있었는데(build_offline_bundle._export_locked_requirements)
# 정작 테스트서버가 쓰는 Docker 경로만 빠져 있었다.
#
# torch 를 따로 까는 이유. torch 는 변형(CPU/CUDA)별로 **다른 인덱스**에서 온다. lock 이
# 고정하는 것은 버전이지 변형이 아니므로, 버전은 lock 에서 읽고 변형은 TORCH_INDEX 로 고른다.
# 이어서 nvidia-*·triton 을 requirements 에서 걷어낸다 — CPU 이미지에 그대로 두면 CUDA
# 런타임 ~2.7GB 가 딸려와 이미지가 3.4GB → 9.9GB 가 된다(고객사 운영노드는 CPU 전용이라 전부 사표).
# GPU 노드는 TORCH_INDEX 를 cu### 로 넘기면 되고, 그 경우 CUDA 런타임은 torch 휠이 직접 가져온다.
#
# 해시. 반출 번들(폐쇄망)은 --require-hashes 로 무결성을 검증한다. Docker 빌드는 변형 인덱스가
# 섞여 해시 모드를 함께 쓸 수 없어 **버전 고정까지만** 한다 — 재현성 문제(버전 드리프트)는
# 닫히고, 공급망 검증은 반출 경로가 계속 담당한다.
set -euo pipefail

if [ "$#" -eq 0 ]; then
    echo "usage: $0 <extra> [<extra> ...]" >&2
    exit 2
fi

UV_VERSION="${UV_VERSION:-0.11.26}"
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cpu}"
LOCK_REQ=/tmp/requirements.lock.txt
APP_REQ=/tmp/requirements.app.txt

export_args=(export --frozen --no-dev --no-emit-project --no-hashes)
for extra in "$@"; do
    export_args+=(--extra "$extra")
done

python -m pip install --no-cache-dir "uv==${UV_VERSION}"
# --frozen: lock 을 갱신하지 않는다. lock 과 pyproject 가 어긋나 있으면 여기서 실패해야 한다
# (조용히 재해석하면 고정의 의미가 없다).
uv "${export_args[@]}" > "${LOCK_REQ}"

TORCH_VERSION="$(sed -n 's/^torch==\([^ ;]*\).*/\1/p' "${LOCK_REQ}" | head -1)"
if [ -z "${TORCH_VERSION}" ]; then
    echo "[locked-install] uv.lock 에 torch 핀이 없다 — extras 인자를 확인할 것" >&2
    exit 1
fi

# torch 와 그 GPU 동반 패키지를 걷어낸다. 변형(CPU/CUDA)은 TORCH_INDEX 가 정하고, CUDA 변형이면
# 그쪽 휠이 필요한 것을 스스로 가져온다 — 여기 남겨 두면 CPU 이미지에도 딸려온다.
#   nvidia-*·triton  CUDA 런타임 ~2.7GB (이미지 3.4GB → 9.9GB)
#   cuda-*           cuda-toolkit·bindings·pathfinder ~25MB. 크기보다 **라이선스**가 이유다 —
#                    NVIDIA 독점 라이선스라 CPU 배포본에 들어가면 OSS 검토서에 없는 항목이 생긴다.
grep -vE '^(torch==|nvidia-|triton==|cuda-)' "${LOCK_REQ}" > "${APP_REQ}"

echo "[locked-install] torch==${TORCH_VERSION} (index=${TORCH_INDEX})"
echo "[locked-install] 고정 패키지 $(grep -c '==' "${APP_REQ}") 종"

python -m pip install --index-url "${TORCH_INDEX}" "torch==${TORCH_VERSION}"
python -m pip install -r "${APP_REQ}"
# --no-deps: 의존성은 위에서 lock 기준으로 이미 깔았다. 빼면 pip 이 pyproject 범위를 다시
# 해석해 고정이 풀린다(이 스크립트의 존재 이유가 사라진다).
python -m pip install -e . --no-deps

# 설치 후 대조 — lock 이 말한 버전이 실제로 깔렸는지 본다. 조용히 어긋나면 재현성 주장이 거짓이 된다.
python - "${APP_REQ}" <<'PY'
import re
import sys
from collections import defaultdict
from importlib.metadata import PackageNotFoundError, version

# 한 패키지가 환경 마커로 갈려 여러 줄인 경우가 있다(실측: numpy 2.4.6/2.5.0 · scipy 1.17.1/1.18.0
# 이 python_full_version 3.12 경계로 분기). 마커를 평가하려면 packaging 이 필요한데 이 시점에
# 있다고 보장할 수 없으므로, **lock 이 제시한 후보 집합 안에 있으면 통과**로 본다.
# 이래도 목적은 달성된다 — 잡으려는 것은 "lock 에 없는 버전이 깔리는 것"(범위 재해석 드리프트)이다.
wanted = defaultdict(set)
for line in open(sys.argv[1], encoding="utf-8"):
    m = re.match(r"^([A-Za-z0-9._-]+)==([^\s;]+)", line)
    if m:
        wanted[m.group(1).lower().replace("_", "-")].add(m.group(2))

mismatch = []
for name, candidates in wanted.items():
    try:
        got = version(name)
    except PackageNotFoundError:
        continue          # 환경 마커로 대상 밖(win32 전용 등) — 미설치가 정상
    if got not in candidates:
        mismatch.append(f"{name}: lock {'|'.join(sorted(candidates))} != 설치 {got}")

if mismatch:
    print("[locked-install] 버전 불일치:\n  " + "\n  ".join(mismatch), file=sys.stderr)
    raise SystemExit(1)
split = sum(1 for c in wanted.values() if len(c) > 1)
print(f"[locked-install] 대조 통과 — {len(wanted)}종 (마커 분기 {split}종)")
PY
