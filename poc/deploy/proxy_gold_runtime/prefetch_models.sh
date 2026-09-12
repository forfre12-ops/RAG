#!/bin/sh
set -eu

ROOT=${PROXY_ROOT:-/home/kopia/proxy_gold_runtime}
CONTAINER=${PROXY_PREFETCH_CONTAINER:-koipa-proxy-ollama-prefetch}
QWEN_MANIFEST=bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8
QWEN4_MANIFEST=359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7
GEMMA_MANIFEST=f4031aab637d1ffa37b42570452ae0e4fad0314754d17ded67322e4b95836f8a

if [ "$(readlink -f "$ROOT")" != "/home/kopia/proxy_gold_runtime" ]; then
  echo "unexpected proxy runtime root: $ROOT" >&2
  exit 2
fi
if [ "$(docker inspect -f '{{index .Config.Labels "io.koipa.purpose"}}' "$CONTAINER")" != "proxy-model-prefetch" ]; then
  echo "refusing to use an unlabeled prefetch container" >&2
  exit 2
fi

docker exec "$CONTAINER" ollama pull qwen3:14b
qwen_path="$ROOT/ollama/models/manifests/registry.ollama.ai/library/qwen3/14b"
qwen_actual=$(sha256sum "$qwen_path" | awk '{print $1}')
test "$qwen_actual" = "$QWEN_MANIFEST"

docker exec "$CONTAINER" ollama pull qwen3:4b
qwen4_path="$ROOT/ollama/models/manifests/registry.ollama.ai/library/qwen3/4b"
qwen4_actual=$(sha256sum "$qwen4_path" | awk '{print $1}')
test "$qwen4_actual" = "$QWEN4_MANIFEST"

docker exec "$CONTAINER" ollama pull gemma3:12b
gemma_path="$ROOT/ollama/models/manifests/registry.ollama.ai/library/gemma3/12b"
gemma_actual=$(sha256sum "$gemma_path" | awk '{print $1}')
test "$gemma_actual" = "$GEMMA_MANIFEST"

docker exec "$CONTAINER" ollama list
marker_tmp="$ROOT/.MODELS_VERIFIED.tmp.$$"
{
  printf 'verified_at=%s\n' "$(date -Is)"
  printf 'qwen3_14b_manifest=sha256:%s\n' "$qwen_actual"
  printf 'qwen3_4b_manifest=sha256:%s\n' "$qwen4_actual"
  printf 'gemma3_12b_manifest=sha256:%s\n' "$gemma_actual"
} > "$marker_tmp"
mv "$marker_tmp" "$ROOT/MODELS_VERIFIED"
