# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Security tests for archive containment in extract_archive.

Verifies that path-traversal and sibling-prefix attacks are rejected
regardless of which guard catches them (dot-dot check or is_relative_to).
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from chaoscypher_core.services.package.archive.extract import (
    ArchiveSecurityError,
    extract_archive,
)


@pytest.mark.unit
def test_extract_rejects_sibling_prefix_path(tmp_path: Path) -> None:
    """A member whose effective path lands in a sibling dir (dest+suffix) must be rejected.

    The canonical sibling-prefix attack uses "../outx/pwn" which resolves to a
    sibling directory.  The dot-dot guard (line ~181) catches this particular
    vector before the is_relative_to check is reached, so the error message will
    say "traversal" rather than "escapes".  Both messages come from
    ArchiveSecurityError, confirming the attack is blocked.  The is_relative_to
    fix is still applied as defense-in-depth for any vector that somehow bypasses
    the dot-dot guard (e.g., OS-specific normalisation edge cases).
    """
    archive = tmp_path / "evil.zip"
    dest = tmp_path / "out"
    dest.mkdir()

    with zipfile.ZipFile(archive, "w") as zf:
        # "../outx/pwn" — once resolved against dest=/tmp/out, lands at /tmp/outx/pwn
        # which startswith("/tmp/out") but is NOT relative_to.
        zf.writestr("../outx/pwn", "owned")

    # Either the dot-dot guard ("traversal") or the containment guard ("escapes")
    # must fire — both mean the attack is blocked.
    with pytest.raises(ArchiveSecurityError, match="traversal|escapes"):
        extract_archive(archive, dest)
