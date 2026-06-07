#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""License-scan ChaosCypher's frontend production dependencies.

Why: same rationale as `license_check_python.py` — a GPL-family transitive
dep in the production bundle would force the proprietary edition to
inherit AGPL/GPL terms.

Scope: production deps only (excludes devDependencies). Driven by
`license-checker-rseidelsohn --production --json` from inside
`packages/interface/`.

Policy: `tools/license_check/policy.toml` (shared with the
Python scan).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "tools" / "license_check" / "policy.toml"
INTERFACE_DIR = REPO_ROOT / "packages" / "interface"


def load_policy() -> dict:
    """Read the TOML policy file."""
    if not POLICY_PATH.exists():
        print(f"ERROR: policy file not found at {POLICY_PATH}", file=sys.stderr)
        sys.exit(2)
    with POLICY_PATH.open("rb") as fh:
        return tomllib.load(fh)


def run_license_checker() -> dict:
    """Invoke license-checker-rseidelsohn against production deps.

    The frontend package.json must declare license-checker-rseidelsohn as
    a devDependency. We run it via `npx --no-install` to avoid a silent
    download on CI machines that haven't run `npm install` yet (the call
    fails fast in that case).
    """
    if not INTERFACE_DIR.exists():
        print(f"ERROR: {INTERFACE_DIR} not found", file=sys.stderr)
        sys.exit(2)
    cmd = [
        "npx",
        "--no-install",
        "license-checker-rseidelsohn",
        "--production",
        "--json",
        "--excludePrivatePackages",
    ]
    import os

    result = subprocess.run(
        cmd, cwd=INTERFACE_DIR, capture_output=True, text=True, shell=(os.name == "nt")
    )
    if result.returncode != 0:
        print("ERROR: license-checker-rseidelsohn failed:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        print(
            "\nDid you run `npm install` inside packages/interface? "
            "license-checker-rseidelsohn must be installed as a devDependency.",
            file=sys.stderr,
        )
        sys.exit(2)
    return json.loads(result.stdout)


def license_denied(license_text: str, denied_licenses: list[str]) -> list[str]:
    """Return the list of deny-keywords matched in `license_text`.

    Word-boundary aware so "GPL" matches "(GPL)" but not "LGPL"/"AGPL".
    """
    text = license_text or ""
    hits: list[str] = []
    for kw in denied_licenses:
        pattern = r"(?<![A-Za-z])" + re.escape(kw) + r"(?![A-Za-z])"
        if re.search(pattern, text, re.IGNORECASE):
            hits.append(kw)
    return hits


def license_allowed(license_text: str, allowed_licenses: list[str]) -> bool:
    """Return True if every license token is on the allow list."""
    text = (license_text or "").strip()
    if not text:
        return False
    allow_lower = {a.lower() for a in allowed_licenses}
    # SPDX expressions can be parenthesized: "(MIT OR Apache-2.0)".
    text_inner = text.strip("()")
    if text_inner.lower() in allow_lower:
        return True
    tokens = [
        t.strip(" .()")
        for t in re.split(r"\s+OR\s+|\s+AND\s+|;|/|,", text_inner)
        if t.strip(" .()")
    ]
    if not tokens:
        return False
    return all(t.lower() in allow_lower for t in tokens)


def package_allowed(name: str, license_text: str, allow_packages: list[dict]) -> tuple[bool, str]:
    """Check the per-package allowlist for the `frontend` ecosystem."""
    name_lower = name.lower()
    lic_lower = (license_text or "").lower()
    for entry in allow_packages:
        if entry.get("ecosystem", "").lower() != "frontend":
            continue
        # Strip version suffix from the registry's name key ("foo@1.2.3").
        if entry["name"].lower() != name_lower:
            continue
        substring = entry["license_substring"].lower()
        if substring in lic_lower:
            return True, entry["reason"].strip().replace("\n", " ")
    return False, ""


def summarize(rows: list[dict]) -> None:
    """Print the top-N license distribution."""
    from collections import Counter

    counts = Counter(r["License"] or "UNKNOWN" for r in rows)
    print(f"\nLicense distribution ({len(rows)} production packages):")
    for lic, n in counts.most_common(10):
        print(f"  {n:3d}  {lic}")


def main() -> int:
    """Run the frontend license scan."""
    policy = load_policy()
    denied = policy.get("denied_licenses", [])
    allowed = policy.get("allowed_licenses", [])
    allow_packages = policy.get("allow_packages", [])

    print("Querying license-checker-rseidelsohn (frontend production deps)...")
    data = run_license_checker()
    print(f"  {len(data)} production packages to scan")

    rows: list[dict] = []
    for full_name, info in data.items():
        # full_name is "pkg@version"; split on the last '@' (scoped packages
        # like @mui/material@9.0.0 must preserve the leading scope).
        if "@" in full_name[1:]:
            name, version = full_name.rsplit("@", 1)
        else:
            name, version = full_name, "?"
        rows.append(
            {
                "Name": name,
                "Version": info.get("version", version),
                "License": str(info.get("licenses", "")),
                "Repository": info.get("repository", ""),
            }
        )

    violations: list[tuple[str, str, str, list[str]]] = []
    exceptions_used: list[tuple[str, str, str]] = []
    unrecognized: list[tuple[str, str]] = []

    own_package_names = {"chaoscypher-frontend"}

    for row in rows:
        name = row["Name"]
        if name.lower() in own_package_names:
            continue
        lic = row["License"]
        deny_hits = license_denied(lic, denied)
        if deny_hits:
            ok, reason = package_allowed(name, lic, allow_packages)
            if ok:
                exceptions_used.append((name, lic, reason))
                continue
            violations.append((name, row["Version"], lic, deny_hits))
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
            f"allow-list (no deny hit — investigate and either extend "
            f"allowed_licenses or [[allow_packages]]):"
        )
        for name, lic in unrecognized:
            print(f"  - {name}: {lic!r}")

    if violations:
        print(f"\nFAIL: {len(violations)} production dependency license violation(s):")
        for name, ver, lic, hits in violations:
            hits_str = ", ".join(hits)
            print(f"  - {name}@{ver}: license={lic!r} matched deny-list [{hits_str}]")
        print(
            "\nIf the dependency is genuinely OK (e.g. tri-licensed and we use a "
            "permissive option), add an entry to [[allow_packages]] (ecosystem='frontend') "
            f"in {POLICY_PATH.relative_to(REPO_ROOT)} with a clear reason. "
            "Otherwise replace the dep."
        )
        return 1

    print("\nOK: no license violations in the frontend production closure.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
