# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``ArchiveExtractor``'s security guards, on the source-upload path.

The repo keeps two hand-maintained copies of the same archive checks:

* the ``.ccx`` import path — ``extract_archive`` →
  ``_validate_member_for_extraction`` — which has security tests in
  ``tests/unit/services/package/archive/test_extract.py``; and
* the user-upload source path — ``ArchiveLoader`` → ``ArchiveExtractor`` →
  ``_validate_zip_member`` / ``_validate_tar_member`` — which had none.

Every ``raise`` in the upload-path pair was executed by zero tests, so
replacing both validators with no-ops left the whole suite green while the
same mutation on the ``.ccx`` sibling failed three tests. The guards
themselves work; what was missing was any cover to notice if they stopped.
``extract.py:120-124`` records that the two copies have already silently
diverged once in production, which is what makes the gap worth closing.

Each test drives a real malicious archive through the public
``ArchiveExtractor.extract()`` and also asserts nothing landed outside the
destination directory.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

from chaoscypher_core.services.sources.loaders.archive.exceptions import ArchiveSecurityError
from chaoscypher_core.services.sources.loaders.archive.extractor import ArchiveExtractor
from chaoscypher_core.settings import EngineSettings, PathSettings


_S_IFLNK = 0o120000


def _extractor(tmp_path: Path) -> ArchiveExtractor:
    settings = EngineSettings(paths=PathSettings(data_dir=str(tmp_path)))
    return ArchiveExtractor(settings=settings)


def _write_zip(archive: Path, members: list[tuple[str, bytes, int]]) -> None:
    """Write a zip whose members carry explicit ``external_attr`` modes."""
    with zipfile.ZipFile(archive, "w") as zf:
        for name, data, unix_mode in members:
            info = zipfile.ZipInfo(filename=name)
            if unix_mode:
                info.external_attr = unix_mode << 16
            zf.writestr(info, data)


def _write_tar_gz(archive: Path, members: list[tarfile.TarInfo], payload: bytes = b"x") -> None:
    with tarfile.open(archive, "w:gz") as tf:
        for info in members:
            if info.type == tarfile.SYMTYPE:
                tf.addfile(info)
            else:
                info.size = len(payload)
                tf.addfile(info, io.BytesIO(payload))


def _regular(name: str) -> tarfile.TarInfo:
    return tarfile.TarInfo(name=name)


def _symlink(name: str, target: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.type = tarfile.SYMTYPE
    info.linkname = target
    info.size = 0
    return info


def _assert_nothing_escaped(tmp_path: Path, dest: Path) -> None:
    """No file may exist under tmp_path outside dest, bar the archive itself."""
    strays = [
        p
        for p in tmp_path.rglob("*")
        if p.is_file() and dest not in p.parents and p.suffix not in {".zip", ".gz"}
    ]
    assert strays == [], f"extraction wrote outside dest: {strays}"


class TestZipMemberSecurity:
    """``_validate_zip_member`` — the upload path's zip guard."""

    def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.zip"
        _write_zip(archive, [("../escaped.txt", b"pwned", 0)])
        dest = tmp_path / "out"

        with pytest.raises(ArchiveSecurityError, match="Path traversal"):
            _extractor(tmp_path).extract(archive, dest)

        _assert_nothing_escaped(tmp_path, dest)

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.zip"
        _write_zip(archive, [("/etc/cron.d/pwned", b"pwned", 0)])
        dest = tmp_path / "out"

        with pytest.raises(ArchiveSecurityError, match="Absolute path"):
            _extractor(tmp_path).extract(archive, dest)

        _assert_nothing_escaped(tmp_path, dest)

    def test_rejects_symlink_member(self, tmp_path: Path) -> None:
        """A zip symlink is a host-file read primitive for the RAG indexer."""
        archive = tmp_path / "evil.zip"
        _write_zip(archive, [("link.txt", b"/etc/passwd", _S_IFLNK | 0o777)])
        dest = tmp_path / "out"

        with pytest.raises(ArchiveSecurityError, match="Unsafe file type"):
            _extractor(tmp_path).extract(archive, dest)

        _assert_nothing_escaped(tmp_path, dest)

    def test_accepts_a_benign_member(self, tmp_path: Path) -> None:
        """Negative control: the guard must not reject an ordinary document."""
        archive = tmp_path / "good.zip"
        _write_zip(archive, [("docs/readme.txt", b"hello", 0o100644)])
        dest = tmp_path / "out"

        _extractor(tmp_path).extract(archive, dest)

        assert (dest / "docs" / "readme.txt").read_bytes() == b"hello"


class TestTarMemberSecurity:
    """``_validate_tar_member`` — the upload path's tar.gz guard."""

    def test_rejects_parent_traversal(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.tar.gz"
        _write_tar_gz(archive, [_regular("../escaped.txt")])
        dest = tmp_path / "out"

        with pytest.raises(ArchiveSecurityError, match="Path traversal"):
            _extractor(tmp_path).extract(archive, dest)

        _assert_nothing_escaped(tmp_path, dest)

    def test_rejects_absolute_path(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.tar.gz"
        _write_tar_gz(archive, [_regular("/etc/cron.d/pwned")])
        dest = tmp_path / "out"

        with pytest.raises(ArchiveSecurityError, match="Absolute path"):
            _extractor(tmp_path).extract(archive, dest)

        _assert_nothing_escaped(tmp_path, dest)

    def test_rejects_symlink_member(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.tar.gz"
        _write_tar_gz(archive, [_symlink("passwd.txt", "/etc/passwd")])
        dest = tmp_path / "out"

        with pytest.raises(ArchiveSecurityError, match="Symlinks in archive"):
            _extractor(tmp_path).extract(archive, dest)

        _assert_nothing_escaped(tmp_path, dest)

    def test_accepts_a_benign_member(self, tmp_path: Path) -> None:
        """Negative control for the tar guard."""
        archive = tmp_path / "good.tar.gz"
        _write_tar_gz(archive, [_regular("docs/readme.txt")], payload=b"hello")
        dest = tmp_path / "out"

        _extractor(tmp_path).extract(archive, dest)

        assert (dest / "docs" / "readme.txt").read_bytes() == b"hello"
