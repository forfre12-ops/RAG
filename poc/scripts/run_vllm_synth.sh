#!/usr/bin/env bash
# 합성용 로컬 LLM(vLLM) 서버를 띄운다 — 지재원 서버(211) 기준.
#
# 왜 이 파일이 있는가(2026-09-12). 2026-09-11 18:00:44 에 `qwen3-vllm` 컨테이너가 누군가에 의해
# 멈추고 지워졌다. 되살리려니 **원래 기동 인자가 어디에도 없었다** — 서버의 스크립트·명령 기록,
# 세션 작업 폴더, 전 프로젝트 대화 기록, vLLM 컴파일 캐시까지 전수로 찾아 0건이었다. 그래서
# 서버에 남은 사실로 재구성해 띄웠고(18:21 복구), 같은 일이 또 생기지 않도록 그 명령을 리포에 남긴다.
#
# ⚠ 아래 값 중 **원래 값이 확인된 것과 재구성한 것을 구분해 적는다.**
#   확인됨: 이미지 vllm/vllm-openai:latest · 모델 이름 qwen3-30b · 포트 18000 ·
#           키는 ~/deploy/.env 의 LOCAL_LLM_API_KEY(합성이 그 값을 쓴다) · 가중치는 서버 HF 캐시의
#           ELVISIO/Qwen3-30B-A3B-Instruct-2507-AWQ(서버에 있는 유일한 Qwen3 가중치)
#   재구성: --max-model-len 32768(원래 값 모름 — L4 24GB 에서 KV 캐시가 들어가는 값) ·
#           GPU 메모리 비율 0.92(메모리 기록) · 포트를 사설 IP 에만 연다(원래 공개 범위 모름)
#
# 관계: api 컨테이너는 koipa-airgap_default(172.18.x), vLLM 은 기본 bridge(172.17.x) 라 서로 다른
# 네트워크다. 그래서 합성 설정이 호스트 사설 IP(10.0.8.6:18000)를 경유한다 — 이 스크립트도 같은
# 주소에만 연다. 0.0.0.0 으로 열면 서버 방화벽이 꺼져 있어(ACG 한 겹) 외부에 그대로 노출된다.
#
# ⚠ GPU 는 한 장(L4 24GB)이다. 이 서버가 0.92 를 잡으면 **GPU 학습에는 약 2GB 만 남는다.**
#   학습을 돌릴 때는 이 컨테이너를 내리거나 VLLM_GPU_FRACTION 을 낮춰 다시 띄운다.
#   워커의 문서 분류는 CLASSIFIER_DEVICE 기본값이 cpu 라(e41a7134) 영향을 받지 않는다.
#
# 사용:
#   bash scripts/run_vllm_synth.sh            # 띄운다(이미 있으면 아무것도 하지 않는다)
#   docker rm -f qwen3-vllm                   # 내린다
#   docker logs -f qwen3-vllm                 # 기동 로그(준비까지 3~4분)
set -euo pipefail

NAME="${VLLM_NAME:-qwen3-vllm}"
IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"
MODEL="${VLLM_MODEL:-ELVISIO/Qwen3-30B-A3B-Instruct-2507-AWQ}"
SERVED="${VLLM_SERVED_NAME:-qwen3-30b}"
BIND="${VLLM_BIND:-10.0.8.6}"
PORT="${VLLM_PORT:-18000}"
FRACTION="${VLLM_GPU_FRACTION:-0.92}"
MAX_LEN="${VLLM_MAX_MODEL_LEN:-32768}"
ENV_FILE="${KOIPA_ENV_FILE:-$HOME/deploy/.env}"

# 키는 합성 설정에서 읽는다 — 화면에 찍지 않는다. 두 값이 다르면 합성이 401 로 실패한다.
KEY="$(grep -E '^LOCAL_LLM_API_KEY=' "$ENV_FILE" | tail -1 | cut -d= -f2-)"
[ -n "$KEY" ] || { echo "⛔ $ENV_FILE 에 LOCAL_LLM_API_KEY 가 없다 — 합성과 키를 맞출 수 없어 중단한다"; exit 1; }

if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "이미 있다: $NAME — 다시 띄우려면 먼저 'docker rm -f $NAME'"
  docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -x "$NAME.*" || true
  exit 0
fi

docker run -d --name "$NAME" --gpus all --restart unless-stopped --ipc=host \
  -p "${BIND}:${PORT}:8000" \
  -v /data/huggingface:/root/.cache/huggingface \
  -v /data/vllm-cache:/root/.cache/vllm \
  -e HF_HUB_OFFLINE=1 \
  "$IMAGE" "$MODEL" \
  --served-model-name "$SERVED" --api-key "$KEY" \
  --gpu-memory-utilization "$FRACTION" --max-model-len "$MAX_LEN"

echo "== 기동 대기(모델 16GB 적재 + 컴파일 — 보통 3~4분)"
for _ in $(seq 1 90); do
  if docker logs "$NAME" 2>&1 | grep -q 'Application startup complete'; then
    echo "준비 완료"
    break
  fi
  if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "⛔ 컨테이너가 죽었다 — 마지막 로그:"; docker logs --tail 40 "$NAME" 2>&1; exit 1
  fi
  sleep 10
done

echo "== 모델 목록(키로 인증)"
curl -fsS -m 10 -H "Authorization: Bearer $KEY" "http://${BIND}:${PORT}/v1/models" | head -c 200; echo
echo "== GPU"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
echo
echo "확인은 api 컨테이너에서 합성 경로로 한 건 생성해 보는 것이 가장 확실하다:"
echo "  docker exec koipa-airgap-api-1 python3 -c \"from koipa.adapters.llm import build_provider; p=build_provider(); print(p.name, p.model); print(p.generate('한 문장으로 답하라: 영업비밀의 정의는?', max_tokens=64).text[:120])\""
