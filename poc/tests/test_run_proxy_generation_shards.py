"""Sequential proxy-generation shard controller contracts."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from scripts import run_proxy_generation_shards as controller


_CATALOG = (
    Path(__file__).resolve().parents[1] / "datasets/proxy_gold/scenario_catalog.v1.json"
)
_TRAINING_CATALOG = (
    Path(__file__).resolve().parents[1]
    / "datasets/proxy_gold/training_scenario_catalog.v1.json"
)
_REVISION = "sha256:" + "1" * 64


def _model_attestation(*, live: bool = True) -> dict[str, object]:
    core: dict[str, object] = {
        "schema_version": "ollama-model-attestation-v1",
        "status": "verified" if live else "pending_live_verification",
        "endpoint_kind": "ollama_openai_compatible",
        "endpoint_identity_sha256": "7" * 64,
        "requested_model": "qwen3:14b",
        "canonical_model": "qwen3:14b" if live else None,
        "resolved_model": "qwen3:14b" if live else None,
        "live_model_digest": _REVISION if live else None,
        "expected_model_digest": _REVISION,
    }
    return {
        **core,
        "checked_at": "2026-08-08T00:00:00+00:00" if live else None,
        "binding_sha256": hashlib.sha256(
            json.dumps(
                core,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


@pytest.fixture(autouse=True)
def _stub_runtime_model_attestation(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        controller,
        "_runtime_model_attestation",
        lambda *, provider, model_manifest_sha256, live: _model_attestation(
            live=live
        ),
    )


def _kwargs(tmp_path: Path, *, prefix: str) -> dict:
    return {
        "catalog_path": _CATALOG,
        "generation_out_root": tmp_path / "generation",
        "controller_out_root": tmp_path / "controllers",
        "run_prefix": prefix,
        "intended_use": "evaluation",
        "provider": "local_openai",
        "model_manifest_sha256": _REVISION,
        "candidate_buffer_factor": 1.0,
        "oversample_factor": 1.0,
        "max_quality_retries": 2,
    }


def _verified(spec: controller.ShardSpec) -> dict[str, object]:
    return {
        "generation_run_contract_sha256": f"contract-{spec.index}",
        "generation_namespace": spec.generation_namespace,
        "candidates_sha256": f"candidates-{spec.index}",
        "rejected_sha256": f"rejected-{spec.index}",
        "stats_sha256": f"stats-{spec.index}",
        "candidate_count": 100,
        "candidate_by_factor_profile": spec.selection_targets_by_factor_profile,
        "rejected_count": 0,
        "target_met": True,
    }


def _install_fake_verifier(monkeypatch: pytest.MonkeyPatch, seen: list[int]) -> None:
    def verify(shard_dir, *, spec, contract):
        del shard_dir, contract
        seen.append(spec.index)
        return _verified(spec)

    monkeypatch.setattr(controller, "_verify_completed_shard", verify)


def _fake_runner(
    calls: list[list[str]],
    *,
    fail_index: int | None = None,
    interrupt_index: int | None = None,
):
    def run(command, **kwargs):
        assert kwargs == {
            "cwd": str(controller._POC),
            "capture_output": True,
            "text": True,
            "check": False,
        }
        command = list(command)
        calls.append(command)
        assert "--target-counts" in command
        assert "--allow-partial" not in command
        index = int(command[command.index("--shard-index") + 1])
        if index == interrupt_index:
            raise KeyboardInterrupt
        if index == fail_index:
            return subprocess.CompletedProcess(
                command, 7, stdout="failed output", stderr="generation failed"
            )
        out_root = Path(command[command.index("--out-root") + 1])
        if "--resume-run" in command:
            resume_dir = Path(command[command.index("--resume-run") + 1])
            assert resume_dir.is_absolute()
            assert resume_dir.parent == out_root.resolve()
            assert resume_dir.is_dir()
            completed_dir = resume_dir
        else:
            run_id = command[command.index("--run-id") + 1]
            completed_dir = out_root / run_id
            completed_dir.mkdir(parents=True)
        (completed_dir / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    return run


def _shard_indices(calls: list[list[str]]) -> list[int]:
    return [int(call[call.index("--shard-index") + 1]) for call in calls]


def test_controller_runs_ten_shards_sequentially_and_commits_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[list[str]] = []
    verified: list[int] = []
    _install_fake_verifier(monkeypatch, verified)

    run_dir, stats, exit_code = controller.run_controller(
        **_kwargs(tmp_path, prefix="eval-v1"),
        subprocess_runner=_fake_runner(calls),
    )

    assert exit_code == 0
    assert _shard_indices(calls) == list(range(10))
    assert verified == list(range(10))
    assert stats["successful_shards"] == 10
    assert stats["failed_shards"] == 0
    assert stats["base_final_target_total"] == 1000
    assert stats["prejudge_candidate_target_total"] == 1000
    assert stats["verified_candidate_count"] == 1000
    assert len(stats["base_final_target_by_factor_profile"]) == 21
    assert sum(stats["base_final_target_by_factor_profile"].values()) == 1000
    assert (
        stats["verified_candidate_by_factor_profile"]
        == stats["prejudge_candidate_target_by_factor_profile"]
    )
    assert stats["target_met"] is True
    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert complete["target_met"] is True
    assert complete["exit_code"] == 0
    assert complete["manifest_sha256"] == controller._sha256_file(
        run_dir / "manifest.json"
    )
    assert complete["stats_sha256"] == controller._sha256_file(run_dir / "stats.json")
    assert complete["progress_sha256"] == controller._sha256_file(
        run_dir / "progress.json"
    )
    assert manifest["target_counts"] is True
    assert manifest["intended_use"] == "evaluation"
    assert manifest["catalog_split_role"] == "frozen_proxy_eval_only"
    assert manifest["shard_count"] == 10
    assert manifest["runtime_model_attestation"]["status"] == "verified"
    assert (
        manifest["runtime_model_attestation"]["binding_sha256"]
        == manifest["model_runtime_attestation_sha256"]
        == complete["model_runtime_attestation_sha256"]
    )
    assert (
        complete["runtime_model_attestation"]["binding_sha256"]
        == manifest["model_runtime_attestation_sha256"]
    )
    assert [row["base_final_targets"] for row in manifest["shards"]] == [
        {"S1": 25, "S2": 25, "S3": 30, "TS": 20}
    ] * 10
    assert not list(run_dir.glob(".*.tmp*"))
    if os.name != "nt":
        assert stat.S_IMODE(run_dir.stat().st_mode) == 0o2750
        assert stat.S_IMODE((run_dir / "logs").stat().st_mode) == 0o2750
        for artifact in (
            "manifest.json",
            "progress.json",
            "stats.json",
            "COMPLETE.json",
        ):
            assert stat.S_IMODE((run_dir / artifact).stat().st_mode) == 0o640


def test_one_failed_subprocess_does_not_block_later_shards_but_is_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls: list[list[str]] = []
    verified: list[int] = []
    _install_fake_verifier(monkeypatch, verified)

    run_dir, stats, exit_code = controller.run_controller(
        **_kwargs(tmp_path, prefix="continue-after-failure"),
        subprocess_runner=_fake_runner(calls, fail_index=3),
    )

    assert _shard_indices(calls) == list(range(10))
    assert verified == [0, 1, 2, 4, 5, 6, 7, 8, 9]
    assert exit_code == 1
    assert stats["successful_shards"] == 9
    assert stats["failed_shards"] == 1
    assert stats["results"][3]["returncode"] == 7
    assert stats["results"][3]["status"] == "failed"
    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    assert complete["target_met"] is False
    assert complete["exit_code"] == 1


def test_live_model_attestation_failure_prevents_any_controller_or_shard_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    kwargs = _kwargs(tmp_path, prefix="attestation-block")

    def fail(**_kwargs):
        raise controller.ProxyShardControllerError(
            "generation runtime model attestation failed: digest mismatch"
        )

    monkeypatch.setattr(controller, "_runtime_model_attestation", fail)
    with pytest.raises(
        controller.ProxyShardControllerError, match="digest mismatch"
    ):
        controller.run_controller(
            **kwargs,
            subprocess_runner=_fake_runner([]),
        )

    assert not kwargs["generation_out_root"].exists()
    assert not kwargs["controller_out_root"].exists()


def test_explicit_resume_skips_only_verified_completed_shards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    first_calls: list[list[str]] = []
    first_verified: list[int] = []
    _install_fake_verifier(monkeypatch, first_verified)
    kwargs = _kwargs(tmp_path, prefix="resume-after-stop")

    run_dir, interrupted, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner(first_calls, interrupt_index=1),
    )
    assert exit_code == 130
    assert interrupted["status"] == "interrupted"
    assert _shard_indices(first_calls) == [0, 1]
    assert not (run_dir / "COMPLETE.json").exists()
    interrupted_progress = json.loads(
        (run_dir / "progress.json").read_text(encoding="utf-8")
    )
    assert interrupted_progress["active_shard_index"] == 1
    incomplete_shard = kwargs["generation_out_root"] / "resume-after-stop-s01"
    incomplete_shard.mkdir()

    resume_calls: list[list[str]] = []
    resume_verified: list[int] = []
    _install_fake_verifier(monkeypatch, resume_verified)
    resumed_dir, stats, exit_code = controller.run_controller(
        **kwargs,
        resume_controller=run_dir,
        subprocess_runner=_fake_runner(resume_calls),
    )

    assert resumed_dir == run_dir
    assert exit_code == 0
    assert _shard_indices(resume_calls) == list(range(1, 10))
    assert resume_verified == list(range(10))
    assert stats["skipped_verified_shards"] == 1
    assert stats["resumed_completed_shards"] == 1
    assert stats["launched_shards"] == 9
    assert stats["results"][0]["status"] == "skipped_verified"
    assert stats["results"][1]["status"] == "resumed_completed"
    assert "--resume-run" in stats["results"][1]["command"]
    assert Path(
        stats["results"][1]["command"][
            stats["results"][1]["command"].index("--resume-run") + 1
        ]
    ) == incomplete_shard.resolve()
    resumed_manifest = json.loads(
        (resumed_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(resumed_manifest["runtime_model_attestation_revalidations"]) == 1
    assert (
        resumed_manifest["runtime_model_attestation_revalidations"][0][
            "binding_sha256"
        ]
        == resumed_manifest["model_runtime_attestation_sha256"]
    )


def test_existing_shard_is_never_skipped_without_explicit_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    kwargs = _kwargs(tmp_path, prefix="fresh-collision")
    existing = kwargs["generation_out_root"] / "fresh-collision-s00"
    existing.mkdir(parents=True)
    calls: list[list[str]] = []
    verified: list[int] = []
    _install_fake_verifier(monkeypatch, verified)

    _, stats, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner(calls),
    )

    assert exit_code == 1
    assert _shard_indices(calls) == list(range(1, 10))
    assert verified == list(range(1, 10))
    assert stats["results"][0]["failure"] == ("existing_shard_requires_explicit_resume")


def test_incomplete_existing_shard_fails_resume_and_remaining_shards_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    kwargs = _kwargs(tmp_path, prefix="resume-invalid")
    first_calls: list[list[str]] = []
    _install_fake_verifier(monkeypatch, [])
    run_dir, _, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner(first_calls, interrupt_index=1),
    )
    assert exit_code == 130
    (kwargs["generation_out_root"] / "resume-invalid-s01").mkdir()

    verified: list[int] = []

    def verify(shard_dir, *, spec, contract):
        del shard_dir, contract
        verified.append(spec.index)
        if spec.index == 1:
            raise controller.ProxyShardControllerError("tampered or incomplete shard")
        return _verified(spec)

    monkeypatch.setattr(controller, "_verify_completed_shard", verify)
    resume_calls: list[list[str]] = []
    _, stats, exit_code = controller.run_controller(
        **kwargs,
        resume_controller=run_dir,
        subprocess_runner=_fake_runner(resume_calls),
    )

    assert exit_code == 1
    assert verified[:2] == [0, 1]
    assert _shard_indices(resume_calls) == list(range(1, 10))
    assert "--resume-run" in resume_calls[0]
    assert stats["results"][1]["status"] == "failed"
    assert "tampered or incomplete" in stats["results"][1]["failure"]
    assert stats["failed_shards"] == 1


def test_resume_rejects_tampered_controller_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    kwargs = _kwargs(tmp_path, prefix="controller-tamper")
    _install_fake_verifier(monkeypatch, [])
    run_dir, _, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner([], interrupt_index=0),
    )
    assert exit_code == 130
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provider"] = "tampered-provider"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        controller.ProxyShardControllerError,
        match="manifest contract fields mismatch",
    ):
        controller.run_controller(
            **kwargs,
            resume_controller=run_dir,
            subprocess_runner=_fake_runner([]),
        )


def test_generation_controller_v2_cannot_resume_as_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    kwargs = _kwargs(tmp_path, prefix="legacy-controller")
    _install_fake_verifier(monkeypatch, [])
    run_dir, _, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner([], interrupt_index=0),
    )
    assert exit_code == 130
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "proxy-generation-shard-controller-v2"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        controller.ProxyShardControllerError, match="schema version mismatch"
    ):
        controller.run_controller(
            **kwargs,
            resume_controller=run_dir,
            subprocess_runner=_fake_runner([]),
        )


def test_shard_verifier_binds_controller_fields_and_target_met(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    kwargs = _kwargs(tmp_path, prefix="verify-fields")
    catalog_version, catalog_sha256, specs = controller._build_shard_specs(
        catalog_path=_CATALOG,
        run_prefix=kwargs["run_prefix"],
        intended_use="evaluation",
        candidate_buffer_factor=1.0,
        oversample_factor=1.0,
    )
    contract = controller._controller_contract(
        run_prefix=kwargs["run_prefix"],
        intended_use="evaluation",
        catalog_path=_CATALOG,
        catalog_version=catalog_version,
        catalog_sha256=catalog_sha256,
        generation_out_root=kwargs["generation_out_root"],
        provider=kwargs["provider"],
        model_manifest_sha256=kwargs["model_manifest_sha256"],
        runtime_model_attestation=_model_attestation(),
        candidate_buffer_factor=1.0,
        oversample_factor=1.0,
        max_quality_retries=kwargs["max_quality_retries"],
        specs=specs,
    )
    spec = specs[0]
    shard_dir = tmp_path / "shard"
    shard_dir.mkdir()
    manifest = {
        "run_id": spec.run_id,
        "generation_namespace": spec.generation_namespace,
        "catalog_version": contract["catalog_version"],
        "catalog_sha256": contract["catalog_sha256"],
        "runner_code_sha256": contract["builder_code_sha256"],
        "generator_code_sha256": contract["generator_code_sha256"],
        "selection_targets": spec.selection_targets,
        "selection_targets_by_scenario": spec.selection_targets_by_scenario,
        "base_final_targets": spec.base_final_targets,
        "base_final_targets_by_scenario": spec.base_final_targets_by_scenario,
        "partition": spec.partition,
        "plan_sha256": spec.plan_sha256,
        "max_quality_retries": contract["max_quality_retries"],
        "provider": {
            "requested": contract["provider"],
            "model": contract["model_requested_name"],
            "revision": "sha256:" + "2" * 64,
            "endpoint_identity_sha256": contract[
                "model_endpoint_identity_sha256"
            ],
            "model_attestation_binding_sha256": contract[
                "model_runtime_attestation_sha256"
            ],
        },
    }
    (shard_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (shard_dir / "COMPLETE.json").write_text(
        json.dumps({"target_met": True}), encoding="utf-8"
    )
    attestation = {
        "generation_run_contract_sha256": "a" * 64,
        "generation_namespace": spec.generation_namespace,
        "candidates_sha256": "b" * 64,
        "rejected_sha256": "c" * 64,
        "stats_sha256": "d" * 64,
        "input_count": 100,
        "rejected_count": 0,
        "candidate_by_factor_profile": spec.selection_targets_by_factor_profile,
        "selection_target_by_factor_profile": (
            spec.selection_targets_by_factor_profile
        ),
        "base_final_target_by_factor_profile": (
            spec.base_final_targets_by_factor_profile
        ),
        "factor_profile_by_scenario": spec.factor_profile_by_scenario,
        "generation_model_attestation_binding_sha256": contract[
            "model_runtime_attestation_sha256"
        ],
    }
    monkeypatch.setattr(
        controller,
        "attest_generation_input",
        lambda *args, **kwargs: dict(attestation),
    )

    with pytest.raises(controller.ProxyShardControllerError, match="provider.revision"):
        controller._verify_completed_shard(shard_dir, spec=spec, contract=contract)

    manifest["provider"]["revision"] = contract["model_manifest_sha256"]
    (shard_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    tampered_profiles = dict(spec.selection_targets_by_factor_profile)
    first_profile = next(iter(tampered_profiles))
    tampered_profiles[first_profile] += 1
    attestation["candidate_by_factor_profile"] = tampered_profiles
    with pytest.raises(
        controller.ProxyShardControllerError,
        match="attestation.candidate_by_factor_profile",
    ):
        controller._verify_completed_shard(shard_dir, spec=spec, contract=contract)
    attestation["candidate_by_factor_profile"] = (
        spec.selection_targets_by_factor_profile
    )
    changed_runtime = _model_attestation()
    changed_runtime["binding_sha256"] = "0" * 64
    monkeypatch.setattr(
        controller,
        "_runtime_model_attestation",
        lambda **_kwargs: changed_runtime,
    )
    with pytest.raises(
        controller.ProxyShardControllerError,
        match="live generation model binding changed",
    ):
        controller._verify_completed_shard(shard_dir, spec=spec, contract=contract)
    monkeypatch.setattr(
        controller,
        "_runtime_model_attestation",
        lambda **_kwargs: _model_attestation(),
    )
    (shard_dir / "COMPLETE.json").write_text(
        json.dumps({"target_met": False}), encoding="utf-8"
    )
    with pytest.raises(controller.ProxyShardControllerError, match="target_met"):
        controller._verify_completed_shard(shard_dir, spec=spec, contract=contract)


def test_training_profile_plans_exactly_2700_synthetic_across_ten_shards():
    _, _, specs = controller._build_shard_specs(
        catalog_path=_TRAINING_CATALOG,
        run_prefix="train-v1",
        intended_use="training",
        candidate_buffer_factor=2.0,
        oversample_factor=2.5,
    )

    assert len(specs) == 10
    assert sum(sum(spec.base_final_targets.values()) for spec in specs) == 2700
    assert sum(sum(spec.selection_targets.values()) for spec in specs) == 5400
    assert sum(spec.planned for spec in specs) == 6750
    assert sorted(sum(spec.base_final_targets.values()) for spec in specs) == [
        180,
        180,
        180,
        180,
        180,
        360,
        360,
        360,
        360,
        360,
    ]
    assert {
        grade: sum(spec.base_final_targets.get(grade, 0) for spec in specs)
        for grade in ("TS", "S1", "S2", "S3")
    } == {"TS": 750, "S1": 750, "S2": 750, "S3": 450}
    base_profiles = controller._sum_count_maps(
        [spec.base_final_targets_by_factor_profile for spec in specs]
    )
    selection_profiles = controller._sum_count_maps(
        [spec.selection_targets_by_factor_profile for spec in specs]
    )
    planned_profiles = controller._sum_count_maps(
        [spec.planned_by_factor_profile for spec in specs]
    )
    assert len(base_profiles) == len(selection_profiles) == len(planned_profiles) == 21
    assert sum(base_profiles.values()) == 2700
    assert sum(selection_profiles.values()) == 5400
    assert sum(planned_profiles.values()) == 6750
    assert base_profiles["s1-s2-v2-m0"] == 750
    assert base_profiles["ts-s2-v2-m1"] == 375
    assert base_profiles["ts-s2-v2-m2"] == 375
    assert base_profiles["s2-s1-v1-m1"] == 150


def test_controller_rejects_catalog_split_use_mismatch():
    with pytest.raises(
        controller.ProxyShardControllerError,
        match="requires catalog split_role",
    ):
        controller._build_shard_specs(
            catalog_path=_TRAINING_CATALOG,
            run_prefix="wrong-split",
            intended_use="evaluation",
            candidate_buffer_factor=1.0,
            oversample_factor=1.0,
        )


def test_controller_dry_run_validates_training_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    generation_root = tmp_path / "generation"
    controller_root = tmp_path / "controllers"
    result = controller.main(
        [
            "--catalog",
            str(_TRAINING_CATALOG),
            "--generation-out-root",
            str(generation_root),
            "--controller-out-root",
            str(controller_root),
            "--run-prefix",
            "train-dry-v1",
            "--intended-use",
            "training",
            "--provider",
            "local_openai",
            "--model-manifest-sha256",
            _REVISION,
            "--target-counts",
            "--candidate-buffer-factor",
            "2.0",
            "--oversample-factor",
            "2.5",
            "--dry-run",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert payload["dry_run"] is True
    assert payload["controller_contract"]["intended_use"] == "training"
    assert payload["controller_contract"]["expected_base_total"] == 2700
    assert (
        payload["runtime_model_attestation"]["status"]
        == "pending_live_verification"
    )
    assert all(
        len(shard["base_final_targets_by_factor_profile"]) == 21
        for shard in payload["controller_contract"]["shards"]
    )
    assert len(payload["commands"]) == 10
    assert not generation_root.exists()
    assert not controller_root.exists()


@pytest.mark.parametrize("forbidden", ["--allow-partial", None])
def test_cli_requires_target_counts_and_forbids_allow_partial(forbidden):
    argv = [
        "--run-prefix",
        "cli-contract",
        "--intended-use",
        "evaluation",
        "--provider",
        "local_openai",
        "--model-manifest-sha256",
        _REVISION,
    ]
    if forbidden:
        argv.extend(["--target-counts", forbidden])
    with pytest.raises(SystemExit) as exc:
        controller.main(argv)
    assert exc.value.code == 2
