# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package Service - ChaosCypher Package Management.

Provides framework-agnostic utilities for creating, validating,
importing, and distributing ChaosCypher (.ccx) packages. This module is used
by CLI, Cortex, and other tools that need package management capabilities.

Submodules:
    - models: Package manifest and metadata models (JSON format)
    - archive: ZIP archive creation, extraction, and inspection
    - validation: Package structure and manifest validation
    - import: Package import service and loaders

For Lexicon interactions (authentication, package registry), see:
    chaoscypher_core.services.lexicon

Example:
    from chaoscypher_core.services.package import (
        # Models
        PackageManifest,
        PackageValidationError,
        # Archive operations
        create_archive,
        extract_archive,
        get_archive_info,
        ArchiveOptions,
        ArchiveInfo,
        # Validation
        validate_package_directory,
        PackageValidationResult,
        # Import
        ImportService,
        ImportOptions,
        ImportStats,
    )

    # Validate a package
    result = validate_package_directory(Path("./my-package"))
    if result.is_valid:
        # Build the archive
        create_archive(Path("./my-package"), Path("my-package-0.1.0.ccx"))

    # Import a package
    service = ImportService(graph_repository, sources_repository)
    stats = await service.import_from_bytes(archive_bytes)
"""

# Archive operations
from chaoscypher_core.services.package.archive import (
    ArchiveInfo,
    ArchiveOptions,
    ArchiveSecurityError,
    create_archive,
    extract_archive,
    format_size,
    get_archive_info,
)

# Import service
from chaoscypher_core.services.package.importer.models import (
    IdMapper,
    ImportOptions,
    ImportStats,
)
from chaoscypher_core.services.package.importer.service import ImportService

# Models
from chaoscypher_core.services.package.models import (
    PackageManifest,
    PackageValidationError,
)

# Validation
from chaoscypher_core.services.package.validation import (
    PackageValidationResult,
    validate_package_directory,
)


__all__ = [
    "ArchiveInfo",
    "ArchiveOptions",
    "ArchiveSecurityError",
    "IdMapper",
    "ImportOptions",
    "ImportService",
    "ImportStats",
    "PackageManifest",
    "PackageValidationError",
    "PackageValidationResult",
    "create_archive",
    "extract_archive",
    "format_size",
    "get_archive_info",
    "validate_package_directory",
]
