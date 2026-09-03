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

Second backstop — the CI *shape*:
``semgrep test`` passes the fixture as an EXPLICIT FILE PATH, which
bypasses semgrep's ignore lists entirely. That blindness let CC040 and
CC041 sit dead for months: with no repo ``.semgrepignore``, semgrep's
built-in default dropped every ``tests/`` path, so the two test-scoped
rules matched zero targets in ``make lint-claude`` while the per-rule
self-tests above stayed green. ``test_ci_shape_scan_reaches_test_paths``
closes that hole by scanning a DIRECTORY the way the Makefile does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[5]
_RULES_DIR = _REPO_ROOT / "tools" / "semgrep" / "rules"
_FIXTURES_DIR = _REPO_ROOT / "tools" / "semgrep" / "tests"
# Fixture tree for the CI-shape scan. Its violations live one level down
# in a directory literally named ``tests`` — the path component semgrep's
# built-in ignore list drops — so the scan only reaches them while the
# repo-root .semgrepignore is present and keeps test paths in scope.
_CI_SHAPE_DIR = _REPO_ROOT / "tools" / "semgrep" / "ci-shape"
_SEMGREPIGNORE = _REPO_ROOT / ".semgrepignore"


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
            encoding="utf-8",
            errors="replace",
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
        # encoding/errors pinned explicitly: semgrep's rule messages contain
        # non-ASCII (em dashes), and Windows' default cp1252 locale decoding
        # raises UnicodeDecodeError mid-read on the reader thread.
        return subprocess.run(  # noqa: S603 — cmd is a hardcoded list (no shell, no untrusted input)
            cmd,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    # semgrep silently drops `# ruleid:` / `# ok:` annotations it cannot
    # parse (e.g. a trailing "— rationale" after the rule id) and still
    # exits 0 — the annotation's assertion just stops being enforced.
    if "malformed rule ID" in proc.stderr:
        pytest.fail(
            f"semgrep dropped an annotation in the fixture for {rule_yaml.name}"
            " (malformed rule ID — put rationale comments on their own line,"
            f" not after the id):\n  stderr:\n{proc.stderr}"
        )


# ---------------------------------------------------------------------------
# CI-shape scan — the blind spot the per-rule `semgrep test` above cannot see
# ---------------------------------------------------------------------------

# (rule stem, violations planted in the CI-shape fixture tree).
# Only the two test-scoped rules need this: CC040 and CC041 are the only
# rules whose `paths.include` is confined to `**/tests/**`, which is exactly
# what semgrep's built-in ignore list filters out.
_CI_SHAPE_CASES = (
    ("cc-040-memory-sqlite-in-tests", 1),
    ("cc-041-asyncio-run-in-tests", 1),
)


def test_repo_semgrepignore_exists() -> None:
    """A repo-root ``.semgrepignore`` must exist.

    Without one, semgrep falls back to its built-in default list, which
    contains ``test/`` and ``tests/`` — silently zeroing out every target
    CC040 and CC041 can match. See the file's own header comment.
    """
    assert _SEMGREPIGNORE.is_file(), (
        f"{_SEMGREPIGNORE.relative_to(_REPO_ROOT)} is missing. Semgrep then applies "
        "its BUILT-IN default ignore list, which drops every `tests/` path — and "
        "CC040/CC041 (paths.include: '**/tests/**') go back to scanning zero targets "
        "in `make lint-claude` while reporting success."
    )


def _semgrep_project_root_is_discoverable() -> bool:
    """True when semgrep can anchor repo-root config for a subdirectory scan.

    Semgrep locates the *project root* via git and anchors both
    ``.semgrepignore`` and root-anchored ``paths.exclude`` patterns
    (e.g. CC005's ``"/packages/core/src/chaoscypher_core/adapters/**"``) to
    it. In a checkout with no ``.git`` — notably the
    ``packages/docker/test`` container, which receives the tree as bind
    mounts and excludes ``.git/`` via .dockerignore — a scan rooted at a
    subdirectory never sees ``<root>/.semgrepignore``, so semgrep's
    BUILT-IN default (which drops every ``tests/`` path) applies instead.

    Measured in that container on 2026-08-13: the identical
    ``semgrep --config <rule> tools/semgrep/ci-shape`` command reported
    ``Targets scanned: 0`` before ``git init /app`` and ``Targets scanned: 2,
    Findings: 1`` immediately after — root discovery is the whole mechanism.
    (The same missing root also makes CC005/CC019's absolute excludes miss,
    which is why the containerized ``lint-claude`` reports findings the
    host-side run does not. The host-side gate is the authoritative one.)
    """
    return (_REPO_ROOT / ".git").exists()


def _semgrep_json(rule_yaml: Path, target: Path) -> dict[str, Any]:
    """Run the `make lint-claude` invocation shape and return parsed JSON.

    Deliberately mirrors Makefile's ``lint-claude`` target: a DIRECTORY
    argument (not an explicit file path), run from the repo root so the
    repo ``.semgrepignore`` applies. ``--json`` replaces ``--error`` so
    findings don't turn into a nonzero exit that the retry helper would
    have to disambiguate from a spawn failure.
    """
    cmd = [
        "uv",
        "run",
        "--with",
        "semgrep",
        "--no-project",
        "semgrep",
        "--config",
        str(rule_yaml),
        target.relative_to(_REPO_ROOT).as_posix(),
        "--metrics",
        "off",
        "--json",
    ]
    proc = _run_semgrep_test(cmd)
    if proc.returncode != 0:
        pytest.fail(
            f"CI-shape semgrep scan failed for {rule_yaml.name}\n"
            f"  cmd: {' '.join(cmd)}\n"
            f"  exit: {proc.returncode}\n"
            f"  stdout:\n{proc.stdout}\n"
            f"  stderr:\n{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"semgrep --json emitted unparseable output for {rule_yaml.name}: {exc}\n"
            f"  stdout:\n{proc.stdout}\n  stderr:\n{proc.stderr}"
        )


@pytest.mark.parametrize(
    ("rule_stem", "expected_findings"),
    _CI_SHAPE_CASES,
    ids=[stem for stem, _ in _CI_SHAPE_CASES],
)
def test_ci_shape_scan_reaches_test_paths(rule_stem: str, expected_findings: int) -> None:
    """The CI invocation shape must actually reach ``tests/`` files.

    The sibling ``test_semgrep_rule_self_test`` passes its fixture as an
    explicit file path, which bypasses ignore lists — so it reports green
    even when the rule scans nothing in CI. This test scans a *directory*
    from the repo root, exactly like ``make lint-claude``, and fails if
    either the target count drops to zero (the ignore list swallowed the
    tree again) or the planted violations stop being reported (the rule
    itself regressed).
    """
    if not _uv_available():
        pytest.skip("uv not available; semgrep self-tests require uv run")
    if not _semgrep_project_root_is_discoverable():
        # NOT a soft-pedalled failure: without a git project root semgrep
        # cannot apply <root>/.semgrepignore to a subdirectory scan at all,
        # so this assertion would be measuring semgrep's root discovery
        # rather than the repo's ignore list. The deletion regression is
        # still caught here by test_repo_semgrepignore_exists (unconditional),
        # and the behavioural assertion runs for real in every host-side
        # `make lint-claude` / `make ci` / pre-commit invocation.
        pytest.skip(
            f"no git project root at {_REPO_ROOT} (e.g. the packages/docker/test "
            "container, which bind-mounts the tree and excludes .git/). Semgrep "
            "anchors .semgrepignore to the git project root, so a subdirectory "
            "scan cannot reach it here — see _semgrep_project_root_is_discoverable. "
            "The authoritative CC040/CC041 gate is the HOST-SIDE `make lint-claude`, "
            "where this test executes for real."
        )

    rule_yaml = _RULES_DIR / f"{rule_stem}.yml"
    assert rule_yaml.is_file(), f"missing rule {rule_yaml.relative_to(_REPO_ROOT)}"

    payload = _semgrep_json(rule_yaml, _CI_SHAPE_DIR)
    scanned = payload.get("paths", {}).get("scanned", [])
    findings = payload.get("results", [])

    assert len(scanned) > 0, (
        f"{rule_stem}: 'Targets scanned' is 0 for a directory scan of "
        f"{_CI_SHAPE_DIR.relative_to(_REPO_ROOT).as_posix()}. The rule is DEAD in "
        "`make lint-claude` — every candidate file was filtered out before the rule "
        "ran. Almost certainly the repo-root .semgrepignore was deleted or stopped "
        "re-including test paths, letting semgrep's built-in default (which ignores "
        "`tests/`) take over again.\n"
        f"  semgrep errors: {payload.get('errors')}"
    )
    assert len(findings) == expected_findings, (
        f"{rule_stem}: expected {expected_findings} finding(s) from a CI-shape "
        f"directory scan of {_CI_SHAPE_DIR.relative_to(_REPO_ROOT).as_posix()}, got "
        f"{len(findings)}. Scanned {len(scanned)} target(s): {scanned}\n"
        f"  findings: {[(f.get('path'), f.get('start', {}).get('line')) for f in findings]}"
    )
