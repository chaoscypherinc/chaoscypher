# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Utilities - Generic ZIP extraction and inspection.

Provides general-purpose ZIP archive operations (secure extraction +
inspection). These utilities are format-agnostic: they back the source
loaders, the compose resolver, and ``db/list`` / ``package/export``. CCX 3.0
packages are built by ``ccx-format`` (``ccx.PackageBuilder``), not here.

Example:
    from chaoscypher_core.services.package.archive import (
        extract_archive,
        get_archive_info,
    )

    # Extract archive
    extract_archive(
        archive_path=Path("./my-package.ccx"),
        dest_dir=Path("./extracted"),
    )

    # Get archive info
    info = get_archive_info(Path("./my-package.ccx"))
    print(f"Files: {info.file_count}, Size: {info.compressed_size}")
"""

from chaoscypher_core.services.package.archive.extract import (
    ArchiveSecurityError,
    extract_archive,
)
from chaoscypher_core.services.package.archive.info import (
    ArchiveInfo,
    format_size,
    get_archive_info,
)


__all__ = [
    "ArchiveInfo",
    "ArchiveSecurityError",
    "extract_archive",
    "format_size",
    "get_archive_info",
]
