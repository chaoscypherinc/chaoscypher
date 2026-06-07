# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Utility Utilities.

File integrity verification and checksum utilities for export operations.

Provides utilities for ensuring data integrity during export workflows, including
checksum calculation (SHA-256/SHA-512) and verification to detect corruption or
tampering. All utilities are pure functions with no dependencies, working in both
backend and CLI environments.

Components:
- FileIntegrityChecker: Calculate and verify file checksums with multiple algorithms

Example:
    from chaoscypher_core.services.export.utils import FileIntegrityChecker

    # Calculate checksums
    sha512, sha256 = FileIntegrityChecker.calculate_checksums(file_data)

    # Verify integrity
    is_valid = FileIntegrityChecker.verify_checksum(
        file_data,
        expected_sha512=sha512,
        expected_sha256=sha256
    )

"""

from chaoscypher_core.services.export.utils.file_integrity import FileIntegrityChecker


__all__ = [
    "FileIntegrityChecker",
]
