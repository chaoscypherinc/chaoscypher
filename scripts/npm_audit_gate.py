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


def _version_tuple(version: str) -> tuple[int, ...]:
    """Return a comparable tuple for a dotted version, ignoring any suffix."""
    parts: list[int] = []
    for chunk in str(version).split("-")[0].split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            break
    return tuple(parts)


def installed_versions(directory: Path) -> dict[str, str]:
    """Map package name -> resolved version from a package-lock.json."""
    lock = directory / "package-lock.json"
    # Separate handlers, not `except (OSError, json.JSONDecodeError)`: ruff-format
    # on py314 strips the parens off a tuple except-clause, which is Python 2
    # syntax and fails to parse. Keep these split.
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except OSError:  # pragma: no cover - unreadable lockfile
        return {}
    except json.JSONDecodeError:  # pragma: no cover - malformed lockfile
        return {}
    versions: dict[str, str] = {}
    for path, meta in (data.get("packages") or {}).items():
        if not path.startswith("node_modules/"):
            continue
        name = path.split("node_modules/", 1)[1]
        version = meta.get("version")
        if name and version and name not in versions:
            versions[name] = version
    return versions


def is_downgrade(fix: object, current: dict[str, str]) -> bool:
    """Return True when npm's suggested 'fix' moves a package *backwards*.

    npm resolves `fixAvailable` to any version that avoids the advisory, which
    for a deep transitive dep is often an ancient release of an unrelated
    parent. Observed 2026-08-07: the only remediation npm offered for
    ``image-size`` was ``@easyops-cn/docusaurus-search-local@0.29.0`` while the
    project is on ``0.55.3`` — the latest published. Rolling back 26 minor
    versions of the docs search plugin is a regression, not a fix, so it is
    reported rather than blocking.
    """
    if not isinstance(fix, dict):
        return False
    name, version = fix.get("name"), fix.get("version")
    if not name or not version or name not in current:
        return False
    return _version_tuple(version) < _version_tuple(current[name])


def _advisory_titles(entry: dict) -> list[str]:
    """Return human-readable advisory titles/URLs for one vulnerability entry."""
    titles: list[str] = []
    for via in entry.get("via", []):
        if isinstance(via, dict):
            title = via.get("title", "?")
            url = via.get("url", "")
            titles.append(f"{title} {url}".strip())
    return titles


def classify(
    report: dict, floor: str, current: dict[str, str] | None = None
) -> tuple[list[dict], list[dict], int]:
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
        fix = entry.get("fixAvailable", False)
        downgrade = is_downgrade(fix, current or {})
        record = {
            "name": name,
            "severity": severity,
            "range": entry.get("range", "*"),
            "titles": titles,
            "fix": fix,
            "downgrade": downgrade,
        }
        if fix and not downgrade:
            fixable.append(record)
        else:
            unfixable.append(record)
    return fixable, unfixable, propagated


def _print_group(header: str, records: list[dict]) -> None:
    """Print one classified group with its advisory detail."""
    print(f"\n{header}")
    for rec in records:
        fix = rec["fix"]
        if rec.get("downgrade") and isinstance(fix, dict):
            fix_note = f"npm's only remediation is a DOWNGRADE to {fix.get('name')}@{fix.get('version')} — not a fix"
        elif isinstance(fix, dict):
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
    fixable, unfixable, propagated = classify(report, args.severity, installed_versions(directory))

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
