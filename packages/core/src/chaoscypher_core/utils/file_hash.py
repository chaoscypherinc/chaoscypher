# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The SHA-256 of a file, streamed and off the event loop: one copy for every upload path.

The upload service (REST, URL and site imports) and the MCP ``add_document``
pipeline hash a document's file for its ``content_hash``. A file can be
hundreds of MB, so it is read in blocks (never whole) in a worker thread.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


__all__ = ["READ_BLOCK_BYTES", "hash_file_async"]

READ_BLOCK_BYTES = 1024 * 1024
"""How much of the file is read at a time (1 MiB)."""


def _hash_file(path: Path) -> str:
    """Stream-hash the file in blocks and return the hex digest."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(READ_BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


async def hash_file_async(path: Path) -> str:
    """Return the lowercase hex SHA-256 of ``path``, read in blocks in a worker thread."""
    return await asyncio.to_thread(_hash_file, path)
