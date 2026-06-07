# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Info - Get information about .ccx package archives.

Provides utilities for inspecting archive contents without extraction.

Example:
    from chaoscypher_core.services.package.archive import get_archive_info

    info = get_archive_info(Path("./my-package.ccx"))
    print(f"Size: {info.compressed_size}, Files: {info.file_count}")
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chaoscypher_core.exceptions import NotFoundError


if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class ArchiveInfo:
    """Information about a .ccx archive.

    Attributes:
        compressed_size: Size of the archive file in bytes.
        uncompressed_size: Total uncompressed size of all files in bytes.
        file_count: Number of files in the archive.
        contents: List of file paths in the archive.
    """

    compressed_size: int
    uncompressed_size: int
    file_count: int
    contents: tuple[str, ...]

    @property
    def compressed_size_formatted(self) -> str:
        """Get formatted compressed size."""
        return format_size(self.compressed_size)

    @property
    def uncompressed_size_formatted(self) -> str:
        """Get formatted uncompressed size."""
        return format_size(self.uncompressed_size)


def get_archive_info(archive_path: Path) -> ArchiveInfo:
    """Get information about a .ccx archive.

    Inspects the archive without extracting to get size and content info.

    Args:
        archive_path: Path to the archive file.

    Returns:
        ArchiveInfo with size and content details.

    Raises:
        NotFoundError: If archive doesn't exist.
        zipfile.BadZipFile: If archive is corrupted.

    Example:
        >>> info = get_archive_info(Path("./package.ccx"))
        >>> print(f"Compressed: {info.compressed_size_formatted}")
        >>> print(f"Files: {info.file_count}")
    """
    if not archive_path.exists():
        raise NotFoundError("Archive", str(archive_path))

    compressed_size = archive_path.stat().st_size
    uncompressed_size = 0
    contents: list[str] = []

    with zipfile.ZipFile(archive_path, "r") as zipf:
        for member in zipf.infolist():
            contents.append(member.filename)
            if not member.is_dir():
                uncompressed_size += member.file_size

    return ArchiveInfo(
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
        file_count=len(contents),
        contents=tuple(contents),
    )


def format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string.

    Standalone function for convenience.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Formatted string (e.g., "1.5 MB").

    Example:
        >>> format_size(1536000)
        '1.5 MB'
    """
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


__all__ = [
    "ArchiveInfo",
    "format_size",
    "get_archive_info",
]
