# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""File integrity utilities for checksums and verification.

Provides utilities for calculating and verifying file checksums
using multiple hash algorithms (SHA-256 and SHA-512).

Pure utility functions with zero dependencies - works in both backend and CLI.
"""

import base64
import hashlib
from pathlib import Path

import structlog


logger = structlog.get_logger(__name__)


class FileIntegrityChecker:
    """Handles file integrity checks with multiple hash algorithms.

    Provides static methods for calculating and verifying checksums
    using SHA-256 and SHA-512 algorithms with base64 encoding. This
    class is used to ensure data integrity for imported files and
    verify that files have not been corrupted or tampered with.

    All methods are static - zero state, pure utility functions.
    Works in both backend and CLI environments with no dependencies
    on FastAPI or SQLModel.

    Attributes:
        None - all methods are static

    Example:
        >>> from chaoscypher_core.utils.file_integrity import FileIntegrityChecker
        >>>
        >>> # Calculate checksums for file data
        >>> with open("document.pdf", "rb") as f:
        ...     data = f.read()
        >>> sha512, sha256 = FileIntegrityChecker.calculate_checksums(data)
        >>>
        >>> # Later, verify the file hasn't changed
        >>> with open("document.pdf", "rb") as f:
        ...     new_data = f.read()
        >>> is_valid = FileIntegrityChecker.verify_checksum(
        ...     new_data,
        ...     expected_sha512=sha512,
        ...     expected_sha256=sha256
        ... )
        >>> if is_valid:
        ...     print("File integrity verified")
        >>>
        >>> # Or verify file directly from path
        >>> is_valid = FileIntegrityChecker.verify_file_checksum(
        ...     "document.pdf",
        ...     expected_sha512=sha512
        ... )

    Note:
        SHA-512 is always required and verified. SHA-256 is optional
        but will be verified if provided. Both checksums are base64
        encoded for compact storage.

    """

    @staticmethod
    def calculate_checksums(data: bytes) -> tuple[str, str]:
        """Calculate SHA-512 and SHA-256 checksums for data.

        Args:
            data: Binary data to hash

        Returns:
            Tuple of (sha512_base64, sha256_base64)

        Example:
            >>> with open("file.bin", "rb") as f:
            ...     data = f.read()
            >>> sha512, sha256 = FileIntegrityChecker.calculate_checksums(data)

        """
        sha512_hash = hashlib.sha512(data).digest()
        sha256_hash = hashlib.sha256(data).digest()

        sha512_b64 = base64.b64encode(sha512_hash).decode("utf-8")
        sha256_b64 = base64.b64encode(sha256_hash).decode("utf-8")

        return sha512_b64, sha256_b64

    @staticmethod
    def verify_checksum(
        data: bytes, expected_sha512: str, expected_sha256: str | None = None
    ) -> bool:
        """Verify data against expected checksums.

        Args:
            data: Binary data to verify
            expected_sha512: Expected SHA-512 checksum (base64 encoded)
            expected_sha256: Optional expected SHA-256 checksum (base64 encoded)

        Returns:
            True if checksums match, False otherwise

        Example:
            >>> valid = FileIntegrityChecker.verify_checksum(
            ...     data,
            ...     expected_sha512="abc123...",
            ...     expected_sha256="def456..."  # optional
            ... )

        """
        calculated_sha512, calculated_sha256 = FileIntegrityChecker.calculate_checksums(data)

        # SHA-512 is required
        if calculated_sha512 != expected_sha512:
            logger.error(
                "sha512_checksum_mismatch",
                expected=expected_sha512[:16] + "...",
                got=calculated_sha512[:16] + "...",
            )
            return False

        # SHA-256 is optional but verified if provided
        if expected_sha256 and calculated_sha256 != expected_sha256:
            logger.warning(
                "sha256_checksum_mismatch",
                expected=expected_sha256[:16] + "...",
                got=calculated_sha256[:16] + "...",
            )
            return False

        return True

    @staticmethod
    def calculate_file_checksum(file_path: str) -> tuple[str, str]:
        """Calculate checksums for a file on disk.

        Args:
            file_path: Path to file

        Returns:
            Tuple of (sha512_base64, sha256_base64)

        Example:
            >>> sha512, sha256 = FileIntegrityChecker.calculate_file_checksum("data.bin")

        """
        with Path(file_path).open("rb") as f:
            data = f.read()
        return FileIntegrityChecker.calculate_checksums(data)

    @staticmethod
    def verify_file_checksum(
        file_path: str, expected_sha512: str, expected_sha256: str | None = None
    ) -> bool:
        """Verify file against expected checksums.

        Args:
            file_path: Path to file
            expected_sha512: Expected SHA-512 checksum (base64 encoded)
            expected_sha256: Optional expected SHA-256 checksum (base64 encoded)

        Returns:
            True if checksums match, False otherwise

        Example:
            >>> valid = FileIntegrityChecker.verify_file_checksum(
            ...     "data.bin",
            ...     expected_sha512="abc123..."
            ... )

        """
        with Path(file_path).open("rb") as f:
            data = f.read()
        return FileIntegrityChecker.verify_checksum(data, expected_sha512, expected_sha256)


__all__ = ["FileIntegrityChecker"]
