# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export a store's entries for a reference pack, and import them back.

A pack ships the completions its graph was built from as a plain store file
(``llm_store.db``, same schema) with its sha256 in the manifest
(``llm_store``, ``llm_store_sha256``), so the graph rebuilds with zero model
calls. The exported file is checkpointed out of WAL mode so it stands alone,
and importing reads it read-only, so its sha256 never moves.
"""

from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from chaoscypher_core.cache.sqlite_store import SqliteCacheStore, read_entries


EXPORT_MAX_BYTES = 1 << 62
"""The cap of an exported file: none in effect (an export must hold every entry it takes)."""


def file_sha256(path: Path) -> str:
    """The hex sha256 of a file's bytes."""
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def export_store(
    store: SqliteCacheStore, path: Path, *, namespace: str, model_build: str | None = None
) -> str:
    """Write a fresh store file at ``path`` holding ``namespace``'s entries; return its sha256.

    ``model_build`` keeps only the entries that build produced (a pack takes
    its extractor's answers). An existing file at ``path`` is replaced.
    """
    path = Path(path)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{path}{suffix}").unlink(missing_ok=True)
    out = SqliteCacheStore(path, max_bytes=EXPORT_MAX_BYTES)
    try:
        rows = read_entries(store.path, namespace=namespace, model_build=model_build)
        out.put_many(namespace, ((key, entry) for _ns, key, entry in rows))
    finally:
        out.close()
    with closing(sqlite3.connect(path)) as con:
        con.execute("PRAGMA journal_mode=DELETE")  # checkpoints the WAL into the file
    return file_sha256(path)


def import_store(path: Path, into: SqliteCacheStore, *, namespace: str) -> int:
    """Add every entry of the store file at ``path`` to ``into`` under ``namespace``.

    Entries ``into`` already holds are kept, so a second import adds 0.
    Returns how many entries were added.

    Raises:
        ValidationError: When ``path`` is not a store of this schema version.
    """
    return into.put_many(
        namespace, ((key, entry) for _ns, key, entry in read_entries(path)), replace=False
    )


__all__ = ["EXPORT_MAX_BYTES", "export_store", "file_sha256", "import_store"]
