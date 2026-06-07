# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Self-test sweep for every CC0xx semgrep rule.

For each ``tools/semgrep/rules/cc-NNN-*.yml`` rule, this test
finds the paired fixture ``tools/semgrep/tests/cc-NNN-*.py``
(matched by stem) and invokes ``semgrep test`` against it. The fixture
uses ``# ruleid: <rule-id>`` to mark lines that MUST trigger and
``# ok: <rule-id>`` to mark lines that MUST NOT. ``semgrep test``
returns non-zero whenever expectations and actuals disagree.

Why this exists: in May 2026 the CC044 AST rule was silently dead for
weeks because nothing exercised the rule on a known-bad input. Test
sweeps that don't have rule-level self-tests give a green light even
when the rule is no-op'd. This file is the semgrep half of that
backstop; the AST half lives in ``test_cc{NNN}_*.py`` siblings.

Discovery contract:
- Every ``cc-NNN-<slug>.yml`` MUST have a paired
  ``cc-NNN-<slug>.py`` fixture (same stem). Missing pairs fail the
  test loudly.
- ``semgrep`` is invoked via ``uv run --with semgrep --no-project`` so
  it runs in an isolated environment (matches the ``make lint-claude``
  invocation; semgrep pins ``click<8.2`` which conflicts with
  ``chaoscypher-cli``'s ``click>=8.3``).
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[5]
_RULES_DIR = _REPO_ROOT / "tools" / "semgrep" / "rules"
_FIXTURES_DIR = _REPO_ROOT / "tools" / "semgrep" / "tests"


def _rule_yamls() -> list[Path]:
    return sorted(_RULES_DIR.glob("cc-*.yml"))


def _fixture_for(rule_yaml: Path) -> Path:
    return _FIXTURES_DIR / f"{rule_yaml.stem}.py"


def test_semgrep_rules_dir_is_non_empty() -> None:
    """Trip-wire: if someone moves the rules dir, this fails loudly."""
    yamls = _rule_yamls()
    assert yamls, (
        f"No cc-*.yml rules found under {_RULES_DIR}. If the rules dir moved, update this test."
    )


def test_every_semgrep_rule_has_a_paired_fixture() -> None:
    """Every rule YAML must have a paired fixture file.

    Forces new rules to ship with a self-test the moment they land —
    nothing else here will fail if a rule has no fixture, because
    pytest's parametrize would just generate zero cases.
    """
    missing: list[str] = []
    for yaml in _rule_yamls():
        fixture = _fixture_for(yaml)
        if not fixture.is_file():
            missing.append(
                f"  {yaml.relative_to(_REPO_ROOT)} -> expected {fixture.relative_to(_REPO_ROOT)}"
            )
    assert not missing, (
        "Every CC semgrep rule must have a paired fixture in "
        f"{_FIXTURES_DIR.relative_to(_REPO_ROOT)}/.\n"
        "Missing fixtures (one per line):\n" + "\n".join(missing)
    )


def _uv_available() -> bool:
    return shutil.which("uv") is not None


# Per-rule subprocess budget. The default 120s was tight when the whole
# core suite runs serially: `uv run --with semgrep` resolves/launches an
# isolated env each call, and under suite-wide memory pressure that cost
# (plus semgrep's own startup) intermittently blew past 120s for an
# arbitrary subset of rules. 300s gives generous headroom without
# masking a genuinely hung rule.
_SEMGREP_TIMEOUT_SECONDS = 300


@pytest.fixture(scope="session", autouse=True)
def _warm_semgrep_env() -> None:
    """Warm the isolated semgrep env once per session.

    `uv run --with semgrep --no-project semgrep test ...` triggers
    dependency resolution/provisioning for the semgrep env on its first
    call. When that cost lands on the first parametrized rule case while
    the full core suite is competing for resources, it intermittently
    pushes that case over the per-test timeout. Resolving the env once
    up front (via a cheap `semgrep --version`) moves the cold-start cost
    out of any individual rule's budget and makes the per-rule runs
    uniform. Best-effort: if uv/semgrep can't be provisioned here, the
    per-rule tests still run (and skip on missing uv).
    """
    if not _uv_available():
        return
    warm_cmd = ["uv", "run", "--with", "semgrep", "--no-project", "semgrep", "--version"]
    try:
        subprocess.run(  # noqa: S603 — hardcoded arg list, no shell, no untrusted input
            warm_cmd,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=_SEMGREP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired, OSError:
        # Warm-up is an optimization only; never fail the session for it.
        pass


# Stderr signatures of TRANSIENT environment/OS flakes that have nothing to
# do with whether the rule matches — they mean the semgrep process never got
# to evaluate the rule at all. Seen under full-suite pressure: uv re-provisions
# the ephemeral semgrep env and the just-written `semgrep` launcher is
# intermittently blocked by Windows Application Control / AV scanners
# (`os error 4551`), or uv's cache is momentarily contended. A genuine rule
# regression instead exits 1 and prints a `semgrep test` expectation diff, so
# these signatures don't mask real failures.
_TRANSIENT_SPAWN_SIGNATURES = (
    "failed to spawn",
    "os error 4551",  # Windows: ERROR_VIRUS_INFECTED / app-control block
    "application control policy",
    "no such file or directory (os error 2)",
    "resource temporarily unavailable",
    "text file busy",
)


def _is_transient_spawn_failure(proc: subprocess.CompletedProcess[str]) -> bool:
    """True when a nonzero exit looks like a transient env/spawn flake.

    Restricted to a known set of stderr signatures so a genuine rule
    regression (exit 1 + a ``semgrep test`` expectation diff) is never
    silently retried away.
    """
    if proc.returncode == 0:
        return False
    blob = f"{proc.stdout}\n{proc.stderr}".lower()
    return any(sig in blob for sig in _TRANSIENT_SPAWN_SIGNATURES)


# How many times to (re)attempt a single rule's `semgrep test` when the
# attempt fails for a transient (non-rule) reason. The first attempt plus
# up to this many retries. Bursts of OS app-control spawn blocks can hit
# several consecutive invocations, so allow a few retries with backoff.
_SEMGREP_MAX_ATTEMPTS = 4
_SEMGREP_RETRY_BACKOFF_SECONDS = 1.5


def _run_semgrep_test(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run `semgrep test`, retrying transient flakes a few times.

    Two failure modes here are environment noise, not rule bugs: a
    `TimeoutExpired` (env provisioning / startup contention under suite
    memory pressure) and a transient process-spawn block (see
    `_TRANSIENT_SPAWN_SIGNATURES`, e.g. a Windows app-control scanner
    intermittently blocking the just-provisioned semgrep launcher). Both
    are retried with a short backoff. A real rule regression (clean
    nonzero exit with a `semgrep test` diff) does NOT match the transient
    signatures and is returned immediately without retry.
    """

    def _once() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 — cmd is a hardcoded list (no shell, no untrusted input)
            cmd,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
            timeout=_SEMGREP_TIMEOUT_SECONDS,
        )

    last_proc: subprocess.CompletedProcess[str] | None = None
    for attempt in range(_SEMGREP_MAX_ATTEMPTS):
        try:
            proc = _once()
        except subprocess.TimeoutExpired:
            # Treat as transient: retry until attempts are exhausted, then
            # let the final TimeoutExpired propagate (so the failure is loud).
            if attempt == _SEMGREP_MAX_ATTEMPTS - 1:
                raise
            time.sleep(_SEMGREP_RETRY_BACKOFF_SECONDS)
            continue

        last_proc = proc
        if proc.returncode == 0 or not _is_transient_spawn_failure(proc):
            # Success, or a genuine (non-transient) rule failure — return now.
            return proc
        if attempt < _SEMGREP_MAX_ATTEMPTS - 1:
            time.sleep(_SEMGREP_RETRY_BACKOFF_SECONDS)

    # Exhausted retries on transient failures — return the last result so the
    # assertion surfaces the (still-transient) stderr for the operator.
    assert last_proc is not None
    return last_proc


@pytest.mark.parametrize("rule_yaml", _rule_yamls(), ids=lambda p: p.stem)
def test_semgrep_rule_self_test(rule_yaml: Path) -> None:
    """Each rule's `semgrep test` must pass against its paired fixture.

    Failure surface area:
    - Rule's pattern no longer matches the deliberately-bad code in
      the fixture (the regression we want to catch).
    - Rule matches something tagged `# ok:` (false positive).
    - Fixture annotations and rule id drift apart.
    """
    if not _uv_available():
        pytest.skip("uv not available; semgrep self-tests require uv run")

    fixture = _fixture_for(rule_yaml)
    if not fixture.is_file():
        pytest.fail(
            f"No fixture for {rule_yaml.name}. Expected: "
            f"{fixture.relative_to(_REPO_ROOT)}. See "
            "test_every_semgrep_rule_has_a_paired_fixture for the contract."
        )

    cmd = [
        "uv",
        "run",
        "--with",
        "semgrep",
        "--no-project",
        "semgrep",
        "test",
        "--config",
        str(rule_yaml),
        str(fixture),
    ]
    proc = _run_semgrep_test(cmd)
    if proc.returncode != 0:
        pytest.fail(
            f"semgrep test failed for {rule_yaml.name}\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  exit: {proc.returncode}\n"
            f"  stdout:\n{proc.stdout}\n"
            f"  stderr:\n{proc.stderr}"
        )
