# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package Manifest - Unified manifest for .ccx packages.

Extends ExportManifest with package management functionality.
All manifests use JSON format for serialization.

The PackageManifest is the single source of truth for package metadata,
used by CLI, Cortex, and any other consumers.

Example:
    from chaoscypher_core.services.package.models import PackageManifest

    manifest = PackageManifest(
        name="john/medical-ontology",
        package_version="1.0.0",
        package_type=["knowledge"],
        generator="chaoscypher-cli@1.0.0",
    )

    # Validate
    errors = manifest.validate_fields()
    if errors:
        raise PackageValidationError(errors)
"""

from __future__ import annotations

import json
import re
from datetime import datetime  # noqa: TC003 - Pydantic needs runtime access
from typing import TYPE_CHECKING, Any

from pydantic import Field, field_validator

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.export.models.schemas import (
    ExportManifest,
)


if TYPE_CHECKING:
    from pathlib import Path


# Package name pattern: owner/name or just name (lowercase alphanumeric, hyphens, underscores)
PACKAGE_NAME_PATTERN = re.compile(r"^(?:[a-z0-9_-]+/)?[a-z0-9_-]+$")

# Semantic version pattern
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(?:-[a-z0-9.]+)?(?:\+[a-z0-9.]+)?$")

# Valid SPDX license identifiers
VALID_LICENSES = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "GPL-3.0",
        "GPL-2.0",
        "BSD-3-Clause",
        "BSD-2-Clause",
        "ISC",
        "CC0-1.0",
        "CC-BY-4.0",
        "Unlicense",
        "PROPRIETARY",
    }
)


class PackageValidationError(ValidationError):
    """Raised when package validation fails.

    Attributes:
        errors: List of validation error messages.
    """

    def __init__(self, errors: list[str]) -> None:
        """Initialize with list of errors.

        Args:
            errors: List of validation error messages.
        """
        message = f"Package validation failed: {'; '.join(errors)}"
        super().__init__(message=message, field="package", details={"errors": errors})
        self.errors = errors


def validate_package_name(name: str) -> list[str]:
    """Validate package name format.

    Args:
        name: Package name to validate.

    Returns:
        List of validation errors (empty if valid).

    Example:
        >>> validate_package_name("john/my-package")
        []
        >>> validate_package_name("Invalid Name")
        ["Invalid package name..."]
    """
    errors: list[str] = []
    if not name:
        errors.append("Package name is required")
    elif not PACKAGE_NAME_PATTERN.match(name):
        errors.append(
            f"Invalid package name '{name}'. "
            "Must be lowercase alphanumeric with hyphens/underscores, "
            "optionally prefixed with owner/ (e.g., 'my-package' or 'john/my-package')"
        )
    return errors


def validate_version(version: str) -> list[str]:
    """Validate semantic version format.

    Args:
        version: Version string to validate.

    Returns:
        List of validation errors (empty if valid).

    Example:
        >>> validate_version("1.0.0")
        []
        >>> validate_version("v1")
        ["Invalid version..."]
    """
    errors: list[str] = []
    if not version:
        errors.append("Version is required")
    elif not VERSION_PATTERN.match(version):
        errors.append(
            f"Invalid version '{version}'. "
            "Must be semantic version format (e.g., '1.0.0', '1.0.0-beta.1')"
        )
    return errors


class PackageManifest(ExportManifest):
    """Extended manifest for .ccx package management.

    Inherits from ExportManifest and adds:
    - JSON file I/O methods
    - Extended validation
    - Package scaffolding support
    - Homepage/repository fields

    All fields from ExportManifest are available:
    - ccx_version, package_type, name, package_version
    - author, license, description, tags
    - created_at, derived_from, dependencies
    - contents, *_stats, generator
    - version, generated_at, database_name, title, stats, sources (from GraphBreakdown)

    Additional fields:
    - homepage: Project homepage URL
    - repository: Source repository URL
    - updated_at: Last modification timestamp

    Example:
        manifest = PackageManifest(
            name="john/medical",
            package_version="1.0.0",
            package_type=["knowledge"],
            generator="chaoscypher-cli@1.0.0",
            description="Medical ontology",
        )
    """

    # Additional fields not in ExportManifest
    homepage: str | None = Field(None, description="Project homepage URL")
    repository: str | None = Field(None, description="Source repository URL")
    updated_at: datetime | None = Field(None, description="Last modification timestamp")

    @field_validator("name")
    @classmethod
    def validate_name_format(cls, v: str) -> str:
        """Validate package name format."""
        errors = validate_package_name(v)
        if errors:
            raise ValueError(errors[0])
        return v

    @field_validator("package_version")
    @classmethod
    def validate_version_format(cls, v: str) -> str:
        """Validate version format."""
        errors = validate_version(v)
        if errors:
            raise ValueError(errors[0])
        return v

    def validate_fields(self) -> list[str]:
        """Validate all manifest fields.

        Returns:
            List of validation error messages (empty if valid).

        Example:
            errors = manifest.validate_fields()
            if errors:
                for error in errors:
                    print(f"Error: {error}")
        """
        errors: list[str] = []

        # Name and version (already validated by Pydantic, but check for empty)
        errors.extend(validate_package_name(self.name))
        errors.extend(validate_version(self.package_version))

        # Description
        if not self.description:
            errors.append("Description is required")

        # License validation
        if self.license and self.license not in VALID_LICENSES:
            errors.append(
                f"Unknown license '{self.license}'. "
                f"Valid options: {', '.join(sorted(VALID_LICENSES))}"
            )

        # Package type is validated by parent class

        return errors

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to dictionary.

        Returns:
            Dictionary representation.
        """
        return self.model_dump(mode="json")

    @classmethod
    def from_json(cls, path: Path) -> PackageManifest:
        """Load manifest from JSON file.

        Args:
            path: Path to manifest.json file.

        Returns:
            PackageManifest instance.

        Raises:
            FileNotFoundError: If file doesn't exist.
            json.JSONDecodeError: If JSON is invalid.
            pydantic.ValidationError: If data doesn't match schema.

        Example:
            manifest = PackageManifest.from_json(Path("./manifest.json"))
        """
        if not path.exists():
            msg = f"Manifest file not found: {path}"
            raise FileNotFoundError(msg)

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        return cls.model_validate(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PackageManifest:
        """Create manifest from dictionary.

        Args:
            data: Dictionary with manifest data.

        Returns:
            PackageManifest instance.
        """
        return cls.model_validate(data)


__all__ = [
    "PACKAGE_NAME_PATTERN",
    "VALID_LICENSES",
    "VERSION_PATTERN",
    "PackageManifest",
    "PackageValidationError",
    "validate_package_name",
    "validate_version",
]
