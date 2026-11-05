# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``hash_file_async``: the one streamed SHA-256 of a file every upload path uses."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.utils import file_hash
from chaoscypher_core.utils.file_hash import hash_file_async


if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_matches_sha256_of_the_whole_file(tmp_path: Path) -> None:
    body = b"doc-a " * 400_000  # over one read block
    path = tmp_path / "doc-a.txt"
    path.write_bytes(body)

    assert await hash_file_async(path) == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_reads_in_blocks_never_the_whole_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "doc-a.txt"
    path.write_bytes(b"x" * (3 * file_hash.READ_BLOCK_BYTES + 1))
    sizes: list[int] = []
    real_open = type(path).open

    class _Watched:
        def __init__(self, handle: Any) -> None:
            self.handle = handle

        def __enter__(self) -> _Watched:
            return self

        def __exit__(self, *exc: object) -> None:
            self.handle.close()

        def read(self, size: int = -1) -> bytes:
            sizes.append(size)
            return bytes(self.handle.read(size))

    def watching_open(self: Path, *args: Any, **kwargs: Any) -> _Watched:
        return _Watched(real_open(self, *args, **kwargs))

    monkeypatch.setattr(type(path), "open", watching_open)

    await hash_file_async(path)

    assert sizes and all(0 < size <= file_hash.READ_BLOCK_BYTES for size in sizes)
