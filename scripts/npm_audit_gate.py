# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Actionability-aware ``npm audit`` gate.

Why this exists: ``npm audit --audit-level=high`` fails on every high-severity
advisory, including ones with **no published fix**. For a large build-only
dependency tree (``packages/docs`` pulls ~1000 transitive packages through
Docusaurus) that turns any fresh upstream advisory into a hard stop on every
push, with no action available to clear it — the 2026-08-07 ``image-size``
advisories (GHSA-w3rx-r6r6-pgpr, GHSA-5p2g-fcmc-qvqq, ``fixAvailable: false``
on every published version) blocked the entire repo that way.

Blocking on an unfixable finding does not mitigate it; it just trains people
to bypass the gate. So this gate splits advisories by whether anything can be
done about them:

* **fixable** high/critical (npm reports a ``fixAvailable`` target) → FAIL.
  There is a bump to take, so it stays blocking.
* **unfixable** high/critical (``fixAvailable: false``) → REPORT, exit 0.
  Recorded loudly in the output so it is visible, not silent.

This is self-clearing: the moment upstream publishes a fix, the finding moves
into the fixable bucket and blocks again until the bump is taken.

Scope note: this is for build-only trees whose output ships as pre-rendered
static assets. ``packages/interface`` compiles into the shipped bundle and
keeps the strict ``npm audit --audit-level=high``.

Usage:
    uv run python scripts/npm_audit_gate.py packages/docs
    uv run python scripts/npm_audit_gate.py packages/docs --severity critical
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


#: Severities the gate considers, ordered least to most severe.
_SEVERITY_ORDER = ["info", "low", "moderate", "high", "critical"]


def _at_or_above(severity: str, floor: str) -> bool:
    """Return True when ``severity`` ranks at or above ``floor``."""
    try:
        return _SEVERITY_ORDER.index(severity) >= _SEVERITY_ORDER.index(floor)
    except ValueError:
        # Unknown severity string: treat as blocking rather than silently pass.
        return True


def _run_audit(directory: Path) -> dict:
    """Run ``npm audit --json`` in ``directory`` and return the parsed report.

    ``npm audit`` exits non-zero whenever it finds anything, so the exit code
    is deliberately ignored — the report body is the signal.
    """
    proc = subprocess.run(
        ["npm", "audit", "--json"],
        cwd=directory,
        capture_output=True,
        text=True,
        shell=sys.platform == "win32",
        check=False,
    )
    if not proc.stdout.strip():
        msg = f"npm audit produced no output in {directory} (stderr: {proc.stderr.strip()[:400]})"
        raise RuntimeError(msg)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - malformed npm output
        msg = f"could not parse npm audit JSON from {directory}: {exc}"
        raise RuntimeError(msg) from exc


def _advisory_titles(entry: dict) -> list[str]:
    """Return human-readable advisory titles/URLs for one vulnerability entry."""
    titles: list[str] = []
    for via in entry.get("via", []):
        if isinstance(via, dict):
            title = via.get("title", "?")
            url = via.get("url", "")
            titles.append(f"{title} {url}".strip())
    return titles


def classify(report: dict, floor: str) -> tuple[list[dict], list[dict], int]:
    """Split a parsed report into (fixable, unfixable, propagated_count) at/above ``floor``.

    Only entries that **carry** an advisory are classified. npm lists one entry
    per affected package, so a single unfixable root shows up again for every
    package that depends on it — and those propagated entries can carry
    ``fixAvailable: true`` even when the root has no published fix (verified
    2026-08-07: npm reported a fix for 8 ``@docusaurus/*`` packages whose only
    advisory came from ``image-size``, and ``npm audit fix`` cleared none of
    them). Classifying on those would block on findings nothing can clear.

    An entry carries an advisory when its ``via`` list contains advisory
    objects; a ``via`` of plain package-name strings means it is downstream of
    another entry. Propagated entries are counted, not classified.

    ``fixAvailable`` is ``False`` when npm knows of no published fix, and either
    ``True`` or a ``{name, version, isSemVerMajor}`` object when one exists.
    """
    fixable: list[dict] = []
    unfixable: list[dict] = []
    propagated = 0
    for name, entry in sorted((report.get("vulnerabilities") or {}).items()):
        severity = entry.get("severity", "unknown")
        if not _at_or_above(severity, floor):
            continue
        titles = _advisory_titles(entry)
        if not titles:
            # Downstream of another entry — its fate follows the root's.
            propagated += 1
            continue
        record = {
            "name": name,
            "severity": severity,
            "range": entry.get("range", "*"),
            "titles": titles,
            "fix": entry.get("fixAvailable", False),
        }
        if record["fix"]:
            fixable.append(record)
        else:
            unfixable.append(record)
    return fixable, unfixable, propagated


def _print_group(header: str, records: list[dict]) -> None:
    """Print one classified group with its advisory detail."""
    print(f"\n{header}")
    for rec in records:
        fix = rec["fix"]
        if isinstance(fix, dict):
            major = " (SEMVER-MAJOR)" if fix.get("isSemVerMajor") else ""
            fix_note = f"fix: {fix.get('name')}@{fix.get('version')}{major}"
        elif fix:
            fix_note = "fix: available"
        else:
            fix_note = "fix: none published"
        print(f"  - {rec['name']} {rec['range']} [{rec['severity']}] — {fix_note}")
        for title in rec["titles"]:
            print(f"      {title}")


def main() -> int:
    """Run the gate and return a process exit code."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("directory", help="package directory containing package-lock.json")
    parser.add_argument(
        "--severity",
        default="high",
        choices=_SEVERITY_ORDER,
        help="minimum severity the gate considers (default: high)",
    )
    args = parser.parse_args()

    directory = Path(args.directory)
    if not (directory / "package-lock.json").is_file():
        print(f"[npm-audit-gate] no package-lock.json in {directory}", file=sys.stderr)
        return 2

    report = _run_audit(directory)
    fixable, unfixable, propagated = classify(report, args.severity)

    print(f"[npm-audit-gate] {directory} — severity floor: {args.severity}")
    if propagated:
        print(f"[npm-audit-gate] {propagated} downstream package(s) affected by the roots below.")

    if unfixable:
        _print_group(
            f"UNFIXABLE ({len(unfixable)}) — reported, not blocking (no published fix):",
            unfixable,
        )

    if fixable:
        _print_group(f"FIXABLE ({len(fixable)}) — blocking, a bump is available:", fixable)
        print(
            f"\n[npm-audit-gate] FAIL: {len(fixable)} advisory root(s) have a published fix. Take the bump."
        )
        return 1

    if unfixable:
        print(
            f"\n[npm-audit-gate] PASS: {len(unfixable)} unfixable advisory root(s) recorded above. "
            "Re-check when upstream publishes; they block again the moment a fix exists."
        )
    else:
        print("[npm-audit-gate] PASS: no advisories at or above the severity floor.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
