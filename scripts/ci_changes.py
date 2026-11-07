#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Which parts of CI a change needs: the CI workflow's ``changes`` job.

One detection for every CI job that runs only for some changes (until 2026-11-06 the image
build carried its own inline copy). Writes ``key=true|false`` lines to ``$GITHUB_OUTPUT``:

* ``code``: false only when every changed path is private operations material (the
  ``internal/`` tree, which the public export strips, outside the parts shipped tests read).
  The heavy jobs (quality, unit shards, coverage, frontend, gates, audits, image) run on
  ``code``; one small job runs the checks that do read that tree in their place. A change the
  detection cannot place (no range, an empty diff, a git error) is ``code``.
* ``docker``: a path that changes what ``docker build`` produces.

The range. A pull request: its whole diff against the base branch (``origin/<base>...HEAD``),
so a superseded run cancelled half-way loses nothing. A push to main: everything since the
newest commit of this branch whose CI run succeeded (``<green>..HEAD``), not since the commit
before the push: a superseded run on main is cancelled, and a code change it carried must not
be skipped because the newest push changed only private files. With no green ancestor (or the
runs list unreadable), everything runs.

Stdlib only, Python 3.11 (the runner's system ``python3`` runs it before any setup).

Usage:  python3 scripts/ci_changes.py --workflow ci.yml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Iterable, Sequence


# The private operations tree: routines' runbooks, plans, metrics and their tools. Stripped from
# the public export, so nothing that ships is built from it.
PRIVATE_TREE = "internal/"
# Parts of that tree the shipped packages' unit tests read (the benchmark reference packs,
# holdouts, run archive and tools), so a change there runs the full suite.
READ_BY_SHIPPED_TESTS = (PRIVATE_TREE + "benchmarks/",)

# The paths that change what `docker build` produces: the docker tree itself (Dockerfile,
# entrypoint, nginx/supervisor configs, digest bumps), the Python manifests and lockfile the
# image installs from, and the frontend manifest it builds from.
DOCKER = re.compile(
    r"^(packages/docker/|pyproject\.toml$|uv\.lock$|packages/[^/]+/pyproject\.toml$"
    r"|packages/interface/package(-lock)?\.json$)"
)

Git = Callable[[Sequence[str]], str]


def is_private_only(path: str) -> bool:
    """A path no shipped artifact, test or gate outside the private tree's own checks reads."""
    return path.startswith(PRIVATE_TREE) and not path.startswith(READ_BY_SHIPPED_TESTS)


def classify(paths: Sequence[str] | None) -> dict[str, bool]:
    """The outputs for a changed-path list; ``None`` (or empty) is a change that could be anything."""
    if not paths:
        return {"code": True, "docker": True}
    return {
        "code": not all(is_private_only(p) for p in paths),
        "docker": any(DOCKER.match(p) for p in paths),
    }


def push_base(
    head: str, green: Iterable[str], is_ancestor: Callable[[str, str], bool]
) -> str | None:
    """The newest green commit that ``head`` builds on (newest first in ``green``), or None."""
    for sha in green:
        if sha != head and is_ancestor(sha, head):
            return sha
    return None


def changed_range(
    event: str,
    base_ref: str,
    head: str,
    green: Callable[[], list[str]],
    is_ancestor: Callable[[str, str], bool],
) -> str | None:
    """The git range whose diff decides, or None when there is no trustworthy one."""
    if event == "pull_request":
        return f"origin/{base_ref}...HEAD" if base_ref else None
    if event == "push":
        base = push_base(head, green(), is_ancestor)
        return f"{base}..HEAD" if base else None
    return None


def green_runs(api: str, repo: str, workflow: str, branch: str, token: str) -> list[str]:
    """Head SHAs of this workflow's successful push runs on ``branch``, newest first."""
    url = (
        f"{api}/repos/{repo}/actions/workflows/{workflow}/runs"
        f"?branch={branch}&event=push&status=success&per_page=50"
    )
    request = urllib.request.Request(  # noqa: S310 - a fixed https API endpoint
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        runs = json.load(response).get("workflow_runs", [])
    return [str(run["head_sha"]) for run in runs if run.get("head_sha")]


def _git(args: Sequence[str]) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _ancestor(git: Git) -> Callable[[str, str], bool]:
    def check(older: str, newer: str) -> bool:
        try:
            git(["merge-base", "--is-ancestor", older, newer])
        except subprocess.CalledProcessError:
            return False
        return True

    return check


def detect(environ: dict[str, str], workflow: str, git: Git = _git) -> tuple[dict[str, bool], str]:
    """(outputs, why) for the run ``environ`` describes."""
    event = environ.get("GITHUB_EVENT_NAME", "")
    base_ref = environ.get("GITHUB_BASE_REF", "")
    head = git(["rev-parse", "HEAD"]).strip()

    def green() -> list[str]:
        try:
            return green_runs(
                environ.get("GITHUB_API_URL", "https://api.github.com"),
                environ["GITHUB_REPOSITORY"],
                workflow,
                environ.get("GITHUB_REF_NAME", "main"),
                environ.get("GITHUB_TOKEN", ""),
            )
        except (OSError, KeyError, ValueError) as exc:
            print(f"Could not list the green {workflow} runs ({exc}).")
            return []

    if event == "pull_request" and base_ref:
        git(["fetch", "-q", "origin", base_ref])
    rng = changed_range(event, base_ref, head, green, _ancestor(git))
    if rng is None:
        return classify(None), f"no range to diff for event {event!r}: run everything"
    # --no-renames: a file moved out of a shipped tree lists its old path too.
    try:
        paths = [p for p in git(["diff", "--name-only", "--no-renames", rng]).splitlines() if p]
    except subprocess.CalledProcessError as exc:
        return classify(None), f"git diff {rng} failed ({exc.returncode}): run everything"
    return classify(paths), f"{len(paths)} changed path(s) in {rng}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--workflow", required=True, help="this workflow's file name (ci.yml)")
    args = parser.parse_args(argv)
    outputs, why = detect(dict(os.environ), args.workflow)
    print(why)
    lines = [f"{key}={str(value).lower()}" for key, value in outputs.items()]
    print("\n".join(lines))
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
