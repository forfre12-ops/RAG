"""Check whether the current readiness report is releasable.

Operational readiness can be CONDITIONALLY_READY while external human review is
still pending. This release gate is stricter: by default every gate must be PASS.

Two failure classes are distinguished (mirroring build_operational_readiness):
  - FAIL    = a genuine regression / defect (P1 F1/FNR, P2 recall/latency, gold
              quality). These ALWAYS block a release.
  - BLOCKED = a data-ceiling / self-resolving item — human_review below the
              minimum, or model parity pending a deploy-time CLASSIFIER_MODEL_DIR
              promotion. These block by default.

--allow-conditional (env RELEASE_GATE_ALLOW_CONDITIONAL=1) turns on PILOT mode:
a CONDITIONALLY_READY verdict (only BLOCKED gates, no FAIL) is treated as
releasable, with a loud audited waiver listing exactly which gates are waived.
It NEVER waives a FAIL gate or a missing report — a real regression blocks the
pilot exactly as it blocks GA. This lets the release gate be wired into the
deploy path (so genuine regressions block shipping) without turning red on the
known human_review data ceiling during the pilot.
"""

from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import json
import os
from pathlib import Path

# Gate statuses a PILOT (--allow-conditional) release is permitted to waive.
# Everything else — notably FAIL, and any unrecognised status — is a hard blocker.
_WAIVABLE = {"BLOCKED"}
_VALID_VERDICTS = {"PASS", "CONDITIONALLY_READY", "FAIL"}
_VALID_GATE_STATUSES = {"PASS", "BLOCKED", "FAIL"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _git_sha() -> str:
    """Current HEAD, or "" when git is unavailable (bundle/airgap installs)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - absence of git is not a gate failure by itself
        return ""


def _parse_ts(raw: object) -> "dt.datetime | None":
    text = str(raw or "").strip()
    if not text:
        return None
    # Reports carry either a date ("2026-08-05") or a full ISO timestamp.
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            got = dt.datetime.strptime(text, fmt)
            return got if got.tzinfo else got.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _freshness_problems(payload: dict, args) -> "list[str]":
    """Reasons this evidence must not be trusted for the build being released.

    Four independent questions, each of which has silently gone unasked:
      1. How old is the evidence?
      2. Was it produced from the code we are shipping?
      3. Does it describe the model the server will actually load?
      4. Was it produced under the deployment profile we are shipping?
    """
    problems: list[str] = []

    generated = _parse_ts(payload.get("generated_at"))
    if generated is None:
        problems.append("readiness report has no parseable generated_at")
    elif args.max_age_days > 0:
        age = (dt.datetime.now(dt.timezone.utc) - generated).days
        if age > args.max_age_days:
            problems.append(
                f"readiness report is {age}d old (limit {args.max_age_days}d, "
                f"generated_at={payload.get('generated_at')})"
            )

    want_sha = (args.expect_git_sha or "").strip() or _git_sha()
    got_sha = str(payload.get("git_sha") or payload.get("git_commit") or "").strip()
    if want_sha:
        if not got_sha:
            problems.append(
                "readiness report records no git sha; cannot prove it came from this build"
            )
        elif not (got_sha.startswith(want_sha[:12]) or want_sha.startswith(got_sha[:12])):
            problems.append(f"readiness git sha {got_sha[:12]} != build {want_sha[:12]}")

    if args.expect_model_dir:
        for key in ("deployed_model", "evaluated_model"):
            got = str(payload.get(key) or "").strip()
            if not got:
                problems.append(f"readiness report has no {key}")
            elif Path(got).name != Path(args.expect_model_dir).name:
                problems.append(
                    f"{key}={got} does not match the model being deployed "
                    f"({args.expect_model_dir})"
                )

    if args.expect_profile:
        got = str(payload.get("deploy_profile") or "").strip()
        if got and got != args.expect_profile:
            problems.append(
                f"readiness deploy_profile={got} != {args.expect_profile}"
            )
        elif not got:
            problems.append("readiness report has no deploy_profile")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readiness", default="reports/operational_readiness.json")
    # Freshness / identity. Off by default so existing callers keep working; a
    # real release must pass --require-fresh.
    ap.add_argument("--require-fresh", action="store_true",
                    default=_env_flag("RELEASE_GATE_REQUIRE_FRESH"),
                    help="treat stale / mismatched evidence as a hard blocker")
    ap.add_argument("--max-age-days", type=int, default=14,
                    help="maximum age of the readiness report (0 disables)")
    ap.add_argument("--expect-git-sha", default="",
                    help="sha the evidence must come from (default: current HEAD)")
    ap.add_argument("--expect-model-dir", default="",
                    help="model dir the server will load, e.g. artifacts/.../v-fe4b386b")
    ap.add_argument("--expect-profile", default="",
                    help="deploy profile being shipped, e.g. onprem-local")
    ap.add_argument(
        "--allow-conditional",
        action="store_true",
        default=_env_flag("RELEASE_GATE_ALLOW_CONDITIONAL"),
        help=(
            "PILOT mode: treat a CONDITIONALLY_READY verdict (only BLOCKED gates — "
            "e.g. human_review below minimum, model parity pending) as releasable, "
            "with an audited waiver. NEVER waives a FAIL gate or a missing report. "
            "Env: RELEASE_GATE_ALLOW_CONDITIONAL=1."
        ),
    )
    args = ap.parse_args()

    path = Path(args.readiness)
    if not path.exists():
        # A missing report is an evidence gap, not a known data ceiling — never waivable.
        print(f"[release-gate] FAIL: missing readiness report: {path}")
        return 1

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[release-gate] FAIL: invalid readiness JSON: {path} ({exc})")
        return 1
    if not isinstance(payload, dict):
        print(f"[release-gate] FAIL: readiness report must be a JSON object: {path}")
        return 1
    # ── Freshness / identity checks ───────────────────────────────────────────
    #
    # Why. The gate used to read only `verdict` + gate statuses. It never asked
    # *when* the evidence was produced or *what* it describes. Measured 2026-08-15:
    #
    #     reports/release_artifact_manifest.json  status=READY  generated_at=2026-06-01
    #     reports/operational_readiness.json      verdict=FAIL  generated_at=2026-08-05
    #
    # A stale READY can therefore be replayed months later against a different
    # build. The readiness report already carries `generated_at`, `deployed_model`
    # and `evaluated_model` — the gate simply did not look. It does now.
    #
    # These checks are opt-in by argument so existing callers keep working, but
    # --require-fresh is what a real release must use.
    stale_reasons = _freshness_problems(payload, args)
    if stale_reasons:
        if args.require_fresh:
            print("[release-gate] FAIL: evidence is stale or does not describe this build")
            for r in stale_reasons:
                print(f"- {r}")
            return 1
        for r in stale_reasons:
            print(f"[release-gate] WARN (not enforced; pass --require-fresh): {r}")

    verdict = payload.get("verdict", "UNKNOWN")
    gates = payload.get("gates", [])
    if verdict not in _VALID_VERDICTS:
        print(f"[release-gate] FAIL: invalid or missing verdict: {verdict!r}")
        return 1
    if not isinstance(gates, list) or not gates:
        print("[release-gate] FAIL: readiness gates are missing or empty")
        return 1
    malformed = [
        g for g in gates
        if not isinstance(g, dict) or not g.get("name") or g.get("status") not in _VALID_GATE_STATUSES
    ]
    if malformed:
        print("[release-gate] FAIL: malformed gate entries")
        for gate in malformed[:10]:
            print(f"- {gate!r}")
        return 1

    non_pass = [g for g in gates if g.get("status") != "PASS"]
    hard_blockers = [g for g in non_pass if g.get("status") not in _WAIVABLE]
    waivable = [g for g in non_pass if g.get("status") in _WAIVABLE]

    # A genuine regression (any FAIL / unknown status, or a FAIL verdict) always
    # blocks, regardless of pilot mode.
    if hard_blockers or verdict == "FAIL":
        print(f"[release-gate] BLOCKED (hard): verdict={verdict}")
        for gate in (hard_blockers or non_pass):
            print(f"- {gate.get('name')}: {gate.get('status')} ({gate.get('detail')})")
        return 1

    if not waivable:
        print("[release-gate] PASS")
        return 0

    # Only data-ceiling / self-resolving BLOCKED gates remain.
    if args.allow_conditional:
        # ASCII-only output: this may print to a cp949/Windows console at the
        # customer site, where a non-ASCII glyph would raise UnicodeEncodeError
        # and crash the gate (blocking a pilot release for the wrong reason).
        print(f"[release-gate] PASS (PILOT WAIVER): verdict={verdict}")
        print(
            "  [!] AUDITED WAIVER - the gates below are waived for a PILOT release "
            "(NOT GA). Genuine regressions (FAIL) are never waived:"
        )
        for gate in waivable:
            print(f"  - WAIVED {gate.get('name')}: {gate.get('status')} ({gate.get('detail')})")
        print(
            "  -> GA still requires these to reach PASS (fill human_review, promote "
            "CLASSIFIER_MODEL_DIR). Re-run without --allow-conditional for the GA gate."
        )
        return 0

    print(f"[release-gate] BLOCKED: verdict={verdict}")
    for gate in waivable:
        print(f"- {gate.get('name')}: {gate.get('status')} ({gate.get('detail')})")
    print(
        "  (PILOT: re-run with --allow-conditional to waive data-ceiling gates "
        "for a pilot release; FAIL gates are never waived.)"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
