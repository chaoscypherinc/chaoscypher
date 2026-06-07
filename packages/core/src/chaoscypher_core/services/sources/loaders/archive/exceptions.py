# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Loader Exceptions.

Custom exceptions for archive extraction, format detection, and handler errors.
All exceptions inherit from ArchiveLoaderError for unified error handling.

Example:
    from chaoscypher_core.services.sources.loaders.archive import (
        ArchiveLoaderError,
        ArchiveExtractionError,
    )

    try:
        extractor.extract(archive_path, dest_dir)
    except ArchiveExtractionError as e:
        logger.error("extraction_failed", error=str(e))
"""

from chaoscypher_core.exceptions import ChaosCypherException
from chaoscypher_core.services.package.archive.extract import ArchiveSecurityError


class ArchiveLoaderError(ChaosCypherException):
    """Base exception for all archive loader errors.

    All archive-related exceptions inherit from this class,
    allowing unified error handling in the loader.
    """

    def __init__(self, message: str = "", details: dict | None = None) -> None:
        """Initialize archive loader error.

        Args:
            message: Error description.
            details: Additional error details.
        """
        super().__init__(message=message, code="ARCHIVE_LOADER_ERROR", details=details or {})


class ArchiveExtractionError(ArchiveLoaderError):
    """Raised when archive extraction fails.

    Causes:
    - Corrupted archive file
    - Unsupported archive format
    - Disk space or permission issues
    - Archive exceeds size/file count limits
    """


class FormatDetectionError(ArchiveLoaderError):
    """Raised when documentation format cannot be reliably detected.

    Contains list of indicators found during detection attempt.

    Attributes:
        indicators: List of indicators found during detection.
    """

    def __init__(self, message: str, indicators: list[str] | None = None) -> None:
        """Initialize FormatDetectionError.

        Args:
            message: Error description.
            indicators: List of indicators found during detection attempt.
        """
        super().__init__(message)
        self.indicators = indicators or []


class HandlerError(ArchiveLoaderError):
    """Raised when a handler fails to process content.

    Non-fatal: Individual file processing failures are logged
    and skipped, allowing other files to be processed.
    """


class UnsupportedArchiveError(ArchiveLoaderError):
    """Raised when archive format is not supported.

    Supported formats: .zip, .tar.gz, .tgz
    """


__all__ = [
    "ArchiveExtractionError",
    "ArchiveLoaderError",
    "ArchiveSecurityError",  # Re-export from package.archive
    "FormatDetectionError",
    "HandlerError",
    "UnsupportedArchiveError",
]
