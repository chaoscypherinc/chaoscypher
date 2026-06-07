# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package Models - Manifest and metadata for .ccx packages.

Provides the unified manifest model for ChaosCypher packages (.ccx format).
Uses JSON serialization for all manifests.

Example:
    from chaoscypher_core.services.package.models import PackageManifest

    # Create a new manifest
    manifest = PackageManifest(
        name="john/medical-ontology",
        package_version="1.0.0",
        description="Medical terminology ontology",
        package_type=["knowledge", "templates"],
        generator="chaoscypher-cli@1.0.0",
    )

    # Save to JSON
    manifest.to_json(Path("./manifest.json"))

    # Load from JSON
    manifest = PackageManifest.from_json(Path("./manifest.json"))
"""

from chaoscypher_core.services.package.models.manifest import (
    PackageManifest,
    PackageValidationError,
    validate_package_name,
    validate_version,
)


__all__ = [
    "PackageManifest",
    "PackageValidationError",
    "validate_package_name",
    "validate_version",
]
