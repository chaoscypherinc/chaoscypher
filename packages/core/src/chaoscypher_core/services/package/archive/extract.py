# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Extraction - Extract .ccx package archives.

Extracts ZIP archives to directories with security validation.
Prevents path traversal attacks and validates archive integrity.

Example:
    from chaoscypher_core.services.package.archive import extract_archive

    extract_archive(
        archive_path=Path("./my-package.ccx"),
        dest_dir=Path("./extracted"),
    )
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.exceptions import NotFoundError, ValidationError


if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)


class ArchiveSecurityError(ValidationError):
    """Raised when archive contains unsafe paths or content."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        """Initialize archive security error.

        Args:
            message: Description of the security violation.
            details: Additional error details.
        """
        super().__init__(message=message, field="archive", details=details)


# Unix file-type mask in ZipInfo.external_attr upper 16 bits
_S_IFMT = 0o170000
_S_IFLNK = 0o120000  # symbolic link
_S_IFCHR = 0o020000  # character device
_S_IFBLK = 0o060000  # block device
_S_IFIFO = 0o010000  # FIFO / named pipe
_BLOCKED_FILE_TYPES = (_S_IFLNK, _S_IFCHR, _S_IFBLK, _S_IFIFO)


def _zip_member_unix_mode(member: zipfile.ZipInfo) -> int:
    """Return the Unix file-type bits (symlink/device/fifo) from a ZipInfo.

    ``external_attr`` packs Unix mode bits in its upper 16 bits when the
    creating OS was Unix-like. Returns 0 when no Unix mode is present.
    """
    return (member.external_attr >> 16) & _S_IFMT


def _validate_zip_member(member: zipfile.ZipInfo, dest_dir: Path) -> None:
    """Validate a ZIP archive member for security issues.

    Checks for:
    - Absolute paths
    - Path traversal (``..``)
    - Paths escaping the destination directory
    - Symlinks, character/block devices, FIFOs (reading through a symlink
      extracted from a malicious archive can leak host files via the RAG
      indexer; devices/FIFOs have no legitimate use in a document archive)

    Raises:
        ArchiveSecurityError: If the member is unsafe.

    """
    # Reject Unix-side symlinks / devices / FIFOs before any path work.
    unix_mode = _zip_member_unix_mode(member)
    if unix_mode in _BLOCKED_FILE_TYPES:
        msg = f"Unsafe file type in archive ({oct(unix_mode)}): {member.filename}"
        raise ArchiveSecurityError(msg)

    member_path = Path(member.filename)

    # Check for absolute paths
    if member_path.is_absolute():
        msg = f"Absolute path in archive is not allowed: {member.filename}"
        raise ArchiveSecurityError(msg)

    # Check for path traversal
    if ".." in member_path.parts:
        msg = f"Path traversal in archive is not allowed: {member.filename}"
        raise ArchiveSecurityError(msg)

    # Resolve the full path and check it's within dest_dir (is_relative_to is
    # the correct containment check — str.startswith has sibling-prefix bugs).
    try:
        full_path = (dest_dir / member_path).resolve()
        dest_resolved = dest_dir.resolve()
        if not full_path.is_relative_to(dest_resolved):
            msg = f"Path escapes destination directory: {member.filename}"
            raise ArchiveSecurityError(msg)
    except (OSError, ValueError) as e:
        msg = f"Invalid path in archive: {member.filename} ({e})"
        raise ArchiveSecurityError(msg) from e


def extract_archive(
    archive_path: Path,
    dest_dir: Path,
    *,
    strip_components: int = 0,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> Path:
    """Extract a .ccx package archive to a directory.

    Extracts a ZIP archive with security validation to prevent
    path traversal attacks.

    Args:
        archive_path: Path to the .ccx archive file.
        dest_dir: Destination directory for extraction.
        strip_components: Number of leading path components to strip.
        progress_callback: Optional callback(filename, current, total) for progress.

    Returns:
        Path to the extraction directory.

    Raises:
        NotFoundError: If archive doesn't exist.
        zipfile.BadZipFile: If archive is corrupted or invalid.
        ArchiveSecurityError: If archive contains unsafe paths.
        PermissionError: If unable to read archive or write to destination.

    Example:
        >>> extract_archive(
        ...     Path("./package.ccx"),
        ...     Path("./extracted/"),
        ... )
        PosixPath('./extracted')
    """
    if not archive_path.exists():
        raise NotFoundError("Archive", str(archive_path))

    dest_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "extracting_archive",
        archive=str(archive_path),
        destination=str(dest_dir),
        strip_components=strip_components,
    )

    with zipfile.ZipFile(archive_path, "r") as zipf:
        members = zipf.infolist()
        total_members = len(members)

        # First pass: validate all members (without mutating filenames)
        for member in members:
            # Skip directories
            if member.is_dir():
                continue

            # Compute the effective name after stripping, without mutating member
            effective_name = member.filename
            if strip_components > 0:
                parts = Path(effective_name).parts
                if len(parts) <= strip_components:
                    continue  # Skip this member entirely
                effective_name = str(Path(*parts[strip_components:]))

            # Replicate _validate_zip_member checks against the effective name
            effective_path = Path(effective_name)
            if effective_path.is_absolute():
                msg = f"Absolute path in archive is not allowed: {effective_name}"
                raise ArchiveSecurityError(msg)
            if ".." in effective_path.parts:
                msg = f"Path traversal in archive is not allowed: {effective_name}"
                raise ArchiveSecurityError(msg)
            try:
                full_path = (dest_dir / effective_path).resolve()
                dest_resolved = dest_dir.resolve()
                if not full_path.is_relative_to(dest_resolved):
                    msg = f"Path escapes destination directory: {effective_name}"
                    raise ArchiveSecurityError(msg)
            except (OSError, ValueError) as e:
                msg = f"Invalid path in archive: {effective_name} ({e})"
                raise ArchiveSecurityError(msg) from e

        # Second pass: extract validated members
        for idx, member in enumerate(members):
            # Skip directories (they're created automatically)
            if member.is_dir():
                continue

            # Apply strip_components again for actual extraction
            original_filename = member.filename
            if strip_components > 0:
                parts = Path(member.filename).parts
                if len(parts) <= strip_components:
                    continue
                member.filename = str(Path(*parts[strip_components:]))

            zipf.extract(member, dest_dir)

            # Restore original filename for next iteration if needed
            member.filename = original_filename

            if progress_callback:
                progress_callback(member.filename, idx + 1, total_members)

    logger.info(
        "archive_extracted",
        destination=str(dest_dir),
        file_count=total_members,
    )

    return dest_dir


__all__ = [
    "ArchiveSecurityError",
    "extract_archive",
]
