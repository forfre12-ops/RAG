"""Long-running proxy-judge shard controller safety contracts."""

from __future__ import annotations

from collections import Counter
import json
import os
import subprocess
from pathlib import Path

import pytest

from scripts import run_proxy_judging_shards as controller


_JUDGE_REVISION = "sha256:" + "2" * 64
_SHADOW_REVISION = "sha256:" + "3" * 64
_PROFILE_COUNTS = {
    f"profile-{index:02d}": 5 if index < 16 else 4 for index in range(21)
}
_PROFILE_BY_SCENARIO = {
    f"scenario-{index:02d}": profile
    for index, profile in enumerate(_PROFILE_COUNTS)
}
_GOLD_PROFILE_COUNTS = {
    profile: (3 if index < 13 else 2)
    for index, profile in enumerate(_PROFILE_COUNTS)
}
_UNCERTAIN_PROFILE_COUNTS = {
    profile: _PROFILE_COUNTS[profile] - _GOLD_PROFILE_COUNTS[profile]
    for profile in _PROFILE_COUNTS
}


def _runtime_attestation(model: str, digest: str) -> dict[str, object]:
    core: dict[str, object] = {
        "schema_version": "ollama-model-attestation-v1",
        "status": "verified",
        "endpoint_kind": "ollama_openai_compatible",
        "endpoint_identity_sha256": "7" * 64,
        "requested_model": model,
        "canonical_model": model,
        "resolved_model": model,
        "live_model_digest": digest,
        "expected_model_digest": digest,
    }
    return {
        **core,
        "checked_at": "2026-08-08T00:00:00+00:00",
        "binding_sha256": controller._sha256_bytes(
            controller._canonical_json_bytes(core)
        ),
    }


@pytest.fixture(autouse=True)
def _stub_controller_model_preflight(monkeypatch: pytest.MonkeyPatch):
    def preflight(
        *,
        base_url,
        judge_model,
        judge_model_manifest_sha256,
        shadow_model,
        shadow_model_manifest_sha256,
        live,
    ):
        del base_url, live
        return {
            "primary": _runtime_attestation(
                judge_model, judge_model_manifest_sha256
            ),
            "shadow": (
                _runtime_attestation(
                    shadow_model, shadow_model_manifest_sha256
                )
                if shadow_model is not None
                else None
            ),
        }

    monkeypatch.setattr(controller, "_preflight_judge_runtime_models", preflight)


def _generation_inputs(tmp_path: Path) -> tuple[dict[str, object], list[dict]]:
    rows = []
    for index in range(controller.SHARD_COUNT):
        upstream = {
            "schema": "proxy-generation-input-attestation-v1",
            "status": "verified",
            "generation_run_id": f"generation-v1-s{index:02d}",
            "generation_namespace": f"generation-v1-s{index:02d}",
            "generation_run_contract_sha256": f"{index:064x}",
            "generation_provider": {
                "runtime": "local_openai",
                "model": "qwen3:14b",
                "model_revision": "sha256:" + "1" * 64,
            },
            "candidates_sha256": f"{index + 10:064x}",
            "rejected_sha256": f"{index + 20:064x}",
            "stats_sha256": f"{index + 30:064x}",
            "input_count": 100,
            "rejected_count": 0,
            "candidate_by_grade": {"S1": 25, "S2": 25, "S3": 30, "TS": 20},
            "candidate_by_factor_profile": _PROFILE_COUNTS,
            "selection_target_by_factor_profile": _PROFILE_COUNTS,
            "base_final_target_by_factor_profile": _PROFILE_COUNTS,
            "factor_profile_by_scenario": _PROFILE_BY_SCENARIO,
            "attestation_sha256": f"{index + 40:064x}",
        }
        rows.append(
            {
                "index": index,
                "generation_run_id": upstream["generation_run_id"],
                "input_path": str(
                    (
                        tmp_path
                        / "generation"
                        / str(upstream["generation_run_id"])
                        / "candidates.jsonl"
                    ).resolve()
                ),
                "upstream_generation": upstream,
            }
        )
    attestation = {
        "schema": "proxy-generation-controller-input-attestation-v2",
        "status": "verified",
        "generation_controller_run_prefix": "generation-v1",
        "generation_controller_run_contract_sha256": "a" * 64,
        "generation_controller_dir_sha256": "b" * 64,
        "generation_out_root_sha256": "c" * 64,
        "manifest_sha256": "d" * 64,
        "stats_sha256": "e" * 64,
        "progress_sha256": "f" * 64,
        "complete_sha256": "1" * 64,
        "intended_use": "evaluation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "shard_count": 10,
        "input_count": 1000,
        "planned_by_factor_profile": {
            profile: count * 10 for profile, count in _PROFILE_COUNTS.items()
        },
        "base_final_target_by_factor_profile": {
            profile: count * 10 for profile, count in _PROFILE_COUNTS.items()
        },
        "candidate_by_factor_profile": {
            profile: count * 10 for profile, count in _PROFILE_COUNTS.items()
        },
        "shards": [],
        "attestation_sha256": "2" * 64,
    }
    return attestation, rows


def _kwargs(tmp_path: Path, *, prefix: str) -> dict:
    return {
        "generation_controller_dir": tmp_path / "generation-controller",
        "generation_out_root": tmp_path / "generation",
        "judging_out_root": tmp_path / "judged",
        "controller_out_root": tmp_path / "judge-controllers",
        "run_prefix": prefix,
        "intended_use": "evaluation",
        "base_url": "http://ollama:11434/v1/",
        "judge_model": "gemma3:12b",
        "judge_model_manifest_sha256": _JUDGE_REVISION,
        "shadow_model": None,
        "shadow_model_manifest_sha256": None,
        "k_min": 2,
        "k_max": 3,
        "temperature": 0.6,
        "min_self_consistency": 0.67,
        "require_evidence": False,
    }


def _install_generation_attestation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attestation, rows = _generation_inputs(tmp_path)

    def attest(
        controller_dir,
        *,
        generation_out_root,
        intended_use,
        runtime_base_url,
        revalidate_runtime,
    ):
        assert controller_dir == (tmp_path / "generation-controller").resolve()
        assert generation_out_root == (tmp_path / "generation").resolve()
        assert intended_use == "evaluation"
        assert runtime_base_url == "http://ollama:11434/v1"
        assert revalidate_runtime is True
        return attestation, rows

    monkeypatch.setattr(controller, "_attest_generation_controller", attest)


def _verified(spec: controller.JudgeShardSpec) -> dict[str, object]:
    return {
        "judge_manifest_sha256": f"{spec.index + 100:064x}",
        "gold_candidate_sha256": f"{spec.index + 110:064x}",
        "uncertain_sha256": f"{spec.index + 120:064x}",
        "journal_sha256": f"{spec.index + 130:064x}",
        "stats_sha256": f"{spec.index + 140:064x}",
        "progress_sha256": f"{spec.index + 150:064x}",
        "complete_sha256": f"{spec.index + 160:064x}",
        "primary_judge_runtime_binding_sha256": _runtime_attestation(
            "gemma3:12b", _JUDGE_REVISION
        )["binding_sha256"],
        "shadow_judge_runtime_binding_sha256": None,
        "input_sha256": spec.upstream_generation["candidates_sha256"],
        "input_count": 100,
        "completed_count": 100,
        "gold_candidate_count": 55,
        "uncertain_count": 45,
        "gold_by_scenario": {},
        "gold_by_factor_profile": _GOLD_PROFILE_COUNTS,
        "uncertain_by_factor_profile": _UNCERTAIN_PROFILE_COUNTS,
        "base_target_by_scenario": {},
        "base_target_by_factor_profile": _PROFILE_COUNTS,
        "gold_shortfall_by_scenario": {},
        "gold_shortfall_by_factor_profile": {},
        "ready_for_exact_assembly": True,
        "target_met": True,
    }


def _install_judge_verifier(monkeypatch: pytest.MonkeyPatch, seen: list[int]) -> None:
    def verify(shard_dir, *, spec, contract):
        del shard_dir
        assert contract["concurrency"] == 1
        seen.append(spec.index)
        return _verified(spec)

    monkeypatch.setattr(controller, "_verify_completed_judging_shard", verify)


def _fake_runner(
    calls: list[list[str]],
    *,
    fail_index: int | None = None,
    interrupt_index: int | None = None,
):
    def run(command, **kwargs):
        environment = kwargs.pop("env")
        assert kwargs == {
            "cwd": str(controller._POC),
            "capture_output": True,
            "text": True,
            "check": False,
        }
        assert not controller._BLOCKED_PYTHON_ENV.intersection(environment)
        assert environment["PYTHONNOUSERSITE"] == "1"
        assert environment["PYTHONHASHSEED"] == "0"
        command = list(command)
        calls.append(command)
        assert command[1] == "-I"
        assert "--allow-unattested-legacy-input" not in command
        assert "--no-shadow" in command
        run_id = command[command.index("--run-id") + 1]
        index = int(run_id.rsplit("-s", 1)[1])
        if index == interrupt_index:
            raise KeyboardInterrupt
        if index == fail_index:
            return subprocess.CompletedProcess(
                command, 9, stdout="failed", stderr="judge failed"
            )
        out_root = Path(command[command.index("--out-root") + 1])
        completed_dir = out_root / run_id
        completed_dir.mkdir(parents=True)
        (completed_dir / "COMPLETE.json").write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    return run


def _indices(calls: list[list[str]]) -> list[int]:
    return [int(call[call.index("--run-id") + 1].rsplit("-s", 1)[1]) for call in calls]


def test_runs_all_ten_judges_sequentially_and_commits_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    verified: list[int] = []
    _install_judge_verifier(monkeypatch, verified)
    calls: list[list[str]] = []

    run_dir, stats, exit_code = controller.run_controller(
        **_kwargs(tmp_path, prefix="judge-v1"),
        subprocess_runner=_fake_runner(calls),
    )

    assert exit_code == 0
    assert _indices(calls) == list(range(10))
    assert verified == list(range(10)) * 2
    assert stats["successful_shards"] == 10
    assert stats["failed_shards"] == 0
    assert stats["verified_input_count"] == 1000
    assert stats["verified_completed_count"] == 1000
    assert stats["gold_candidate_count"] == 550
    assert stats["uncertain_count"] == 450
    assert stats["ready_for_exact_assembly"] is True
    assert stats["gold_shortfall_by_factor_profile"] == {}
    assert stats["gold_by_factor_profile"] == {
        profile: count * 10 for profile, count in _GOLD_PROFILE_COUNTS.items()
    }
    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert complete["target_met"] is True
    assert complete["exit_code"] == 0
    complete_material = {
        key: value
        for key, value in complete.items()
        if key != "complete_payload_sha256"
    }
    assert complete["complete_payload_sha256"] == controller._sha256_bytes(
        controller._canonical_json_bytes(complete_material)
    )
    assert complete["manifest_sha256"] == controller._sha256_file(
        run_dir / "manifest.json"
    )
    assert manifest["concurrency"] == 1
    assert manifest["base_url"] == "http://ollama:11434/v1"
    assert manifest["allow_unattested_legacy_input"] is False
    assert manifest["runtime_model_attestations"]["primary"]["status"] == (
        "verified"
    )
    assert (
        complete["runtime_model_attestations"]["primary"]["binding_sha256"]
        == manifest["primary_judge_runtime_attestation_sha256"]
    )


def test_primary_judge_preflight_failure_writes_no_controller_or_shard_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    kwargs = _kwargs(tmp_path, prefix="judge-preflight-block")

    def fail(**_kwargs):
        raise controller.ProxyJudgingShardControllerError(
            "judge runtime model preflight failed: digest mismatch"
        )

    monkeypatch.setattr(controller, "_preflight_judge_runtime_models", fail)
    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="digest mismatch",
    ):
        controller.run_controller(
            **kwargs,
            subprocess_runner=_fake_runner([]),
        )

    assert not kwargs["judging_out_root"].exists()
    assert not kwargs["controller_out_root"].exists()


def test_positive_shard_shortfalls_are_summed_and_make_controller_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)

    def verify(shard_dir, *, spec, contract):
        del shard_dir, contract
        result = _verified(spec)
        if spec.index in {0, 1}:
            result["gold_shortfall_by_scenario"] = {
                f"missing-scenario-{spec.index}": 1
            }
            result["gold_shortfall_by_factor_profile"] = {
                "profile-00": 2 if spec.index == 0 else 1
            }
            result["ready_for_exact_assembly"] = False
        return result

    monkeypatch.setattr(controller, "_verify_completed_judging_shard", verify)
    run_dir, stats, exit_code = controller.run_controller(
        **_kwargs(tmp_path, prefix="judge-profile-shortfall"),
        subprocess_runner=_fake_runner([]),
    )

    assert stats["all_shards_processed"] is True
    assert stats["ready_for_exact_assembly"] is False
    assert stats["gold_shortfall_by_factor_profile"] == {"profile-00": 3}
    assert stats["gold_shortfall_by_scenario"] == {
        "missing-scenario-0": 1,
        "missing-scenario-1": 1,
    }
    assert stats["target_met"] is False
    assert exit_code == 1
    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    assert complete["target_met"] is False
    assert complete["exit_code"] == 1


def test_controller_artifacts_use_group_readable_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    _install_judge_verifier(monkeypatch, [])
    real_chmod = os.chmod
    chmod_calls: list[tuple[str, int]] = []

    def recording_chmod(path, mode):
        chmod_calls.append((str(path), mode))
        real_chmod(path, mode)

    monkeypatch.setattr(controller.os, "chmod", recording_chmod)
    controller.run_controller(
        **_kwargs(tmp_path, prefix="permissions-v1"),
        subprocess_runner=_fake_runner([]),
    )

    modes = [mode for _, mode in chmod_calls]
    assert controller._ARTIFACT_DIRECTORY_MODE in modes
    assert controller._ARTIFACT_FILE_MODE in modes
    assert all(mode in {0o640, 0o2750} for mode in modes)


def test_failed_judge_does_not_block_later_shards_and_final_is_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    verified: list[int] = []
    _install_judge_verifier(monkeypatch, verified)
    calls: list[list[str]] = []

    run_dir, stats, exit_code = controller.run_controller(
        **_kwargs(tmp_path, prefix="continue-v1"),
        subprocess_runner=_fake_runner(calls, fail_index=3),
    )

    assert exit_code == 1
    assert _indices(calls) == list(range(10))
    assert verified == [0, 1, 2, 4, 5, 6, 7, 8, 9] * 2
    assert stats["successful_shards"] == 9
    assert stats["failed_shards"] == 1
    assert stats["results"][3]["returncode"] == 9
    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    assert complete["target_met"] is False
    assert complete["exit_code"] == 1


def test_final_revalidation_blocks_toctou_change_before_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    calls_by_index = {index: 0 for index in range(10)}

    def changing_verifier(shard_dir, *, spec, contract):
        del shard_dir, contract
        calls_by_index[spec.index] += 1
        verified = _verified(spec)
        if spec.index == 0 and calls_by_index[0] == 2:
            verified["gold_candidate_sha256"] = "f" * 64
        return verified

    monkeypatch.setattr(
        controller, "_verify_completed_judging_shard", changing_verifier
    )

    run_dir, stats, exit_code = controller.run_controller(
        **_kwargs(tmp_path, prefix="toctou-v1"),
        subprocess_runner=_fake_runner([]),
    )

    assert exit_code == 1
    assert calls_by_index == {index: 2 for index in range(10)}
    assert stats["successful_shards"] == 9
    assert stats["failed_shards"] == 1
    assert stats["final_revalidation"]["status"] == "incomplete"
    assert stats["results"][0]["status"] == "failed"
    assert "changed verified fields" in stats["results"][0]["failure"]
    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    assert complete["target_met"] is False


def test_final_revalidation_reopens_generation_controller_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    initial, rows = _generation_inputs(tmp_path)
    attestation_calls = 0

    def changing_generation_attestation(
        controller_dir,
        *,
        generation_out_root,
        intended_use,
        runtime_base_url,
        revalidate_runtime,
    ):
        nonlocal attestation_calls
        del controller_dir, generation_out_root
        assert intended_use == "evaluation"
        assert runtime_base_url == "http://ollama:11434/v1"
        assert revalidate_runtime is True
        attestation_calls += 1
        if attestation_calls == 1:
            return initial, rows
        changed = {**initial, "manifest_sha256": "9" * 64}
        return changed, rows

    monkeypatch.setattr(
        controller,
        "_attest_generation_controller",
        changing_generation_attestation,
    )
    _install_judge_verifier(monkeypatch, [])

    _, stats, exit_code = controller.run_controller(
        **_kwargs(tmp_path, prefix="generation-toctou-v1"),
        subprocess_runner=_fake_runner([]),
    )

    assert attestation_calls == 2
    assert exit_code == 1
    assert stats["successful_shards"] == 0
    assert stats["failed_shards"] == 10
    assert stats["final_revalidation"]["status"] == "failed"
    assert all(
        "generation controller/input attestation changed" in row["failure"]
        for row in stats["results"]
    )


def test_explicit_resume_skips_only_fully_verified_existing_shards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    _install_judge_verifier(monkeypatch, [])
    kwargs = _kwargs(tmp_path, prefix="resume-v1")
    first_calls: list[list[str]] = []

    run_dir, interrupted, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner(first_calls, interrupt_index=1),
    )
    assert exit_code == 130
    assert interrupted["status"] == "interrupted"
    assert _indices(first_calls) == [0, 1]
    assert not (run_dir / "COMPLETE.json").exists()
    incomplete_shard = kwargs["judging_out_root"] / "resume-v1-s01"
    incomplete_shard.mkdir()
    (incomplete_shard / "partial.txt").write_text(
        "preserve me\n", encoding="utf-8"
    )

    verified: list[int] = []
    _install_judge_verifier(monkeypatch, verified)
    resume_calls: list[list[str]] = []
    resumed_dir, stats, exit_code = controller.run_controller(
        **kwargs,
        resume_controller=run_dir,
        subprocess_runner=_fake_runner(resume_calls),
    )

    assert resumed_dir == run_dir
    assert exit_code == 0
    assert verified == list(range(10)) * 2
    assert _indices(resume_calls) == list(range(1, 10))
    assert stats["skipped_verified_shards"] == 1
    assert stats["recovered_completed_shards"] == 1
    assert stats["launched_shards"] == 9
    assert stats["results"][0]["status"] == "skipped_verified"
    assert stats["results"][1]["status"] == "recovered_completed"
    assert stats["results"][1]["recovery_of"] == "resume-v1-s01"
    assert Path(stats["results"][1]["shard_dir"]) != incomplete_shard
    assert (incomplete_shard / "partial.txt").read_text(encoding="utf-8") == (
        "preserve me\n"
    )
    assert (run_dir / "logs/shard-01.attempt-01.stdout.log").is_file()


def test_repeated_judge_recovery_preserves_every_partial_and_reuses_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    _install_judge_verifier(monkeypatch, [])
    prefix = "repeated-recovery-v1"
    kwargs = _kwargs(tmp_path, prefix=prefix)
    run_dir, _, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner([], interrupt_index=1),
    )
    assert exit_code == 130

    original_partial = kwargs["judging_out_root"] / f"{prefix}-s01"
    original_partial.mkdir()
    (original_partial / "partial.txt").write_text(
        "original partial\n", encoding="utf-8"
    )
    manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    _, rows = _generation_inputs(tmp_path)
    spec = controller._build_specs(
        run_prefix=prefix, shard_attestations=rows
    )[1]
    recovery_ids = [
        controller._recovery_judge_run_id(
            spec=spec,
            run_contract_sha256=manifest["run_contract_sha256"],
            recovery_index=index,
        )
        for index in (1, 2)
    ]

    def interrupt_after_artifact(
        expected_run_id: str, *, complete: bool
    ):
        def run(command, **call_kwargs):
            environment = call_kwargs.pop("env")
            assert call_kwargs == {
                "cwd": str(controller._POC),
                "capture_output": True,
                "text": True,
                "check": False,
            }
            assert not controller._BLOCKED_PYTHON_ENV.intersection(environment)
            command = list(command)
            run_id = command[command.index("--run-id") + 1]
            assert run_id == expected_run_id
            out_root = Path(command[command.index("--out-root") + 1])
            artifact_dir = out_root / run_id
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "partial.txt").write_text(
                f"{run_id}\n", encoding="utf-8"
            )
            if complete:
                (artifact_dir / "COMPLETE.json").write_text(
                    "{}\n", encoding="utf-8"
                )
            raise KeyboardInterrupt

        return run

    _, _, exit_code = controller.run_controller(
        **kwargs,
        resume_controller=run_dir,
        subprocess_runner=interrupt_after_artifact(
            recovery_ids[0], complete=False
        ),
    )
    assert exit_code == 130
    recovery_one = kwargs["judging_out_root"] / recovery_ids[0]
    recovery_one_marker = (recovery_one / "partial.txt").read_bytes()

    _, _, exit_code = controller.run_controller(
        **kwargs,
        resume_controller=run_dir,
        subprocess_runner=interrupt_after_artifact(
            recovery_ids[1], complete=True
        ),
    )
    assert exit_code == 130
    recovery_two = kwargs["judging_out_root"] / recovery_ids[1]
    recovery_two_marker = (recovery_two / "partial.txt").read_bytes()

    final_calls: list[list[str]] = []
    _, stats, exit_code = controller.run_controller(
        **kwargs,
        resume_controller=run_dir,
        subprocess_runner=_fake_runner(final_calls),
    )

    assert exit_code == 0
    assert _indices(final_calls) == list(range(2, 10))
    assert stats["results"][1]["status"] == "skipped_recovery_verified"
    assert stats["results"][1]["judge_run_id"] == recovery_ids[1]
    assert stats["results"][1]["recovery_index"] == 2
    assert stats["skipped_recovery_verified_shards"] == 1
    assert (original_partial / "partial.txt").read_text(encoding="utf-8") == (
        "original partial\n"
    )
    assert (recovery_one / "partial.txt").read_bytes() == recovery_one_marker
    assert (recovery_two / "partial.txt").read_bytes() == recovery_two_marker


def test_existing_shard_is_not_reused_by_a_new_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    verified: list[int] = []
    _install_judge_verifier(monkeypatch, verified)
    kwargs = _kwargs(tmp_path, prefix="fresh-v1")
    (kwargs["judging_out_root"] / "fresh-v1-s00").mkdir(parents=True)
    calls: list[list[str]] = []

    _, stats, exit_code = controller.run_controller(
        **kwargs, subprocess_runner=_fake_runner(calls)
    )

    assert exit_code == 1
    assert _indices(calls) == list(range(1, 10))
    assert verified == list(range(1, 10)) * 2
    assert stats["results"][0]["failure"] == (
        "existing_judge_shard_requires_explicit_resume"
    )


def test_dangling_symlink_collision_is_not_launched_as_a_fresh_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    _install_judge_verifier(monkeypatch, [])
    kwargs = _kwargs(tmp_path, prefix="dangling-v1")
    colliding_path = (kwargs["judging_out_root"] / "dangling-v1-s00").resolve()
    real_is_linklike = controller._is_linklike
    monkeypatch.setattr(
        controller,
        "_is_linklike",
        lambda path: Path(path) == colliding_path or real_is_linklike(path),
    )
    calls: list[list[str]] = []

    _, stats, exit_code = controller.run_controller(
        **kwargs, subprocess_runner=_fake_runner(calls)
    )

    assert exit_code == 1
    assert _indices(calls) == list(range(1, 10))
    assert stats["results"][0]["failure"] == (
        "existing_judge_shard_requires_explicit_resume"
    )


def test_invalid_existing_shard_fails_resume_but_remaining_shards_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    _install_judge_verifier(monkeypatch, [])
    kwargs = _kwargs(tmp_path, prefix="invalid-resume-v1")
    run_dir, _, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner([], interrupt_index=1),
    )
    assert exit_code == 130
    (kwargs["judging_out_root"] / "invalid-resume-v1-s01").mkdir()

    seen: list[int] = []

    def verify(shard_dir, *, spec, contract):
        del shard_dir, contract
        seen.append(spec.index)
        if spec.index == 1:
            raise controller.ProxyJudgingShardControllerError("incomplete judge shard")
        return _verified(spec)

    monkeypatch.setattr(controller, "_verify_completed_judging_shard", verify)
    calls: list[list[str]] = []
    _, stats, exit_code = controller.run_controller(
        **kwargs,
        resume_controller=run_dir,
        subprocess_runner=_fake_runner(calls),
    )

    assert exit_code == 1
    assert seen[:2] == [0, 1]
    assert _indices(calls) == list(range(1, 10))
    assert stats["results"][1]["status"] == "failed"
    assert "incomplete judge shard" in stats["results"][1]["failure"]


def test_resume_rejects_tampered_controller_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    _install_judge_verifier(monkeypatch, [])
    kwargs = _kwargs(tmp_path, prefix="tamper-v1")
    run_dir, _, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner([], interrupt_index=0),
    )
    assert exit_code == 130
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["judge_model_manifest_sha256"] = "sha256:" + "9" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="resume manifest/contract mismatch",
    ):
        controller.run_controller(
            **kwargs,
            resume_controller=run_dir,
            subprocess_runner=_fake_runner([]),
        )


def test_judge_controller_v2_cannot_resume_as_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _install_generation_attestation(monkeypatch, tmp_path)
    _install_judge_verifier(monkeypatch, [])
    kwargs = _kwargs(tmp_path, prefix="legacy-controller")
    run_dir, _, exit_code = controller.run_controller(
        **kwargs,
        subprocess_runner=_fake_runner([], interrupt_index=0),
    )
    assert exit_code == 130
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "proxy-judge-shard-controller-v2"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="resume manifest/contract mismatch",
    ):
        controller.run_controller(
            **kwargs,
            resume_controller=run_dir,
            subprocess_runner=_fake_runner([]),
        )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_completed_judge_shard(
    shard_dir: Path,
    *,
    spec: controller.JudgeShardSpec,
    contract: dict[str, object],
    records: list[dict[str, object]],
) -> None:
    shard_dir.mkdir(parents=True)
    primary_runtime_attestation = _runtime_attestation(
        str(contract["judge_model"]), str(contract["judge_model_manifest_sha256"])
    )
    gold_rows = [
        {
            **record,
            "status": "consensus_gold",
            "source_record_sha256": controller._record_digest(record),
            "decision_bucket": "gold_candidate",
            "primary_judge_model": "gemma3:12b",
            "primary_judge_model_revision": _JUDGE_REVISION,
            "judging_lineage": [
                "consensus_gate:proxy_semantic_quality_v2",
                "primary_judge:local_openai:gemma3:12b",
            ],
        }
        for record in records
    ]
    gold_path = shard_dir / "gold_candidate.jsonl"
    uncertain_path = shard_dir / "uncertain.jsonl"
    journal_path = shard_dir / "decisions.journal.jsonl"
    gold_text = "".join(
        json.dumps(gold, ensure_ascii=False, sort_keys=True) + "\n"
        for gold in gold_rows
    )
    gold_path.write_text(gold_text, encoding="utf-8")
    uncertain_path.write_text("", encoding="utf-8")
    journal_path.write_text(gold_text, encoding="utf-8")
    record_count = len(records)
    gold_by_grade = dict(Counter(str(record["label"]) for record in records))
    gold_by_scenario = dict(
        sorted(Counter(str(record["scenario_id"]) for record in records).items())
    )
    gold_by_factor_profile = dict(
        sorted(
            Counter(str(record["factor_profile_id"]) for record in records).items()
        )
    )
    base_by_scenario = dict(
        spec.upstream_generation["base_final_target_by_scenario"]
    )
    base_by_factor_profile = dict(
        spec.upstream_generation["base_final_target_by_factor_profile"]
    )
    shortfall_by_scenario = {
        key: target - gold_by_scenario.get(key, 0)
        for key, target in base_by_scenario.items()
        if gold_by_scenario.get(key, 0) < target
    }
    shortfall_by_factor_profile = {
        key: target - gold_by_factor_profile.get(key, 0)
        for key, target in base_by_factor_profile.items()
        if gold_by_factor_profile.get(key, 0) < target
    }
    stats = {
        "run_id": spec.judge_run_id,
        "input": record_count,
        "completed": record_count,
        "gold_candidate": record_count,
        "uncertain": 0,
        "gold_by_grade": gold_by_grade,
        "gold_by_scenario": gold_by_scenario,
        "gold_by_factor_profile": gold_by_factor_profile,
        "uncertain_by_factor_profile": {},
        "base_target_by_scenario": base_by_scenario,
        "base_target_by_factor_profile": base_by_factor_profile,
        "gold_shortfall_by_scenario": shortfall_by_scenario,
        "gold_shortfall_by_factor_profile": shortfall_by_factor_profile,
        "ready_for_exact_assembly": not (
            shortfall_by_scenario or shortfall_by_factor_profile
        ),
        "uncertain_by_status": {},
        "judge_errors": 0,
        "rule_errors_advisory": 0,
        "advisory_rule_disagreements": 0,
        "judge_parse_failures": 0,
        "input_sha256": spec.upstream_generation["candidates_sha256"],
        "intended_use": "evaluation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "claim_scope": "synthetic_proxy_candidate_only",
        "human_reviewed": False,
    }
    stats_path = shard_dir / "stats.json"
    _write_json(stats_path, stats)
    _write_json(
        shard_dir / "progress.json",
        {
            **stats,
            "status": "complete",
            "last_doc_id": records[-1]["doc_id"],
        },
    )
    manifest = {
        "schema_version": controller.JUDGE_RUN_SCHEMA_VERSION,
        "run_id": spec.judge_run_id,
        "status": "complete",
        "input_reference": str(spec.input_path),
        "input_sha256": spec.upstream_generation["candidates_sha256"],
        "input_count": record_count,
        "upstream_generation": spec.upstream_generation,
        "intended_use": "evaluation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "claim_scope": "synthetic_proxy_candidate_only",
        "human_reviewed": False,
        "gate_version": controller.PROXY_GATE_VERSION,
        "legacy_require_rule_evidence_requested": False,
        "min_self_consistency": 0.67,
        "rule_source": "static_keyword_seeds",
        "generator_models": [
            {
                "provider": "ollama",
                "model": "qwen3:14b",
                "canonical_model": "qwen314b",
            }
        ],
        "primary_judge": {
            "provider": "local_openai",
            "model": "gemma3:12b",
            "canonical_model": "gemma312b",
        },
        "primary_judge_model_revision": _JUDGE_REVISION,
        "primary_judge_runtime_attestation": primary_runtime_attestation,
        "shadow_judge": None,
        "shadow_judge_model_revision": None,
        "shadow_judge_runtime_attestation": None,
        "gold_candidate_path": "gold_candidate.jsonl",
        "uncertain_path": "uncertain.jsonl",
        "journal_path": "decisions.journal.jsonl",
        "stats_path": "stats.json",
        "stats": stats,
    }
    manifest_path = shard_dir / "run_manifest.json"
    _write_json(manifest_path, manifest)
    _write_json(
        shard_dir / "COMPLETE.json",
        {
            "schema_version": controller.JUDGE_RUN_SCHEMA_VERSION,
            "run_id": spec.judge_run_id,
            "manifest_sha256": controller._sha256_file(manifest_path),
            "gold_candidate_sha256": controller._sha256_file(gold_path),
            "uncertain_sha256": controller._sha256_file(uncertain_path),
            "stats_sha256": controller._sha256_file(stats_path),
            "input_sha256": spec.upstream_generation["candidates_sha256"],
            "intended_use": "evaluation",
            "catalog_split_role": "frozen_proxy_eval_only",
            "upstream_generation": spec.upstream_generation,
            "primary_judge_runtime_attestation": primary_runtime_attestation,
            "shadow_judge_runtime_attestation": None,
        },
    )


def test_completed_shard_verifier_binds_input_model_hashes_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    input_path = (tmp_path / "generation/candidates.jsonl").resolve()
    input_path.parent.mkdir(parents=True)
    input_path.write_text('{"doc_id":"doc-1"}\n', encoding="utf-8")
    upstream = {
        "generation_run_id": "generation-v1-s00",
        "generation_run_contract_sha256": "1" * 64,
        "candidates_sha256": controller._sha256_file(input_path),
        "input_count": 1,
        "base_final_target_by_scenario": {"scenario-1": 1},
        "base_final_target_by_factor_profile": {"profile-00": 1},
        "attestation_sha256": "3" * 64,
    }
    spec = controller.JudgeShardSpec(
        index=0,
        generation_run_id="generation-v1-s00",
        judge_run_id="judge-v1-s00",
        input_path=input_path,
        upstream_generation=upstream,
    )
    record = {
        "doc_id": "doc-1",
        "text": "sufficient source text",
        "label": "S2",
        "scenario_id": "scenario-1",
        "factor_profile_id": "profile-00",
        "expected_factor_scores": {"secrecy": 1, "value": 1, "management": 1},
        "generation_lineage": ["generator:ollama:qwen3:14b"],
    }
    monkeypatch.setattr(
        controller,
        "attest_generation_input",
        lambda *args, **kwargs: dict(upstream),
    )
    monkeypatch.setattr(controller, "load_candidates", lambda path: [record])
    monkeypatch.setattr(
        controller,
        "verify_ollama_model",
        lambda **kwargs: _runtime_attestation(
            str(kwargs["requested_model"]), str(kwargs["expected_manifest_sha256"])
        ),
    )
    contract = {
        "intended_use": "evaluation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "base_url": "http://ollama:11434/v1",
        "judge_model": "gemma3:12b",
        "judge_model_manifest_sha256": _JUDGE_REVISION,
        "shadow_model": None,
        "shadow_model_manifest_sha256": None,
        "min_self_consistency": "0.67",
        "require_evidence": False,
    }
    shard_dir = tmp_path / "judged/judge-v1-s00"
    _write_completed_judge_shard(
        shard_dir, spec=spec, contract=contract, records=[record]
    )

    verified = controller._verify_completed_judging_shard(
        shard_dir, spec=spec, contract=contract
    )
    assert verified["target_met"] is True
    assert verified["input_count"] == 1
    assert verified["gold_candidate_count"] == 1

    gold_path = shard_dir / "gold_candidate.jsonl"
    complete_path = shard_dir / "COMPLETE.json"
    original_gold = gold_path.read_bytes()
    original_complete = complete_path.read_bytes()
    tampered_gold = json.loads(original_gold.decode("utf-8"))
    tampered_gold["factor_profile_id"] = "profile-tampered"
    gold_path.write_text(
        json.dumps(tampered_gold, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    complete = json.loads(original_complete)
    complete["gold_candidate_sha256"] = controller._sha256_file(gold_path)
    _write_json(complete_path, complete)
    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="output/source binding mismatch",
    ):
        controller._verify_completed_judging_shard(
            shard_dir, spec=spec, contract=contract
        )
    gold_path.write_bytes(original_gold)
    complete_path.write_bytes(original_complete)

    manifest_path = shard_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["primary_judge_model_revision"] = "sha256:" + "9" * 64
    _write_json(manifest_path, manifest)
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["manifest_sha256"] = controller._sha256_file(manifest_path)
    _write_json(complete_path, complete)
    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="model identity/revision mismatch",
    ):
        controller._verify_completed_judging_shard(
            shard_dir, spec=spec, contract=contract
        )


def test_completed_shard_verifier_rejects_reordered_decision_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    input_path = (tmp_path / "generation/candidates.jsonl").resolve()
    input_path.parent.mkdir(parents=True)
    input_path.write_text('{"doc_id":"doc-1"}\n{"doc_id":"doc-2"}\n', encoding="utf-8")
    upstream = {
        "generation_run_id": "generation-v1-s00",
        "generation_run_contract_sha256": "1" * 64,
        "candidates_sha256": controller._sha256_file(input_path),
        "input_count": 2,
        "base_final_target_by_scenario": {"scenario-1": 1, "scenario-2": 1},
        "base_final_target_by_factor_profile": {"profile-00": 2},
        "attestation_sha256": "3" * 64,
    }
    spec = controller.JudgeShardSpec(
        index=0,
        generation_run_id="generation-v1-s00",
        judge_run_id="judge-order-s00",
        input_path=input_path,
        upstream_generation=upstream,
    )
    records = [
        {
            "doc_id": f"doc-{index}",
            "text": f"source text {index}",
            "label": "S2",
            "scenario_id": f"scenario-{index}",
            "factor_profile_id": "profile-00",
            "expected_factor_scores": {
                "secrecy": 1,
                "value": 1,
                "management": 1,
            },
            "generation_lineage": ["generator:ollama:qwen3:14b"],
        }
        for index in (1, 2)
    ]
    monkeypatch.setattr(
        controller,
        "attest_generation_input",
        lambda *args, **kwargs: dict(upstream),
    )
    monkeypatch.setattr(controller, "load_candidates", lambda path: records)
    monkeypatch.setattr(
        controller,
        "verify_ollama_model",
        lambda **kwargs: _runtime_attestation(
            str(kwargs["requested_model"]), str(kwargs["expected_manifest_sha256"])
        ),
    )
    contract = {
        "intended_use": "evaluation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "base_url": "http://ollama:11434/v1",
        "judge_model": "gemma3:12b",
        "judge_model_manifest_sha256": _JUDGE_REVISION,
        "shadow_model": None,
        "shadow_model_manifest_sha256": None,
        "min_self_consistency": "0.67",
        "require_evidence": False,
    }
    shard_dir = tmp_path / "judged/judge-order-s00"
    _write_completed_judge_shard(
        shard_dir, spec=spec, contract=contract, records=records
    )
    journal_path = shard_dir / "decisions.journal.jsonl"
    journal_lines = journal_path.read_text(encoding="utf-8").splitlines()
    journal_path.write_text("\n".join(reversed(journal_lines)) + "\n", encoding="utf-8")

    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="decision journal mismatch",
    ):
        controller._verify_completed_judging_shard(
            shard_dir, spec=spec, contract=contract
        )


def test_partial_judge_directory_without_complete_is_never_promoted(tmp_path: Path):
    shard_dir = tmp_path / "judged/partial-s00"
    shard_dir.mkdir(parents=True)
    _write_json(
        shard_dir / "run_manifest.json",
        {
            "schema_version": controller.JUDGE_RUN_SCHEMA_VERSION,
            "run_id": "partial-s00",
            "status": "running",
        },
    )
    spec = controller.JudgeShardSpec(
        index=0,
        generation_run_id="generation-v1-s00",
        judge_run_id="partial-s00",
        input_path=tmp_path / "generation/candidates.jsonl",
        upstream_generation={},
    )

    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="missing or non-regular judge shard 0 stats",
    ):
        controller._verify_completed_judging_shard(
            shard_dir,
            spec=spec,
            contract={"intended_use": "evaluation"},
        )


def _write_generation_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    generation_root = (tmp_path / "generation").resolve()
    generation_root.mkdir()
    prefix = "generation-v1"
    controller_dir = tmp_path / prefix
    controller_dir.mkdir()
    generation_revision = "sha256:" + "5" * 64
    generation_runtime_attestation = _runtime_attestation(
        "qwen3:14b", generation_revision
    )

    def attestation(input_path, *, intended_use):
        assert intended_use == "evaluation"
        index = int(input_path.parent.name.rsplit("-s", 1)[1])
        return {
            "generation_run_id": input_path.parent.name,
            "generation_namespace": input_path.parent.name,
            "generation_run_contract_sha256": f"{index + 1:064x}",
            "candidates_sha256": f"{index + 11:064x}",
            "rejected_sha256": f"{index + 21:064x}",
            "stats_sha256": f"{index + 31:064x}",
            "input_count": 100,
            "rejected_count": 0,
            "candidate_by_grade": {"S1": 25, "S2": 25, "S3": 30, "TS": 20},
            "candidate_by_factor_profile": _PROFILE_COUNTS,
            "selection_target_by_factor_profile": _PROFILE_COUNTS,
            "base_final_target_by_factor_profile": _PROFILE_COUNTS,
            "factor_profile_by_scenario": _PROFILE_BY_SCENARIO,
            "generation_model_attestation_binding_sha256": (
                generation_runtime_attestation["binding_sha256"]
            ),
            "attestation_sha256": f"{index + 41:064x}",
        }

    monkeypatch.setattr(controller, "attest_generation_input", attestation)
    shards = [
        {
            "index": index,
            "run_id": f"{prefix}-s{index:02d}",
            "generation_namespace": f"{prefix}-s{index:02d}",
            "planned": 100,
            "planned_by_factor_profile": _PROFILE_COUNTS,
            "selection_targets_by_factor_profile": _PROFILE_COUNTS,
            "base_final_targets_by_factor_profile": _PROFILE_COUNTS,
            "factor_profile_by_scenario": _PROFILE_BY_SCENARIO,
        }
        for index in range(10)
    ]
    contract_material = {
        "schema_version": controller.GENERATION_CONTROLLER_SCHEMA_VERSION,
        "run_prefix": prefix,
        "intended_use": "evaluation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "shard_count": 10,
        "target_counts": True,
        "expected_base_total": 1000,
        "expected_grade_totals": {"S1": 250, "S2": 250, "S3": 300, "TS": 200},
        "catalog_version": "test-v1",
        "catalog_sha256": "1" * 64,
        "builder_code_sha256": "2" * 64,
        "generator_code_sha256": "3" * 64,
        "controller_code_sha256": "4" * 64,
        "generation_out_root_sha256": controller._path_digest(generation_root),
        "provider": "local_openai",
        "model_manifest_sha256": generation_revision,
        "model_runtime_attestation_sha256": generation_runtime_attestation[
            "binding_sha256"
        ],
        "model_endpoint_identity_sha256": generation_runtime_attestation[
            "endpoint_identity_sha256"
        ],
        "model_requested_name": "qwen3:14b",
        "candidate_buffer_factor": "1",
        "oversample_factor": "1",
        "max_quality_retries": 2,
        "shards": shards,
    }
    contract_sha = controller._sha256_bytes(
        controller._canonical_json_bytes(contract_material)
    )
    results = []
    for index in range(10):
        run_id = f"{prefix}-s{index:02d}"
        (generation_root / run_id).mkdir()
        attested = attestation(
            generation_root / run_id / "candidates.jsonl",
            intended_use="evaluation",
        )
        results.append(
            {
                "index": index,
                "run_id": run_id,
                "shard_dir": str((generation_root / run_id).resolve()),
                "status": "completed",
                "target_met": True,
                "generation_run_contract_sha256": attested[
                    "generation_run_contract_sha256"
                ],
                "generation_namespace": attested["generation_namespace"],
                "candidates_sha256": attested["candidates_sha256"],
                "rejected_sha256": attested["rejected_sha256"],
                "stats_sha256": attested["stats_sha256"],
                "candidate_count": 100,
                "candidate_by_factor_profile": _PROFILE_COUNTS,
                "model_runtime_attestation_sha256": (
                    generation_runtime_attestation["binding_sha256"]
                ),
                "rejected_count": 0,
            }
        )
    stats = {
        "schema_version": controller.GENERATION_CONTROLLER_SCHEMA_VERSION,
        "run_prefix": prefix,
        "intended_use": "evaluation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "run_contract_sha256": contract_sha,
        "model_runtime_attestation_sha256": generation_runtime_attestation[
            "binding_sha256"
        ],
        "status": "complete",
        "shard_count": 10,
        "attempted_shards": 10,
        "successful_shards": 10,
        "failed_shards": 0,
        "verified_candidate_count": 1000,
        "planned_generation_attempts_by_factor_profile": {
            profile: count * 10 for profile, count in _PROFILE_COUNTS.items()
        },
        "base_final_target_by_factor_profile": {
            profile: count * 10 for profile, count in _PROFILE_COUNTS.items()
        },
        "prejudge_candidate_target_by_factor_profile": {
            profile: count * 10 for profile, count in _PROFILE_COUNTS.items()
        },
        "verified_candidate_by_factor_profile": {
            profile: count * 10 for profile, count in _PROFILE_COUNTS.items()
        },
        "target_met": True,
        "results": results,
    }
    progress = {
        "schema_version": controller.GENERATION_CONTROLLER_SCHEMA_VERSION,
        "run_prefix": prefix,
        "run_contract_sha256": contract_sha,
        "status": "complete",
        "completed_shard_indices": list(range(10)),
        "successful_shards": 10,
        "failed_shards": 0,
        "results": results,
    }
    stats_path = controller_dir / "stats.json"
    progress_path = controller_dir / "progress.json"
    _write_json(stats_path, stats)
    _write_json(progress_path, progress)
    manifest = {
        **contract_material,
        "run_contract_sha256": contract_sha,
        "status": "complete",
        "target_met": True,
        "runtime_model_attestation": generation_runtime_attestation,
        "runtime_model_attestation_revalidations": [],
        "stats": stats,
        "final_artifacts": {
            "stats": "stats.json",
            "stats_sha256": controller._sha256_file(stats_path),
            "progress": "progress.json",
            "progress_sha256": controller._sha256_file(progress_path),
        },
    }
    manifest_path = controller_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    _write_json(
        controller_dir / "COMPLETE.json",
        {
            "schema_version": controller.GENERATION_CONTROLLER_SCHEMA_VERSION,
            "run_prefix": prefix,
            "run_contract_sha256": contract_sha,
            "model_runtime_attestation_sha256": generation_runtime_attestation[
                "binding_sha256"
            ],
            "runtime_model_attestation": generation_runtime_attestation,
            "manifest_sha256": controller._sha256_file(manifest_path),
            "stats_sha256": controller._sha256_file(stats_path),
            "progress_sha256": controller._sha256_file(progress_path),
            "target_met": True,
            "exit_code": 0,
        },
    )
    return controller_dir, generation_root


def test_generation_controller_attestation_rechecks_controller_and_all_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    controller_dir, generation_root = _write_generation_controller(
        tmp_path, monkeypatch
    )

    attestation, shards = controller._attest_generation_controller(
        controller_dir,
        generation_out_root=generation_root,
        intended_use="evaluation",
    )

    assert attestation["status"] == "verified"
    assert attestation["shard_count"] == 10
    assert attestation["input_count"] == 1000
    assert len(shards) == 10
    assert [row["index"] for row in shards] == list(range(10))

    stats_path = controller_dir / "stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    stats["results"][0]["candidates_sha256"] = "f" * 64
    _write_json(stats_path, stats)
    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="embedded stats mismatch|artifact hashes mismatch",
    ):
        controller._attest_generation_controller(
            controller_dir,
            generation_out_root=generation_root,
            intended_use="evaluation",
        )


def test_generation_controller_live_model_revalidation_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    controller_dir, generation_root = _write_generation_controller(
        tmp_path, monkeypatch
    )
    generation_revision = "sha256:" + "5" * 64
    monkeypatch.setattr(
        controller,
        "verify_ollama_model",
        lambda **_kwargs: _runtime_attestation(
            "qwen3:14b", generation_revision
        ),
    )

    attestation, _ = controller._attest_generation_controller(
        controller_dir,
        generation_out_root=generation_root,
        intended_use="evaluation",
        runtime_base_url="http://ollama:11434/v1",
        revalidate_runtime=True,
    )
    assert attestation["generation_model_runtime_revalidation"]["status"] == (
        "verified"
    )

    changed = _runtime_attestation("qwen3:14b", generation_revision)
    changed["binding_sha256"] = "0" * 64
    monkeypatch.setattr(
        controller,
        "verify_ollama_model",
        lambda **_kwargs: changed,
    )
    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="live binding changed",
    ):
        controller._attest_generation_controller(
            controller_dir,
            generation_out_root=generation_root,
            intended_use="evaluation",
            runtime_base_url="http://ollama:11434/v1",
            revalidate_runtime=True,
        )


def test_build_specs_rejects_mapless_factor_profile_attestation(tmp_path: Path):
    _, rows = _generation_inputs(tmp_path)
    upstream = dict(rows[0]["upstream_generation"])
    upstream.pop("candidate_by_factor_profile")
    rows[0] = {**rows[0], "upstream_generation": upstream}

    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="candidate_by_factor_profile must be a count map",
    ):
        controller._build_specs(run_prefix="mapless", shard_attestations=rows)


def test_generation_shard_symlink_rebinding_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    controller_dir, generation_root = _write_generation_controller(
        tmp_path, monkeypatch
    )
    original = generation_root / "generation-v1-s00"
    original.rmdir()
    rebound_target = tmp_path / "rebound-target"
    rebound_target.mkdir()
    try:
        original.symlink_to(rebound_target, target_is_directory=True)
    except OSError:
        real_is_linklike = controller._is_linklike
        monkeypatch.setattr(
            controller,
            "_is_linklike",
            lambda path: Path(path) == original or real_is_linklike(path),
        )

    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="must not traverse a symlink|junction",
    ):
        controller._attest_generation_controller(
            controller_dir,
            generation_out_root=generation_root,
            intended_use="evaluation",
        )


def test_checked_path_rejects_generation_controller_symlink_before_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "actual-controller"
    target.mkdir()
    link = tmp_path / "controller-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        real_is_linklike = controller._is_linklike
        monkeypatch.setattr(
            controller,
            "_is_linklike",
            lambda path: Path(path) == link or real_is_linklike(path),
        )

    with pytest.raises(
        controller.ProxyJudgingShardControllerError,
        match="must not traverse a symlink|junction",
    ):
        controller._checked_path(link, purpose="generation controller")


@pytest.mark.parametrize(
    ("field", "value", "failure"),
    [
        ("judge_model", "noop", "blocked model"),
        (
            "judge_model",
            "gemma3:12b\n--no-shadow",
            "unsafe or unsupported characters",
        ),
        (
            "base_url",
            "http://ollama:11434/v1\n",
            "whitespace or control characters",
        ),
    ],
)
def test_model_and_url_command_inputs_fail_closed(field, value, failure):
    arguments = {
        "run_prefix": "validation-v1",
        "intended_use": "evaluation",
        "base_url": "http://ollama:11434/v1",
        "judge_model": "gemma3:12b",
        "judge_model_manifest_sha256": _JUDGE_REVISION,
        "shadow_model": None,
        "shadow_model_manifest_sha256": None,
        "k_min": 2,
        "k_max": 3,
        "temperature": 0.6,
        "min_self_consistency": 0.67,
    }
    arguments[field] = value
    with pytest.raises(controller.ProxyJudgingShardControllerError, match=failure):
        controller._validate_common_arguments(**arguments)


def test_subprocess_environment_blocks_python_import_injection(monkeypatch):
    for name in controller._BLOCKED_PYTHON_ENV:
        monkeypatch.setenv(name, f"attacker-{name}")

    environment = controller._subprocess_environment()

    assert not controller._BLOCKED_PYTHON_ENV.intersection(environment)
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONHASHSEED"] == "0"


def test_cli_contract_has_no_parallel_or_legacy_escape_hatch():
    with pytest.raises(SystemExit) as exc:
        controller.main(
            [
                "--generation-controller",
                "missing",
                "--run-prefix",
                "cli-v1",
                "--intended-use",
                "evaluation",
                "--judge-model-manifest-sha256",
                _JUDGE_REVISION,
                "--no-shadow",
                "--max-workers",
                "2",
            ]
        )
    assert exc.value.code == 2

    with pytest.raises(SystemExit) as exc:
        controller.main(
            [
                "--generation-controller",
                "missing",
                "--run-prefix",
                "cli-v1",
                "--intended-use",
                "evaluation",
                "--judge-model-manifest-sha256",
                _JUDGE_REVISION,
                "--no-shadow",
                "--allow-unattested-legacy-input",
            ]
        )
    assert exc.value.code == 2
