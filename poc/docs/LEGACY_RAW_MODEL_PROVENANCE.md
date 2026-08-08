# Legacy Raw-Model Provenance

Historical models may be compared only in `raw_model` mode. Before comparison,
create a provenance record with:

```text
python scripts/attest_legacy_training_corpus.py \
  --train <train.jsonl> --validation <validation.jsonl> --test <test.jsonl> \
  --model-dir <historical-model-dir> \
  --historical-build-manifest <preserved-build-manifest.json> \
  --output <legacy-attestation.json>
```

The attestation re-reads and hashes all three corpus files, records normalized
text hashes, discloses any derived document/family identities, binds the model
directory tree hash, and binds the historical build-manifest bytes. Comparison
re-verifies all of these values against the supplied model directory.

This is historical operator provenance, not cryptographic training-execution
proof. It supports only frozen-proxy raw-model regression comparison. A
`bundle_operating_point` comparison always requires full, materialized
`proxy-training-run-v1` manifests for both models.
