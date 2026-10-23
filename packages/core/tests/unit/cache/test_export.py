# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export a store's namespace for a reference pack, and import it back."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from chaoscypher_core.cache.export import export_store, file_sha256, import_store
from chaoscypher_core.cache.sqlite_store import SqliteCacheStore
from chaoscypher_core.cache.store import CacheEntry, decode_value, encode_value
from chaoscypher_core.exceptions import ValidationError


_MB = 1024 * 1024


def _entry(obj: object, build: str = "ollama:m:sha256:abc", site: str = "extraction") -> CacheEntry:
    value = encode_value(obj)
    return CacheEntry(
        value=value,
        model_build=build,
        call_site=site,
        seeded=True,
        latency_ms=250,
        bytes=len(value),
    )


def _source(tmp_path: Path) -> SqliteCacheStore:
    store = SqliteCacheStore(tmp_path / "completions.db", max_bytes=_MB)
    store.put("bench:run1", "k1", _entry({"content": "one"}))
    store.put("bench:run1", "k2", _entry({"content": "two"}, build="ollama:other:sha256:def"))
    store.put("completions", "k3", _entry({"content": "three"}))
    return store


def test_export_writes_only_the_namespace_and_returns_the_files_sha(tmp_path: Path) -> None:
    source = _source(tmp_path)
    out = tmp_path / "pack" / "llm_store.db"
    sha = export_store(source, out, namespace="bench:run1")
    assert sha == file_sha256(out) == hashlib.sha256(out.read_bytes()).hexdigest()
    assert not Path(f"{out}-wal").exists()  # the file stands alone in a pack
    con = sqlite3.connect(out)
    try:
        rows = con.execute("SELECT namespace, key FROM entries ORDER BY key").fetchall()
    finally:
        con.close()
    assert rows == [("bench:run1", "k1"), ("bench:run1", "k2")]


def test_export_can_keep_only_one_model_build(tmp_path: Path) -> None:
    source = _source(tmp_path)
    out = tmp_path / "llm_store.db"
    export_store(source, out, namespace="bench:run1", model_build="ollama:m:sha256:abc")
    into = SqliteCacheStore(tmp_path / "into.db", max_bytes=_MB)
    assert import_store(out, into, namespace="completions") == 1
    got = into.get("completions", "k1")
    assert got is not None and decode_value(got.value) == {"content": "one"}
    assert got.latency_ms == 250 and got.call_site == "extraction" and got.seeded is True


def test_import_adds_the_entries_and_is_idempotent(tmp_path: Path) -> None:
    source = _source(tmp_path)
    out = tmp_path / "llm_store.db"
    sha = export_store(source, out, namespace="bench:run1")
    into = SqliteCacheStore(tmp_path / "into.db", max_bytes=_MB)
    assert import_store(out, into, namespace="completions") == 2
    assert import_store(out, into, namespace="completions") == 0
    assert into.stats("completions", _MB).entries == 2
    assert file_sha256(out) == sha  # importing reads the pack file, never rewrites it


def test_a_second_export_replaces_the_file(tmp_path: Path) -> None:
    source = _source(tmp_path)
    out = tmp_path / "llm_store.db"
    export_store(source, out, namespace="bench:run1")
    export_store(source, out, namespace="completions")
    into = SqliteCacheStore(tmp_path / "into.db", max_bytes=_MB)
    assert import_store(out, into, namespace="completions") == 1


def test_importing_a_file_of_another_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO meta VALUES ('schema_version', '0')")
    con.commit()
    con.close()
    into = SqliteCacheStore(tmp_path / "into.db", max_bytes=_MB)
    with pytest.raises(ValidationError, match="schema"):
        import_store(path, into, namespace="completions")
