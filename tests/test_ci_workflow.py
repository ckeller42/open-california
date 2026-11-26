"""Invariants of .github/workflows/ci.yml that a quiet edit can break without any job turning red.

``ci-ok`` is the one required check: a job missing from its ``needs`` can fail on every PR and
nobody is stopped. Each rule is derived from the jobs in the file, so a new job (e.g. firmware-sim)
must be added to ``ci-ok`` or to the short exception list below, with a reason.
"""

from pathlib import Path

import pytest
import yaml

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
JOBS = yaml.safe_load(CI.read_text())["jobs"]

# Jobs ci-ok must NOT wait for, and why.
NOT_REQUIRED = {
    "ci-ok": "itself",
    "pairing-real-stack": "fails on every run (no Bluetooth module in the runner's VM): continue-on-error",
    "ios": "macOS runner, only on iOS changes; not a required check",
}
# The heavy jobs: each runs on a PR only when tools/ci_changes.py says its area changed.
HEAVY = {"gui-e2e", "pairing-real-stack", "firmware-host-e2e-split", "firmware-build", "firmware-qemu"}
PUSH_TO_MAIN = "github.event_name == 'push' && github.ref == 'refs/heads/main'"


def needs(job):
    n = JOBS[job].get("needs", [])
    return {n} if isinstance(n, str) else set(n)


def test_ci_ok_waits_for_every_required_job():
    assert "ci-ok" in JOBS
    assert set(NOT_REQUIRED) <= set(JOBS) | {"ios"}, "stale entry in NOT_REQUIRED"
    assert needs("ci-ok") == set(JOBS) - set(NOT_REQUIRED)


def test_ci_ok_runs_always_and_fails_on_failure_or_cancelled():
    job = JOBS["ci-ok"]
    assert job["if"] == "always()"
    script = "".join(str(step.get("run", "")) + str(step.get("env", "")) for step in job["steps"])
    assert "needs.*.result" in script
    assert '" failure "' in script and '" cancelled "' in script and "exit 1" in script


def test_pairing_real_stack_never_makes_the_run_red():
    assert JOBS["pairing-real-stack"]["continue-on-error"] is True


def test_firmware_release_needs_both_firmware_jobs_and_runs_only_on_push_to_main():
    assert {"firmware-build", "firmware-qemu"} <= needs("firmware-release")
    assert JOBS["firmware-release"]["if"] == PUSH_TO_MAIN


@pytest.mark.parametrize("job", sorted(HEAVY))
def test_heavy_job_is_gated_by_changes(job):
    assert "changes" in needs(job)


@pytest.mark.parametrize(
    "job", sorted(j for j in JOBS if "changes" in needs(j) and j not in ("ci-ok", "ios"))
)
def test_gated_job_runs_on_every_push_to_main(job):
    """A job behind ``changes`` must still run on every push to main (ci.yml triggers ``push`` on
    main only) and must not run after a cancel."""
    cond = JOBS[job]["if"]
    assert "!cancelled()" in cond
    assert "github.event_name == 'push' ||" in cond
    assert "needs.changes.outputs." in cond


def test_push_trigger_is_main_only():
    """The gated jobs' ``github.event_name == 'push'`` override means "push to main" only while this holds."""
    on = yaml.safe_load(CI.read_text())[True]  # YAML 1.1 reads the key `on` as True
    assert on["push"]["branches"] == ["main"]


def test_every_change_area_gates_a_job():
    """An area of tools/ci_changes.py that no job reads is a heavy job that lost its gate (or a dead filter)."""
    from tools.ci_changes import AREAS

    conds = " ".join(str(j.get("if", "")) for j in JOBS.values())
    assert [a for a in AREAS if "needs.changes.outputs.%s " % a not in conds] == []
