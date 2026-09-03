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


@pytest.mark.unit
def test_extract_rejects_symlink_member(tmp_path: Path) -> None:
    """A Unix symlink member must be rejected before anything is written.

    The blocked-file-type check lives in the first-pass validation loop; a
    symlink extracted from a malicious archive can leak host files via the
    RAG indexer.
    """
    import stat

    archive = tmp_path / "evil-symlink.zip"
    dest = tmp_path / "out"
    dest.mkdir()

    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("innocuous.txt")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")

    with pytest.raises(ArchiveSecurityError, match="Unsafe file type"):
        extract_archive(archive, dest)

    assert not (dest / "innocuous.txt").exists()


@pytest.mark.unit
def test_extract_rejects_symlink_member_with_strip_components(tmp_path: Path) -> None:
    """The file-type check is name-independent — stripping must not bypass it."""
    import stat

    archive = tmp_path / "evil-symlink-nested.zip"
    dest = tmp_path / "out"
    dest.mkdir()

    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("pkg/inner.txt")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")

    with pytest.raises(ArchiveSecurityError, match="Unsafe file type"):
        extract_archive(archive, dest, strip_components=1)


# ---------------------------------------------------------------------------
# Volume limits (zip-bomb defenses, mirrored from ArchiveExtractor)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_extract_rejects_too_many_members(tmp_path: Path) -> None:
    archive = tmp_path / "many.zip"
    dest = tmp_path / "out"
    with zipfile.ZipFile(archive, "w") as zf:
        for i in range(4):
            zf.writestr(f"f{i}.txt", "x")

    with pytest.raises(ArchiveSecurityError, match="file limit"):
        extract_archive(archive, dest, max_files=3)


@pytest.mark.unit
def test_extract_rejects_declared_size_over_limit(tmp_path: Path) -> None:
    archive = tmp_path / "big.zip"
    dest = tmp_path / "out"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("big.txt", "a" * 4096)

    with pytest.raises(ArchiveSecurityError, match="declared size limit"):
        extract_archive(archive, dest, max_total_bytes=1024)


@pytest.mark.unit
def test_extract_streaming_tally_stops_lying_member(tmp_path: Path, monkeypatch) -> None:
    """A member stream yielding more bytes than declared trips the byte tally.

    CPython's ``ZipExtFile`` bounds reads by the declared size, so the tally
    is defense-in-depth (crafted archives, future zipfile behavior changes).
    Simulate the overrun by faking the member stream: declared sizes stay
    tiny (passing the pre-check), the stream carries 4096 real bytes.
    """
    import io

    archive = tmp_path / "liar.zip"
    dest = tmp_path / "out"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("liar.txt", "tiny")

    monkeypatch.setattr(
        zipfile.ZipFile,
        "open",
        lambda self, member, mode="r": io.BytesIO(b"a" * 4096),
    )
    with pytest.raises(ArchiveSecurityError, match="possible zip bomb"):
        extract_archive(archive, dest, max_total_bytes=1024)
    assert not (dest / "liar.txt").exists()  # partial output removed


@pytest.mark.unit
def test_extract_within_limits_succeeds(tmp_path: Path) -> None:
    archive = tmp_path / "ok.zip"
    dest = tmp_path / "out"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "hello")
        zf.writestr("sub/b.txt", "world")

    extract_archive(archive, dest, max_files=10, max_total_bytes=1024)

    assert (dest / "a.txt").read_text() == "hello"
    assert (dest / "sub" / "b.txt").read_text() == "world"
