# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Repository files a test may need that not every checkout ships.

Test infrastructure only. The Docker test image and the public export carry
the packages and their tests, not ``internal/`` (reference packs, archived
runs, the holdout) and not every docs asset. A test that reads one of those
skips where it is absent instead of failing the image (audit wave 5: two
leaderboard tests failed the push gate on a missing ``packages/docs`` file;
five earlier tests each wrote their own ``skipif`` for the v3 pack).
"""

from __future__ import annotations

from pathlib import Path

import pytest


def repo_root() -> Path:
    """The monorepo root: the directory holding ``packages/`` (never a package's own root)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "packages").is_dir() and (parent / "pyproject.toml").is_file():
            return parent
    msg = f"no monorepo root above {here}"
    raise FileNotFoundError(msg)


def repo_file_or_skip(relative: str) -> Path:
    """``relative`` under the repo root, or ``pytest.skip`` when this checkout does not ship it.

    Args:
        relative: A path relative to the repo root, e.g.
            ``"internal/benchmarks/reference/war_and_peace_book1-v3/app.db"``.

    Returns:
        The absolute path, which exists.
    """
    path = repo_root() / relative
    if not path.exists():
        pytest.skip(f"{relative} is not in this checkout (Docker test image or public export)")
    return path


__all__ = ["repo_file_or_skip", "repo_root"]
