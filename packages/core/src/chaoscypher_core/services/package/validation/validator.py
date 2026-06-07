# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package Validator - Validate package structure and manifest.

Validates ChaosCypher package directories and manifest files
to ensure they meet the required structure and format.

Example:
    from chaoscypher_core.services.package.validation import (
        validate_package_directory,
        PackageValidationResult,
    )

    result = validate_package_directory(Path("./my-package"))
    if result.is_valid:
        print("Package is valid!")
    else:
        for error in result.errors:
            print(f"Error: {error}")
        for warning in result.warnings:
            print(f"Warning: {warning}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.services.package.models.manifest import (
    PackageManifest,
)


if TYPE_CHECKING:
    from pathlib import Path


logger = structlog.get_logger(__name__)


@dataclass
class PackageValidationResult:
    """Result of package validation.

    Attributes:
        errors: List of validation error messages (blocking issues).
        warnings: List of validation warning messages (non-blocking).
        manifest: Loaded manifest if parsing succeeded.
        stats: Package statistics (file counts, sizes).
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: PackageManifest | None = None
    stats: dict[str, int] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        """Check if validation passed (no errors)."""
        return len(self.errors) == 0


def validate_package_directory(path: Path) -> PackageValidationResult:
    """Validate a package directory structure and manifest.

    Checks for:
    - Required manifest.json file
    - Valid manifest content and fields
    - Required data/ directory
    - Optional but recommended files (README.md, LICENSE)

    Args:
        path: Package directory path.

    Returns:
        PackageValidationResult with errors, warnings, and parsed manifest.

    Example:
        >>> result = validate_package_directory(Path("./my-package"))
        >>> if result.is_valid:
        ...     print(f"Package: {result.manifest.name}")
        ... else:
        ...     print(f"Errors: {result.errors}")
    """
    result = PackageValidationResult()

    # Check directory exists
    if not path.exists():
        result.errors.append(f"Directory not found: {path}")
        return result

    if not path.is_dir():
        result.errors.append(f"Not a directory: {path}")
        return result

    logger.debug("validating_package", path=str(path))

    # Check manifest.json
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        result.errors.append("Missing required file: manifest.json")
    else:
        try:
            manifest = PackageManifest.from_json(manifest_path)
            result.manifest = manifest

            # Validate manifest fields
            field_errors = manifest.validate_fields()
            result.errors.extend(field_errors)

        except FileNotFoundError:
            result.errors.append("manifest.json not found")
        except Exception as e:
            result.errors.append(f"Invalid manifest.json: {e}")

    # Check data directory
    data_dir = path / "data"
    if not data_dir.exists():
        result.errors.append("Missing required directory: data/")
    elif not data_dir.is_dir():
        result.errors.append("'data' must be a directory")
    else:
        # Count data files
        result.stats["data_files"] = sum(1 for _ in data_dir.rglob("*") if _.is_file())
        if result.stats["data_files"] == 0:
            result.warnings.append("No data files found in data/ directory")

    # Check optional but recommended files
    if not (path / "README.md").exists():
        result.warnings.append("Missing README.md (recommended for documentation)")

    if not (path / "LICENSE").exists() and not (path / "LICENSE.md").exists():
        result.warnings.append("Missing LICENSE file (recommended)")

    # Calculate total size
    total_size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    result.stats["total_size"] = total_size

    logger.debug(
        "validation_complete",
        path=str(path),
        errors=len(result.errors),
        warnings=len(result.warnings),
        is_valid=result.is_valid,
    )

    return result


__all__ = [
    "PackageValidationResult",
    "validate_package_directory",
]
