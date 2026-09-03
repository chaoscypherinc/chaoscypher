# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tree-wide secret scan with a sandbox-capable fallback.

Prefers the ``gitleaks`` binary — the same engine as the pre-commit hook and
the ``gitleaks.yml`` workflow, honoring ``.gitleaks.toml``. Where the binary
cannot exist (the cloud routine sandbox can neither download GitHub release
assets nor bundle Go binaries — see ``internal/DEFERRED.md`` § Pending
routine-sandbox gitleaks capability), it falls back to ``detect-secrets``
over the tracked tree, compared against ``.secrets.baseline`` so audited
false positives don't fail the gate.

Fallback exclusions mirror ``.gitleaks.toml``'s philosophy (narrow, shipped
trees scanned in full): ``internal/`` (private, never shipped), lockfiles
(registry hashes trip entropy detectors and churn on every bump), and the
baseline itself.

Two distinct non-zero exits from ``detect-secrets-hook`` are told apart
rather than lumped together (#448). Reading its ``main`` in 1.5.0:

* **1** — secrets were found that the baseline does not cover. The baseline
  is *not* written on this path; the hook returns before touching it.
* **3** — no new secrets; the baseline itself was rewritten, normally just
  to restore stored line numbers after unrelated edits shifted them.

Treating 3 as a finding is what turned this gate red for three nights after
#445 shifted two e2e fixture files: any merged PR that moves a line in a
file carrying a baselined entry would do it again. So 3 is reported as
drift and does not fail the gate, while 1 still does, and any other code is
a tooling failure. The scan runs against a *copy* of the baseline, so it can
never dirty the working tree — routines no longer need a manual
``git checkout --`` after every sweep to honor the guardrail that forbids
them regenerating it.

``--refresh-baseline`` applies that same bookkeeping rewrite to the tracked
file, for a human clearing accumulated drift. It cannot introduce an entry:
the hook only removes stale records and re-stamps line numbers, and it never
reaches that code path while an uncovered secret exists.

The refreshed file is normalized before it is written back, because
``detect-secrets`` emits whatever the host platform gives it: run on
Windows it rewrites all ~100 records with backslash paths and adds an
``is_baseline_file`` filter carrying an absolute local path. Committed as
such, every entry would stop matching on Linux and the gate would go red
with fabricated findings. Normalization makes the flag platform-agnostic
rather than a footgun documented in prose.

Exit codes: 0 clean, 1 findings, 2 tooling failure.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = REPO_ROOT / ".secrets.baseline"

# detect-secrets-hook exit codes (detect_secrets/pre_commit_hook.py, 1.5.0)
_EXIT_SECRETS_FOUND = 1
_EXIT_BASELINE_REWRITTEN = 3

_FALLBACK_EXCLUDE = (
    r"^internal/",
    r"uv\.lock$",
    r"package-lock\.json$",
    r"^\.secrets\.baseline$",
)

_CHUNK = 400


def _run_gitleaks() -> int:
    # gitleaks exits 1 for leaks; anything else non-zero (bad config,
    # missing git objects) is a tooling failure, not a finding.
    result = subprocess.run(
        ["gitleaks", "detect", "--redact", "--verbose", "--no-banner"],
        cwd=REPO_ROOT,
        check=False,
    )
    if result.returncode == 0:
        return 0
    if result.returncode == 1:
        return 1
    print(
        f"secrets_scan: gitleaks exited {result.returncode} "
        "— unrecognized status, treating as a tooling failure",
        file=sys.stderr,
    )
    return 2


def _tracked_files() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    )
    files = [f for f in listing.stdout.decode().split("\0") if f]
    patterns = [re.compile(p) for p in _FALLBACK_EXCLUDE]
    return [f for f in files if not any(p.search(f) for p in patterns)]


def _scan_against(hook: str, baseline_path: Path, files: list[str]) -> tuple[bool, bool, int]:
    """Run the hook over ``files``, comparing against ``baseline_path``.

    Args:
        hook: Path to the ``detect-secrets-hook`` executable.
        baseline_path: Baseline the scan compares against. The hook may
            rewrite it, so callers pass a copy unless they want it updated.
        files: Tracked files to scan, already filtered by the exclusions.

    Returns:
        ``(found, drifted, tooling_exit)`` — whether any chunk reported an
        uncovered secret, whether any chunk rewrote the baseline for
        bookkeeping, and a non-zero exit code if the hook itself failed
        (0 when it did not).

    """
    found = False
    drifted = False
    for i in range(0, len(files), _CHUNK):
        result = subprocess.run(
            [hook, "--baseline", str(baseline_path), *files[i : i + _CHUNK]],
            cwd=REPO_ROOT,
            check=False,
        )
        if result.returncode == _EXIT_SECRETS_FOUND:
            found = True
        elif result.returncode == _EXIT_BASELINE_REWRITTEN:
            drifted = True
        elif result.returncode != 0:
            print(
                f"secrets_scan: detect-secrets-hook exited {result.returncode} "
                "— unrecognized status, treating as a tooling failure",
                file=sys.stderr,
            )
            return found, drifted, 2
    return found, drifted, 0


def _normalize_baseline() -> None:
    """Make a freshly-written baseline byte-identical across platforms.

    Rewrites record paths to forward slashes and drops any
    ``is_baseline_file`` filter, whose ``filename`` is an absolute path to
    this machine's checkout. Without this a Windows refresh commits ~100
    backslash paths that match nothing on Linux, and leaks a local path.
    """
    data = json.loads(BASELINE.read_text(encoding="utf-8"))

    data["filters_used"] = [
        f
        for f in data.get("filters_used", [])
        if f.get("path") != "detect_secrets.filters.common.is_baseline_file"
    ]

    results = {}
    for filename, entries in data.get("results", {}).items():
        posix_name = filename.replace("\\", "/")
        for entry in entries:
            if "filename" in entry:
                entry["filename"] = entry["filename"].replace("\\", "/")
        results[posix_name] = entries
    data["results"] = dict(sorted(results.items()))

    BASELINE.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _preflight(refresh: bool) -> tuple[str, list[str]] | int:
    """Resolve the hook and the file list, or return an exit code on failure."""
    hook = shutil.which("detect-secrets-hook")
    if hook is None:
        print(
            "secrets_scan: neither gitleaks nor detect-secrets-hook is "
            "available — run `uv sync --all-packages --extra dev` first",
            file=sys.stderr,
        )
        return 2
    if not BASELINE.exists():
        print(
            f"secrets_scan: {BASELINE.name} missing — regenerate with "
            "`uv run detect-secrets scan > .secrets.baseline` and audit it",
            file=sys.stderr,
        )
        return 2
    files = _tracked_files()
    mode = "refreshing baseline over" if refresh else "scanning"
    print(
        f"secrets_scan: gitleaks unavailable, {mode} {len(files)} tracked "
        f"files with detect-secrets (baseline: {BASELINE.name})"
    )
    return hook, files


def _run_detect_secrets(refresh: bool = False) -> int:
    resolved = _preflight(refresh)
    if isinstance(resolved, int):
        return resolved
    hook, files = resolved

    if refresh:
        found, drifted, tooling = _scan_against(hook, BASELINE, files)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            scratch = Path(tmpdir) / BASELINE.name
            shutil.copy2(BASELINE, scratch)
            found, drifted, tooling = _scan_against(hook, scratch, files)

    if tooling:
        return tooling
    if found:
        print(
            "secrets_scan: potential secrets found (see above). Real secret "
            "-> rotate it and purge the commit. False positive -> re-run "
            "`uv run detect-secrets scan > .secrets.baseline`, audit with "
            "`uv run detect-secrets audit .secrets.baseline`, and commit "
            "the baseline.",
            file=sys.stderr,
        )
        return 1
    if refresh:
        if drifted:
            _normalize_baseline()
            print(f"secrets_scan: {BASELINE.name} refreshed — review the diff and commit it")
        else:
            print(f"secrets_scan: clean — {BASELINE.name} already current, nothing to refresh")
        return 0
    if drifted:
        print(
            "secrets_scan: clean — no new secrets. The baseline's stored line "
            "numbers have drifted from the tree (an edit moved a baselined "
            "line, or a baselined file no longer holds it). Not a finding, "
            "and not fatal; clear it when convenient with "
            "`uv run python scripts/secrets_scan.py --refresh-baseline`."
        )
        return 0
    print("secrets_scan: clean")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh-baseline",
        action="store_true",
        help=(
            "Rewrite .secrets.baseline in place to clear line-number drift. "
            "Never adds an entry — review the diff and commit it yourself."
        ),
    )
    args = parser.parse_args()

    if args.refresh_baseline:
        if shutil.which("detect-secrets-hook") is None:
            print(
                "secrets_scan: --refresh-baseline needs detect-secrets-hook "
                "— run `uv sync --all-packages --extra dev` first",
                file=sys.stderr,
            )
            return 2
        return _run_detect_secrets(refresh=True)

    if shutil.which("gitleaks"):
        return _run_gitleaks()
    return _run_detect_secrets()


if __name__ == "__main__":
    sys.exit(main())
