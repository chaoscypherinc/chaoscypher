# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-probe pass rates across the models of a saved probe-benchmark run.

Usage (from the repo root):
    uv run python scripts/benchmark/probe_calibration.py <results.json> [SECTION]

Prints each probe's pass rate over the models that ran it and whether that
rate sits in its tier's calibration band (easy >= 90%, medium 50-80%,
hard 10-50%). Section H is the production-shaped tier, so every probe in it
is judged against the hard band regardless of the legacy tier in its id.
"""

import json
import sys
from collections import defaultdict


BANDS = {"easy": (0.90, 1.01), "medium": (0.50, 0.80), "hard": (0.10, 0.50)}


def main() -> None:
    """Print the calibration table for one results file."""
    with open(sys.argv[1], encoding="utf-8") as fh:
        payload = json.load(fh)
    section = sys.argv[2] if len(sys.argv) > 2 else None
    rows = payload["results"] if isinstance(payload, dict) else payload
    passed: dict[str, dict[str, bool]] = defaultdict(dict)
    models: list[str] = []
    for row in rows:
        verdicts = [
            v
            for v in (row.get("metrics") or {}).get("verdicts", [])
            if section is None or v.get("section") == section
        ]
        if not verdicts:
            continue
        model = row["model_label"].replace(" (local)", "")
        models.append(model)
        for v in verdicts:
            passed[v["id"]][model] = v["passed"]
    n = len(models)
    print(f"{n} models: {', '.join(models)}\n")
    print(f"{'probe':<22}{'pass':>7}{'rate':>7}  verdict")
    off_band = []
    for pid in sorted(passed):
        tier = pid.split("-")[1]
        k = sum(passed[pid].values())
        rate = k / n
        lo, hi = BANDS["hard"] if pid.endswith("-carrier") else BANDS[tier]
        tag = "in band" if lo <= rate < hi else ("too easy" if rate >= hi else "too hard")
        if tag != "in band":
            off_band.append(f"{pid} ({tag}, {rate:.0%})")
        print(f"{pid:<22}{k:>3}/{n:<3}{rate:>6.0%}  {tag}")
    print("\nmodel totals:")
    for model in models:
        k = sum(passed[p][model] for p in passed)
        print(f"  {model:<28}{k:>3}/{len(passed)}  {k / len(passed):.0%}")
    print(
        f"\n{len(passed) - len(off_band)}/{len(passed)} in band; out of band: {', '.join(off_band) or 'none'}"
    )


if __name__ == "__main__":
    main()
