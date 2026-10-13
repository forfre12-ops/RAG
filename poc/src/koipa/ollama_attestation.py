"""Fail-closed runtime attestation for an Ollama-hosted model.

The generation and judging pipelines use Ollama through its OpenAI-compatible
``/v1`` endpoint.  A configured model name is not enough to prove which model
blob is actually loaded, so this module resolves the name against Ollama's
local ``/api/tags`` inventory and binds the run to the inventory digest.

Raw endpoint URLs are deliberately never returned or included in exceptions.
Only a digest of a credential-free, normalized endpoint identity is exposed.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import SplitResult, urlsplit, urlunsplit


ATTESTATION_SCHEMA_VERSION = "ollama-model-attestation-v1"
_SHA256_REVISION_RE = re.compile(r"sha256:[0-9a-f]{64}")
_LIVE_DIGEST_RE = re.compile(r"(?:sha256:)?([0-9a-f]{64})")
_MAX_TAGS_RESPONSE_BYTES = 4 * 1024 * 1024


class OllamaAttestationError(ValueError):
    """The live Ollama endpoint or model digest could not be proven."""


def _canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalize_expected_digest(value: object, *, required: bool) -> str | None:
    digest = str(value or "").strip()
    if not digest:
        if required:
            raise OllamaAttestationError(
                "expected model manifest digest is required as sha256:<64 hex>"
            )
        return None
    if _SHA256_REVISION_RE.fullmatch(digest) is None:
        raise OllamaAttestationError(
            "expected model manifest digest must be sha256:<64 lowercase hex>"
        )
    return digest


def _normalize_model_name(value: object) -> str:
    raw_model = str(value or "")
    model = raw_model.strip()
    if raw_model != model:
        raise OllamaAttestationError(
            "requested Ollama model must not contain surrounding whitespace"
        )
    if not model or any(character.isspace() for character in model):
        raise OllamaAttestationError(
            "requested Ollama model must be a non-empty name without whitespace"
        )
    if any(character in model for character in "?#"):
        raise OllamaAttestationError("requested Ollama model name is invalid")
    return model


def _normalized_ollama_urls(base_url: object) -> tuple[str, str]:
    """Return credential-free endpoint identity material and the tags URL."""
    raw = str(base_url or "").strip()
    if not raw:
        raise OllamaAttestationError("Ollama OpenAI-compatible base URL is missing")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise OllamaAttestationError("Ollama base URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise OllamaAttestationError(
            "Ollama base URL must be an http(s) OpenAI-compatible endpoint"
        )
    if parsed.username is not None or parsed.password is not None:
        raise OllamaAttestationError("Ollama base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise OllamaAttestationError(
            "Ollama base URL must not contain a query string or fragment"
        )

    host = str(parsed.hostname).lower()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    local_hostname = (
        host == "localhost"
        or host.endswith(".localhost")
        or host in {"host.docker.internal", "gateway.docker.internal"}
        or host.endswith((".local", ".internal", ".svc", ".svc.cluster.local"))
        # Explicit Docker/container service-name forms.  Arbitrary dotless
        # names are not accepted because DNS search paths can resolve them
        # outside the intended local runtime.
        or host == "ollama"
        or host.startswith("ollama-")
        or host.endswith("-ollama")
    )
    local_address = bool(
        address
        and (address.is_loopback or address.is_private or address.is_link_local)
    )
    if not (local_hostname or local_address):
        raise OllamaAttestationError(
            "Ollama attestation endpoint must be local or private-network hosted"
        )

    path = (parsed.path or "").rstrip("/")
    if not (path == "/v1" or path.endswith("/v1")):
        raise OllamaAttestationError(
            "Ollama OpenAI-compatible base URL must end with /v1"
        )
    root_path = path[:-3].rstrip("/")
    tags_path = f"{root_path}/api/tags" if root_path else "/api/tags"

    rendered_host = f"[{host}]" if ":" in host else host
    netloc = f"{rendered_host}:{port}" if port is not None else rendered_host
    normalized_base = urlunsplit(
        SplitResult(parsed.scheme.lower(), netloc, path, "", "")
    )
    tags_url = urlunsplit(
        SplitResult(parsed.scheme.lower(), netloc, tags_path, "", "")
    )
    return normalized_base, tags_url


def _endpoint_identity_sha256(normalized_base_url: str) -> str:
    material = {
        "endpoint_kind": "ollama_openai_compatible",
        "normalized_base_url": normalized_base_url,
    }
    return _sha256_bytes(_canonical_json_bytes(material))


def _entry_aliases(entry: Mapping[str, object]) -> set[str]:
    aliases: set[str] = set()
    for field in ("name", "model"):
        value = entry.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        name = value.strip()
        if value != name:
            continue
        aliases.add(name)
        if name.endswith(":latest"):
            aliases.add(name[: -len(":latest")])
    return aliases


def _resolve_model_entry(
    payload: object, *, requested_model: str
) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise OllamaAttestationError("Ollama tags response must be a JSON object")
    models = payload.get("models")
    if not isinstance(models, list):
        raise OllamaAttestationError("Ollama tags response has no models list")
    matches = [
        entry
        for entry in models
        if isinstance(entry, Mapping) and requested_model in _entry_aliases(entry)
    ]
    if not matches:
        raise OllamaAttestationError(
            "requested model is missing from the live Ollama inventory"
        )
    if len(matches) != 1:
        raise OllamaAttestationError(
            "requested model alias is ambiguous in the live Ollama inventory"
        )
    return matches[0]


def _live_digest(entry: Mapping[str, object]) -> str:
    digest = str(entry.get("digest") or "").strip()
    match = _LIVE_DIGEST_RE.fullmatch(digest)
    if match is None:
        raise OllamaAttestationError(
            "resolved Ollama model has no valid sha256 inventory digest"
        )
    return f"sha256:{match.group(1)}"


def _attestation_binding(material: Mapping[str, object]) -> str:
    return _sha256_bytes(_canonical_json_bytes(material))


def _finalize_attestation(
    core: Mapping[str, object], *, checked_at: str | None
) -> dict[str, object]:
    return {
        **core,
        "checked_at": checked_at,
        "binding_sha256": _attestation_binding(core),
    }


def validate_ollama_attestation(
    value: object, *, require_verified: bool = True
) -> dict[str, object]:
    """Validate a serialized attestation and recompute its stable binding."""
    if not isinstance(value, Mapping):
        raise OllamaAttestationError("Ollama model attestation must be an object")
    required_fields = {
        "schema_version",
        "status",
        "endpoint_kind",
        "endpoint_identity_sha256",
        "requested_model",
        "canonical_model",
        "resolved_model",
        "live_model_digest",
        "expected_model_digest",
        "checked_at",
        "binding_sha256",
    }
    if set(value) != required_fields:
        raise OllamaAttestationError("Ollama model attestation fields are invalid")
    if value.get("schema_version") != ATTESTATION_SCHEMA_VERSION:
        raise OllamaAttestationError("Ollama model attestation schema is invalid")
    if value.get("endpoint_kind") != "ollama_openai_compatible":
        raise OllamaAttestationError("Ollama model attestation endpoint kind is invalid")
    endpoint_digest = str(value.get("endpoint_identity_sha256") or "")
    if re.fullmatch(r"[0-9a-f]{64}", endpoint_digest) is None:
        raise OllamaAttestationError("Ollama endpoint identity digest is invalid")
    requested = _normalize_model_name(value.get("requested_model"))
    status = value.get("status")
    if require_verified and status != "verified":
        raise OllamaAttestationError("live Ollama model attestation is not verified")
    if status == "verified":
        canonical = _normalize_model_name(value.get("canonical_model"))
        resolved = _normalize_model_name(value.get("resolved_model"))
        if requested not in _entry_aliases(
            {"name": canonical, "model": resolved}
        ):
            raise OllamaAttestationError(
                "attested requested model does not match the resolved exact alias"
            )
        live = _normalize_expected_digest(value.get("live_model_digest"), required=True)
        expected = _normalize_expected_digest(
            value.get("expected_model_digest"), required=True
        )
        if live != expected:
            raise OllamaAttestationError("attested Ollama model digests disagree")
        checked_at = value.get("checked_at")
        if not isinstance(checked_at, str):
            raise OllamaAttestationError("verified Ollama attestation time is missing")
        try:
            parsed_time = datetime.fromisoformat(checked_at)
        except ValueError as exc:
            raise OllamaAttestationError(
                "verified Ollama attestation time is invalid"
            ) from exc
        if parsed_time.tzinfo is None:
            raise OllamaAttestationError(
                "verified Ollama attestation time must include a timezone"
            )
    elif status == "pending_live_verification" and not require_verified:
        canonical = resolved = live = None
        expected = _normalize_expected_digest(
            value.get("expected_model_digest"), required=False
        )
        if any(
            value.get(field) is not None
            for field in (
                "canonical_model",
                "resolved_model",
                "live_model_digest",
                "checked_at",
            )
        ):
            raise OllamaAttestationError("pending Ollama attestation is invalid")
    else:
        raise OllamaAttestationError("Ollama model attestation status is invalid")

    core: dict[str, object] = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "status": status,
        "endpoint_kind": "ollama_openai_compatible",
        "endpoint_identity_sha256": endpoint_digest,
        "requested_model": requested,
        "canonical_model": canonical,
        "resolved_model": resolved,
        "live_model_digest": live,
        "expected_model_digest": expected,
    }
    if value.get("binding_sha256") != _attestation_binding(core):
        raise OllamaAttestationError("Ollama model attestation binding mismatch")
    return dict(value)


def pending_ollama_model_attestation(
    *,
    base_url: object,
    requested_model: str,
    expected_manifest_sha256: str | None,
) -> dict[str, object]:
    """Validate dry-run syntax without contacting the endpoint."""
    model = _normalize_model_name(requested_model)
    expected = _normalize_expected_digest(
        expected_manifest_sha256,
        required=False,
    )
    normalized_base, _ = _normalized_ollama_urls(base_url)
    core: dict[str, object] = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "status": "pending_live_verification",
        "endpoint_kind": "ollama_openai_compatible",
        "endpoint_identity_sha256": _endpoint_identity_sha256(normalized_base),
        "requested_model": model,
        "canonical_model": None,
        "resolved_model": None,
        "live_model_digest": None,
        "expected_model_digest": expected,
    }
    return _finalize_attestation(core, checked_at=None)


def verify_ollama_model(
    *,
    base_url: object,
    requested_model: str,
    expected_manifest_sha256: str,
    timeout_seconds: float = 5.0,
    urlopen: Callable[..., Any] | None = None,
) -> dict[str, object]:
    """Verify one exact, unambiguous Ollama model against its live digest."""
    model = _normalize_model_name(requested_model)
    expected = _normalize_expected_digest(expected_manifest_sha256, required=True)
    normalized_base, tags_url = _normalized_ollama_urls(base_url)
    if timeout_seconds <= 0:
        raise OllamaAttestationError("Ollama attestation timeout must be positive")

    request = urllib.request.Request(  # noqa: S310 - caller-pinned local endpoint
        tags_url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    opener = urlopen or urllib.request.urlopen
    try:
        response = opener(request, timeout=float(timeout_seconds))
        with response:
            status = int(getattr(response, "status", 200) or 200)
            if status != 200:
                raise OllamaAttestationError(
                    "Ollama model inventory returned a non-200 status"
                )
            body = response.read(_MAX_TAGS_RESPONSE_BYTES + 1)
    except OllamaAttestationError:
        raise
    except Exception as exc:  # noqa: BLE001 - sanitize endpoint/error details
        raise OllamaAttestationError(
            f"Ollama model inventory is unreachable ({type(exc).__name__})"
        ) from exc
    if len(body) > _MAX_TAGS_RESPONSE_BYTES:
        raise OllamaAttestationError("Ollama tags response exceeds the size limit")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OllamaAttestationError(
            "Ollama tags response is not valid UTF-8 JSON"
        ) from exc

    entry = _resolve_model_entry(payload, requested_model=model)
    live_digest = _live_digest(entry)
    if live_digest != expected:
        raise OllamaAttestationError(
            "live Ollama model digest does not match the expected model manifest"
        )
    canonical_model = str(entry.get("name") or entry.get("model") or "").strip()
    resolved_model = str(entry.get("model") or canonical_model).strip()
    if not canonical_model or not resolved_model:
        raise OllamaAttestationError("resolved Ollama model name is missing")

    core: dict[str, object] = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "status": "verified",
        "endpoint_kind": "ollama_openai_compatible",
        "endpoint_identity_sha256": _endpoint_identity_sha256(normalized_base),
        "requested_model": model,
        "canonical_model": canonical_model,
        "resolved_model": resolved_model,
        "live_model_digest": live_digest,
        "expected_model_digest": expected,
    }
    return _finalize_attestation(
        core,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


__all__ = [
    "ATTESTATION_SCHEMA_VERSION",
    "OllamaAttestationError",
    "pending_ollama_model_attestation",
    "validate_ollama_attestation",
    "verify_ollama_model",
]
