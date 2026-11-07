#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Run the dev repo's operations-tooling tests; a no-op on the public mirror.

The repo's own operations tools (merge gating, routine gates, release and
dashboard tooling) carry their own unit tests, which no CI step ran until
2026-10-15. One of them had been failing on `main` unnoticed: it needed a
global git identity that fresh machines and CI runners do not have. Wired into
the `lint-internal-refs` step beside the other dev-only checks.

No-ops when the private tree is absent: the public export strips it, and this
script still ships and runs there.

Its own ``run_ci.py`` step, ``internal-tool-tests`` (2026-11-05; it was part of
``lint-internal-refs`` until then): GitHub CI runs it in every unit-test shard, where
``CI_TEST_SHARD`` and ``CI_TEST_DURATIONS`` (the repo-root conftest) give each shard its share
of each suite, and the benchmark tools' suite no longer holds one job for 19 minutes on a small
runner. A change only to the private tree skips the shards, and the ``internal-checks`` job runs
the whole step instead (2026-11-06, ``scripts/ci_changes.py``). With ``CI_TEST_DURATIONS_OUT``
set, each suite records its seconds in a file of its own beside that path, so the shard's
unit-test record is not overwritten.

Usage:  python scripts/run_internal_tool_tests.py [--root PATH]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


# Assembled so the internal-refs scan of this file stays clean. Each suite runs in its own pytest
# session (their conftests differ); the benchmark tools' 300-odd tests run in parallel (~4 min
# rather than ~10), and joined CI on 2026-10-15 after two of them had been red unnoticed.
_SUITES: tuple[tuple[Path, tuple[str, ...]], ...] = (
    (Path("internal") / "scripts" / "tests", ()),
    (Path("internal") / "benchmarks" / "tools" / "tests", ("-n", "auto")),
)


_DURATIONS_OUT = "CI_TEST_DURATIONS_OUT"
_NO_TESTS = 5  # pytest.ExitCode.NO_TESTS_COLLECTED


def suite_env(rel: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment a suite runs with: its own durations record when the run keeps one.

    ``durations-shard-3.json`` becomes ``durations-shard-3-benchmarks-tools.json`` for the
    benchmark tools' suite: two sessions writing one path would keep only the last.
    """
    env = dict(os.environ if environ is None else environ)
    if out := env.get(_DURATIONS_OUT):
        path = Path(out)
        slug = "-".join(part for part in rel.parts if part not in {"internal", "tests"})
        env[_DURATIONS_OUT] = str(path.with_name(f"{path.stem}-{slug}{path.suffix}"))
    return env


def main(argv: list[str] | None = None) -> int:
    """Run pytest over the tooling tests under ``--root``; exit with its status."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    suites = [((args.root / rel).resolve(), rel, extra) for rel, extra in _SUITES]
    present = [suite for suite in suites if suite[0].is_dir()]
    if not present:
        print(f"OK: no operations-tooling tests under {args.root.resolve()} (public tree).")
        return 0
    worst = 0
    for tests, rel, extra in present:  # every suite runs; the first failure's status is the step's
        print(f"Running operations-tooling tests in {tests}", flush=True)
        cmd = [sys.executable, "-m", "pytest", str(tests), "-q", "-p", "no:cacheprovider", *extra]
        code = subprocess.run(cmd, check=False, env=suite_env(rel)).returncode
        if code == _NO_TESTS and os.environ.get("CI_TEST_SHARD"):
            code = 0  # this shard's share of the suite is empty, which is no failure
        worst = worst or code
    return worst


if __name__ == "__main__":
    sys.exit(main())
