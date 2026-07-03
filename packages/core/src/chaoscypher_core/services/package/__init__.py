# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Package Service - ChaosCypher Package Management.

Provides framework-agnostic utilities for importing and distributing
ChaosCypher (.ccx) packages, plus the generic ZIP archive helpers the upload
pipeline and source loaders depend on. This module is used by CLI, Cortex,
and other tools that need package management capabilities.

Submodules:
    - archive: generic ZIP extraction (``extract_archive``) and inspection
      (``get_archive_info`` / ``format_size`` / ``ArchiveInfo``). Used by the
      source loaders, the compose resolver, ``db/list``, and ``package/export``.
    - importer: CCX 3.0 package import service (``CcxImporter``).

CCX 3.0 packages are built by ``ccx-format`` (``ccx.PackageBuilder`` via the
``CcxExporter``) and read/validated with ``ccx.open_package(...)``; the
``CcxImporter`` here upserts a package's contents by stable CCX IRI.

For Lexicon interactions (authentication, package registry), see:
    chaoscypher_core.services.lexicon

Example:
    from chaoscypher_core.services.package import (
        # Generic archive utilities
        extract_archive,
        get_archive_info,
        # CCX 3.0 import
        CcxImporter,
        ImportOptions,
        ImportStats,
    )

    # Import a CCX 3.0 package
    importer = CcxImporter(graph_repository, sources_repository)
    stats = await importer.import_from_bytes(package_bytes)
"""

# Archive operations (generic ZIP extraction + inspection)
from chaoscypher_core.services.package.archive import (
    ArchiveInfo,
    ArchiveSecurityError,
    extract_archive,
    format_size,
    get_archive_info,
)

# CCX 3.0 import service
from chaoscypher_core.services.package.importer.models import (
    ImportOptions,
    ImportStats,
)
from chaoscypher_core.services.package.importer.service import CcxImporter


__all__ = [
    "ArchiveInfo",
    "ArchiveSecurityError",
    "CcxImporter",
    "ImportOptions",
    "ImportStats",
    "extract_archive",
    "format_size",
    "get_archive_info",
]
