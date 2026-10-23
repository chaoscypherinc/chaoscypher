# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the portable CI runner's step-plan composition (scripts/run_ci.py).

The runner is the make-free equivalent of ``make ci`` / ``make ci-local``; these
tests pin the step ordering so the two stay in sync.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_run_ci():
    """Import scripts/run_ci.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "run_ci.py"
    spec = importlib.util.spec_from_file_location("run_ci", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_RUN_CI = _load_run_ci()

# The host-side checks both modes share, in order (mirrors the Makefile prefix).
_COMMON = [
    "lint",
    "types",
    "typecheck",
    "lint-claude",
    "lint-secrets",
    "lint-internal-refs",
    "docstrings",
    "deadcode",
    "bundle-size",
    "docs-build",
    "docs-generated",
    "license-check",
    "test-cov-interface",
]


def test_docker_mode_matches_make_ci():
    """`--mode docker` mirrors `make ci`: shared checks + Docker tests + advisory diff + e2e-cli + security."""
    assert _RUN_CI.build_plan("docker") == [
        *_COMMON,
        "docker-test",
        "coverage-diff-advisory",
        "e2e-cli",
        "security",
    ]


def test_local_mode_matches_make_ci_local():
    """`--mode local` mirrors `make ci-local`: shared checks + host tests + e2e-cli + security."""
    assert _RUN_CI.build_plan("local") == [*_COMMON, "test-cov-internal", "e2e-cli", "security"]


# Full-plan steps GitHub CI runs natively rather than through `run_ci.py --steps` (the workflow
# file and job that run each); every other step must appear in a job's `run_ci.py --steps` list.
_CI_NATIVE = {
    "lint": [("ci.yml", "python-quality"), ("ci.yml", "frontend")],
    "typecheck": [("ci.yml", "python-quality"), ("ci.yml", "frontend")],
    "test-cov-interface": [("ci.yml", "frontend")],
    "test-cov-internal": [("ci.yml", "python-tests")],
    "docker-test": [("ci.yml", "python-tests")],
    "coverage-diff-advisory": [("ci.yml", "python-coverage")],
}


def _workflows() -> dict[str, dict]:
    import yaml

    root = Path(__file__).resolve().parents[5] / ".github" / "workflows"
    return {p.name: yaml.safe_load(p.read_text(encoding="utf-8")) for p in root.glob("*.yml")}


def test_push_mode_is_the_fast_tier_and_a_subset_of_the_full_plan():
    """The pre-push hook's tier: about four minutes, every step of it also a full-plan step."""
    push = _RUN_CI.build_plan("push")
    assert push == [
        "lint",
        "types",
        "typecheck",
        "lint-claude",
        "lint-secrets",
        "docstrings",
        "deadcode",
    ]
    assert set(push) <= set(_RUN_CI.build_plan("local"))


def test_every_full_plan_step_runs_in_github_ci():
    """The fast pre-push hook drops no check: GitHub CI runs every step of the full local plan.

    Until 2026-10-15 the hook ran all of them (an hour); five (docstrings, deadcode, bundle-size,
    docs-generated, e2e-cli) ran nowhere else, so the remaining-gates job took them.
    """
    import re

    workflows = _workflows()
    via_run_ci: set[str] = set()
    for spec in workflows.values():
        for job in spec.get("jobs", {}).values():
            for step in job.get("steps", []):
                for match in re.finditer(r"run_ci\.py --steps ([\w,-]+)", str(step.get("run", ""))):
                    via_run_ci.update(match.group(1).split(","))
    for step, runs_in in _CI_NATIVE.items():
        for workflow, job in runs_in:
            assert job in workflows[workflow]["jobs"], f"{step}: {workflow} has no job {job}"
    full = set(_RUN_CI.build_plan("local")) | set(_RUN_CI.build_plan("docker"))
    missing = sorted(full - via_run_ci - set(_CI_NATIVE))
    assert not missing, f"full-plan steps no GitHub CI job runs: {missing}"


def test_security_step_runs_every_subcommand_and_reports_the_union(monkeypatch, capsys):
    """``security`` must not fail-fast (issue #519): all four gates run, the rollup names each, and the exit code is the first failure's."""
    expected = [command for command, _ in _RUN_CI._STEPS["security"]]
    codes = iter([3, 0, 1, 0])
    calls: list[str] = []

    def fake_shell(command: str, subdir: str | None) -> int:
        calls.append(command)
        return next(codes)

    monkeypatch.setattr(_RUN_CI, "_run_shell", fake_shell)
    assert _RUN_CI._run_step("security") == 3
    assert calls == expected, "every security sub-command must run, in plan order"
    out = capsys.readouterr().out
    assert out.count("PASS") == 2
    assert out.count("FAIL (exit 3)") == 1
    assert out.count("FAIL (exit 1)") == 1


def test_ordinary_steps_still_fail_fast(monkeypatch):
    """Only the aggregating steps run past a failure; everything else stops at the first red."""
    calls: list[str] = []

    def fake_shell(command: str, subdir: str | None) -> int:
        calls.append(command)
        return 1

    monkeypatch.setattr(_RUN_CI, "_run_shell", fake_shell)
    assert len(_RUN_CI._STEPS["lint"]) > 1
    assert _RUN_CI._run_step("lint") == 1
    assert len(calls) == 1


def test_unknown_mode_raises():
    """An unknown mode is rejected rather than silently running a partial plan."""
    with pytest.raises(ValueError, match="unknown mode"):
        _RUN_CI.build_plan("bogus")


def test_every_planned_step_has_a_definition():
    """Each step in either plan has a command definition in _STEPS."""
    for mode in ("docker", "local"):
        for step in _RUN_CI.build_plan(mode):
            assert step in _RUN_CI._STEPS, f"{step} missing from _STEPS"


def test_docker_image_build_is_defined_but_off_plan():
    """``docker-image-build`` must stay out of both default plans.

    It builds the production image and therefore needs a Docker daemon, which
    not every CI environment provides — putting it in a plan would fail those
    runs at this step. It is meant to be invoked explicitly
    (``run_ci.py --steps docker-image-build``) before cutting a release and on
    any change to ``packages/docker/Dockerfile``, which no default plan builds.
    """
    assert "docker-image-build" in _RUN_CI._STEPS
    for mode in ("docker", "local"):
        assert "docker-image-build" not in _RUN_CI.build_plan(mode)


def test_steps_never_inherit_the_hooks_git_repository_pointers(monkeypatch):
    """run_ci is the pre-push hook: git's GIT_DIR/GIT_INDEX_FILE/... must not reach a step.

    On 2026-10-15 the operations-tooling tests' throwaway `git init --bare` and
    `git config` ran against the real repository through an inherited GIT_DIR
    (core.bare=true, user.* overwritten, a worktree switched to a stray branch).
    """
    hook_env = {
        "PATH": "/usr/bin",
        "GIT_DIR": "/repo/.git/worktrees/x",
        "GIT_INDEX_FILE": "/repo/.git/worktrees/x/index",
        "GIT_WORK_TREE": "/repo",
        "GIT_PREFIX": "",
        "GIT_SSH_COMMAND": "ssh -i key",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": "*",
    }
    env = _RUN_CI.step_env(hook_env)
    assert {k for k in env if k.startswith("GIT_")} == {
        "GIT_SSH_COMMAND",
        "GIT_CONFIG_COUNT",
        "GIT_CONFIG_KEY_0",
        "GIT_CONFIG_VALUE_0",
    }
    assert env["PATH"] == "/usr/bin"

    seen: list[dict[str, str]] = []
    monkeypatch.setattr(_RUN_CI.os, "environ", hook_env)
    monkeypatch.setattr(
        _RUN_CI.subprocess,
        "run",
        lambda *a, **kw: seen.append(kw["env"]) or _RUN_CI.subprocess.CompletedProcess(a, 0),
    )
    assert _RUN_CI._run_shell("true", None) == 0
    assert "GIT_DIR" not in seen[0] and "GIT_INDEX_FILE" not in seen[0]


def test_each_run_gets_a_private_tmpdir_every_step_sees_and_it_is_removed(monkeypatch, tmp_path):
    """No other process can delete a CI run's temp files mid-step (2026-10-15: 404 setup errors).

    pytest keeps its sessions under <tempdir>/pytest-of-<user>, shared with every pytest on the
    machine; a run's steps now get TMPDIR pointing at a directory of the run's own.
    """
    seen: list[str] = []

    def fake_run(*_a, **kw):
        seen.append(kw["env"]["TMPDIR"])
        assert Path(kw["env"]["TMPDIR"]).is_dir()  # it exists while the step runs
        return _RUN_CI.subprocess.CompletedProcess(_a, 0)

    monkeypatch.setattr(_RUN_CI.subprocess, "run", fake_run)
    monkeypatch.setattr(_RUN_CI.tempfile, "tempdir", str(tmp_path))
    assert _RUN_CI.main(["--steps", "lint,typecheck"]) == 0
    assert len(seen) == len(_RUN_CI._STEPS["lint"]) + len(_RUN_CI._STEPS["typecheck"])
    assert len(set(seen)) == 1, "one directory for the whole run"
    assert Path(seen[0]).parent == tmp_path and Path(seen[0]).name.startswith("chaoscypher-ci-")
    assert not Path(seen[0]).exists(), "removed when the run ends"
    assert _RUN_CI._RUN_TMP is None
    assert "TMPDIR" not in _RUN_CI.step_env({"PATH": "/bin"}), "outside a run, nothing is forced"
