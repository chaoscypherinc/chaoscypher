# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Extraction Utilities.

Handles secure extraction of ZIP and TAR.GZ archives with validation.
Reuses security patterns from existing package archive module.

Example:
    from chaoscypher_core.services.sources.loaders.archive import (
        ArchiveExtractor,
    )

    extractor = ArchiveExtractor()
    extracted_path = extractor.extract(archive_path, dest_dir)
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.exceptions import NotFoundError
from chaoscypher_core.services.package.archive.extract import (
    ArchiveSecurityError,
    _validate_zip_member,
)
from chaoscypher_core.services.sources.loaders.archive.exceptions import (
    ArchiveExtractionError,
    UnsupportedArchiveError,
)


if TYPE_CHECKING:
    from collections.abc import Callable

    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class ArchiveExtractor:
    """Secure archive extraction for documentation archives.

    Supports:
    - ZIP archives (.zip)
    - Gzipped TAR archives (.tar.gz, .tgz)

    Security:
    - Path traversal prevention
    - Absolute path rejection
    - Symlink validation
    - Size limits (configurable)
    - File count limits (configurable)
    """

    def __init__(
        self,
        settings: EngineSettings,
        max_size: int | None = None,
        max_files: int | None = None,
    ) -> None:
        """Initialize extractor with settings.

        Args:
            settings: Engine settings for configuration (required).
            max_size: Override maximum extracted size in bytes.
            max_files: Override maximum number of files to extract.
        """
        self.settings = settings
        archive_settings = settings.archive
        self.max_size = max_size or archive_settings.max_extracted_size_mb * 1024 * 1024
        self.max_files = max_files or archive_settings.max_files

    def extract(
        self,
        archive_path: Path,
        dest_dir: Path,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> Path:
        """Extract archive to destination directory.

        Args:
            archive_path: Path to archive file.
            dest_dir: Destination directory for extraction.
            progress_callback: Optional callback(filename, current, total).

        Returns:
            Path to extraction directory.

        Raises:
            NotFoundError: If archive doesn't exist.
            UnsupportedArchiveError: If archive format is unsupported.
            ArchiveExtractionError: If extraction fails.
            ArchiveSecurityError: If archive contains unsafe paths.
        """
        if not archive_path.exists():
            raise NotFoundError("Archive", str(archive_path))

        archive_type = self._get_archive_type(archive_path)

        logger.info(
            "archive_extraction_started",
            archive=str(archive_path),
            destination=str(dest_dir),
            archive_type=archive_type,
            max_size=self.max_size,
            max_files=self.max_files,
        )

        dest_dir.mkdir(parents=True, exist_ok=True)

        try:
            if archive_type == "zip":
                self._extract_zip(archive_path, dest_dir, progress_callback)
            elif archive_type == "tar.gz":
                self._extract_tar_gz(archive_path, dest_dir, progress_callback)
            else:
                msg = f"Unsupported archive type: {archive_type}"
                raise UnsupportedArchiveError(msg)

            logger.info(
                "archive_extraction_complete",
                destination=str(dest_dir),
            )

            return dest_dir

        except (ArchiveSecurityError, UnsupportedArchiveError):  # fmt: skip
            raise
        except Exception as e:
            msg = f"Failed to extract archive: {e}"
            raise ArchiveExtractionError(msg) from e

    def _get_archive_type(self, archive_path: Path) -> str:
        """Determine archive type from extension.

        Args:
            archive_path: Path to archive file.

        Returns:
            Archive type string: 'zip' or 'tar.gz'.

        Raises:
            UnsupportedArchiveError: If format is not supported.
        """
        name = archive_path.name.lower()

        if name.endswith(".zip"):
            return "zip"

        if name.endswith((".tar.gz", ".tgz")):
            return "tar.gz"

        # Try to detect from magic bytes
        try:
            with archive_path.open("rb") as f:
                magic = f.read(4)

            # ZIP magic: PK\x03\x04
            if magic[:4] == b"PK\x03\x04":
                return "zip"

            # Gzip magic: \x1f\x8b
            if magic[:2] == b"\x1f\x8b":
                return "tar.gz"

        except Exception:
            logger.debug("archive_magic_byte_check_failed", path=str(archive_path))

        msg = f"Unsupported archive format: {archive_path.suffix}"
        raise UnsupportedArchiveError(msg)

    def _extract_zip(
        self,
        archive_path: Path,
        dest_dir: Path,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        """Extract ZIP archive with security validation.

        Args:
            archive_path: Path to ZIP file.
            dest_dir: Destination directory.
            progress_callback: Optional progress callback.

        Raises:
            ArchiveSecurityError: If archive contains unsafe paths.
            ArchiveExtractionError: If extraction fails or limits exceeded.
        """
        with zipfile.ZipFile(archive_path, "r") as zipf:
            members = zipf.infolist()
            total_members = len(members)

            # Check file count limit
            if total_members > self.max_files:
                msg = f"Archive exceeds file limit: {total_members} > {self.max_files}"
                raise ArchiveExtractionError(msg)

            # Declared-size quick reject (cheap — zip bombs with small declared
            # sizes still pass this but will hit the actual-bytes cap below).
            declared_total = sum(m.file_size for m in members)
            if declared_total > self.max_size:
                msg = f"Archive exceeds declared size limit: {declared_total} > {self.max_size}"
                raise ArchiveExtractionError(msg)

            # First pass: validate all members (symlinks, traversal, etc.)
            for member in members:
                if member.is_dir():
                    continue
                _validate_zip_member(member, dest_dir)

            # Second pass: stream-extract with per-byte accounting so that
            # a zip bomb claiming tiny file_size but decompressing to GB is
            # killed the instant it crosses the cap.
            actual_total = 0
            copy_chunk = 1024 * 1024  # 1 MB
            for idx, member in enumerate(members):
                if member.is_dir():
                    continue
                target = dest_dir / member.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                with zipf.open(member, "r") as src, target.open("wb") as dst:
                    while chunk := src.read(copy_chunk):
                        actual_total += len(chunk)
                        if actual_total > self.max_size:
                            # Remove the partial file so we don't leak disk.
                            try:
                                dst.close()
                                target.unlink(missing_ok=True)
                            except OSError:
                                pass
                            msg = (
                                f"Archive exceeds actual-size limit during "
                                f"extraction ({actual_total} > {self.max_size}); "
                                f"possible zip bomb"
                            )
                            raise ArchiveExtractionError(msg)
                        dst.write(chunk)
                if progress_callback:
                    progress_callback(member.filename, idx + 1, total_members)

            logger.debug(
                "zip_extraction_complete",
                file_count=total_members,
                declared_total=declared_total,
                actual_total=actual_total,
            )

    def _extract_tar_gz(
        self,
        archive_path: Path,
        dest_dir: Path,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> None:
        """Extract TAR.GZ archive with security validation.

        Args:
            archive_path: Path to TAR.GZ file.
            dest_dir: Destination directory.
            progress_callback: Optional progress callback.

        Raises:
            ArchiveSecurityError: If archive contains unsafe paths.
            ArchiveExtractionError: If extraction fails or limits exceeded.
        """
        with tarfile.open(archive_path, "r:gz") as tarf:
            members = tarf.getmembers()
            total_members = len(members)

            # Check file count limit
            if total_members > self.max_files:
                msg = f"Archive exceeds file limit: {total_members} > {self.max_files}"
                raise ArchiveExtractionError(msg)

            # Check total size and validate all members first
            total_size = 0
            for member in members:
                total_size += member.size

                # Skip directories for validation
                if member.isdir():
                    continue

                # Validate member
                self._validate_tar_member(member, dest_dir)

            if total_size > self.max_size:
                msg = f"Archive exceeds size limit: {total_size} > {self.max_size}"
                raise ArchiveExtractionError(msg)

            # Extract validated members
            for idx, member in enumerate(members):
                tarf.extract(member, dest_dir, filter="data")

                if progress_callback and not member.isdir():
                    progress_callback(member.name, idx + 1, total_members)

            logger.debug(
                "tar_gz_extraction_complete",
                file_count=total_members,
                total_size=total_size,
            )

    def _validate_tar_member(self, member: tarfile.TarInfo, dest_dir: Path) -> None:
        """Validate TAR archive member for security issues.

        Args:
            member: TAR archive member to validate.
            dest_dir: Destination directory.

        Raises:
            ArchiveSecurityError: If member is unsafe.
        """
        member_path = Path(member.name)

        # Check for absolute paths
        if member_path.is_absolute():
            msg = f"Absolute path in archive is not allowed: {member.name}"
            raise ArchiveSecurityError(msg)

        # Check for path traversal
        if ".." in member_path.parts:
            msg = f"Path traversal in archive is not allowed: {member.name}"
            raise ArchiveSecurityError(msg)

        # Check for symlinks (security risk)
        if member.issym() or member.islnk():
            msg = f"Symlinks in archive are not allowed: {member.name}"
            raise ArchiveSecurityError(msg)

        # Resolve the full path and check it's within dest_dir
        try:
            full_path = (dest_dir / member_path).resolve()
            dest_resolved = dest_dir.resolve()

            if not full_path.is_relative_to(dest_resolved):
                msg = f"Path escapes destination directory: {member.name}"
                raise ArchiveSecurityError(msg)

        except (OSError, ValueError) as e:  # fmt: skip
            msg = f"Invalid path in archive: {member.name} ({e})"
            raise ArchiveSecurityError(msg) from e


__all__ = ["ArchiveExtractor"]
