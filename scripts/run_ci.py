# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Portable CI runner — the make-free equivalent of ``make ci`` / ``make ci-local``.

Why this exists: the pre-push hook and the Makefile both need to run the full
local CI pipeline, but ``make`` isn't available on Windows (Git Bash ships
``bash`` but not ``make``). This script is the single source of truth for the
CI *chain*; ``make ci`` / ``make ci-local`` delegate to it, and the pre-push
hook calls it directly, so the gate is identical with or without ``make`` on
both Windows and Linux.

Each step runs through the platform shell (``cmd.exe`` on Windows, ``sh`` on
POSIX), so plain tool invocations (``uv``, ``npm``, ``npx``, ``docker``)
resolve via PATH on either OS.

Usage:
    uv run python scripts/run_ci.py                 # == make ci (docker mode)
    uv run python scripts/run_ci.py --mode local    # == make ci-local
    uv run python scripts/run_ci.py --steps lint,typecheck   # run a subset
    uv run python scripts/run_ci.py --list          # print the step plan
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INTERFACE = "packages/interface"
COVERAGE_XML = REPO_ROOT / "packages/docker/test-output/coverage.xml"

# Sentinel payload for the advisory diff-cover step (Python logic, not a plain
# command list — it must check for the coverage file and never fail the build).
ADVISORY_DIFF = "advisory-diff-cover"

# A command is (shell_command, subdir_relative_to_repo_root_or_None). A step
# payload is either a list of such commands or the ADVISORY_DIFF sentinel.
# Step command lists below mirror the matching Makefile targets verbatim so the
# two stay equivalent — update both together.
Command = tuple[str, str | None]
_STEPS: dict[str, list[Command] | str] = {
    "lint": [
        ("uv run ruff check packages/", None),
        ("uv run ruff format --check packages/", None),
        ("npm run lint", INTERFACE),
    ],
    "types": [
        # generate-types.sh is a bash script (uses git rev-parse + set -euo);
        # bash ships on both Linux and Windows Git Bash.
        ("bash scripts/generate-types.sh", None),
    ],
    "typecheck": [
        (
            "uv run mypy packages/core/src packages/cortex/src "
            "packages/neuron/src packages/cli/src --config-file=pyproject.toml",
            None,
        ),
        ("npx tsc --noEmit", INTERFACE),
    ],
    "lint-claude": [
        ("uv run pytest packages/core/tests/unit/scripts/ -q", None),
        ("uv run lint-imports", None),
        (
            "uv run --with semgrep --no-project semgrep "
            "--config tools/semgrep/rules/ packages/ --metrics off --error",
            None,
        ),
        ("uv run python scripts/lint_claude_rules.py packages/", None),
    ],
    "lint-secrets": [
        ("gitleaks detect --redact --verbose --no-banner", None),
    ],
    "lint-internal-refs": [
        ("uv run python scripts/check_no_internal_refs.py", None),
        ("uv run python scripts/check_spdx_headers.py", None),
    ],
    "docstrings": [
        (
            "uv run interrogate -c pyproject.toml packages/core/src "
            "packages/cortex/src packages/neuron/src packages/cli/src -f 100 -v",
            None,
        ),
    ],
    "deadcode": [
        (
            "uv run vulture packages/core/src packages/cortex/src "
            "packages/neuron/src packages/cli/src .vulture_whitelist.py "
            "--min-confidence 80",
            None,
        ),
        ("npm run deadcode", INTERFACE),
    ],
    "bundle-size": [
        ("npm run build", INTERFACE),
        ("npx size-limit", INTERFACE),
    ],
    "license-check": [
        ("uv run python scripts/license_check_python.py", None),
        ("uv run python scripts/license_check_interface.py", None),
    ],
    "test-cov-interface": [
        ("npm run test:coverage -- --run", INTERFACE),
    ],
    "docker-test": [
        (
            "docker compose -f docker-compose.yml run --rm test make test-cov-internal",
            "packages/docker/test",
        ),
        ("docker compose -f docker-compose.yml down", "packages/docker/test"),
    ],
    "coverage-diff-advisory": ADVISORY_DIFF,
    "test-cov-internal": [
        (
            "uv run pytest packages/core/tests packages/cortex/tests "
            "packages/cli/tests packages/neuron/tests --import-mode=importlib -n auto "
            "--cov=packages/core/src/chaoscypher_core "
            "--cov=packages/cortex/src/chaoscypher_cortex "
            "--cov=packages/cli/src/chaoscypher_cli "
            "--cov=packages/neuron/src/chaoscypher_neuron "
            "--cov-report=term-missing --cov-report=html:coverage_html "
            "--cov-report=xml:coverage_html/coverage.xml --cov-fail-under=80",
            None,
        ),
    ],
    "security": [
        ("uv run pip-audit --ignore-vuln PYSEC-2022-42969", None),
        ("npm audit --audit-level=high", INTERFACE),
    ],
}

# Step order shared by both modes (mirrors the Makefile `ci` / `ci-local` prefix).
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
    "license-check",
    "test-cov-interface",
]
# `make ci`: tests run in Docker + advisory diff-cover. `make ci-local`: tests
# run on the host (used inside the Docker container, which has make).
_MODE_SUFFIX = {
    "docker": ["docker-test", "coverage-diff-advisory", "security"],
    "local": ["test-cov-internal", "security"],
}


def build_plan(mode: str) -> list[str]:
    """Return the ordered step names for ``mode`` ("docker" or "local")."""
    if mode not in _MODE_SUFFIX:
        msg = f"unknown mode {mode!r} (expected 'docker' or 'local')"
        raise ValueError(msg)
    return [*_COMMON, *_MODE_SUFFIX[mode]]


def _run_shell(command: str, subdir: str | None) -> int:
    cwd = REPO_ROOT / subdir if subdir else REPO_ROOT
    label = f"  $ {command}"
    if subdir:
        label += f"   (cwd: {subdir})"
    print(label, flush=True)
    return subprocess.run(command, shell=True, cwd=str(cwd)).returncode  # noqa: S602 - trusted, repo-internal command strings


def _run_advisory_diff_cover() -> int:
    """Advisory diff-cover: never fails the build (matches the Makefile)."""
    if not COVERAGE_XML.exists():
        print(
            f"  (skipping diff-cover - {COVERAGE_XML.as_posix()} not found; "
            "run the docker-test step first)",
            flush=True,
        )
        return 0
    rc = _run_shell(
        f"uv run diff-cover {COVERAGE_XML.as_posix()} --compare-branch=origin/main --fail-under=90",
        None,
    )
    if rc != 0:
        print("  (advisory mode - diff-cover threshold not met, not failing the build)", flush=True)
    return 0


def _run_step(name: str) -> int:
    payload = _STEPS[name]
    if isinstance(payload, str):  # the ADVISORY_DIFF sentinel
        return _run_advisory_diff_cover()
    for command, subdir in payload:
        rc = _run_shell(command, subdir)
        if rc != 0:
            return rc
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CI sweep; return a process exit code."""
    parser = argparse.ArgumentParser(description="Portable CI runner (make-free).")
    parser.add_argument("--mode", choices=["docker", "local"], default="docker")
    parser.add_argument("--steps", help="comma-separated subset of steps to run, in plan order")
    parser.add_argument("--list", action="store_true", help="print the step plan and exit")
    args = parser.parse_args(argv)

    plan = build_plan(args.mode)
    if args.steps:
        requested = [s.strip() for s in args.steps.split(",") if s.strip()]
        unknown = [s for s in requested if s not in _STEPS]
        if unknown:
            print(f"Unknown step(s): {', '.join(unknown)}. Known: {', '.join(_STEPS)}", flush=True)
            return 2
        plan = [s for s in plan if s in requested]
        # Preserve any requested step that isn't in the mode's default chain.
        plan += [s for s in requested if s not in plan]

    if args.list:
        print(f"CI plan (mode={args.mode}):", flush=True)
        for i, name in enumerate(plan, 1):
            print(f"  {i:2}. {name}", flush=True)
        return 0

    print(f"==== Portable CI ({args.mode} mode) - {len(plan)} steps ====", flush=True)
    for i, name in enumerate(plan, 1):
        print(f"\n[{i}/{len(plan)}] {name}", flush=True)
        start = time.monotonic()
        rc = _run_step(name)
        elapsed = time.monotonic() - start
        if rc != 0:
            print(f"\nFAILED at step '{name}' (exit {rc}) after {elapsed:.1f}s", flush=True)
            return rc
        print(f"  [OK] {name} ({elapsed:.1f}s)", flush=True)

    print("\n========================================", flush=True)
    print("ALL CI CHECKS PASSED", flush=True)
    print("========================================", flush=True)
    print("Safe to push!", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
