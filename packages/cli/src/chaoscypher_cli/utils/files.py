# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""File Utilities - File operations for package management.

Provides CLI-specific utilities for downloading files and directory operations.
Archive operations are re-exported from chaoscypher_core.

Example:
    from chaoscypher_cli.utils.files import (
        create_archive,
        extract_archive,
        download_file,
    )

    # Create a package archive (from core)
    archive_path = create_archive(Path("./my-package"), Path("./my-package.ccx"))

    # Download a file (CLI-specific with Rich progress)
    await download_file("https://example.com/pkg.ccx", Path("./pkg.ccx"))
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import httpx
from rich.progress import Progress, SpinnerColumn, TextColumn

from chaoscypher_core.app_config import get_settings

# Re-export archive functions from core
from chaoscypher_core.services.package import (
    ArchiveInfo,
    ArchiveOptions,
    create_archive,
    extract_archive,
    format_size,
    get_archive_info,
)


# Package archive extension
CCX_EXTENSION = ".ccx"


async def download_file(
    url: str,
    dest_path: Path,
    *,
    progress: bool = True,
    timeout: float = 300.0,
    headers: dict[str, str] | None = None,
) -> Path:
    """Download a file from URL with optional progress display.

    Args:
        url: URL to download from.
        dest_path: Local path to save the file.
        progress: Show download progress bar.
        timeout: Request timeout in seconds.
        headers: Optional HTTP headers (e.g., for authentication).

    Returns:
        Path to the downloaded file.

    Raises:
        httpx.HTTPStatusError: If server returns error status.
        httpx.RequestError: If network error occurs.
        PermissionError: If unable to write to destination.

    Example:
        >>> await download_file(
        ...     "https://hub.example.com/pkg.ccx",
        ...     Path("./pkg.ccx")
        ... )
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    chunk_size = get_settings().cli.download_chunk_size_bytes

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("GET", url, headers=headers or {}) as response:
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))

            # Use temp file to avoid partial downloads
            with tempfile.NamedTemporaryFile(delete=False, dir=dest_path.parent) as temp_file:
                temp_path = Path(temp_file.name)

                if progress and total_size > 0:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        transient=True,
                    ) as prog:
                        task = prog.add_task(f"Downloading {dest_path.name}...", total=total_size)
                        async for chunk in response.aiter_bytes(chunk_size):
                            temp_file.write(chunk)
                            prog.update(task, advance=len(chunk))
                else:
                    async for chunk in response.aiter_bytes(chunk_size):
                        temp_file.write(chunk)

            # Atomic move to final destination
            shutil.move(temp_path, dest_path)

    return dest_path


__all__ = [
    "CCX_EXTENSION",
    "ArchiveInfo",
    "ArchiveOptions",
    "create_archive",
    "download_file",
    "extract_archive",
    "format_size",
    "get_archive_info",
]
