# Proxy-gold local LLM runtime

Operational status remains **PILOT_FAILED / REDESIGN_IN_PROGRESS /
CUSTOMER_ACCURACY_UNVERIFIED**. These runs are synthetic/public proxy evidence,
not a validation of accuracy on customer documents.

This runtime is intentionally separate from the deployed KL API/worker stack.
It exposes no host port, joins only the internal `koipa-proxy-gold-internal`
network, loads at most one model, and must never restart the product services.
The active batch model remains warm for five minutes; switching from generation
to judging evicts it automatically because `OLLAMA_MAX_LOADED_MODELS=1`.

The image and model manifests are pinned in `model-lock.json`. Model blobs are
prefetched while a temporary container has egress; the steady-state Compose
service is then started on the internal-only network. A runner must join that
network explicitly and use `http://ollama:11434/v1`.

After the validated release extraction below, start only the isolated Ollama
service from that release:

```sh
: "${PROXY_RELEASE_ROOT:?run the validated release extraction step first}"
export PROXY_OLLAMA_DATA=/home/kopia/proxy_gold_runtime/ollama
docker compose -f "$PROXY_RELEASE_ROOT/deploy/proxy_gold_runtime/compose.yaml" config
docker compose -f "$PROXY_RELEASE_ROOT/deploy/proxy_gold_runtime/compose.yaml" up -d ollama
```

Before and after every batch, record `docker ps`, API
`/api/v1/healthz/live`, `nvidia-smi`, the model manifest digest, and the
generation/judging run manifests. Stop the runtime if free GPU memory drops
below 2 GiB or the deployed API becomes unhealthy.

## Release extraction and exact-image preflight

The workstation build output is
`artifacts/proxy_runtime/proxy-runtime.tar.gz`. The uploaded archive and every
extracted release belong under `/home/kopia/proxy_gold_runtime/releases`; run
outputs belong under `/home/kopia/proxy_gold_runtime/runs/runner`. A release
archive has the canonical top-level directory `proxy-runtime/`, so extract it
into a new immutable release directory with `--strip-components=1`. Without
that option the later `/release/scripts/...` paths are one directory too deep.

```sh
# Workstation, before upload
python scripts/build_proxy_runtime_release.py \
  --verify-only artifacts/proxy_runtime/proxy-runtime.tar.gz
sha256sum artifacts/proxy_runtime/proxy-runtime.tar.gz

# KL, after uploading to a new archive path. Copy the workstation sha256 here.
export EXPECTED_ARCHIVE_SHA256=RELEASE_SHA256
export PROXY_RELEASES_ROOT=/home/kopia/proxy_gold_runtime/releases
export PROXY_RELEASE_ARCHIVE="$PROXY_RELEASES_ROOT/proxy-runtime-$EXPECTED_ARCHIVE_SHA256.tar.gz"
export PROXY_RUN_ROOT=/home/kopia/proxy_gold_runtime/runs/runner
export PROXY_ARTIFACT_ROOT=/home/kopia/proxy_gold_runtime/artifacts
export PROXY_RUNNER_IMAGE='sha256:578846335a11f047a1dcbb89276650b80b695b9af3aea37e515f48332b6a6e57'
printf '%s  %s\n' "$EXPECTED_ARCHIVE_SHA256" "$PROXY_RELEASE_ARCHIVE" | sha256sum -c -
ACTUAL_ARCHIVE_SHA256="$(sha256sum "$PROXY_RELEASE_ARCHIVE" | awk '{print $1}')"
test "$ACTUAL_ARCHIVE_SHA256" = "$EXPECTED_ARCHIVE_SHA256"
export ACTUAL_ARCHIVE_SHA256
export PROXY_RELEASE_ROOT="$PROXY_RELEASES_ROOT/$ACTUAL_ARCHIVE_SHA256"
test ! -e "$PROXY_RELEASE_ROOT"
chgrp 1000 "$PROXY_RELEASE_ARCHIVE"
chmod 0440 "$PROXY_RELEASE_ARCHIVE"
RELEASE_STAGING="$(mktemp -d "$PROXY_RELEASES_ROOT/.extract.$ACTUAL_ARCHIVE_SHA256.XXXXXX")"
tar -xzf "$PROXY_RELEASE_ARCHIVE" -C "$RELEASE_STAGING" --strip-components=1
test -f "$RELEASE_STAGING/RELEASE_MANIFEST.json"
test -f "$RELEASE_STAGING/scripts/build_proxy_scenarios.py"
test -f "$RELEASE_STAGING/scripts/judge_proxy_candidates.py"
test -f "$RELEASE_STAGING/scripts/run_proxy_generation_shards.py"
test -f "$RELEASE_STAGING/scripts/run_proxy_judging_shards.py"
chgrp -R 1000 "$RELEASE_STAGING"
find "$RELEASE_STAGING" -type d -exec chmod 0550 '{}' +
find "$RELEASE_STAGING" -type f -exec chmod 0440 '{}' +
docker run --rm --init --pull never \
  --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 \
  --user 1000:1000 \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/release/src:/release \
  -v "$RELEASE_STAGING:/release:ro" \
  -v "$PROXY_RELEASE_ARCHIVE:/archive/proxy-runtime.tar.gz:ro" \
  -w /release \
  "$PROXY_RUNNER_IMAGE" \
  python scripts/build_proxy_runtime_release.py \
    --source-root /release --verify-only /archive/proxy-runtime.tar.gz
mv "$RELEASE_STAGING" "$PROXY_RELEASE_ROOT"
test -d "$PROXY_RUN_ROOT"
test -d "$PROXY_ARTIFACT_ROOT"
df -h "$PROXY_RELEASES_ROOT" "$PROXY_RUN_ROOT" "$PROXY_ARTIFACT_ROOT"
curl --fail --silent --show-error http://localhost:8000/api/v1/healthz/live
```

The generation/judging `runner` below intentionally has no `--gpus` option:
Ollama owns the proxy GPU workload and the disposable Python process only calls
its internal OpenAI-compatible endpoint. After defining it, verify the exact
pinned API image can run the required optimizer before any pilot.

## Ten-shard generation runner contract

Every non-dry-run generation and judging process verifies the requested model
against the live Ollama `GET /api/tags` inventory before creating a run
directory. The requested model name must resolve through one exact name (or
its exact `:latest` alias), and the returned digest must equal the pinned
`model-lock.json` manifest digest. Missing, ambiguous, unreachable, or
mismatched models stop the run. Only local/private Ollama endpoints are
accepted; public/cloud endpoints and credential-bearing URLs are rejected.

The stable attestation binding covers the canonical/resolved model, live and
expected digests, and a credential-free endpoint identity hash. Generation
records it in every row contract, the run manifest, and `COMPLETE.json`.
Generation and judging shard controllers independently revalidate the live
inventory on start/resume and while accepting/finally revalidating shards.
`--dry-run` performs syntax and endpoint-policy validation only and reports
`status=pending_live_verification`; it is never evidence that the model blob
was present or correct.

Run generation in a disposable container attached only to the proxy network.
The runner image must be an approved worker image pinned by digest and the
release directory must be the read-only extraction of one release archive.
The output directory must be writable by uid 1000.  Do not use an archive whose
controller help lacks `--intended-use`; older controllers bind every shard to
evaluation and cannot generate the train-only pool safely.

Generation/controller run directories are published as mode `2750` and every
manifest, progress file, journal, log, JSONL, and COMPLETE marker as `0640`.
With runner gid 1000 this lets the KL SSH account (uid 1001, gid 1000) read and
audit artifacts without a root-only reader helper; content hashes are unchanged.

```sh
: "${PROXY_RELEASE_ROOT:?run the validated release extraction step first}"
export PROXY_RUN_ROOT=/home/kopia/proxy_gold_runtime/runs/runner
export PROXY_ARTIFACT_ROOT=/home/kopia/proxy_gold_runtime/artifacts
# KL에서 2026-08-08 재확인한 배포 API 이미지 ID. 실행 직전 docker image inspect로
# 같은 ID인지 다시 확인하고, 다르면 새 값을 승인·기록한 뒤 사용한다.
test -f "$PROXY_RELEASE_ROOT/scripts/run_proxy_generation_shards.py"
test -d "$PROXY_RUN_ROOT"
test -d "$PROXY_ARTIFACT_ROOT"
test "$(docker image inspect "$PROXY_RUNNER_IMAGE" --format '{{.Id}}')" = "$PROXY_RUNNER_IMAGE"

runner() {
  docker run --rm --init --pull never \
    --network koipa-proxy-gold-internal \
    --read-only --tmpfs /tmp:rw,noexec,nosuid,size=1g \
    --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 \
    --user 1000:1000 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e PYTHONPATH=/release/src:/release \
    -e LOCAL_LLM_BASE_URL=http://ollama:11434/v1 \
    -e LOCAL_LLM_MODEL=qwen3:14b \
    -e LOCAL_LLM_API_KEY=ollama \
    -v "$PROXY_RELEASE_ROOT:/release:ro" \
    -v "$PROXY_ARTIFACT_ROOT:/proxy-artifacts:ro" \
    -v "$PROXY_RUN_ROOT:/work:rw" \
    -w /release \
    "$PROXY_RUNNER_IMAGE" "$@"
}

runner python scripts/run_proxy_generation_shards.py --help
runner python -c 'import numpy, openai, scipy; from scipy.optimize import milp; assert callable(milp); print(numpy.__version__, scipy.__version__, openai.__version__)'
```

## Immutable execution preflight receipt

Before choosing a `BATCH_ID`, commit one preflight receipt.  The receipt binds
the archive hash (which must also be the release-directory basename), release
content manifest, exact runner and deployed API image, product health, GPU
state, isolated Ollama image, shipped model lock, and live `/api/tags` digests.
It contains no endpoint URL or credential.  Keep the receipt and its SHA-256
sidecar with every pilot/controller operator record, and use
`pf-<first 12 receipt-sha256>` as the batch suffix.

```sh
export EXPECTED_PRODUCT_IMAGE='sha256:578846335a11f047a1dcbb89276650b80b695b9af3aea37e515f48332b6a6e57'
export PRODUCT_API_CONTAINER=koipa-jjw-api-1
export PREFLIGHT_ROOT="$PROXY_RUN_ROOT/preflight"
install -d -m 2770 "$PREFLIGHT_ROOT"
chgrp 1000 "$PREFLIGHT_ROOT"

export RELEASE_DIR_BASENAME="$(basename "$PROXY_RELEASE_ROOT")"
test "$RELEASE_DIR_BASENAME" = "$ACTUAL_ARCHIVE_SHA256"
export RUNNER_IMAGE_ID="$(docker image inspect "$PROXY_RUNNER_IMAGE" --format '{{.Id}}')"
test "$RUNNER_IMAGE_ID" = "$PROXY_RUNNER_IMAGE"
export PRODUCT_IMAGE_ID="$(docker inspect "$PRODUCT_API_CONTAINER" --format '{{.Image}}')"
test "$PRODUCT_IMAGE_ID" = "$EXPECTED_PRODUCT_IMAGE"
export OLLAMA_IMAGE_ID="$(docker inspect koipa-proxy-ollama --format '{{.Image}}')"
export OLLAMA_CONFIG_IMAGE="$(docker inspect koipa-proxy-ollama --format '{{.Config.Image}}')"
export PRODUCT_CONFIG_IMAGE="$(docker inspect "$PRODUCT_API_CONTAINER" --format '{{.Config.Image}}')"
export PRODUCT_GPU_REQUESTS="$(docker inspect "$PRODUCT_API_CONTAINER" --format '{{json .HostConfig.DeviceRequests}}')"
export PRODUCT_HEALTH_JSON="$(curl --fail --silent --show-error http://localhost:8000/api/v1/healthz/live)"
export GPU_STATE="$(nvidia-smi --query-gpu=name,memory.total,memory.free,utilization.gpu --format=csv,noheader,nounits)"
export LIVE_MODEL_JSON="$(runner python -c 'import json,urllib.request; from pathlib import Path; lock=json.loads(Path("/release/deploy/proxy_gold_runtime/model-lock.json").read_text()); tags=json.load(urllib.request.urlopen("http://ollama:11434/api/tags",timeout=5)); expected={m["name"]:m["manifest_digest"] for m in lock["models"]}; matches={name:[row for row in tags["models"] if name in {row.get("name"),row.get("model")}] for name in expected}; assert all(len(rows)==1 for rows in matches.values()); live={name:"sha256:"+str(rows[0]["digest"]).removeprefix("sha256:") for name,rows in matches.items()}; assert live==expected,(live,expected); print(json.dumps(live,sort_keys=True,separators=(",",":")))')"

export PREFLIGHT_CHECKED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export RELEASE_MANIFEST_SHA256="$(sha256sum "$PROXY_RELEASE_ROOT/RELEASE_MANIFEST.json" | awk '{print $1}')"
export MODEL_LOCK_SHA256="$(sha256sum "$PROXY_RELEASE_ROOT/deploy/proxy_gold_runtime/model-lock.json" | awk '{print $1}')"
PREFLIGHT_TMP="$(mktemp "$PREFLIGHT_ROOT/.preflight.XXXXXX")"
export PREFLIGHT_TMP
python3 - "$PREFLIGHT_TMP" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(os.environ["PROXY_RELEASE_ROOT"]).resolve()
manifest_path = root / "RELEASE_MANIFEST.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
model_lock_path = root / "deploy/proxy_gold_runtime/model-lock.json"
model_lock = json.loads(model_lock_path.read_text(encoding="utf-8"))

declared = {row["path"]: row for row in manifest["files"]}
assert len(declared) == len(manifest["files"])
actual = set()
for path in root.rglob("*"):
    assert not path.is_symlink(), path
    if path.is_file() and path != manifest_path:
        actual.add(path.relative_to(root).as_posix())
assert actual == set(declared), (sorted(actual - set(declared)), sorted(set(declared) - actual))
for relative, row in declared.items():
    payload = (root / relative).read_bytes()
    assert len(payload) == row["bytes"], relative
    assert hashlib.sha256(payload).hexdigest() == row["sha256"], relative
descriptor = [{"path": row["path"], "bytes": row["bytes"], "sha256": row["sha256"]} for row in manifest["files"]]
content_sha256 = hashlib.sha256((json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")).hexdigest()
assert content_sha256 == manifest["content_hash"]["sha256"]

health = json.loads(os.environ["PRODUCT_HEALTH_JSON"])
assert health.get("status") == "ok"
gpu_requests = json.loads(os.environ["PRODUCT_GPU_REQUESTS"])
assert isinstance(gpu_requests, list) and gpu_requests
assert any("gpu" in capability for request in gpu_requests for group in request.get("Capabilities", []) for capability in group)
expected_ollama_image = model_lock["ollama_image"]["image_digest"]
assert os.environ["OLLAMA_IMAGE_ID"] == expected_ollama_image
assert os.environ["OLLAMA_CONFIG_IMAGE"] == "ollama/ollama@" + expected_ollama_image
assert os.environ["ACTUAL_ARCHIVE_SHA256"] == os.environ["RELEASE_DIR_BASENAME"]
assert os.environ["RUNNER_IMAGE_ID"] == os.environ["PROXY_RUNNER_IMAGE"]

receipt = {
    "schema": "proxy-runtime-preflight-receipt-v1",
    "checked_at": os.environ["PREFLIGHT_CHECKED_AT"],
    "release": {
        "archive_sha256": os.environ["ACTUAL_ARCHIVE_SHA256"],
        "directory_basename": os.environ["RELEASE_DIR_BASENAME"],
        "manifest_sha256": os.environ["RELEASE_MANIFEST_SHA256"],
        "content_sha256": content_sha256,
        "verified_file_count": len(declared),
    },
    "runner": {"image_id": os.environ["RUNNER_IMAGE_ID"]},
    "product": {
        "container": os.environ["PRODUCT_API_CONTAINER"],
        "configured_image": os.environ["PRODUCT_CONFIG_IMAGE"],
        "image_id": os.environ["PRODUCT_IMAGE_ID"],
        "gpu_device_requests": gpu_requests,
        "health": health,
    },
    "gpu": {"host_state": os.environ["GPU_STATE"]},
    "ollama": {
        "configured_image": os.environ["OLLAMA_CONFIG_IMAGE"],
        "container_image_id": os.environ["OLLAMA_IMAGE_ID"],
        "model_lock_sha256": os.environ["MODEL_LOCK_SHA256"],
        "live_models": json.loads(os.environ["LIVE_MODEL_JSON"]),
    },
}
Path(sys.argv[1]).write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
PY
PREFLIGHT_NAME="preflight-${ACTUAL_ARCHIVE_SHA256}-${PREFLIGHT_CHECKED_AT%Z}.json"
PREFLIGHT_PATH="$PREFLIGHT_ROOT/$PREFLIGHT_NAME"
test ! -e "$PREFLIGHT_PATH"
ln "$PREFLIGHT_TMP" "$PREFLIGHT_PATH"
rm "$PREFLIGHT_TMP"
chgrp 1000 "$PREFLIGHT_PATH"
chmod 0440 "$PREFLIGHT_PATH"
sha256sum "$PREFLIGHT_PATH" > "$PREFLIGHT_PATH.sha256"
chgrp 1000 "$PREFLIGHT_PATH.sha256"
chmod 0440 "$PREFLIGHT_PATH.sha256"
export PROXY_PREFLIGHT_RECEIPT="$PREFLIGHT_PATH"
export PROXY_PREFLIGHT_SHA256="$(sha256sum "$PREFLIGHT_PATH" | awk '{print $1}')"
export BATCH_ID="pf-${PROXY_PREFLIGHT_SHA256%${PROXY_PREFLIGHT_SHA256#????????????}}"
printf 'BATCH_ID=%s receipt=%s sha256=%s\n' "$BATCH_ID" "$PROXY_PREFLIGHT_RECEIPT" "$PROXY_PREFLIGHT_SHA256"
```

Refuse to start if any assertion fails. Commands below expand the exact exported
`BATCH_ID`; it is not an arbitrary operator label. A run manifest does
not yet embed this receipt; the receipt SHA in its operator record is therefore
the required release/image provenance bridge for this batch.

## Direct pilot gate (no shard controller)

Run these two pilots before starting either ten-shard controller. Actual
generation and judging automatically resolve the requested Ollama alias through
`/api/tags`, require the live digest to equal the pinned digest, record the
attestation, and reverify it before commit/resume. Generation/controller
`--dry-run` intentionally performs syntax and plan validation only and records
`pending_live_verification`; a dry-run is never evidence of a live model match.

The representative pilot is exactly eight committed candidates: the
`process-optimization` and `pricing-policy` archetypes crossed with
`ts-s2-v2-m2`, `s1-s2-v2-m0`, `s2-s1-v1-m1`, and `s3-s0-v0-m0`. The 2.5
oversample factor permits three generation attempts per scenario after ceiling,
while candidate buffer 1.0 still commits exactly one candidate per scenario.

```sh
: "${BATCH_ID:?commit the preflight receipt first}"
generate_representative8() {
  runner python scripts/build_proxy_scenarios.py \
    --catalog datasets/proxy_gold/scenario_catalog.v1.json \
    --out-root /work/generation \
    --run-id "pilot-representative8-qwen14b-$BATCH_ID" \
    --provider local_openai \
    --model-manifest-sha256 sha256:bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8 \
    --per-scenario 1 \
    --oversample-factor 2.5 --candidate-buffer-factor 1.0 \
    --max-quality-retries 2 \
    --scenario process-optimization-ts-s2-v2-m2 \
    --scenario process-optimization-s1-s2-v2-m0 \
    --scenario process-optimization-s2-s1-v1-m1 \
    --scenario process-optimization-s3-s0-v0-m0 \
    --scenario pricing-policy-ts-s2-v2-m2 \
    --scenario pricing-policy-s1-s2-v2-m0 \
    --scenario pricing-policy-s2-s1-v1-m1 \
    --scenario pricing-policy-s3-s0-v0-m0 \
    "$@"
}

generate_representative8 --dry-run
# Confirm scenarios=8, planned attempts=24, and selection target=8; then execute:
generate_representative8
```

The boundary pilot is exactly the 21 valid factor profiles for
`process-optimization`, again with one committed candidate per scenario. The
deterministic 2.5 apportionment makes 53 planned attempts total (two or three
per profile); it does not independently round every profile to three. The
profile list is explicit so no future catalog addition silently expands this
gate.

```sh
generate_boundary21() {
  set -- "$@" \
    --factor-profile ts-s2-v2-m1 \
    --factor-profile ts-s2-v2-m2 \
    --factor-profile s1-s2-v2-m0 \
    --factor-profile s2-s1-v1-m1 \
    --factor-profile s2-s1-v1-m2 \
    --factor-profile s2-s1-v2-m1 \
    --factor-profile s2-s1-v2-m2 \
    --factor-profile s2-s2-v1-m1 \
    --factor-profile s2-s2-v1-m2 \
    --factor-profile s3-s0-v0-m0 \
    --factor-profile s3-s0-v1-m0 \
    --factor-profile s3-s0-v2-m0 \
    --factor-profile s3-s1-v0-m0 \
    --factor-profile s3-s1-v0-m1 \
    --factor-profile s3-s1-v0-m2 \
    --factor-profile s3-s2-v0-m0 \
    --factor-profile s3-s2-v0-m1 \
    --factor-profile s3-s2-v0-m2 \
    --factor-profile s3-s1-v1-m0 \
    --factor-profile s3-s1-v2-m0 \
    --factor-profile s3-s2-v1-m0

  runner python scripts/build_proxy_scenarios.py \
    --catalog datasets/proxy_gold/scenario_catalog.v1.json \
    --out-root /work/generation \
    --run-id "pilot-boundary21-qwen14b-$BATCH_ID" \
    --provider local_openai \
    --model-manifest-sha256 sha256:bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8 \
    --per-scenario 1 \
    --oversample-factor 2.5 --candidate-buffer-factor 1.0 \
    --max-quality-retries 2 \
    --scenario process-optimization-ts-s2-v2-m1 \
    --scenario process-optimization-ts-s2-v2-m2 \
    --scenario process-optimization-s1-s2-v2-m0 \
    --scenario process-optimization-s2-s1-v1-m1 \
    --scenario process-optimization-s2-s1-v1-m2 \
    --scenario process-optimization-s2-s1-v2-m1 \
    --scenario process-optimization-s2-s1-v2-m2 \
    --scenario process-optimization-s2-s2-v1-m1 \
    --scenario process-optimization-s2-s2-v1-m2 \
    --scenario process-optimization-s3-s0-v0-m0 \
    --scenario process-optimization-s3-s0-v1-m0 \
    --scenario process-optimization-s3-s0-v2-m0 \
    --scenario process-optimization-s3-s1-v0-m0 \
    --scenario process-optimization-s3-s1-v0-m1 \
    --scenario process-optimization-s3-s1-v0-m2 \
    --scenario process-optimization-s3-s2-v0-m0 \
    --scenario process-optimization-s3-s2-v0-m1 \
    --scenario process-optimization-s3-s2-v0-m2 \
    --scenario process-optimization-s3-s1-v1-m0 \
    --scenario process-optimization-s3-s1-v2-m0 \
    --scenario process-optimization-s3-s2-v1-m0 \
    "$@"
}

generate_boundary21 --dry-run
# Confirm scenarios=21, planned attempts=53, and selection target=21; then execute:
generate_boundary21
```

`--factor-profile` above is a second fail-closed assertion in addition to the
explicit scenario IDs. Judge the two committed runs directly, without a shard
controller:

```sh
judge_direct() {
  input_path="$1"
  run_id="$2"
  intended_use="$3"
  runner python scripts/judge_proxy_candidates.py \
    --input "$input_path" \
    --out-root /work/judging \
    --run-id "$run_id" \
    --intended-use "$intended_use" \
    --base-url http://ollama:11434/v1 \
    --judge-model gemma3:12b \
    --judge-model-manifest-sha256 sha256:f4031aab637d1ffa37b42570452ae0e4fad0314754d17ded67322e4b95836f8a \
    --no-shadow \
    --k-min 2 --k-max 3 \
    --temperature 0.6 \
    --min-self-consistency 0.67
}

judge_direct \
  "/work/generation/pilot-representative8-qwen14b-$BATCH_ID/candidates.jsonl" \
  "pilot-representative8-gemma12b-$BATCH_ID" \
  evaluation

judge_direct \
  "/work/generation/pilot-boundary21-qwen14b-$BATCH_ID/candidates.jsonl" \
  "pilot-boundary21-gemma12b-$BATCH_ID" \
  evaluation
```

Both runs must have `COMPLETE.json`; representative judging must retain one
clean candidate for each of its eight scenario IDs and boundary judging one for
each of its 21 scenario IDs. Inspect every document, fact ledger, label and
arithmetic relation. A missing/rejected/uncertain profile fails the pilot and is
rerun only for that explicit profile under a new run ID. Do not weaken the
quality gate or exact-count contract.

First run both commands with `--dry-run`.  Dry-run validates the catalog split,
locked totals, family partition, model digest format, and all ten child commands
without creating output directories. Its model attestation status must be
`pending_live_verification`; only the non-dry execution performs and records
the automatic live Ollama digest verification. Remove only `--dry-run` to
execute.

```sh
runner python scripts/run_proxy_generation_shards.py \
  --catalog datasets/proxy_gold/scenario_catalog.v1.json \
  --generation-out-root /work/generation \
  --controller-out-root /work/controllers \
  --run-prefix "eval-qwen14b-$BATCH_ID" \
  --intended-use evaluation \
  --provider local_openai \
  --model-manifest-sha256 sha256:bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8 \
  --target-counts --candidate-buffer-factor 2.0 --oversample-factor 2.5 \
  --max-quality-retries 2 --dry-run

runner python scripts/run_proxy_generation_shards.py \
  --catalog datasets/proxy_gold/training_scenario_catalog.v1.json \
  --generation-out-root /work/generation \
  --controller-out-root /work/controllers \
  --run-prefix "train-qwen14b-$BATCH_ID" \
  --intended-use training \
  --provider local_openai \
  --model-manifest-sha256 sha256:bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8 \
  --target-counts --candidate-buffer-factor 2.0 --oversample-factor 2.5 \
  --max-quality-retries 2 --dry-run
```

The evaluation contract has a 1,000-record base target, a 2,000-candidate
pre-judge buffer, and 2,510 planned attempts.  The training contract has a
2,700-record synthetic base target (750 TS, 750 S1, 750 S2, 450 S3), a
5,400-candidate pre-judge buffer, and 6,750 planned attempts.  The separate 300
public-real S3 records bring the final training pool to 3,000; they are never
generated by this runner. These `candidate-buffer-factor=2.0` and
`oversample-factor=2.5` settings are the initial production pass, not a reason
to change the final quota. After judging, use the positive
`gold_shortfall_by_scenario` and `gold_shortfall_by_factor_profile` entries to
run only the deficient scenario/profile through the direct build and judge
CLIs under new run IDs. Stop only when both maps are empty. Never lower the
quality gate, reduce an exact grade/scenario/profile quota, or silently replace
a missing cell with another family/shape.

A targeted production top-up has this form. Candidate buffer 2.0 accounts for
the roughly 50% judge yield; it does not change the final exact quota. Every
retry must increment `TOPUP_ROUND`: changing only the scenario count while
reusing a run ID/namespace is forbidden by the immutable run contract.

```sh
topup_cell() {
  scope="$1"          # exactly eval or train
  scenario_id="$2"
  factor_profile_id="$3"
  shortfall="$4"
  topup_round="$5"
  case "$scope" in
    eval)
      catalog=datasets/proxy_gold/scenario_catalog.v1.json
      intended_use=evaluation
      ;;
    train)
      catalog=datasets/proxy_gold/training_scenario_catalog.v1.json
      intended_use=training
      ;;
    *) return 2 ;;
  esac
  test -n "$scenario_id" -a -n "$factor_profile_id" || return 2
  case "$shortfall" in ''|*[!0-9]*|0) return 2 ;; esac
  case "$topup_round" in ''|*[!0-9]*|0) return 2 ;; esac
  cell_key="$(printf '%s\n%s\n' "$scenario_id" "$factor_profile_id" | sha256sum | cut -c1-12)"
  generation_run="${scope}-topup-${cell_key}-r${topup_round}-q14-${BATCH_ID}"
  judge_run="${scope}-topup-${cell_key}-r${topup_round}-g12-${BATCH_ID}"

  runner python scripts/build_proxy_scenarios.py \
    --catalog "$catalog" \
    --out-root /work/generation \
    --run-id "$generation_run" \
    --generation-namespace "$generation_run" \
    --provider local_openai \
    --model-manifest-sha256 sha256:bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8 \
    --scenario "$scenario_id" --factor-profile "$factor_profile_id" \
    --per-scenario "$shortfall" \
    --candidate-buffer-factor 2.0 --oversample-factor 2.5 \
    --max-quality-retries 2
  test -f "$PROXY_RUN_ROOT/generation/$generation_run/COMPLETE.json"

  judge_direct \
    "/work/generation/$generation_run/candidates.jsonl" \
    "$judge_run" \
    "$intended_use"
  test -f "$PROXY_RUN_ROOT/judging/$judge_run/COMPLETE.json"
}

# Examples; use the exact positive scenario/profile deficit from stats.json.
topup_cell eval EVAL_SCENARIO_ID EVAL_FACTOR_PROFILE_ID EVAL_SHORTFALL 1
topup_cell train TRAIN_SCENARIO_ID TRAIN_FACTOR_PROFILE_ID TRAIN_SHORTFALL 1
```

Every initial or top-up generation run has a unique generation namespace. The
ten-shard controller fixes each namespace to that shard's run ID; a direct run
derives it from `--run-id`, or accepts the same value explicitly through
`--generation-namespace`. Never reuse a namespace for a new batch. This makes
document IDs and resume keys disjoint across top-ups while deliberately keeping
the semantic family ID stable, so family-based splitting still blocks related
documents from leaking across partitions.

A fresh prefix refuses every existing shard. On an explicit
`--resume-controller`, a completed generation child is skipped only after full
revalidation. An incomplete regular child is resumed in place through the
builder's exact `--resume-run` journal contract and the controller-recorded
arguments; it is never treated as complete merely because its directory exists.
A symlink, malformed child, or journal/contract mismatch fails closed while
later shards continue. A controller that already has `COMPLETE.json` is
immutable; start targeted direct top-up runs under new run IDs and namespaces.

## Ten-shard judging controller contract

Judge only a successfully committed ten-shard generation controller. The
generation controller directory and its generation output root are separate
inputs and both are rebound to their recorded hashes and paths. Evaluation and
training are separate contracts: the `--intended-use` value must match the
generation controller's catalog split, so never point an `evaluation` judge at
a training generation controller or vice versa.

The following function uses the pinned Gemma 3 12B manifest, disables the
shadow judge, and fixes the consensus parameters to k=2..3, temperature 0.6,
and minimum self-consistency 0.67. It uses the `runner` function defined above.
The first argument is the completed generation prefix, the second is a new
judging prefix, and the third is exactly `evaluation` or `training`.

```sh
judge_shards() {
  generation_prefix="$1"
  judging_prefix="$2"
  intended_use="$3"
  shift 3

  runner python scripts/run_proxy_judging_shards.py \
    --generation-controller "/work/controllers/$generation_prefix" \
    --generation-out-root /work/generation \
    --judging-out-root /work/judging \
    --controller-out-root /work/judging-controllers \
    --run-prefix "$judging_prefix" \
    --intended-use "$intended_use" \
    --base-url http://ollama:11434/v1 \
    --judge-model gemma3:12b \
    --judge-model-manifest-sha256 sha256:f4031aab637d1ffa37b42570452ae0e4fad0314754d17ded67322e4b95836f8a \
    --no-shadow \
    --k-min 2 --k-max 3 \
    --temperature 0.6 \
    --min-self-consistency 0.67 \
    "$@"
}
```

Run dry-run first. It reopens the committed generation controller and all ten
generation envelopes, validates their paths, hashes, counts, model provenance,
and intended-use contract, and prints all ten child commands without creating
judging output directories.

```sh
# Frozen 1,000-record evaluation candidate controller
judge_shards \
  "eval-qwen14b-$BATCH_ID" \
  "eval-gemma12b-$BATCH_ID" \
  evaluation \
  --dry-run

# Train-only 2,700-record synthetic candidate controller
judge_shards \
  "train-qwen14b-$BATCH_ID" \
  "train-gemma12b-$BATCH_ID" \
  training \
  --dry-run
```

Execute by removing only `--dry-run` and keeping every other argument and
prefix identical:

```sh
if judge_shards \
  "eval-qwen14b-$BATCH_ID" \
  "eval-gemma12b-$BATCH_ID" \
  evaluation; then
  EVAL_JUDGE_RC=0
else
  EVAL_JUDGE_RC=$?
fi

test "$EVAL_JUDGE_RC" -eq 0 -o "$EVAL_JUDGE_RC" -eq 1
test -f "$PROXY_RUN_ROOT/judging-controllers/eval-gemma12b-$BATCH_ID/COMPLETE.json"
runner python -c 'import json,sys; from pathlib import Path; s=json.loads(Path(sys.argv[1]).read_text()); print(json.dumps({"target_met": s["target_met"], "by_scenario": s["gold_shortfall_by_scenario"], "by_profile": s["gold_shortfall_by_factor_profile"]}, ensure_ascii=False, sort_keys=True))' "/work/judging-controllers/eval-gemma12b-$BATCH_ID/stats.json"
```

Only an interrupted controller without `COMPLETE.json` can be resumed. Repeat
the exact original arguments and add the explicit controller path; completed
shards are skipped only after their full judge envelope is revalidated. The
judge child CLI has no partial-run resume contract, so the controller never
overwrites an incomplete judge directory. It preserves that directory and uses
a deterministic recovery run ID bound to the original shard and controller
contract. A later resume fully verifies and skips a committed recovery; if a
recovery is also incomplete, it too is preserved and the next deterministic
recovery ID is used. Supply only the controller's effective committed shard
files to the assembler; never pass an abandoned partial judge artifact.

```sh
judge_shards \
  "eval-qwen14b-$BATCH_ID" \
  "eval-gemma12b-$BATCH_ID" \
  evaluation \
  --resume-controller "/work/judging-controllers/eval-gemma12b-$BATCH_ID"
```

Judging is deliberately sequential (`concurrency=1`); do not add parallel
workers on the single-GPU runtime. With the initial 2.0 candidate buffer, all
shards may finish and commit a controller `COMPLETE.json` while scenario/profile
cells remain short. In that expected top-up state the controller exits 1,
publishes `target_met=false`, and records positive
`gold_shortfall_by_scenario` / `gold_shortfall_by_factor_profile` maps. Do not
let `set -e` discard those committed diagnostics:

```sh
if judge_shards \
  "train-qwen14b-$BATCH_ID" \
  "train-gemma12b-$BATCH_ID" \
  training; then
  JUDGE_RC=0
else
  JUDGE_RC=$?
fi

test "$JUDGE_RC" -eq 0 -o "$JUDGE_RC" -eq 1
test -f "$PROXY_RUN_ROOT/judging-controllers/train-gemma12b-$BATCH_ID/COMPLETE.json"
runner python -c 'import json,sys; from pathlib import Path; s=json.loads(Path(sys.argv[1]).read_text()); print(json.dumps({"target_met": s["target_met"], "by_scenario": s["gold_shortfall_by_scenario"], "by_profile": s["gold_shortfall_by_factor_profile"]}, ensure_ascii=False, sort_keys=True))' "/work/judging-controllers/train-gemma12b-$BATCH_ID/stats.json"
```

An exit code other than 0/1 or a missing/invalid controller `COMPLETE.json` is a
hard failure. For committed exit 1, use new direct generation and judging run
IDs for only the positive cells. Never resume a completed controller. Read the
controller's effective committed shard paths from its `stats.json`; this is
required because a recovered judge run has a deterministic recovery ID rather
than the original prefix. Never use a broad recovery glob or an abandoned
partial directory.

The current public training artifact is mounted read-only from
`/home/kopia/proxy_gold_runtime/artifacts/public_s3_training/public-s3-train-300-20260808-v3`.
Upload public v1 and blind v2 as separate immutable directories under
`/home/kopia/proxy_gold_runtime/artifacts/public_s3_eval` before assembly. Blind
v2 is used here only as an envelope/hash and leakage-block boundary: do not open
its document body or run evaluation/tuning against it until the final model and
operating point are locked.

First freeze the exact evaluation 1,000. The controller extractor below accepts
only the four successful status values, requires exactly ten unique committed
shards, and maps their container paths back to the isolated host run root. Add
only this batch's explicitly named direct top-ups.

```sh
export EVAL_JUDGE_PREFIX="eval-gemma12b-$BATCH_ID"
export EVAL_CONTROLLER_HOST="$PROXY_RUN_ROOT/judging-controllers/$EVAL_JUDGE_PREFIX"
test -f "$EVAL_CONTROLLER_HOST/COMPLETE.json"

set --
for judged in $(python3 - "$EVAL_CONTROLLER_HOST/stats.json" <<'PY'
import json
import sys
from pathlib import PurePosixPath

stats = json.load(open(sys.argv[1], encoding="utf-8"))
successful = {"completed", "recovered_completed", "skipped_verified", "skipped_recovery_verified"}
paths = []
for row in stats["results"]:
    if row.get("status") not in successful:
        continue
    path = PurePosixPath(str(row["shard_dir"])) / "gold_candidate.jsonl"
    assert path.is_absolute() and path.parts[:3] == ("/", "work", "judging"), path
    paths.append(str(path))
assert len(paths) == 10 and len(set(paths)) == 10, paths
print("\n".join(paths))
PY
)
do
  judged_host="$PROXY_RUN_ROOT/${judged#/work/}"
  test -f "$judged_host"
  test -f "$(dirname "$judged_host")/COMPLETE.json"
  set -- "$@" --input "$judged"
done
for judged_host in "$PROXY_RUN_ROOT"/judging/eval-topup-*"$BATCH_ID"/gold_candidate.jsonl
do
  test -f "$judged_host" || continue
  test -f "$(dirname "$judged_host")/COMPLETE.json"
  judged_relative=${judged_host#"$PROXY_RUN_ROOT"/}
  set -- "$@" --input "/work/$judged_relative"
done
test "$#" -ge 20
set -- "$@" --input /proxy-artifacts/public_s3_eval/public-s3-300-20260808-v1/records.jsonl

export FROZEN_RUN_ID="proxy-gold-1000-$BATCH_ID"
export FROZEN_DIR_HOST="$PROXY_RUN_ROOT/frozen/$FROZEN_RUN_ID"
export FROZEN_PROXY_CONTAINER="/work/frozen/$FROZEN_RUN_ID/proxy_gold_1000.jsonl"
test ! -e "$FROZEN_DIR_HOST"
install -d -m 2770 "$PROXY_RUN_ROOT/frozen"
chgrp 1000 "$PROXY_RUN_ROOT/frozen"
install -d -m 2770 "$FROZEN_DIR_HOST"
chgrp 1000 "$FROZEN_DIR_HOST"
runner python scripts/assemble_proxy_gold.py \
  "$@" \
  --origin-profile public-s3-hybrid-v2 \
  --catalog datasets/proxy_gold/scenario_catalog.v1.json \
  --out "$FROZEN_PROXY_CONTAINER" \
  --report "/work/frozen/$FROZEN_RUN_ID/assembly.json"
runner python -c 'import hashlib,json,sys; from collections import Counter; from pathlib import Path; corpus=Path(sys.argv[1]); rows=[json.loads(x) for x in corpus.read_text(encoding="utf-8").splitlines() if x]; report=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")); assert len(rows)==1000; assert Counter(r["label"] for r in rows)=={"TS":200,"S1":250,"S2":250,"S3":300}; assert report["ready"] is True; assert report["artifact"]["records"]==1000; assert report["artifact"]["sha256"]==hashlib.sha256(corpus.read_bytes()).hexdigest(); print(report["artifact"])' "$FROZEN_PROXY_CONTAINER" "/work/frozen/$FROZEN_RUN_ID/assembly.json"
runner python -c 'import hashlib,sys; from pathlib import Path; files=[Path(x) for x in sys.argv[1:3]]; sums=Path(sys.argv[3]); lines=[f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in files]; f=sums.open("x", encoding="ascii", newline="\n"); f.writelines(lines); f.close(); [p.chmod(0o440) for p in [*files,sums]]' "$FROZEN_PROXY_CONTAINER" "/work/frozen/$FROZEN_RUN_ID/assembly.json" "/work/frozen/$FROZEN_RUN_ID/SHA256SUMS"
chmod 0550 "$FROZEN_DIR_HOST"
```

Now assemble the independent 2,700 synthetic training candidates with the 300
public-real S3 records. The frozen proxy path above is exclusion-only here.

```sh
export TRAIN_JUDGE_PREFIX="train-gemma12b-$BATCH_ID"
export TRAIN_CONTROLLER_HOST="$PROXY_RUN_ROOT/judging-controllers/$TRAIN_JUDGE_PREFIX"
test -f "$TRAIN_CONTROLLER_HOST/COMPLETE.json"

set --
for judged in $(python3 - "$TRAIN_CONTROLLER_HOST/stats.json" <<'PY'
import json
import sys
from pathlib import PurePosixPath

stats = json.load(open(sys.argv[1], encoding="utf-8"))
successful = {"completed", "recovered_completed", "skipped_verified", "skipped_recovery_verified"}
paths = []
for row in stats["results"]:
    if row.get("status") not in successful:
        continue
    path = PurePosixPath(str(row["shard_dir"])) / "gold_candidate.jsonl"
    assert path.is_absolute() and path.parts[:3] == ("/", "work", "judging"), path
    paths.append(str(path))
assert len(paths) == 10 and len(set(paths)) == 10, paths
print("\n".join(paths))
PY
)
do
  judged_host="$PROXY_RUN_ROOT/${judged#/work/}"
  test -f "$judged_host"
  test -f "$(dirname "$judged_host")/COMPLETE.json"
  set -- "$@" --input "$judged"
done
for judged_host in "$PROXY_RUN_ROOT"/judging/train-topup-*"$BATCH_ID"/gold_candidate.jsonl
do
  test -f "$judged_host" || continue
  test -f "$(dirname "$judged_host")/COMPLETE.json"
  judged_relative=${judged_host#"$PROXY_RUN_ROOT"/}
  set -- "$@" --input "/work/$judged_relative"
done
test "$#" -ge 20

export TRAINING_POOL_RUN_ID="proxy-training-pool-$BATCH_ID"
runner python scripts/assemble_proxy_training_pool.py \
  "$@" \
  --public-s3-training-artifact /proxy-artifacts/public_s3_training/public-s3-train-300-20260808-v3 \
  --frozen-primary "$FROZEN_PROXY_CONTAINER" \
  --blocked-public-holdout /proxy-artifacts/public_s3_eval/public-s3-300-20260808-v1 \
  --blocked-public-holdout /proxy-artifacts/public_s3_eval/public-s3-300-blind-20260808-v2 \
  --catalog datasets/proxy_gold/training_scenario_catalog.v1.json \
  --out-root /work/training-pool \
  --run-id "$TRAINING_POOL_RUN_ID"
test -f "$PROXY_RUN_ROOT/training-pool/$TRAINING_POOL_RUN_ID/COMPLETE"

export MATERIALIZED_RUN_ID="materialized-$BATCH_ID"
runner python scripts/materialize_proxy_training_set.py \
  --input "/work/training-pool/$TRAINING_POOL_RUN_ID/training_pool.jsonl" \
  --frozen-corpus "$FROZEN_PROXY_CONTAINER" \
  --blocked-corpus /proxy-artifacts/public_s3_eval/public-s3-300-20260808-v1 \
  --blocked-corpus /proxy-artifacts/public_s3_eval/public-s3-300-blind-20260808-v2 \
  --out-root /work/materialized \
  --run-id "$MATERIALIZED_RUN_ID"
test -f "$PROXY_RUN_ROOT/materialized/$MATERIALIZED_RUN_ID/COMPLETE"
runner python -c 'import json,sys; from pathlib import Path; root=Path(sys.argv[1]); manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8")); assert manifest["inputs"]["training"]["row_count"]==3000; assert set(manifest["artifacts"])=={"train_documents","validation_documents","calibration_documents","train_chunks"}; print({k:v["records"] for k,v in manifest["artifacts"].items()})' "/work/materialized/$MATERIALIZED_RUN_ID"
```

The assemblers, not shell file order, make the deterministic exact selections.
Do not lower any quality gate or quota to turn exit 1 into exit 0. The
materialized run above is the only training input accepted below.

Immediately before a controller `COMPLETE.json` commit it reopens and
revalidates all ten generation inputs and all ten judge envelopes. A changed,
partial, duplicated, missing, reordered, or hash-invalid artifact therefore
fails closed. A controller that already published `COMPLETE.json` is immutable;
use new direct top-up IDs rather than trying to resume it.

Judge/controller run directories are mode `2750`. Atomic temporary files and
final manifests, progress files, journals, JSONL buckets, logs, stats, and
COMPLETE markers are mode `0640`, allowing the runner owner and gid 1000
operators to audit them. These local envelopes use hashes but no signature or
MAC. Restrict write permission on completed generation, judging, and controller
directories: an actor who can rewrite every artifact and its hash can defeat
hash-only authenticity checks.

## Offline GPU training runner contract

Training is the only disposable Python runner that receives `--gpus all`.
Before it starts, stop only the isolated proxy Ollama service so it releases its
GPU allocation. Keep the deployed product API/worker running and confirm the
product live endpoint before and after training. Do not stop or restart a
product container.

The product's current baseline directory is a copy source only. Make a separate
isolated copy under the proxy runtime, mount that copy read-only, and require
the recorded tree hash before training. Never mount the live product model path
into the training container.

```sh
export PROXY_BASE_MODEL_SOURCE=/home/kopia/poc/artifacts/classifier_p1_v5_clean/v-fe4b386b
export PROXY_BASE_MODEL_ROOT=/home/kopia/proxy_gold_runtime/base-model
export PROXY_TRAIN_ROOT=/home/kopia/proxy_gold_runtime/runs/training
export EXPECTED_BASE_MODEL_TREE_SHA256=7ff4c78156002f857121e6ddd724d40c0d38a59d91b9489ab1af9ab1c4d02036

test -d "$PROXY_BASE_MODEL_SOURCE"
test ! -e "$PROXY_BASE_MODEL_ROOT/v-fe4b386b"
install -d -m 0750 "$PROXY_BASE_MODEL_ROOT"
install -d -m 2770 "$PROXY_TRAIN_ROOT"
chgrp 1000 "$PROXY_BASE_MODEL_ROOT" "$PROXY_TRAIN_ROOT"
chmod 2750 "$PROXY_BASE_MODEL_ROOT"
chmod 2770 "$PROXY_TRAIN_ROOT"
for writable_dir in home cache triton torchinductor hf-cache torch-cache \
  checkpoints finalized attestations comparisons public-s3-evaluations
do
  install -d -m 2770 "$PROXY_TRAIN_ROOT/$writable_dir"
  chgrp 1000 "$PROXY_TRAIN_ROOT/$writable_dir"
done
BASE_MODEL_STAGING="$(mktemp -d "$PROXY_BASE_MODEL_ROOT/.v-fe4b386b.XXXXXX")"
cp -a --reflink=auto "$PROXY_BASE_MODEL_SOURCE/." "$BASE_MODEL_STAGING/"
chgrp -R 1000 "$BASE_MODEL_STAGING"
chmod -R a-w,u+rX,g+rX,o-rwx "$BASE_MODEL_STAGING"
mv "$BASE_MODEL_STAGING" "$PROXY_BASE_MODEL_ROOT/v-fe4b386b"

docker compose \
  -f "$PROXY_RELEASE_ROOT/deploy/proxy_gold_runtime/compose.yaml" \
  stop ollama
test "$(docker inspect -f '{{.State.Running}}' koipa-proxy-ollama)" = false
curl --fail --silent --show-error http://localhost:8000/api/v1/healthz/live
nvidia-smi
```

The training container has no network and sets all Hugging Face offline flags.
Its base-model and materialized proxy run mounts are read-only; only the new
training output root is writable.

```sh
training_runner() {
  docker run --rm --init --pull never \
    --gpus all \
    --network none \
    --read-only --tmpfs /tmp:rw,nosuid,size=4g \
    --cap-drop ALL --security-opt no-new-privileges --pids-limit 512 \
    --user 1000:1000 \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e PYTHONPATH=/release/src:/release \
    -e HF_HUB_OFFLINE=1 \
    -e TRANSFORMERS_OFFLINE=1 \
    -e HF_DATASETS_OFFLINE=1 \
    -e HOME=/work/home \
    -e XDG_CACHE_HOME=/work/cache \
    -e HF_HOME=/work/hf-cache \
    -e TORCH_HOME=/work/torch-cache \
    -e TRITON_CACHE_DIR=/work/triton \
    -e TORCHINDUCTOR_CACHE_DIR=/work/torchinductor \
    -e EXPECTED_BASE_MODEL_TREE_SHA256="$EXPECTED_BASE_MODEL_TREE_SHA256" \
    -v "$PROXY_RELEASE_ROOT:/release:ro" \
    -v "$PROXY_BASE_MODEL_ROOT:/base-model:ro" \
    -v "$PROXY_RUN_ROOT:/proxy-runs:ro" \
    -v "$PROXY_ARTIFACT_ROOT:/proxy-artifacts:ro" \
    -v /home/kopia/poc/datasets/labeled_p1_v5_clean:/legacy-baseline:ro \
    -v "$PROXY_TRAIN_ROOT:/work:rw" \
    -w /release \
    "$PROXY_RUNNER_IMAGE" "$@"
}

training_runner python -c 'import os; from pathlib import Path; from koipa.proxy_model_comparison import hash_model_directory; expected=os.environ["EXPECTED_BASE_MODEL_TREE_SHA256"]; actual=hash_model_directory(Path("/base-model/v-fe4b386b"))["tree_sha256"]; assert actual == expected, (actual, expected); print(actual)'
training_runner python -c 'import json,torch,transformers,datasets,mlflow,accelerate,evaluate,sklearn,scipy; assert torch.cuda.is_available(); assert torch.cuda.device_count()==1; print(json.dumps({"torch":torch.__version__,"cuda_runtime":torch.version.cuda,"cuda_device":torch.cuda.get_device_name(0),"transformers":transformers.__version__,"datasets":datasets.__version__,"mlflow":mlflow.__version__,"accelerate":accelerate.__version__,"evaluate":evaluate.__version__,"sklearn":sklearn.__version__,"scipy":scipy.__version__},sort_keys=True))'

export LEGACY_ATTESTATION_CONTAINER=/work/attestations/baseline-v-fe4b386b.json
export LEGACY_ATTESTATION_HOST="$PROXY_TRAIN_ROOT/attestations/baseline-v-fe4b386b.json"
if test ! -e "$LEGACY_ATTESTATION_HOST" \
  -a ! -e "$LEGACY_ATTESTATION_HOST.COMPLETE"
then
  training_runner python scripts/attest_legacy_training_corpus.py \
    --train /legacy-baseline/train.jsonl \
    --validation /legacy-baseline/val.jsonl \
    --test /legacy-baseline/test.jsonl \
    --model-dir /base-model/v-fe4b386b \
    --historical-build-manifest /legacy-baseline/manifest.json \
    --output "$LEGACY_ATTESTATION_CONTAINER"
fi
test -f "$LEGACY_ATTESTATION_HOST"
test -f "$LEGACY_ATTESTATION_HOST.COMPLETE"

training_runner python scripts/p1_train_classifier.py \
  --mode full \
  --proxy-candidate-mode \
  --proxy-training-run-dir "/proxy-runs/materialized/$MATERIALIZED_RUN_ID" \
  --base-model /base-model/v-fe4b386b \
  --output-dir "/work/checkpoints/proxy-candidate-$BATCH_ID" \
  --no-mlflow
test -f "$PROXY_TRAIN_ROOT/checkpoints/proxy-candidate-$BATCH_ID/TRAINING_CANDIDATES_COMPLETE"
```

The checkpoint root is deliberately `proxy_checkpoint_candidates_only` and is
not deployable. Finalization selects an epoch only on the independent validation
documents, fits temperature and the escalation threshold only on the independent
calibration documents, and publishes the restricted deployment candidate. It
must not read the frozen 1,000 or either public challenge while selecting or
calibrating.

```sh
export FINALIZED_RUN_ID="proxy-finalized-$BATCH_ID"
training_runner python scripts/finalize_proxy_classifier.py \
  --training-run-dir "/proxy-runs/materialized/$MATERIALIZED_RUN_ID" \
  --checkpoint-root "/work/checkpoints/proxy-candidate-$BATCH_ID" \
  --out-root /work/finalized \
  --run-id "$FINALIZED_RUN_ID" \
  --batch-size 8 \
  --device cuda \
  --fnr-target 0.05
test -f "$PROXY_TRAIN_ROOT/finalized/$FINALIZED_RUN_ID/COMPLETE"
```

The existing baseline predates the proxy-training manifest contract. Therefore
the initial A/B is valid only in explicit `raw_model` mode with its immutable
legacy-corpus attestation. Do not use `bundle_operating_point` or reuse the
frozen/public records to tune temperature, thresholds, checkpoints, or labels.
Public v1 is the public-original S3 slice of the frozen corpus and is not an
independent challenge or tuning input. Blind v2 remains the only final public
S3 challenge after the model and operating point are locked.

```sh
export COMPARISON_RUN_ID="proxy-raw-compare-$BATCH_ID"
training_runner python scripts/compare_proxy_models.py \
  --frozen-corpus "/proxy-runs/frozen/$FROZEN_RUN_ID/proxy_gold_1000.jsonl" \
  --frozen-manifest "/proxy-runs/frozen/$FROZEN_RUN_ID/assembly.json" \
  --baseline-model-dir /base-model/v-fe4b386b \
  --candidate-model-dir "/work/finalized/$FINALIZED_RUN_ID" \
  --baseline-legacy-training-attestation "$LEGACY_ATTESTATION_CONTAINER" \
  --candidate-training-manifest "/proxy-runs/materialized/$MATERIALIZED_RUN_ID/manifest.json" \
  --public-s3-challenge /proxy-artifacts/public_s3_eval/public-s3-300-blind-20260808-v2/records.jsonl \
  --output-root /work/comparisons \
  --run-id "$COMPARISON_RUN_ID" \
  --batch-size 8 \
  --device cuda \
  --comparison-mode raw_model
test -f "$PROXY_TRAIN_ROOT/comparisons/$COMPARISON_RUN_ID/COMPLETE.json"

export PUBLIC_S3_EVAL_RUN_ID="public-s3-blind-candidate-$BATCH_ID"
training_runner python scripts/evaluate_public_s3_challenge.py \
  --challenge-records /proxy-artifacts/public_s3_eval/public-s3-300-blind-20260808-v2/records.jsonl \
  --model-dir "/work/finalized/$FINALIZED_RUN_ID" \
  --output-root /work/public-s3-evaluations \
  --run-id "$PUBLIC_S3_EVAL_RUN_ID" \
  --batch-size 8 \
  --device cuda
test -f "$PROXY_TRAIN_ROOT/public-s3-evaluations/$PUBLIC_S3_EVAL_RUN_ID/COMPLETE.json"
```

Blind v2 remains sealed: it is used only by the automated leakage blocker until
the model and operating point are locked. Do not display its records, evaluate
it, or use it for any tuning before that lock.

After finalization and comparison exit, record their committed hashes, confirm
`http://localhost:8000/api/v1/healthz/live` is still healthy, and only then
restart the isolated Ollama service if more generation/judging work remains.
The finalized artifact has role `proxy_deployment_candidate` but remains
`production_eligible=false` and
`customer_document_deployment_approved=false` until the later customer-document
and single-reviewer validation phase.
