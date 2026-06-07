# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Creation - Create .ccx package archives.

Creates ZIP archives from package directories.
Supports configurable compression levels and file exclusion.

Example:
    from chaoscypher_core.services.package.archive import create_archive

    archive_path = create_archive(
        source_dir=Path("./my-package"),
        output_path=Path("./my-package.ccx"),
        options=ArchiveOptions(compression_level=9),
    )
"""

from __future__ import annotations

import fnmatch
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.exceptions import NotFoundError, ValidationError


if TYPE_CHECKING:
    from collections.abc import Callable

logger = structlog.get_logger(__name__)

# Default patterns to exclude from archives
DEFAULT_EXCLUDES = frozenset(
    {
        "__pycache__",
        "*.pyc",
        "*.pyo",
        ".git",
        ".gitignore",
        ".DS_Store",
        "*.egg-info",
        ".venv",
        "venv",
        "node_modules",
        ".idea",
        ".vscode",
        "*.swp",
        "*.swo",
    }
)


@dataclass
class ArchiveOptions:
    """Options for archive creation.

    Attributes:
        compression_level: Deflate compression level (1-9, higher = smaller but slower).
        exclude_patterns: File patterns to exclude from archive.
        follow_symlinks: Whether to follow symbolic links.
    """

    compression_level: int = 9
    exclude_patterns: frozenset[str] = field(default_factory=lambda: DEFAULT_EXCLUDES)
    follow_symlinks: bool = False

    def should_exclude(self, name: str) -> bool:
        """Check if a file/directory should be excluded.

        Args:
            name: File or directory name (not full path).

        Returns:
            True if the name matches any exclusion pattern.
        """
        base_name = Path(name).name
        return any(fnmatch.fnmatch(base_name, pattern) for pattern in self.exclude_patterns)


def create_archive(
    source_dir: Path,
    output_path: Path,
    *,
    options: ArchiveOptions | None = None,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> Path:
    """Create a .ccx package archive from a directory.

    Creates a ZIP archive containing all files from the source directory,
    excluding patterns specified in options.

    Args:
        source_dir: Directory containing package files to archive.
        output_path: Path for the output .ccx archive file.
        options: Archive creation options (compression, exclusions).
        progress_callback: Optional callback(filename, current, total) for progress.

    Returns:
        Path to the created archive.

    Raises:
        NotFoundError: If source_dir doesn't exist.
        ValidationError: If source_dir exists but is not a directory.
        PermissionError: If unable to read source or write output.
        OSError: If archive creation fails.

    Example:
        >>> archive = create_archive(
        ...     Path("./my-package"),
        ...     Path("./my-package.ccx"),
        ... )
        >>> print(f"Created: {archive}")
    """
    if not source_dir.exists():
        raise NotFoundError("Directory", str(source_dir))

    if not source_dir.is_dir():
        msg = f"Source is not a directory: {source_dir}"
        raise ValidationError(msg, field="source_dir")

    options = options or ArchiveOptions()

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Ensure .ccx extension
    if output_path.suffix != ".ccx":
        output_path = output_path.with_suffix(".ccx")

    # Collect files to archive (for progress tracking)
    files_to_archive = [
        item
        for item in source_dir.rglob("*")
        if item.is_file() and not options.should_exclude(str(item))
    ]

    total_files = len(files_to_archive)

    logger.info(
        "creating_archive",
        source=str(source_dir),
        output=str(output_path),
        file_count=total_files,
        compression_level=options.compression_level,
    )

    # Create ZIP archive with deflate compression
    with zipfile.ZipFile(
        output_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=options.compression_level,
    ) as zipf:
        for idx, item in enumerate(files_to_archive):
            # Get relative path for archive
            arcname = str(item.relative_to(source_dir))

            # Add to archive
            zipf.write(item, arcname=arcname)

            # Progress callback
            if progress_callback:
                progress_callback(arcname, idx + 1, total_files)

    logger.info(
        "archive_created",
        output=str(output_path),
        size_bytes=output_path.stat().st_size,
    )

    return output_path


__all__ = [
    "DEFAULT_EXCLUDES",
    "ArchiveOptions",
    "create_archive",
]
