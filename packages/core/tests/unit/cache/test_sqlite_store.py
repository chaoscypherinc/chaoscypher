# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The SQLite cache store: roundtrip, LRU eviction, stats, schema reset, concurrent writers."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from chaoscypher_core.cache.sqlite_store import SqliteCacheStore
from chaoscypher_core.cache.store import CacheEntry, decode_value, encode_value


_MB = 1024 * 1024


def _entry(size: int, site: str = "chat") -> CacheEntry:
    value = b"x" * size
    return CacheEntry(
        value=value,
        model_build="ollama:m:sha256:abc",
        call_site=site,
        seeded=True,
        latency_ms=100,
        bytes=len(value),
    )


def test_put_get_roundtrip_and_a_miss(tmp_path: Path) -> None:
    store = SqliteCacheStore(tmp_path / "c.db", max_bytes=_MB)
    assert store.get("completions", "k1") is None
    store.put("completions", "k1", _entry(10))
    got = store.get("completions", "k1")
    assert got is not None and got.value == b"x" * 10 and got.latency_ms == 100
    assert got.model_build == "ollama:m:sha256:abc" and got.seeded is True
    assert store.get("other", "k1") is None  # namespaces are separate
    assert store.namespaces() == ["completions"]


def test_values_roundtrip_through_the_shared_codec() -> None:
    obj = {"content": "Bezúkhov", "usage": {"tokens": 3}, "list": [1.5, None]}
    assert decode_value(encode_value(obj)) == obj
    assert encode_value({"b": 1, "a": 2}) == encode_value({"a": 2, "b": 1})  # canonical


def test_eviction_drops_the_least_recently_hit_first(tmp_path: Path) -> None:
    store = SqliteCacheStore(tmp_path / "c.db", max_bytes=1000)
    for i in range(5):
        store.put("completions", f"k{i}", _entry(200))  # 1000 bytes: at the cap
    store.get("completions", "k0")  # k0 becomes the most recently hit
    store.put("completions", "k5", _entry(200))  # over the cap: evict down to 900
    assert store.get("completions", "k0") is not None
    assert store.get("completions", "k5") is not None
    assert store.get("completions", "k1") is None  # the oldest unhit entry went first
    assert store.get("completions", "k2") is None
    assert store.get("completions", "k3") is not None  # only as many as needed went
    assert store.stats("completions", 1000).bytes <= 900


def test_stats_count_hits_misses_bypasses_and_time_saved_per_call_site(tmp_path: Path) -> None:
    store = SqliteCacheStore(tmp_path / "c.db", max_bytes=_MB)
    store.put("completions", "k", _entry(5, "extraction"))
    store.record("completions", "extraction", hit=True, latency_saved_ms=100)
    store.record("completions", "chat", hit=False)
    store.record("completions", "chat", hit=None)
    s = store.stats("completions", _MB)
    assert (s.entries, s.hits, s.misses, s.bypasses, s.latency_saved_ms) == (1, 1, 1, 1, 100)
    assert (s.namespace, s.bytes, s.max_bytes) == ("completions", 5, _MB)
    assert s.by_call_site["extraction"]["hits"] == 1 and s.by_call_site["chat"]["bypasses"] == 1
    assert s.by_call_site["extraction"]["latency_saved_ms"] == 100
    assert store.clear("completions") == 1
    assert store.stats("completions", _MB).entries == 0
    assert store.stats("completions", _MB).hits == 1  # history survives a clear


def test_clear_without_a_namespace_empties_every_namespace(tmp_path: Path) -> None:
    store = SqliteCacheStore(tmp_path / "c.db", max_bytes=_MB)
    store.put("completions", "a", _entry(5))
    store.put("bench:run1", "a", _entry(5))
    assert store.clear() == 2
    assert store.namespaces() == []


def test_an_old_schema_is_reset_not_crashed(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO meta VALUES ('schema_version', '0')")
    con.execute("CREATE TABLE entries (key TEXT)")
    con.commit()
    con.close()
    store = SqliteCacheStore(path, max_bytes=1024)
    assert store.get("completions", "k") is None
    store.put("completions", "k", _entry(3))
    assert store.get("completions", "k") is not None


def test_a_file_that_is_not_a_database_is_reset_not_crashed(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    path.write_bytes(b"this is not sqlite" * 100)
    store = SqliteCacheStore(path, max_bytes=1024)
    store.put("completions", "k", _entry(3))
    assert store.get("completions", "k") is not None


def test_a_reopened_store_keeps_its_entries(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    first = SqliteCacheStore(path, max_bytes=_MB)
    first.put("completions", "k", _entry(3))
    first.close()
    assert SqliteCacheStore(path, max_bytes=_MB).get("completions", "k") is not None


def test_concurrent_writers_on_one_file_keep_every_key(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    stores = [SqliteCacheStore(path, max_bytes=10 * _MB) for _ in range(4)]

    def work(n: int) -> None:
        for i in range(50):
            stores[n].put("completions", f"k{n}-{i}", _entry(20))
            stores[n].put("completions", "shared", _entry(20))
            stores[n].record("completions", "chat", hit=False)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats = stores[0].stats("completions", 10 * _MB)
    assert stats.entries == 201
    assert stats.misses == 200


def test_one_store_shared_by_threads_serialises_its_connection(tmp_path: Path) -> None:
    store = SqliteCacheStore(tmp_path / "c.db", max_bytes=10 * _MB)
    errors: list[BaseException] = []

    def work(n: int) -> None:
        try:
            for i in range(50):
                store.put("completions", f"k{n}-{i}", _entry(20))
                store.get("completions", f"k{n}-{i}")
        except BaseException as exc:  # pragma: no cover - the failure being guarded
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert store.stats("completions", 10 * _MB).entries == 200


def test_the_running_total_follows_replace_evict_and_clear(tmp_path: Path) -> None:
    path = tmp_path / "c.db"
    store = SqliteCacheStore(path, max_bytes=1000)

    def totals() -> tuple[int, int]:
        con = sqlite3.connect(path)
        try:
            kept = con.execute("SELECT bytes FROM totals WHERE id = 1").fetchone()[0]
            summed = con.execute("SELECT COALESCE(SUM(bytes), 0) FROM entries").fetchone()[0]
            return kept, summed
        finally:
            con.close()

    store.put("completions", "a", _entry(100))
    store.put("completions", "a", _entry(300))  # a replace counts once, at its new size
    store.put("bench:r", "b", _entry(200))
    assert totals() == (500, 500)
    for i in range(4):
        store.put("completions", f"k{i}", _entry(200))  # past the cap: evicted
    kept, summed = totals()
    assert kept == summed <= 1000  # evicted to 900 once past the cap, then one more put
    store.clear()
    assert totals() == (0, 0)


def test_record_counts_several_calls_at_once(tmp_path: Path) -> None:
    store = SqliteCacheStore(tmp_path / "c.db", max_bytes=_MB)
    store.record("embeddings", "embeddings.index", hit=True, latency_saved_ms=30, count=3)
    store.record("embeddings", "embeddings.index", hit=False, count=2)
    s = store.stats("embeddings", _MB)
    assert (s.hits, s.misses, s.latency_saved_ms) == (3, 2, 30)
