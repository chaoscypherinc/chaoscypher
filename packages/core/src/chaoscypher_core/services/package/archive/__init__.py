# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Utilities - Create and extract .ccx package archives.

Provides ZIP archive operations for ChaosCypher packages.
All .ccx files use ZIP format for portability and streaming support.

Example:
    from chaoscypher_core.services.package.archive import (
        create_archive,
        extract_archive,
        get_archive_info,
    )

    # Create archive
    archive_path = create_archive(
        source_dir=Path("./my-package"),
        output_path=Path("./my-package.ccx"),
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

from chaoscypher_core.services.package.archive.create import (
    ArchiveOptions,
    create_archive,
)
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
    "ArchiveOptions",
    "ArchiveSecurityError",
    "create_archive",
    "extract_archive",
    "format_size",
    "get_archive_info",
]
