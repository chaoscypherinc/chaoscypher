# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package Validation - Validate package structure and content.

Provides validation utilities for ChaosCypher packages.

Example:
    from chaoscypher_core.services.package.validation import (
        validate_package_directory,
        PackageValidationResult,
    )

    result = validate_package_directory(Path("./my-package"))
    if not result.is_valid:
        for error in result.errors:
            print(f"Error: {error}")
"""

from chaoscypher_core.services.package.validation.validator import (
    PackageValidationResult,
    validate_package_directory,
)


__all__ = [
    "PackageValidationResult",
    "validate_package_directory",
]
