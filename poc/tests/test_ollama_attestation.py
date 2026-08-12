"""Live Ollama model digest attestation contract."""

from __future__ import annotations

import hashlib
import json

import pytest

from koipa.ollama_attestation import (
    OllamaAttestationError,
    pending_ollama_model_attestation,
    validate_ollama_attestation,
    verify_ollama_model,
)


_DIGEST = "1" * 64


class _Response:
    status = 200

    def __init__(self, payload: object) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


def _open_with(payload: object, observed: list[str] | None = None):
    def opener(request, *, timeout: float):
        assert timeout > 0
        if observed is not None:
            observed.append(request.full_url)
        return _Response(payload)

    return opener


def test_live_attestation_converts_v1_to_tags_and_omits_raw_endpoint():
    observed: list[str] = []
    result = verify_ollama_model(
        base_url="http://ollama:11434/tenant/v1/",
        requested_model="qwen3:14b",
        expected_manifest_sha256=f"sha256:{_DIGEST}",
        urlopen=_open_with(
            {
                "models": [
                    {
                        "name": "qwen3:14b",
                        "model": "qwen3:14b",
                        "digest": _DIGEST,
                    }
                ]
            },
            observed,
        ),
    )

    assert observed == ["http://ollama:11434/tenant/api/tags"]
    assert result["status"] == "verified"
    assert result["endpoint_kind"] == "ollama_openai_compatible"
    assert result["requested_model"] == "qwen3:14b"
    assert result["canonical_model"] == result["resolved_model"] == "qwen3:14b"
    assert result["live_model_digest"] == f"sha256:{_DIGEST}"
    assert result["checked_at"]
    assert "url" not in " ".join(result).lower()
    assert validate_ollama_attestation(result) == result


def test_latest_alias_is_exact_but_near_prefix_is_not_accepted():
    payload = {
        "models": [
            {"name": "qwen3:latest", "model": "qwen3:latest", "digest": _DIGEST}
        ]
    }
    result = verify_ollama_model(
        base_url="http://127.0.0.1:11434/v1",
        requested_model="qwen3",
        expected_manifest_sha256=f"sha256:{_DIGEST}",
        urlopen=_open_with(payload),
    )
    assert result["canonical_model"] == "qwen3:latest"

    with pytest.raises(OllamaAttestationError, match="missing"):
        verify_ollama_model(
            base_url="http://127.0.0.1:11434/v1",
            requested_model="qwen",
            expected_manifest_sha256=f"sha256:{_DIGEST}",
            urlopen=_open_with(payload),
        )


def test_ambiguous_exact_alias_fails_closed():
    payload = {
        "models": [
            {"name": "qwen3", "model": "qwen3", "digest": _DIGEST},
            {"name": "qwen3:latest", "model": "qwen3:latest", "digest": _DIGEST},
        ]
    }
    with pytest.raises(OllamaAttestationError, match="ambiguous"):
        verify_ollama_model(
            base_url="http://localhost:11434/v1",
            requested_model="qwen3",
            expected_manifest_sha256=f"sha256:{_DIGEST}",
            urlopen=_open_with(payload),
        )


def test_serialized_attestation_cannot_rebind_requested_model_to_unrelated_name():
    result = verify_ollama_model(
        base_url="http://localhost:11434/v1",
        requested_model="qwen3:14b",
        expected_manifest_sha256=f"sha256:{_DIGEST}",
        urlopen=_open_with(
            {"models": [{"name": "qwen3:14b", "digest": _DIGEST}]}
        ),
    )
    result["requested_model"] = "gemma3:12b"
    core = {
        key: value
        for key, value in result.items()
        if key not in {"binding_sha256", "checked_at"}
    }
    result["binding_sha256"] = hashlib.sha256(
        json.dumps(
            core,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    with pytest.raises(OllamaAttestationError, match="resolved exact alias"):
        validate_ollama_attestation(result)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"models": []}, "missing"),
        (
            {"models": [{"name": "qwen3:14b", "digest": "bad"}]},
            "valid sha256",
        ),
        (
            {"models": [{"name": "qwen3:14b", "digest": "2" * 64}]},
            "does not match",
        ),
    ],
)
def test_missing_invalid_or_mismatched_live_model_fails(payload, message):
    with pytest.raises(OllamaAttestationError, match=message):
        verify_ollama_model(
            base_url="http://localhost:11434/v1",
            requested_model="qwen3:14b",
            expected_manifest_sha256=f"sha256:{_DIGEST}",
            urlopen=_open_with(payload),
        )


def test_unreachable_endpoint_fails_without_leaking_endpoint_details():
    def unavailable(*_args, **_kwargs):
        raise OSError("secret network detail")

    with pytest.raises(OllamaAttestationError) as raised:
        verify_ollama_model(
            base_url="http://ollama:11434/v1",
            requested_model="qwen3:14b",
            expected_manifest_sha256=f"sha256:{_DIGEST}",
            urlopen=unavailable,
        )
    assert "secret" not in str(raised.value)
    assert "http" not in str(raised.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://models.example.com/v1",
        "http://8.8.8.8:11434/v1",
        "http://com/v1",
        "http://user:top-secret@localhost:11434/v1",
        "http://localhost:11434/openai",
    ],
)
def test_external_credentialed_or_non_ollama_endpoint_is_rejected(base_url):
    with pytest.raises(OllamaAttestationError):
        pending_ollama_model_attestation(
            base_url=base_url,
            requested_model="qwen3:14b",
            expected_manifest_sha256=f"sha256:{_DIGEST}",
        )


def test_dry_run_attestation_is_syntax_only_and_visibly_pending():
    pending = pending_ollama_model_attestation(
        base_url="http://host.docker.internal:11434/v1",
        requested_model="qwen3:14b",
        expected_manifest_sha256=None,
    )
    assert pending["status"] == "pending_live_verification"
    assert pending["checked_at"] is None
    assert pending["live_model_digest"] is None
    assert pending["expected_model_digest"] is None
    assert validate_ollama_attestation(pending, require_verified=False) == pending
