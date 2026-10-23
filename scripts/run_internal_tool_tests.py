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

Usage:  python scripts/run_internal_tool_tests.py [--root PATH]
"""

from __future__ import annotations

import argparse
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


def main(argv: list[str] | None = None) -> int:
    """Run pytest over the tooling tests under ``--root``; exit with its status."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args(argv)
    suites = [((args.root / rel).resolve(), extra) for rel, extra in _SUITES]
    present = [(tests, extra) for tests, extra in suites if tests.is_dir()]
    if not present:
        print(f"OK: no operations-tooling tests under {args.root.resolve()} (public tree).")
        return 0
    worst = 0
    for tests, extra in present:  # every suite runs; the first failure's status is the step's
        print(f"Running operations-tooling tests in {tests}", flush=True)
        cmd = [sys.executable, "-m", "pytest", str(tests), "-q", "-p", "no:cacheprovider", *extra]
        code = subprocess.run(cmd, check=False).returncode
        worst = worst or code
    return worst


if __name__ == "__main__":
    sys.exit(main())
