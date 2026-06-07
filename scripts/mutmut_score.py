#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Compute the mutmut mutation score and gate on a ratchet threshold.

Invoke after ``mutmut run`` from inside ``packages/core/``. The script
calls ``mutmut export-cicd-stats`` to generate
``mutants/mutmut-cicd-stats.json``, then reads the killed / survived /
total counts and exits non-zero when the score drops below
``MUTATION_SCORE_FLOOR``.

Why bake the floor into a script rather than mutmut config:
``mutmut`` 3.x has no built-in fail-under flag. The ratchet pattern
("baseline minus 2 percentage points") needs to be recomputed
whenever new mutants land, and pinning it in a script keeps the
mutmut config file declarative.

The score formula matches Stryker's convention: killed / total. Mutants
that timed out or were marked suspicious count as NOT killed -- they
got past the test suite for some reason and that's still a signal.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


# 2026-05-19 baseline: 338 killed / 407 total = 83.0%. Re-baselined
# after the systematic triage of all 143 surviving mutants across
# spend.py / encoding.py / chunk.py:
#   * spend.py: 38 → 0 surviving (logger.warning kwarg coverage + UTC
#     pin + reset_source on unknown key + spend-cap envelope assertions).
#   * encoding.py: 44 → 22 surviving (exact-label assertions, 17
#     confirmed equivalent, 5 chardet-edge test-infeasible).
#   * chunk.py: 60 → 27 surviving (offset arithmetic via hypothesis
#     round-trips, dict-key shape pins; remaining are log-only or
#     end-of-loop sep_len with no downstream read).
# 2-pp drop allowance is preserved.
MUTATION_SCORE_FLOOR = 81.0

_STATS_PATH = Path("mutants/mutmut-cicd-stats.json")


def _ensure_stats() -> dict[str, int]:
    """Regenerate stats with ``mutmut export-cicd-stats`` and load the file.

    Exporting the stats file is cheap (it reads the per-mutant .meta
    files mutmut already produced); doing it inside this script removes
    the foot-gun of running ``mutmut run`` and then forgetting the
    export step.
    """
    # mutmut writes the file directly to the cwd-relative path.
    try:
        subprocess.run(
            ["uv", "run", "mutmut", "export-cicd-stats"],  # noqa: S607 - PATH-resolved uv is intentional for tooling
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        sys.stderr.write(
            f"ERROR: `mutmut export-cicd-stats` failed:\n{exc.stderr.decode('utf-8', 'replace')}\n"
            f"  -> Did you run `make mutate-python` first?\n"
        )
        sys.exit(2)

    if not _STATS_PATH.is_file():
        sys.stderr.write(f"ERROR: {_STATS_PATH} not found after export.\n")
        sys.exit(2)

    return json.loads(_STATS_PATH.read_text("utf-8"))


def main() -> int:
    """Run the mutation score gate and return a process exit code."""
    stats = _ensure_stats()
    killed = int(stats.get("killed", 0))
    survived = int(stats.get("survived", 0))
    timeout = int(stats.get("timeout", 0))
    suspicious = int(stats.get("suspicious", 0))
    no_tests = int(stats.get("no_tests", 0))
    total = int(stats.get("total", 0))

    if total == 0:
        sys.stderr.write(
            "WARNING: mutmut reports 0 total mutants. Did mutmut run "
            "actually generate any?\n"
        )
        return 0  # don't fail an empty run; let the user investigate.

    pct = (killed / total) * 100.0

    sys.stdout.write(
        "\n"
        "=== Mutation score (mutmut) ===\n"
        f"  killed     : {killed}\n"
        f"  survived   : {survived}\n"
        f"  timeout    : {timeout}\n"
        f"  suspicious : {suspicious}\n"
        f"  no_tests   : {no_tests}\n"
        f"  total      : {total}\n"
        f"  score      : {pct:.1f}%  "
        f"({'PASS' if pct >= MUTATION_SCORE_FLOOR else 'FAIL'} vs floor {MUTATION_SCORE_FLOOR:.1f}%)\n"
    )

    if pct < MUTATION_SCORE_FLOOR:
        sys.stderr.write(
            f"\nFAIL: mutation score {pct:.1f}% < floor {MUTATION_SCORE_FLOOR:.1f}%.\n"
            f"  -> Investigate survivors with:\n"
            f"       cd packages/core && uv run mutmut results\n"
            f"  -> Inspect a specific mutant with:\n"
            f"       cd packages/core && uv run mutmut show <mutant_name>\n"
            f"  -> Add killing tests, or (after review) raise the floor in\n"
            f"     scripts/mutmut_score.py if the drop is intentional.\n"
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
