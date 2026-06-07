# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Safe path-resolution helpers that defeat symlink and traversal escapes.

Use ``resolve_within(base, candidate)`` wherever an external caller supplies
a filesystem path that must stay inside a sandbox directory (e.g. MCP tool
arguments, user-supplied upload paths).
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.exceptions import ChaosCypherException


class PathOutsideSandboxError(ChaosCypherException):
    """Raised when a candidate path resolves outside the sandbox base.

    Maps to HTTP 400 (bad request) at the API layer.
    """

    def __init__(self, candidate: str, base: str) -> None:
        """Initialize the error with the offending candidate and sandbox base.

        Args:
            candidate: The caller-supplied path that failed validation.
            base: The sandbox base directory the candidate must stay within.

        """
        super().__init__(
            message=f"Path '{candidate}' is outside sandbox '{base}'",
            code="PATH_OUTSIDE_SANDBOX",
            details={"candidate": candidate, "base": base},
        )


def resolve_within(base: Path | str, candidate: Path | str) -> Path:
    """Resolve *candidate* underneath *base* and reject any escape.

    Steps:
        1. Reject candidates containing a ``..`` segment (traversal).
        2. Resolve to an absolute path:
           - **Absolute candidate**: resolve in place with ``strict=True``.
             Accepted iff the resolved path is inside *base*. This makes
             the helper idempotent — passing the helper's own output back
             in returns the same path instead of raising.
           - **Relative candidate**: resolve as ``(base / candidate)``
             with ``strict=True``. Missing files raise.
        3. Assert the resolved path is equal to or under ``base.resolve()``.
           Symlink escapes (a relative entry pointing at an outside
           target) are caught here.

    Idempotency matters because the MCP ``add_document`` handler resolves
    once at the outer entry point (to validate the caller's input) and
    then the inner pipeline resolves the SAME value again as a
    defense-in-depth check. Pre-2026-05 the helper rejected all absolute
    candidates, so the inner call always failed with
    ``PathOutsideSandboxError`` — and the error message ("Path X is
    outside sandbox Y") lied, because X was literally inside Y; the only
    issue was that X was absolute. Accepting absolute-but-contained
    paths fixes both the silent-failure and the misleading error.

    Args:
        base: Trusted sandbox directory.
        candidate: File path. Either relative to *base*, or absolute
            iff the path resolves inside *base*.

    Returns:
        The fully-resolved absolute ``Path`` inside *base*.

    Raises:
        PathOutsideSandboxError: If the candidate escapes the sandbox via
            ``..``, an absolute path outside *base*, a missing file, or
            a symlink pointing outside *base*.

    """
    base_path = Path(base)
    candidate_path = Path(candidate)

    # Traversal is always rejected, regardless of absolute vs relative.
    if any(part == ".." for part in candidate_path.parts):
        raise PathOutsideSandboxError(str(candidate), str(base))

    # Choose the resolution strategy by absoluteness, but apply the
    # SAME containment check at the end either way. Absolute paths
    # that happen to be inside the sandbox are legitimate (e.g. the
    # outer call already resolved them); absolute paths outside the
    # sandbox are rejected by the relative_to check below.
    try:
        if candidate_path.is_absolute():
            resolved = candidate_path.resolve(strict=True)
        else:
            resolved = (base_path / candidate_path).resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise PathOutsideSandboxError(str(candidate), str(base)) from exc

    resolved_base = base_path.resolve()
    try:
        resolved.relative_to(resolved_base)
    except ValueError as exc:
        raise PathOutsideSandboxError(str(candidate), str(base)) from exc

    return resolved
