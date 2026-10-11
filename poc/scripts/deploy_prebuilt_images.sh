#!/usr/bin/env bash
# Switch 211 (or another compose host) to images that were already built and verified.
# No source tree and no build command are accepted here by design.
set -euo pipefail

usage() {
  cat <<'EOF'
usage: deploy_prebuilt_images.sh --bundle-dir DIR --compose-file FILE --env-file FILE --overlay FILE [options]

  --bundle-dir DIR       contains release-images.json and, for load transport, image tar files
  --compose-file FILE    existing air-gap compose file
  --env-file FILE        existing deployment environment file
  --overlay FILE         persistent image-only compose overlay to write atomically
  --services api[,worker]  components to switch (default: api)
  --transport load|pull  load bundle tar files (default) or pull manifest image references
  --project-name NAME    compose project name (default: koipa-airgap)
  --health-url URL       API health endpoint (default: http://127.0.0.1:8000/api/v1/healthz)
  --health-timeout SEC   API readiness deadline (default: 180)
  -h, --help             show this help

The operation restarts only selected services: api for UI/API releases, or
worker+beat for a worker release. It never builds, never runs migrations, and
never changes the shared .env image tag. Run the prior bundle again to roll back.
EOF
}

bundle_dir=""; compose_file=""; env_file=""; overlay=""
services="api"; transport="load"; project_name="koipa-airgap"
health_url="http://127.0.0.1:8000/api/v1/healthz"; health_timeout=180
while [ "$#" -gt 0 ]; do
  case "$1" in
    --bundle-dir) bundle_dir="${2:?--bundle-dir needs a value}"; shift 2 ;;
    --compose-file) compose_file="${2:?--compose-file needs a value}"; shift 2 ;;
    --env-file) env_file="${2:?--env-file needs a value}"; shift 2 ;;
    --overlay) overlay="${2:?--overlay needs a value}"; shift 2 ;;
    --services) services="${2:?--services needs a value}"; shift 2 ;;
    --transport) transport="${2:?--transport needs a value}"; shift 2 ;;
    --project-name) project_name="${2:?--project-name needs a value}"; shift 2 ;;
    --health-url) health_url="${2:?--health-url needs a value}"; shift 2 ;;
    --health-timeout) health_timeout="${2:?--health-timeout needs a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

for value in "$bundle_dir" "$compose_file" "$env_file" "$overlay"; do
  [ -n "$value" ] || { usage >&2; exit 2; }
done
[ -f "$bundle_dir/release-images.json" ] || { echo "missing manifest: $bundle_dir/release-images.json" >&2; exit 2; }
[ -f "$compose_file" ] && [ -f "$env_file" ] || { echo "compose file or env file does not exist" >&2; exit 2; }
case "$transport" in load|pull) ;; *) echo "--transport must be load or pull" >&2; exit 2;; esac
case "$health_timeout" in *[!0-9]*|"") echo "--health-timeout must be a positive integer" >&2; exit 2;; esac

IFS=',' read -r -a requested <<< "$services"
selected=()
for service in "${requested[@]}"; do
  case "$service" in api|worker) ;; *) echo "unknown service: $service" >&2; exit 2;; esac
  [[ " ${selected[*]} " == *" $service "* ]] || selected+=("$service")
done

# Output is tab-delimited and derived from the signed-off manifest, never from user input.
mapfile -t images < <(python3 - "$bundle_dir/release-images.json" "${selected[*]}" <<'PY'
import json, re, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
if manifest.get("schema") != 1 or not re.fullmatch(r"[0-9a-f]{40}", manifest.get("git_sha", "")):
    raise SystemExit("invalid release manifest schema or git_sha")
want = set(sys.argv[2].split())
found = {row.get("service"): row for row in manifest.get("images", [])}
for service in sorted(want):
    row = found.get(service)
    if not row:
        raise SystemExit(f"manifest does not include requested service: {service}")
    image, image_id = row.get("image", ""), row.get("image_id", "")
    if not re.fullmatch(r"[A-Za-z0-9._/@:-]+", image) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise SystemExit(f"invalid image reference in manifest for {service}")
    print("\t".join([service, image, image_id, row.get("archive", ""), row.get("archive_sha256", ""), manifest["git_sha"]]))
PY
)

declare -A image_for
release_sha=""
for row in "${images[@]}"; do
  IFS=$'\t' read -r service image expected_id archive expected_archive_sha row_sha <<< "$row"
  release_sha="$row_sha"
  if [ "$transport" = "load" ]; then
    [ -n "$archive" ] || { echo "load transport needs an archive for $service" >&2; exit 2; }
    archive_path="$bundle_dir/$archive"
    [ -f "$archive_path" ] || { echo "missing image archive: $archive_path" >&2; exit 2; }
    actual_archive_sha="$(python3 - "$archive_path" <<'PY'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], 'rb') as f:
    for part in iter(lambda: f.read(1024 * 1024), b''):
        h.update(part)
print(h.hexdigest())
PY
)"
    [ "$actual_archive_sha" = "$expected_archive_sha" ] || { echo "archive hash mismatch: $archive" >&2; exit 1; }
    docker load -i "$archive_path"
  else
    docker pull "$image"
  fi
  actual_id="$(docker image inspect --format '{{.Id}}' "$image")"
  [ "$actual_id" = "$expected_id" ] || { echo "image ID mismatch for $service" >&2; exit 1; }
  actual_sha="$(docker image inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$image" | sed -n 's/^KOIPA_BUILD_SHA=//p' | tail -1)"
  [ "$actual_sha" = "$release_sha" ] || { echo "image build SHA mismatch for $service: $actual_sha" >&2; exit 1; }
  image_for[$service]="$image"
done

install -d -m 700 "$(dirname "$overlay")"
tmp_overlay="$(mktemp "${overlay}.tmp.XXXXXX")"
trap 'rm -f "$tmp_overlay"' EXIT
{
  echo 'services:'
  if [ -n "${image_for[api]:-}" ]; then
    printf '  api:\n    image: %s\n' "${image_for[api]}"
  fi
  if [ -n "${image_for[worker]:-}" ]; then
    printf '  worker:\n    image: %s\n  beat:\n    image: %s\n' "${image_for[worker]}" "${image_for[worker]}"
  fi
} > "$tmp_overlay"

docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file" -f "$tmp_overlay" config --quiet
if [ -f "$overlay" ]; then cp -p "$overlay" "${overlay}.previous"; fi
mv -f "$tmp_overlay" "$overlay"
trap - EXIT

compose=(docker compose --project-name "$project_name" --env-file "$env_file" -f "$compose_file" -f "$overlay")
up_services=()
if [ -n "${image_for[api]:-}" ]; then up_services+=(api); fi
if [ -n "${image_for[worker]:-}" ]; then up_services+=(worker beat); fi
"${compose[@]}" up -d --no-deps "${up_services[@]}"
"${compose[@]}" ps "${up_services[@]}"

if [ -n "${image_for[api]:-}" ]; then
  deadline=$(( $(date +%s) + health_timeout ))
  while :; do
    health="$(curl --fail --silent --show-error "$health_url" 2>/dev/null || true)"
    if [ -n "$health" ] && HEALTH_JSON="$health" EXPECTED_SHA="$release_sha" python3 - <<'PY'
import json, os, sys
try:
    payload = json.loads(os.environ["HEALTH_JSON"])
    actual = payload.get("git_sha") or payload.get("build", {}).get("git_sha")
    ok = payload.get("status") == "ok" and actual == os.environ["EXPECTED_SHA"]
except Exception:
    ok = False
sys.exit(0 if ok else 1)
PY
    then
      echo "[prebuilt-deploy] API healthy at committed SHA ${release_sha}"
      break
    fi
    [ "$(date +%s)" -lt "$deadline" ] || { echo "API health/SHA verification timed out; previous overlay is ${overlay}.previous" >&2; exit 1; }
    sleep 3
  done
fi

echo "[prebuilt-deploy] switched only: ${up_services[*]}; rollback overlay: ${overlay}.previous"
