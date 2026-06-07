#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""License-scan ChaosCypher's Python runtime closure.

Why: ChaosCypher ships under AGPL-3.0-only (OSS) with a proprietary
enterprise edition. A transitive GPL-family dep that slips into the
runtime closure would force the enterprise edition to inherit AGPL/GPL
terms. This scan fails the build on any such drift.

Scope: runtime deps only (excludes [dev] extras). The runtime closure
is derived from `uv export --no-dev --all-packages`, then handed to
`pip-licenses --packages <list>` so dev-only tooling (pytest, mypy,
interrogate, vulture, etc.) is ignored — those don't ship in the product.

Policy: `tools/license_check/policy.toml`. Deny-list + allow-list
+ per-package exceptions with reasons.

Exit codes: 0 (clean), 1 (violation), 2 (config / tooling error).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "tools" / "license_check" / "policy.toml"

# Workspace member distributions — already AGPL-3.0-only (our own
# packages); not interesting for the scan and they trip the AGPL deny
# substring. pip-licenses lists them when run inside the workspace venv.
WORKSPACE_MEMBERS = {
    "chaoscypher-core",
    "chaoscypher-cortex",
    "chaoscypher-neuron",
    "chaoscypher-cli",
}


def load_policy() -> dict:
    """Read the TOML policy file."""
    if not POLICY_PATH.exists():
        print(f"ERROR: policy file not found at {POLICY_PATH}", file=sys.stderr)
        sys.exit(2)
    with POLICY_PATH.open("rb") as fh:
        return tomllib.load(fh)


def runtime_package_names() -> list[str]:
    """Resolve the runtime-only package closure via uv.

    Excludes [dev] extras and workspace members (the workspace members
    are listed as editable installs that pip-licenses sees regardless;
    we exclude them by name here to avoid false AGPL hits on our own code).
    """
    result = subprocess.run(
        [
            "uv",
            "export",
            "--no-dev",
            "--all-packages",
            "--format",
            "requirements-txt",
            "--no-hashes",
            "--no-editable",
            "--no-emit-workspace",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print("ERROR: uv export failed:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(2)
    names: list[str] = []
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-e "):
            continue
        # Form: "pkg==1.2.3" or "pkg @ url" or "pkg ; marker"
        token = line.split("==", 1)[0].split(" @ ", 1)[0].split(";", 1)[0].strip()
        if token and token.lower() not in WORKSPACE_MEMBERS:
            names.append(token)
    return sorted(set(names))


def run_pip_licenses(packages: list[str]) -> list[dict]:
    """Invoke pip-licenses against the workspace venv, scoped to packages.

    pip-licenses lives in the core [dev] extras (`packages/core/pyproject.toml`).
    The scoping via `--packages` ensures we only emit metadata for the
    runtime closure; dev-only packages installed in the venv (pytest,
    mypy, etc.) are ignored.
    """
    if not packages:
        return []
    cmd = [
        "uv",
        "run",
        "pip-licenses",
        "--format",
        "json",
        "--from",
        "mixed",
        "--packages",
        *packages,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    if result.returncode != 0:
        print("ERROR: pip-licenses failed:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        print(
            "\nDid you run `uv sync --all-packages --extra dev`? "
            "pip-licenses lives in the core [dev] extras.",
            file=sys.stderr,
        )
        sys.exit(2)
    return json.loads(result.stdout)


def license_allowed(license_text: str, allowed_licenses: list[str]) -> bool:
    """Return True if every license token in `license_text` is on the allow list.

    pip-licenses returns license fields like `"MIT License"` or
    multi-license strings like `"BSD License; GNU General Public License (GPL); Public Domain"`.
    We split on ` OR `, ` AND `, `;`, `/`, `,`, and parens then require
    every non-empty token to match an allow-list entry. As a fallback,
    we also try the first non-empty line in case the package shipped the
    full license text in its metadata (tiktoken does this).
    """
    import re

    text = (license_text or "").strip()
    if not text:
        return False
    allow_lower = {a.lower() for a in allowed_licenses}
    # First-line fallback for packages that shipped the entire license body
    # in their metadata.
    first_line = text.splitlines()[0].strip().rstrip(".")
    if first_line.lower() in allow_lower:
        return True
    # Token-split path. Commas/parens are common delimiters in compound
    # declarations like "BSD-3-Clause, Apache-2.0, dependency licenses".
    tokens = [
        t.strip(" .()") for t in re.split(r"\s+OR\s+|\s+AND\s+|;|/|,", text) if t.strip(" .()")
    ]
    if not tokens:
        return False
    return all(t.lower() in allow_lower for t in tokens)


def license_denied(license_text: str, denied_licenses: list[str]) -> list[str]:
    """Return the list of deny-keywords matched in `license_text`.

    Uses word-boundary regex so a deny keyword "GPL" matches "(GPL)" or
    "GPL-3.0" but NOT "LGPL" or "AGPL" (those are caught by their own
    explicit entries when banned). Case-insensitive.
    """
    import re

    text = license_text or ""
    hits: list[str] = []
    for kw in denied_licenses:
        # \b doesn't anchor on punctuation-adjacent matches like "(GPL)" the
        # way we need, so use a lookaround that requires the keyword be
        # bracketed by non-letter characters (or string ends).
        pattern = r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])"
        if re.search(pattern, text, re.IGNORECASE):
            hits.append(kw)
    return hits


def package_allowed(name: str, license_text: str, allow_packages: list[dict]) -> tuple[bool, str]:
    """Check the per-package allowlist.

    Returns (allowed, reason). `allowed=True` means an explicit exception
    matched. The `reason` is the policy text.
    """
    name_lower = name.lower()
    lic_lower = (license_text or "").lower()
    for entry in allow_packages:
        if entry.get("ecosystem", "python").lower() != "python":
            continue
        if entry["name"].lower() != name_lower:
            continue
        substring = entry["license_substring"].lower()
        if substring in lic_lower:
            return True, entry["reason"].strip().replace("\n", " ")
    return False, ""


def summarize(rows: list[dict]) -> None:
    """Print the top-N license distribution."""
    from collections import Counter

    counts = Counter(r["License"] for r in rows)
    print(f"\nLicense distribution ({len(rows)} runtime packages):")
    for lic, n in counts.most_common(10):
        print(f"  {n:3d}  {lic}")


def main() -> int:
    """Run the license scan and exit with an appropriate status code."""
    policy = load_policy()
    denied = policy.get("denied_licenses", [])
    allowed = policy.get("allowed_licenses", [])
    allow_packages = policy.get("allow_packages", [])

    print("Resolving Python runtime closure (no dev extras)...")
    pkgs = runtime_package_names()
    print(f"  {len(pkgs)} runtime packages to scan")

    print("Querying pip-licenses...")
    rows = run_pip_licenses(pkgs)
    print(f"  pip-licenses returned {len(rows)} entries")

    violations: list[tuple[str, str, str, list[str]]] = []
    exceptions_used: list[tuple[str, str, str]] = []
    unrecognized: list[tuple[str, str]] = []

    for row in rows:
        name = row["Name"]
        lic = row["License"]
        if name.lower() in WORKSPACE_MEMBERS:
            continue
        deny_hits = license_denied(lic, denied)
        if deny_hits:
            ok, reason = package_allowed(name, lic, allow_packages)
            if ok:
                exceptions_used.append((name, lic, reason))
                continue
            violations.append((name, row.get("Version", "?"), lic, deny_hits))
            continue
        if not license_allowed(lic, allowed):
            unrecognized.append((name, lic))

    summarize(rows)

    if exceptions_used:
        print(f"\nAllow-list exceptions applied ({len(exceptions_used)}):")
        for name, lic, reason in exceptions_used:
            print(f"  - {name}  [{lic}]")
            print(f"      reason: {reason}")

    if unrecognized:
        print(
            f"\nWARN: {len(unrecognized)} package(s) have licenses not on the "
            f"allow-list (no deny hit — investigate and either add to "
            f"allowed_licenses or [[allow_packages]]):"
        )
        for name, lic in unrecognized:
            print(f"  - {name}: {lic!r}")

    if violations:
        print(f"\nFAIL: {len(violations)} runtime dependency license violation(s):")
        for name, ver, lic, hits in violations:
            hits_str = ", ".join(hits)
            print(f"  - {name}=={ver}: license={lic!r} matched deny-list [{hits_str}]")
        print(
            "\nIf the dependency is genuinely OK (e.g. tri-licensed and we use a "
            "permissive option), add an entry to [[allow_packages]] in "
            f"{POLICY_PATH.relative_to(REPO_ROOT)} with a clear reason. "
            "Otherwise replace the dep."
        )
        return 1

    print("\nOK: no license violations in the Python runtime closure.")
    if unrecognized:
        # Unrecognized but undenied: surface as warning, don't fail.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
