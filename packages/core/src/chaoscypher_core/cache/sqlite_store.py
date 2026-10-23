# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The one shipped :class:`~chaoscypher_core.cache.store.CacheStore`: one SQLite file.

The file is derived data with its own small schema (not Alembic): a
``meta.schema_version`` that differs from :attr:`SqliteCacheStore.SCHEMA_VERSION`,
or a file that is not a database at all, deletes the file and starts empty
(logged ``llm_cache_schema_reset``). WAL journaling, and ``BEGIN IMMEDIATE``
around every write so two workers on one file serialise on SQLite's own
lock; identical keys hold identical values by construction, so last write
wins is correct. One connection per store, guarded by a lock so threads
sharing a store never interleave transactions.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, TypeVar

import structlog

from chaoscypher_core.cache.store import CacheEntry, CacheStats
from chaoscypher_core.exceptions import ValidationError


logger = structlog.get_logger(__name__)

_T = TypeVar("_T")

DEFAULT_BUSY_TIMEOUT_S = 60.0
"""How long a write waits for another writer's lock (``DatabaseSettings.connection_timeout_secs``'s default)."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS entries (
    namespace TEXT NOT NULL,
    key TEXT NOT NULL,
    value BLOB NOT NULL,
    model_build TEXT NOT NULL,
    call_site TEXT NOT NULL,
    seeded INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at REAL NOT NULL,
    last_hit_at REAL NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (namespace, key)
);
CREATE INDEX IF NOT EXISTS entries_last_hit ON entries (last_hit_at);
CREATE INDEX IF NOT EXISTS entries_namespace_last_hit ON entries (namespace, last_hit_at);
CREATE TABLE IF NOT EXISTS totals (id INTEGER PRIMARY KEY, bytes INTEGER NOT NULL);
INSERT OR IGNORE INTO totals (id, bytes) VALUES (1, 0);
CREATE TRIGGER IF NOT EXISTS entries_bytes_insert AFTER INSERT ON entries BEGIN
    UPDATE totals SET bytes = bytes + new.bytes WHERE id = 1;
END;
CREATE TRIGGER IF NOT EXISTS entries_bytes_delete AFTER DELETE ON entries BEGIN
    UPDATE totals SET bytes = bytes - old.bytes WHERE id = 1;
END;
CREATE TRIGGER IF NOT EXISTS entries_bytes_update AFTER UPDATE OF bytes ON entries BEGIN
    UPDATE totals SET bytes = bytes - old.bytes + new.bytes WHERE id = 1;
END;
CREATE TABLE IF NOT EXISTS stats (
    namespace TEXT NOT NULL,
    call_site TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    misses INTEGER NOT NULL DEFAULT 0,
    bypasses INTEGER NOT NULL DEFAULT 0,
    latency_saved_ms INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (namespace, call_site)
);
"""

_ENTRY_COLUMNS = "value, model_build, call_site, seeded, latency_ms, bytes"
"""The entry columns, in the order :func:`_entry_of` reads them."""


def _entry_of(row: Sequence[Any]) -> CacheEntry:
    """The :class:`CacheEntry` of a row selected as :data:`_ENTRY_COLUMNS`."""
    value, model_build, call_site, seeded, latency_ms, size = row
    return CacheEntry(
        value=bytes(value),
        model_build=str(model_build),
        call_site=str(call_site),
        seeded=bool(seeded),
        latency_ms=int(latency_ms),
        bytes=int(size),
    )


class SqliteCacheStore:
    """A :class:`~chaoscypher_core.cache.store.CacheStore` in one SQLite file, LRU by last hit.

    ``put`` evicts the least-recently-hit entries of the whole file (every
    namespace counts against the one cap) until the total is at most
    :attr:`EVICT_TO` times ``max_bytes``. The total is kept by triggers in
    the ``totals`` row, so a put never sums the table.
    """

    SCHEMA_VERSION = 1
    """Bumped when the tables change; an older file is deleted and recreated."""

    EVICT_TO = 0.9
    """Past the cap, entries are evicted down to this fraction of it (headroom against thrash)."""

    def __init__(
        self, path: Path, *, max_bytes: int, busy_timeout_s: float = DEFAULT_BUSY_TIMEOUT_S
    ) -> None:
        """Open (or create, or reset) the store file at ``path`` with a ``max_bytes`` cap."""
        self._path = Path(path)
        self._max_bytes = max_bytes
        self._busy_timeout_s = busy_timeout_s
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._con = self._open()

    @property
    def path(self) -> Path:
        """The store file."""
        return self._path

    @property
    def max_bytes(self) -> int:
        """The size cap ``put`` enforces."""
        return self._max_bytes

    # ------------------------------------------------------------------ open

    def _connect(self) -> sqlite3.Connection:
        """A WAL connection in autocommit mode (transactions are explicit)."""
        con = sqlite3.connect(
            self._path,
            timeout=self._busy_timeout_s,
            check_same_thread=False,
            isolation_level=None,
        )
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    @staticmethod
    def read_schema_version(con: sqlite3.Connection) -> int | None:
        """The file's schema version; None for a fresh file, -1 for tables without a version."""
        tables = {
            row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not tables:
            return None
        if "meta" not in tables:
            return -1
        row = con.execute("SELECT v FROM meta WHERE k = 'schema_version'").fetchone()
        try:
            return int(row[0]) if row else -1
        except TypeError, ValueError:
            return -1

    def _reset_file(self, found: int | str | None) -> None:
        """Delete the store file and its WAL side files (logged)."""
        for suffix in ("", "-wal", "-shm"):
            Path(f"{self._path}{suffix}").unlink(missing_ok=True)
        logger.warning(
            "llm_cache_schema_reset",
            path=str(self._path),
            found=found,
            wanted=self.SCHEMA_VERSION,
        )

    def _open(self) -> sqlite3.Connection:
        """Connect, reset an incompatible file, create the tables and stamp the version."""
        con = sqlite3.connect(self._path, timeout=self._busy_timeout_s)
        try:
            version: int | str | None = self.read_schema_version(con)
        except sqlite3.DatabaseError as exc:  # not a database at all
            version = type(exc).__name__
        finally:
            con.close()
        if version is not None and version != self.SCHEMA_VERSION:
            self._reset_file(version)
        con = self._connect()
        con.executescript(_SCHEMA)
        con.execute(
            "INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_version', ?)",
            (str(self.SCHEMA_VERSION),),
        )
        return con

    # ----------------------------------------------------------- transactions

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """A ``BEGIN IMMEDIATE`` transaction on the store's connection, rolled back on error."""
        with self._lock:
            self._con.execute("BEGIN IMMEDIATE")
            try:
                yield self._con
            except BaseException:
                self._con.execute("ROLLBACK")
                raise
            self._con.execute("COMMIT")

    def _read(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        """Run a read under the connection lock."""
        with self._lock:
            return fn(self._con)

    # ------------------------------------------------------------- protocol

    def get(self, namespace: str, key: str) -> CacheEntry | None:
        """The entry under ``key`` (bumping ``last_hit_at`` and ``hits``), or None."""
        with self._write() as con:
            con.execute(
                "UPDATE entries SET last_hit_at = ?, hits = hits + 1 "
                "WHERE namespace = ? AND key = ?",
                (time.time(), namespace, key),
            )
            row = con.execute(
                f"SELECT {_ENTRY_COLUMNS} FROM entries WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        return None if row is None else _entry_of(row)

    def put(self, namespace: str, key: str, entry: CacheEntry) -> None:
        """Store ``entry`` under ``key`` (last write wins) and evict past the cap."""
        self.put_many(namespace, [(key, entry)])

    def put_many(
        self,
        namespace: str,
        items: Iterable[tuple[str, CacheEntry]],
        *,
        replace: bool = True,
    ) -> int:
        """Store every ``(key, entry)`` in one transaction, evict once; return how many were new.

        ``replace=False`` keeps an entry already under a key (an import never
        overwrites what the store holds).
        """
        now = time.time()
        conflict = (
            "DO UPDATE SET value = excluded.value, model_build = excluded.model_build, "
            "call_site = excluded.call_site, seeded = excluded.seeded, bytes = excluded.bytes, "
            "latency_ms = excluded.latency_ms, last_hit_at = excluded.last_hit_at"
            if replace
            else "DO NOTHING"
        )
        added = 0
        with self._write() as con:
            for key, entry in items:
                existed = replace and (
                    con.execute(
                        "SELECT 1 FROM entries WHERE namespace = ? AND key = ?", (namespace, key)
                    ).fetchone()
                    is not None
                )
                cursor = con.execute(
                    "INSERT INTO entries (namespace, key, value, model_build, call_site, "
                    "seeded, bytes, latency_ms, created_at, last_hit_at, hits) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0) "
                    f"ON CONFLICT (namespace, key) {conflict}",
                    (
                        namespace,
                        key,
                        entry.value,
                        entry.model_build,
                        entry.call_site,
                        int(entry.seeded),
                        entry.bytes,
                        entry.latency_ms,
                        now,
                        now,
                    ),
                )
                added += int(cursor.rowcount > 0 and not existed)
            self._evict(con)
        return added

    def _evict(self, con: sqlite3.Connection) -> None:
        """Delete least-recently-hit entries until the file is under ``EVICT_TO`` times the cap."""
        total = con.execute("SELECT bytes FROM totals WHERE id = 1").fetchone()[0]
        if total <= self._max_bytes:
            return
        target = int(self.EVICT_TO * self._max_bytes)
        doomed: list[int] = []
        for rowid, size in con.execute(
            "SELECT rowid, bytes FROM entries ORDER BY last_hit_at, rowid"
        ):
            if total <= target:
                break
            doomed.append(rowid)
            total -= size
        con.executemany("DELETE FROM entries WHERE rowid = ?", [(r,) for r in doomed])
        logger.debug("llm_cache_evicted", path=str(self._path), entries=len(doomed))

    def record(
        self,
        namespace: str,
        call_site: str,
        *,
        hit: bool | None,
        latency_saved_ms: int = 0,
        count: int = 1,
    ) -> None:
        """Count ``count`` calls: hits (``True``, adding ``latency_saved_ms``), misses or bypasses."""
        hits, misses, bypasses = (
            count * (hit is True),
            count * (hit is False),
            count * (hit is None),
        )
        with self._write() as con:
            con.execute(
                "INSERT INTO stats (namespace, call_site, hits, misses, bypasses, "
                "latency_saved_ms) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (namespace, call_site) DO UPDATE SET "
                "hits = hits + excluded.hits, misses = misses + excluded.misses, "
                "bypasses = bypasses + excluded.bypasses, "
                "latency_saved_ms = latency_saved_ms + excluded.latency_saved_ms",
                (
                    namespace,
                    call_site,
                    hits,
                    misses,
                    bypasses,
                    latency_saved_ms if hits else 0,
                ),
            )

    def clear(self, namespace: str | None = None) -> int:
        """Remove the entries of ``namespace`` (every one when None); the stats are kept."""
        with self._write() as con:
            if namespace is None:
                cursor = con.execute("DELETE FROM entries")
            else:
                cursor = con.execute("DELETE FROM entries WHERE namespace = ?", (namespace,))
            return int(cursor.rowcount)

    def stats(self, namespace: str, max_bytes: int) -> CacheStats:
        """The size of ``namespace`` now and its history per call site."""

        def read(con: sqlite3.Connection) -> CacheStats:
            """Both queries under one lock hold."""
            entries, size = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM entries WHERE namespace = ?",
                (namespace,),
            ).fetchone()
            by_site: dict[str, dict[str, int]] = {}
            for site, hits, misses, bypasses, saved in con.execute(
                "SELECT call_site, hits, misses, bypasses, latency_saved_ms FROM stats "
                "WHERE namespace = ? ORDER BY call_site",
                (namespace,),
            ):
                by_site[site] = {
                    "hits": hits,
                    "misses": misses,
                    "bypasses": bypasses,
                    "latency_saved_ms": saved,
                }
            return CacheStats(
                namespace=namespace,
                entries=int(entries),
                bytes=int(size),
                max_bytes=max_bytes,
                hits=sum(s["hits"] for s in by_site.values()),
                misses=sum(s["misses"] for s in by_site.values()),
                bypasses=sum(s["bypasses"] for s in by_site.values()),
                latency_saved_ms=sum(s["latency_saved_ms"] for s in by_site.values()),
                by_call_site=by_site,
            )

        return self._read(read)

    def namespaces(self) -> list[str]:
        """The namespaces that hold entries, sorted."""
        return self._read(
            lambda con: [
                row[0]
                for row in con.execute("SELECT DISTINCT namespace FROM entries ORDER BY namespace")
            ]
        )

    def close(self) -> None:
        """Close the connection."""
        with self._lock:
            self._con.close()


def read_entries(
    path: Path, *, namespace: str | None = None, model_build: str | None = None
) -> Iterator[tuple[str, str, CacheEntry]]:
    """``(namespace, key, entry)`` for every entry of the store file at ``path`` (read-only).

    Opens its own read-only connection, so it reads a live store without
    taking its lock and never rewrites the file it reads (a pack's store
    keeps its sha256). ``namespace`` and ``model_build`` filter when given.

    Raises:
        ValidationError: When the file is not a store of this schema version.
    """
    with closing(sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)) as con:
        try:
            version = SqliteCacheStore.read_schema_version(con)
        except sqlite3.DatabaseError as exc:
            msg = f"{path} is not a cache store: {exc}"
            raise ValidationError(msg, field="cache_store") from exc
        if version != SqliteCacheStore.SCHEMA_VERSION:
            msg = (
                f"{path} has cache schema {version}, this build reads "
                f"{SqliteCacheStore.SCHEMA_VERSION}"
            )
            raise ValidationError(msg, field="cache_store")
        clauses, params = [], []
        if namespace is not None:
            clauses.append("namespace = ?")
            params.append(namespace)
        if model_build is not None:
            clauses.append("model_build = ?")
            params.append(model_build)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        for row in con.execute(
            f"SELECT namespace, key, {_ENTRY_COLUMNS} FROM entries{where} ORDER BY rowid",
            params,
        ):
            yield row[0], row[1], _entry_of(row[2:])


__all__ = ["DEFAULT_BUSY_TIMEOUT_S", "SqliteCacheStore", "read_entries"]
