# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the safe_paths.resolve_within helper."""

from pathlib import Path

import pytest

from chaoscypher_core.utils.safe_paths import (
    PathOutsideSandboxError,
    resolve_within,
)


class TestResolveWithin:
    """resolve_within: reject traversal, reject absolute, reject symlink escapes."""

    def test_plain_relative_path_resolves(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("hello")
        resolved = resolve_within(tmp_path, "a.txt")
        assert resolved == target.resolve()

    def test_nested_relative_path_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("x")
        resolved = resolve_within(tmp_path, "sub/b.txt")
        assert resolved == (tmp_path / "sub" / "b.txt").resolve()

    def test_absolute_path_inside_sandbox_accepted(self, tmp_path: Path) -> None:
        """An absolute path that resolves inside the sandbox is accepted.

        Idempotency: ``resolve_within(base, resolve_within(base, "x"))``
        must return the same path as ``resolve_within(base, "x")``. Pre-
        2026-05 the helper rejected all absolute paths, so the MCP
        add_document handler's double-resolution (outer validation +
        inner pipeline) blew up with a misleading "outside sandbox"
        error for paths that were literally inside.
        """
        target = tmp_path / "a.txt"
        target.write_text("hello")
        absolute = str(target)
        resolved = resolve_within(tmp_path, absolute)
        assert resolved == target.resolve()

    def test_resolve_within_is_idempotent(self, tmp_path: Path) -> None:
        """Calling resolve_within twice on the same input is a no-op.

        Pin the contract explicitly: any helper that takes a Path or str
        and returns a sandbox-resolved Path should accept its own output
        back without raising. Catches the regression class where the
        first call returned absolute and the second call rejected it.
        """
        target = tmp_path / "nested" / "x.txt"
        target.parent.mkdir()
        target.write_text("y")
        first = resolve_within(tmp_path, "nested/x.txt")
        second = resolve_within(tmp_path, first)
        third = resolve_within(tmp_path, str(first))
        assert first == second == third == target.resolve()

    def test_absolute_path_outside_sandbox_rejected(self, tmp_path: Path) -> None:
        """An absolute path that resolves OUTSIDE the sandbox is rejected.

        The containment check (``resolved.relative_to(base)``) is the
        actual security boundary; absoluteness alone is not.
        """
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("leak")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        with pytest.raises(PathOutsideSandboxError):
            resolve_within(sandbox, str(outside))

    def test_parent_traversal_rejected(self, tmp_path: Path) -> None:
        (tmp_path / "inside").mkdir()
        with pytest.raises(PathOutsideSandboxError):
            resolve_within(tmp_path / "inside", "../outside.txt")

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(PathOutsideSandboxError):
            resolve_within(tmp_path, "nope.txt")

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "secret.txt"
        outside.write_text("leak")
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        link = sandbox / "evil"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):  # fmt: skip
            pytest.skip("symlinks not supported on this filesystem")
        with pytest.raises(PathOutsideSandboxError):
            resolve_within(sandbox, "evil")

    def test_returns_pathlib_path(self, tmp_path: Path) -> None:
        (tmp_path / "x.txt").write_text("y")
        assert isinstance(resolve_within(tmp_path, "x.txt"), Path)
