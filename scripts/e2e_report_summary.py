# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E test report summary.

Aggregates pytest-json-report files into a terminal summary showing
pass/fail counts, durations, and failure details across all E2E tiers.

Usage:
    python scripts/e2e_report_summary.py test-reports/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BOLD = "\033[1m"


def _format_duration(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


def _outcome_color(outcome: str) -> str:
    """Return ANSI color for an outcome."""
    if outcome == "passed":
        return GREEN
    if outcome == "failed":
        return RED
    if outcome == "skipped":
        return YELLOW
    return CYAN


def _print_report(report_path: Path) -> tuple[int, int, int]:
    """Print summary for a single pytest-json-report file.

    Returns (passed, failed, skipped) counts.
    """
    with report_path.open() as f:
        data = json.load(f)

    summary = data.get("summary", {})
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    errors = summary.get("error", 0)
    total = summary.get("total", 0)
    duration = data.get("duration", 0)

    tier = report_path.stem.replace("-report", "")

    print(f"\n{BOLD}{CYAN}== {tier.upper()} =={RESET}")
    print(
        f"  {GREEN}{passed} passed{RESET}  "
        f"{RED}{failed} failed{RESET}  "
        f"{YELLOW}{skipped} skipped{RESET}  "
        f"{RED}{errors} errors{RESET}  "
        f"({total} total in {_format_duration(duration)})"
    )

    if failed or errors:
        print(f"\n  {BOLD}{RED}Failures:{RESET}")
        for test in data.get("tests", []):
            if test.get("outcome") not in ("failed", "error"):
                continue
            nodeid = test.get("nodeid", "unknown")
            # Shorten nodeid for display
            display_id = nodeid.replace("e2e/", "")
            print(f"    {RED}FAIL{RESET} {display_id}")

            # Extract the assertion error from longrepr
            call = test.get("call", {})
            longrepr = call.get("longrepr", "")
            if longrepr:
                # Find lines starting with "E " (pytest's error marker)
                error_lines = [
                    line.strip()[2:]  # strip "E " prefix
                    for line in longrepr.split("\n")
                    if line.lstrip().startswith("E ")
                ]
                # Show first 2 error lines (usually assertion + details)
                for line in error_lines[:2]:
                    print(f"         {RED}{line[:140]}{RESET}")

    return passed, failed, skipped


def main(reports_dir: str) -> int:
    """Aggregate all JSON reports in the directory."""
    reports_path = Path(reports_dir)
    if not reports_path.exists():
        print(f"{RED}Reports directory not found: {reports_dir}{RESET}")
        return 1

    json_reports = sorted(reports_path.glob("*-report.json"))
    if not json_reports:
        print(f"{YELLOW}No report files found in {reports_dir}{RESET}")
        print("(Run make e2e-cli, e2e-fresh, or e2e-resume first)")
        return 0

    total_passed = 0
    total_failed = 0
    total_skipped = 0

    print(f"{BOLD}{CYAN}E2E Test Report Summary{RESET}")
    print("=" * 60)

    for report in json_reports:
        p, f, s = _print_report(report)
        total_passed += p
        total_failed += f
        total_skipped += s

    print(f"\n{BOLD}{CYAN}== TOTALS =={RESET}")
    print(
        f"  {GREEN}{total_passed} passed{RESET}  "
        f"{RED}{total_failed} failed{RESET}  "
        f"{YELLOW}{total_skipped} skipped{RESET}"
    )

    print(f"\n{CYAN}HTML reports:{RESET}")
    for html in sorted(reports_path.glob("*-report.html")):
        print(f"  {html}")

    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "test-reports/"))
