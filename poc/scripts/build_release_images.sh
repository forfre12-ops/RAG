#!/usr/bin/env bash
# Build an immutable, inspectable release image set outside the 211 host.
#
# Examples (run from anywhere):
#   scripts/build_release_images.sh --services api --output ../release/6bff7bf1
#   scripts/build_release_images.sh --services api,worker --tag 6bff7bf1 --push \
#     --image-prefix registry.example/koipa
#
# This intentionally refuses a dirty checkout. A deployment image must identify an
# exact committed source state; otherwise KOIPA_BUILD_SHA becomes an unverifiable claim.
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"
GIT_BIN="${KOIPA_GIT_BIN:-git}"

usage() {
  cat <<'EOF'
usage: build_release_images.sh [options]

  --services api[,worker]     components to build (default: api,worker)
  --tag TAG                   immutable image tag (default: sha-<HEAD short>)
  --sha SHA                   expected committed SHA (default: HEAD)
  --output DIR                write image tar files plus release-images.json
  --image-prefix PREFIX       e.g. registry.example/koipa; defaults to local names
  --push                      push the already-built image(s) to the configured registry
  --torch-index URL           override the locked torch wheel index
  --overwrite                 allow replacing files in --output DIR
  -h, --help                  show this help

For a UI/API-only release, use --services api. It deliberately does not build
or publish a worker image. This script never deploys and never pushes Git commits.
EOF
}

services="api,worker"
tag=""
expected_sha=""
output_dir=""
image_prefix=""
torch_index=""
push_images=0
overwrite=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --services) services="${2:?--services needs a value}"; shift 2 ;;
    --tag) tag="${2:?--tag needs a value}"; shift 2 ;;
    --sha) expected_sha="${2:?--sha needs a value}"; shift 2 ;;
    --output) output_dir="${2:?--output needs a value}"; shift 2 ;;
    --image-prefix) image_prefix="${2:?--image-prefix needs a value}"; shift 2 ;;
    --torch-index) torch_index="${2:?--torch-index needs a value}"; shift 2 ;;
    --push) push_images=1; shift ;;
    --overwrite) overwrite=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

"$GIT_BIN" diff --quiet && "$GIT_BIN" diff --cached --quiet || {
  echo "refusing a dirty checkout: commit the exact source before producing a release image" >&2
  exit 2
}

head_sha="$("$GIT_BIN" rev-parse HEAD)"
if [ -n "$expected_sha" ] && [ "$expected_sha" != "$head_sha" ]; then
  echo "--sha must equal committed HEAD ($head_sha), got $expected_sha" >&2
  exit 2
fi
release_sha="$head_sha"
if [ -z "$tag" ]; then tag="sha-${release_sha:0:12}"; fi
case "$tag" in
  *[!A-Za-z0-9._-]*|"") echo "invalid image tag: $tag" >&2; exit 2 ;;
esac
if [ "$push_images" -eq 1 ] && [ -z "$image_prefix" ]; then
  echo "--push requires --image-prefix (for example registry.example/koipa)" >&2
  exit 2
fi

declare -A dockerfile=( [api]=Dockerfile.api.prod [worker]=Dockerfile.worker )
declare -A image_base=( [api]=koipa-api [worker]=koipa-worker )
declare -A smoke_module=( [api]=koipa.api.app [worker]=koipa.workers.celery_app )

IFS=',' read -r -a requested <<< "$services"
selected=()
for service in "${requested[@]}"; do
  case "$service" in
    api|worker) ;;
    *) echo "unknown service: $service (expected api or worker)" >&2; exit 2 ;;
  esac
  [[ " ${selected[*]} " == *" $service "* ]] || selected+=("$service")
done
[ "${#selected[@]}" -gt 0 ] || { echo "at least one service is required" >&2; exit 2; }

if [ -n "$output_dir" ]; then
  mkdir -p "$output_dir"
  if [ "$overwrite" -ne 1 ] && find "$output_dir" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    echo "--output directory is not empty (pass --overwrite to replace release artifacts): $output_dir" >&2
    exit 2
  fi
fi

built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
entries=()
for service in "${selected[@]}"; do
  base="${image_base[$service]}"
  image="${base}:${tag}"
  if [ -n "$image_prefix" ]; then image="${image_prefix%/}/${base}:${tag}"; fi

  args=(docker build --pull=false -f "${dockerfile[$service]}"
        --build-arg "KOIPA_BUILD_SHA=${release_sha}"
        --build-arg "KOIPA_BUILD_AT=${built_at}"
        -t "$image" .)
  if [ -n "$torch_index" ]; then args=(docker build --pull=false -f "${dockerfile[$service]}" --build-arg "TORCH_INDEX=${torch_index}" --build-arg "KOIPA_BUILD_SHA=${release_sha}" --build-arg "KOIPA_BUILD_AT=${built_at}" -t "$image" .); fi
  echo "[release-build] ${service}: ${image}"
  "${args[@]}"
  docker run --rm --entrypoint python "$image" -c "import ${smoke_module[$service]}; print('${service} import OK')"

  image_id="$(docker image inspect --format '{{.Id}}' "$image")"
  archive=""
  if [ -n "$output_dir" ]; then
    archive="${service}.tar"
    docker save -o "$output_dir/$archive" "$image"
  fi
  if [ "$push_images" -eq 1 ]; then docker push "$image"; fi
  entries+=("${service}"$'\t'"${image}"$'\t'"${image_id}"$'\t'"${archive}")
done

if [ -n "$output_dir" ]; then
  export RELEASE_SHA="$release_sha" RELEASE_BUILT_AT="$built_at" RELEASE_ENTRIES="$(printf '%s\n' "${entries[@]}")"
  python - "$output_dir/release-images.json" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

out = Path(sys.argv[1])
images = []
for row in filter(None, os.environ["RELEASE_ENTRIES"].splitlines()):
    service, image, image_id, archive = row.split("\t")
    item = {"service": service, "image": image, "image_id": image_id}
    if archive:
        path = out.parent / archive
        item["archive"] = archive
        item["archive_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    images.append(item)

out.write_text(json.dumps({
    "schema": 1,
    "git_sha": os.environ["RELEASE_SHA"],
    "built_at": os.environ["RELEASE_BUILT_AT"],
    "images": images,
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  echo "[release-build] bundle manifest: $output_dir/release-images.json"
fi

echo "[release-build] verified prebuilt image(s): ${selected[*]} @ ${release_sha}"
