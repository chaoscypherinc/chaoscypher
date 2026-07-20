# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Repository - Unified Full-Text and Vector Search (SQLite adapter).

Concrete implementation of :class:`chaoscypher_core.ports.search.SearchRepositoryProtocol`
backed by the SQLite adapter. Lives under ``adapters/sqlite/repos/`` because
it issues raw SQL against the SQLite engine and depends on adapter-specific
features (FTS5 virtual tables, sqlite-vec ``vec0`` virtual table).

Provides keyword search (SQLite FTS5) and vector similarity search (sqlite-vec),
both stored in the main app.db database for WAL-mode concurrency safety.

Tracks the active embedding model name and vector dimensions in a
``search_metadata`` table.  When the configured model or dimensions
change, sets ``needs_full_reindex`` so callers can trigger background
re-embedding.  Per-item dimension mismatches during indexing are queued
via ``schedule_reindex()`` and flushed asynchronously by the caller.
"""

from __future__ import annotations

import json
import re
import struct
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.adapters.sqlite.repos.text_indexer import extract_searchable_text
from chaoscypher_core.exceptions import SchemaIntegrityError


if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, Engine

    from chaoscypher_core.models import Node
    from chaoscypher_core.ports.transactional import TransactionalSession

logger = structlog.get_logger(__name__)

# FTS5 special characters that need escaping
_FTS5_SPECIAL_CHARS = set('"()*:^')

# Maps item_type to the per-type sqlite-vec virtual table. Splitting the
# old mixed vec_search table eliminated the sequential cosine scan that
# fired when callers filtered by item_type — each per-type table now runs
# the KNN MATCH path directly.
_VEC_TABLE_BY_TYPE: dict[str, str] = {
    "chunk": "vec_search_chunks",
    "node": "vec_search_nodes",
    "template": "vec_search_templates",
}
_VEC_TABLES: tuple[str, ...] = tuple(_VEC_TABLE_BY_TYPE.values())

# Parses ``embedding float[NNNN]`` out of a vec0 CREATE VIRTUAL TABLE
# statement so we can verify table-DDL dim vs configured dim.
_VEC_DIM_PATTERN = re.compile(r"embedding\s+float\[(\d+)\]", re.IGNORECASE)


def _vec_table_for(item_type: str) -> str:
    """Resolve the per-type vec0 table name, raising on unknown types."""
    try:
        return _VEC_TABLE_BY_TYPE[item_type]
    except KeyError as exc:
        msg = (
            f"unknown vector item_type {item_type!r}; expected one of {sorted(_VEC_TABLE_BY_TYPE)}"
        )
        raise ValueError(msg) from exc


def _sanitize_fts5_query(query: str) -> str:
    """Sanitize a query string for FTS5 MATCH syntax.

    Wraps each token in double quotes to prevent FTS5 syntax errors.

    Args:
        query: Raw user query string

    Returns:
        Sanitized query safe for FTS5 MATCH

    """
    query = query.strip()
    if not query:
        return ""
    tokens = query.split()
    quoted = [f'"{token.replace(chr(34), "")}"' for token in tokens if token]
    return " ".join(quoted)


def _serialize_float32(vector: list[float]) -> bytes:
    """Serialize a float32 vector to bytes for sqlite-vec.

    Args:
        vector: List of float values

    Returns:
        Packed bytes in little-endian float32 format

    """
    return struct.pack(f"<{len(vector)}f", *vector)


class SearchRepository:
    """Unified search repository using sqlite-vec and FTS5 in app.db.

    All search indices (vector embeddings and fulltext keyword index)
    live in the main app.db database, sharing its WAL mode for
    multi-process concurrency safety.

    Responsibilities:
    - Create and manage vec0 virtual table (sqlite-vec) for vector search
    - Create and manage FTS5 virtual tables for keyword search
    - Index nodes for both keyword and vector search
    - Perform keyword, vector, semantic, and hybrid searches
    - Manage template embeddings for semantic template matching
    """

    def __init__(
        self,
        engine: Engine,
        vector_dim: int = 1024,
        embedding_model: str = "",
    ) -> None:
        """Initialize search repository with app.db engine.

        Args:
            engine: SQLAlchemy engine connected to app.db
            vector_dim: Dimensionality of embedding vectors (default: 1024)
            embedding_model: Name of the embedding model (e.g. HuggingFace model ID).
                Used to detect model changes and trigger reindexing.

        """
        self._engine = engine
        if not isinstance(vector_dim, int) or not (1 <= vector_dim <= 65536):
            msg = f"vector_dim must be an integer 1-65536, got {vector_dim!r}"
            raise ValueError(msg)
        self.vector_dim = vector_dim
        self.embedding_model = embedding_model
        self._reindex_queue: list[dict[str, Any]] = []
        self._reindex_queue_max_size = 10_000
        self._init_schema()
        self._check_model_change()
        self._assert_vec_table_dim_consistency()
        self._warn_on_search_index_drift()

    # ========================================================================
    # Schema Initialization
    # ========================================================================

    def _init_schema(self) -> None:
        """Create per-type vec0 tables and FTS5 tables if they don't exist."""
        from sqlalchemy import text

        with self._engine.begin() as conn:
            # Per-type sqlite-vec virtual tables — one each for chunks,
            # nodes, and templates. Splitting by type lets every search
            # take the native KNN ``embedding MATCH … AND k = ?`` path
            # instead of the cosine sequential scan that fired when the
            # legacy ``vec_search`` table filtered on ``item_type``.
            for type_table in _VEC_TABLES:
                conn.execute(
                    text(
                        f"CREATE VIRTUAL TABLE IF NOT EXISTS {type_table} USING vec0("
                        f"embedding float[{self.vector_dim}],"
                        f"+item_id TEXT"
                        f")"
                    )
                )

            # FTS5 content table
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS fulltext_content ("
                    "node_id TEXT PRIMARY KEY,"
                    "label TEXT NOT NULL DEFAULT '',"
                    "properties TEXT NOT NULL DEFAULT '',"
                    "searchable_text TEXT NOT NULL DEFAULT ''"
                    ")"
                )
            )

            # FTS5 virtual table (content-linked)
            conn.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS fulltext_index USING fts5("
                    "label,"
                    "properties,"
                    "searchable_text,"
                    "content='fulltext_content',"
                    "content_rowid='rowid',"
                    "tokenize='porter unicode61'"
                    ")"
                )
            )

            # Triggers to keep FTS5 in sync with content table
            conn.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS fulltext_content_ai "
                    "AFTER INSERT ON fulltext_content BEGIN "
                    "INSERT INTO fulltext_index(rowid, label, properties, searchable_text) "
                    "VALUES (new.rowid, new.label, new.properties, new.searchable_text); "
                    "END"
                )
            )

            conn.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS fulltext_content_ad "
                    "AFTER DELETE ON fulltext_content BEGIN "
                    "INSERT INTO fulltext_index(fulltext_index, rowid, label, properties, searchable_text) "
                    "VALUES ('delete', old.rowid, old.label, old.properties, old.searchable_text); "
                    "END"
                )
            )

            conn.execute(
                text(
                    "CREATE TRIGGER IF NOT EXISTS fulltext_content_au "
                    "AFTER UPDATE ON fulltext_content BEGIN "
                    "INSERT INTO fulltext_index(fulltext_index, rowid, label, properties, searchable_text) "
                    "VALUES ('delete', old.rowid, old.label, old.properties, old.searchable_text); "
                    "INSERT INTO fulltext_index(rowid, label, properties, searchable_text) "
                    "VALUES (new.rowid, new.label, new.properties, new.searchable_text); "
                    "END"
                )
            )

            # Search metadata (tracks active model config)
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS search_metadata ("
                    "key TEXT PRIMARY KEY,"
                    "value TEXT NOT NULL"
                    ")"
                )
            )

        logger.info(
            "search_schema_initialized",
            vector_dim=self.vector_dim,
            backend="sqlite-vec + FTS5 (app.db)",
        )

    def _check_model_change(self) -> None:
        """Compare stored model config against current and handle changes.

        If embedding_model is empty (backwards compat), skip detection.
        If dimensions changed, drop and recreate every per-type vec0 table.
        If only model name changed, set needs_full_reindex flag.
        Updates stored metadata to current config.
        """
        from sqlalchemy import text

        if not self.embedding_model:
            return

        needs_reindex = False
        with self._engine.begin() as conn:
            # Read stored metadata
            stored_model = None
            stored_dim = None
            rows = conn.execute(
                text(
                    "SELECT key, value FROM search_metadata "
                    "WHERE key IN ('embedding_model', 'vector_dim')"
                )
            ).fetchall()
            for key, value in rows:
                if key == "embedding_model":
                    stored_model = value
                elif key == "vector_dim":
                    stored_dim = int(value)

            # First time — just store and return (engine.begin() commits on exit)
            if stored_model is None:
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO search_metadata (key, value) "
                        "VALUES ('embedding_model', :model)"
                    ),
                    {"model": self.embedding_model},
                )
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO search_metadata (key, value) "
                        "VALUES ('vector_dim', :dim)"
                    ),
                    {"dim": str(self.vector_dim)},
                )
                return

            # Check for changes
            dim_changed = stored_dim is not None and stored_dim != self.vector_dim
            model_changed = stored_model != self.embedding_model

            if not dim_changed and not model_changed:
                return

            # Check if there are any vectors to reindex — summed across
            # all per-type tables since the split.
            total_vectors = 0
            for type_table in _VEC_TABLES:
                row = conn.execute(text(f"SELECT COUNT(*) FROM {type_table}")).fetchone()
                if row is not None:
                    total_vectors += row[0]
            has_vectors = total_vectors > 0

        if dim_changed:
            # Must recreate every per-type vec0 table with new dimensions.
            # Each phase runs in its OWN ``engine.begin()`` block so the
            # ``is_rebuilding`` flag becomes visible to concurrent readers
            # the moment the first txn commits — without resorting to
            # mid-transaction ``conn.commit()`` (CC-Phase 3: SearchRepository
            # never calls ``.commit()`` directly).
            logger.info(
                "vector_dim_changed_recreating_tables",
                old_dim=stored_dim,
                new_dim=self.vector_dim,
            )
            with self._engine.begin() as flag_conn:
                flag_conn.execute(
                    text(
                        "INSERT OR REPLACE INTO search_metadata (key, value) "
                        "VALUES ('is_rebuilding', 'true')"
                    )
                )
            try:
                with self._engine.begin() as ddl_conn:
                    for type_table in _VEC_TABLES:
                        ddl_conn.execute(text(f"DROP TABLE IF EXISTS {type_table}"))
                        ddl_conn.execute(
                            text(
                                f"CREATE VIRTUAL TABLE {type_table} USING vec0("
                                f"embedding float[{self.vector_dim}],"
                                f"+item_id TEXT"
                                f")"
                            )
                        )
            finally:
                with self._engine.begin() as clear_conn:
                    clear_conn.execute(
                        text(
                            "INSERT OR REPLACE INTO search_metadata (key, value) "
                            "VALUES ('is_rebuilding', 'false')"
                        )
                    )
            if has_vectors:
                needs_reindex = True

        elif model_changed and has_vectors:
            # Same dimensions but different model — vectors are in wrong space
            needs_reindex = True

        if needs_reindex:
            logger.warning(
                "search_indexes_stale",
                reason=(
                    "embedding_dimension_mismatch" if dim_changed else "embedding_model_mismatch"
                ),
                action=(
                    "Run 'chaoscypher source rebuild-search' "
                    "or use Settings > Rebuild Search Indexes"
                ),
            )

        # Update stored metadata in its own short transaction.
        with self._engine.begin() as meta_conn:
            meta_conn.execute(
                text(
                    "INSERT OR REPLACE INTO search_metadata (key, value) "
                    "VALUES ('embedding_model', :model)"
                ),
                {"model": self.embedding_model},
            )
            meta_conn.execute(
                text(
                    "INSERT OR REPLACE INTO search_metadata (key, value) "
                    "VALUES ('vector_dim', :dim)"
                ),
                {"dim": str(self.vector_dim)},
            )

        # Set reindex flag via dedicated method (outside the connection)
        if needs_reindex:
            self._set_reindex_flag(True)

    def _assert_vec_table_dim_consistency(self) -> None:
        """Refuse to serve when a per-type vec0 table's DDL dim disagrees with config.

        Pre-launch review F4. ``_check_model_change`` only fires the
        recreate path when ``search_metadata`` already has a stored
        ``vector_dim`` row. Two real-world cases slip past it:

        1. A backup restored without ``search_metadata`` (some backup
           tools skip the metadata table). ``_check_model_change`` takes
           the "first time" branch and writes the configured dim to
           metadata without inspecting the existing vec0 tables.
        2. A backup restored with mismatched ``search_metadata`` and
           vec0 tables (the metadata row says one dim, the vec0 DDL
           says another).

        In both cases queries silently return wrong results — sqlite-vec
        does not validate dimension at query time. This check reads the
        actual CREATE statement out of ``sqlite_master`` and refuses to
        construct the repository when the stored dim disagrees with the
        configured ``self.vector_dim``, surfacing the rebuild path
        instead of a deep-in-the-query crash.
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            for type_table in _VEC_TABLES:
                row = conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
                    {"name": type_table},
                ).fetchone()
                if row is None or not row[0]:
                    # Table absent — `_init_schema` would have just created it
                    # at the right dim; nothing to verify here.
                    continue
                match = _VEC_DIM_PATTERN.search(row[0])
                if not match:
                    logger.warning(
                        "vec_table_dim_unparseable",
                        table=type_table,
                        sql=row[0],
                    )
                    continue
                actual_dim = int(match.group(1))
                if actual_dim == self.vector_dim:
                    continue
                msg = (
                    f"Vector-search table {type_table} declares "
                    f"float[{actual_dim}] but the configured embedding "
                    f"model expects {self.vector_dim}. This usually means "
                    f"the database was restored from a different embedding "
                    f"model. Run 'chaoscypher source rebuild-search' (or "
                    f"Settings → Rebuild Search Indexes) to recreate the "
                    f"vector tables at the correct dimension."
                )
                raise SchemaIntegrityError(msg)

    def check_fts5_integrity(self) -> tuple[bool, str | None]:
        """Run FTS5's ``'integrity-check'`` command against the index.

        Pre-launch review F3. The ``fulltext_index`` virtual table is
        kept in sync with ``fulltext_content`` by three INSERT / UPDATE
        / DELETE triggers (see ``_init_schema``). If a trigger ever
        fails to fire (lock-timeout mid-trigger, schema rebuild gone
        wrong, manual operator edit), the index silently drifts from
        content and search results go stale until a rebuild.

        Row-count comparison doesn't work for FTS5's external-content
        layout: ``SELECT COUNT(*) FROM fulltext_index`` reads from the
        content table at query time, so it agrees with content even
        when the underlying inverted index entries are stale. The
        canonical health check is the FTS5-specific
        ``INSERT INTO <fts_table>(<fts_table>) VALUES('integrity-check')``
        command, which scans the index and raises an
        ``OperationalError`` (typically ``SQLITE_CORRUPT_VTAB``) on
        drift / corruption.

        Returns:
            Tuple of ``(is_consistent, error_message)``. ``error_message``
            is ``None`` when consistent.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError

        try:
            with self._engine.begin() as conn:
                conn.execute(
                    text("INSERT INTO fulltext_index(fulltext_index) VALUES('integrity-check')")
                )
        except OperationalError as e:
            return False, str(e.orig if e.orig is not None else e)
        return True, None

    def _warn_on_search_index_drift(self) -> None:
        """Log a structured WARNING when FTS5 integrity-check fails at boot.

        Called from ``__init__`` after schema setup. The integrity-check
        is intentionally non-fatal — drift is operator-fixable via
        ``chaoscypher source rebuild-search`` and the running process
        can still serve degraded keyword search in the meantime.
        """
        is_consistent, error_message = self.check_fts5_integrity()
        if is_consistent:
            return
        logger.warning(
            "search_index_drift_detected",
            error=error_message,
            action=(
                "Run 'chaoscypher source rebuild-search' or use "
                "Settings → Rebuild Search Indexes to resynchronize."
            ),
        )

    # ========================================================================
    # Node Indexing (both FTS5 and vector)
    # ========================================================================

    def index_node(self, node: Node, *, session: TransactionalSession | None = None) -> None:
        """Index a node for full-text and vector search.

        When ``session`` is provided, the write participates in the
        caller's transaction: the caller owns commit/rollback, and
        failures propagate so the caller can roll back cleanly. When
        ``session`` is None, opens a standalone connection with the
        historical best-effort semantics (errors are logged, not raised).

        Args:
            node: Node to index
            session: Optional caller session to share a transaction with.

        """
        from sqlalchemy import text

        try:
            searchable_text = extract_searchable_text(node)

            with self._acquire_conn(session) as conn:
                # FTS5: upsert content
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO fulltext_content "
                        "(node_id, label, properties, searchable_text) "
                        "VALUES (:node_id, :label, :properties, :searchable_text)"
                    ),
                    {
                        "node_id": node.id,
                        "label": node.label,
                        "properties": json.dumps(node.properties),
                        "searchable_text": searchable_text,
                    },
                )

                # Vector: index embedding if present
                if node.embedding:
                    if len(node.embedding) != self.vector_dim:
                        self.schedule_reindex(node.id, searchable_text, "node")
                    else:
                        self._upsert_vector(conn, node.id, node.embedding, "node")
        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "node_indexing_failed",
                node_id=node.id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def index_nodes_batch(
        self, nodes: list[Node], *, session: TransactionalSession | None = None
    ) -> None:
        """Batch index multiple nodes for keyword and vector search.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            nodes: List of nodes to index
            session: Optional caller session to share a transaction with.

        """
        if not nodes:
            return

        from sqlalchemy import text

        try:
            rows = []
            for node in nodes:
                searchable_text = extract_searchable_text(node)
                rows.append(
                    {
                        "node_id": node.id,
                        "label": node.label,
                        "properties": json.dumps(node.properties),
                        "searchable_text": searchable_text,
                    }
                )

            with self._acquire_conn(session) as conn:
                # Batch FTS5 upsert
                conn.execute(
                    text(
                        "INSERT OR REPLACE INTO fulltext_content "
                        "(node_id, label, properties, searchable_text) "
                        "VALUES (:node_id, :label, :properties, :searchable_text)"
                    ),
                    rows,
                )

                # Batch vector index (queue mismatches, bulk-upsert valid ones)
                valid_embeddings: list[tuple[str, list[float]]] = []
                for node in nodes:
                    if node.embedding:
                        if len(node.embedding) != self.vector_dim:
                            node_text = extract_searchable_text(node)
                            self.schedule_reindex(node.id, node_text, "node")
                        else:
                            valid_embeddings.append((node.id, node.embedding))
                self._upsert_vectors_bulk(conn, valid_embeddings, "node")

            logger.info("nodes_batch_indexed", count=len(nodes))
        except Exception as e:
            if session is not None:
                raise
            logger.exception(
                "batch_indexing_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def index_node_embedding(
        self,
        node_id: str,
        embedding: list[float],
        *,
        session: TransactionalSession | None = None,
    ) -> None:
        """Index a single node's embedding for vector search.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            node_id: ID of the node
            embedding: Embedding vector
            session: Optional caller session to share a transaction with.

        """
        if len(embedding) != self.vector_dim:
            logger.info(
                "embedding_dimension_mismatch_skipped",
                node_id=node_id,
                got_dimensions=len(embedding),
                expected_dimensions=self.vector_dim,
            )
            return

        try:
            with self._acquire_conn(session) as conn:
                self._upsert_vector(conn, node_id, embedding, "node")
        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "embedding_indexing_failed",
                node_id=node_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def index_embeddings_batch(
        self,
        embeddings: list[tuple[str, list[float]]],
        item_type: str = "node",
        text_lookup: dict[str, str] | None = None,
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Batch index embeddings for vector search.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            embeddings: List of (item_id, embedding) tuples
            item_type: Type of items ("node", "chunk", "template")
            text_lookup: Optional mapping of item_id to source text.
                When provided, items with dimension mismatches are
                queued for async re-embedding with their text.
            session: Optional caller session to share a transaction with.

        Returns:
            Number of embeddings indexed

        """
        if not embeddings:
            return 0

        try:
            valid_embeddings: list[tuple[str, list[float]]] = []

            for item_id, embedding in embeddings:
                if len(embedding) != self.vector_dim:
                    source_text = text_lookup.get(item_id) if text_lookup else None
                    if source_text:
                        self.schedule_reindex(item_id, source_text, item_type)
                    else:
                        logger.info(
                            "embedding_dimension_mismatch_no_text",
                            item_id=item_id,
                            got=len(embedding),
                            expected=self.vector_dim,
                        )
                    continue
                valid_embeddings.append((item_id, embedding))

            with self._acquire_conn(session) as conn:
                self._upsert_vectors_bulk(conn, valid_embeddings, item_type)

            indexed_count = len(valid_embeddings)
            logger.info("embeddings_batch_indexed", count=indexed_count, item_type=item_type)
            return indexed_count

        except Exception as e:
            if session is not None:
                raise
            logger.exception(
                "batch_embedding_indexing_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return 0

    def remove_embedding(
        self,
        item_id: str,
        item_type: str,
        *,
        session: TransactionalSession | None = None,
    ) -> None:
        """Remove an embedding from the per-type vector index.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            item_id: ID of item to remove (may include prefix like "chunk:xxx")
            item_type: Type of item ("node", "chunk", "template") — selects
                which per-type vec0 table to delete from.
            session: Optional caller session to share a transaction with.

        """
        from sqlalchemy import text

        table = _vec_table_for(item_type)
        try:
            with self._acquire_conn(session) as conn:
                conn.execute(
                    text(f"DELETE FROM {table} WHERE item_id = :item_id"),
                    {"item_id": item_id},
                )
        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "embedding_removal_failed",
                item_id=item_id,
                item_type=item_type,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def remove_embeddings_batch(
        self,
        item_ids: list[str],
        item_type: str,
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Remove multiple embeddings from the per-type vector index.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            item_ids: List of item IDs to remove.
            item_type: Type of items ("node", "chunk", "template") — selects
                which per-type vec0 table to delete from.
            session: Optional caller session to share a transaction with.

        Returns:
            Number of embeddings removed.

        """
        if not item_ids:
            return 0

        from sqlalchemy import text

        table = _vec_table_for(item_type)
        try:
            with self._acquire_conn(session) as conn:
                # SQLite handles IN clauses efficiently; batch in groups of 500
                # to stay within SQLite's variable limit
                removed = 0
                for i in range(0, len(item_ids), 500):
                    batch = item_ids[i : i + 500]
                    placeholders = ", ".join(f":id_{j}" for j in range(len(batch)))
                    params = {f"id_{j}": item_id for j, item_id in enumerate(batch)}
                    result = conn.execute(
                        text(f"DELETE FROM {table} WHERE item_id IN ({placeholders})"),
                        params,
                    )
                    removed += result.rowcount
            return removed
        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "batch_embedding_removal_failed",
                count=len(item_ids),
                item_type=item_type,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return 0

    # ========================================================================
    # Node Deletion (both FTS5 and vector)
    # ========================================================================

    def delete_node(self, node_id: str, *, session: TransactionalSession | None = None) -> None:
        """Remove a node from both keyword and vector indexes.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            node_id: ID of node to remove
            session: Optional caller session to share a transaction with.

        """
        from sqlalchemy import text

        nodes_table = _vec_table_for("node")
        try:
            with self._acquire_conn(session) as conn:
                conn.execute(
                    text("DELETE FROM fulltext_content WHERE node_id = :node_id"),
                    {"node_id": node_id},
                )
                conn.execute(
                    text(f"DELETE FROM {nodes_table} WHERE item_id = :item_id"),
                    {"item_id": node_id},
                )
        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "node_deletion_failed",
                node_id=node_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def delete_nodes_batch(
        self, node_ids: list[str], *, session: TransactionalSession | None = None
    ) -> int:
        """Remove multiple nodes from both keyword and vector indexes.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            node_ids: List of node IDs to remove
            session: Optional caller session to share a transaction with.

        Returns:
            Number of nodes removed

        """
        from sqlalchemy import text

        nodes_table = _vec_table_for("node")
        removed = 0
        try:
            with self._acquire_conn(session) as conn:
                for i in range(0, len(node_ids), 500):
                    batch = node_ids[i : i + 500]
                    placeholders = ", ".join(f":id_{j}" for j in range(len(batch)))
                    params = {f"id_{j}": nid for j, nid in enumerate(batch)}
                    conn.execute(
                        text(f"DELETE FROM fulltext_content WHERE node_id IN ({placeholders})"),
                        params,
                    )
                    conn.execute(
                        text(f"DELETE FROM {nodes_table} WHERE item_id IN ({placeholders})"),
                        params,
                    )
                    removed += len(batch)
        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "batch_deletion_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

        if removed:
            logger.info("nodes_batch_deleted_from_search", count=removed)
        return removed

    def update_node(self, node: Node, *, session: TransactionalSession | None = None) -> None:
        """Update a node in both keyword and vector indexes.

        Delegates to :meth:`index_node`; the ``session`` kwarg flows
        through unchanged.

        Args:
            node: Node with updated data
            session: Optional caller session to share a transaction with.

        """
        self.index_node(node, session=session)

    # ========================================================================
    # Search Operations
    # ========================================================================

    def keyword_search(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        """Perform full-text keyword search with BM25 ranking.

        Args:
            query: Search query string
            limit: Maximum results

        Returns:
            List of (node_id, score) tuples sorted by relevance

        """
        from sqlalchemy import text

        sanitized = _sanitize_fts5_query(query)
        if not sanitized:
            return []

        try:
            with self._engine.connect() as conn:
                result = conn.execute(
                    text(
                        "SELECT c.node_id, -bm25(fulltext_index, 3.0, 1.0, 0.5) AS score "
                        "FROM fulltext_index fi "
                        "JOIN fulltext_content c ON c.rowid = fi.rowid "
                        "WHERE fulltext_index MATCH :query "
                        "ORDER BY score DESC "
                        "LIMIT :limit"
                    ),
                    {"query": sanitized, "limit": limit},
                )
                return [(row[0], row[1]) for row in result]
        except Exception as e:
            logger.exception(
                "keyword_search_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return []

    def vector_search(
        self,
        query_embedding: list[float],
        k: int = 10,
        item_type: str | None = None,
    ) -> list[tuple[str, float]]:
        """Perform vector similarity search using cosine distance.

        Dispatches to the per-type vec0 table for ``item_type`` so the
        KNN ``embedding MATCH :query AND k = :k`` index path is used.
        When ``item_type`` is None the search runs against every per-type
        table and the merged result is the global top-k by similarity.

        Args:
            query_embedding: Query vector
            k: Number of results
            item_type: Optional filter by type ("node", "chunk", "template")

        Returns:
            List of (item_id, similarity_score) tuples sorted by similarity
            descending.

        """
        from sqlalchemy import text

        if len(query_embedding) != self.vector_dim:
            logger.warning(
                "vector_search_dimension_mismatch",
                got_dimensions=len(query_embedding),
                expected_dimensions=self.vector_dim,
            )
            return []

        # Resolve table set before entering the try-block so an unknown
        # item_type raises instead of being silently swallowed (the
        # try-block swallows other vec0 failures to keep search a soft
        # dependency for the request pipeline).
        if item_type is not None:
            tables: tuple[str, ...] = (_vec_table_for(item_type),)
        else:
            tables = _VEC_TABLES

        try:
            query_bytes = _serialize_float32(query_embedding)

            merged: list[tuple[str, float]] = []
            with self._engine.connect() as conn:
                for table in tables:
                    sql = (
                        f"SELECT item_id, vec_distance_cosine(embedding, :query) AS distance "
                        f"FROM {table} "
                        f"WHERE embedding MATCH :query "
                        f"AND k = :k"
                    )
                    result = conn.execute(text(sql), {"query": query_bytes, "k": k})
                    for row in result:
                        item_id = row[0]
                        distance = row[1]
                        # Cosine distance: 0 = identical, 2 = opposite
                        similarity = max(0.0, min(1.0, 1.0 - distance))
                        merged.append((item_id, similarity))

            if len(tables) > 1:
                merged.sort(key=lambda pair: pair[1], reverse=True)
                return merged[:k]
            return merged

        except Exception as e:
            logger.exception(
                "vector_search_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return []

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Perform keyword search and return results as dicts.

        Args:
            query: Search query string
            limit: Maximum number of results

        Returns:
            List of dicts with 'id' and 'score' keys

        """
        results = self.keyword_search(query, limit)
        return [{"id": node_id, "score": score} for node_id, score in results]

    # ========================================================================
    # Semantic Search (uses embedding provider callback)
    # ========================================================================

    async def semantic_search(
        self,
        query_text: str,
        k: int = 10,
        embedding_provider_callback: Callable[[str], Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Perform semantic search using query text.

        Generates embedding for the query via callback, then performs vector search.

        Args:
            query_text: Text to search for
            k: Number of results to return
            embedding_provider_callback: Async callback that returns embedding

        Returns:
            List of (node_id, similarity_score) tuples

        """
        if not embedding_provider_callback:
            logger.warning("semantic_search_no_embedding_provider")
            return []

        if self.is_rebuilding:
            logger.info(
                "semantic_search_rebuilding_fallback_to_fts",
                query=query_text,
            )
            return self.keyword_search(query_text, limit=k)

        try:
            result = await embedding_provider_callback(query_text)

            if isinstance(result, dict):
                query_embedding = result.get("embedding", [])
            elif hasattr(result, "embedding"):
                query_embedding = result.embedding
            else:
                query_embedding = result

            if not query_embedding:
                logger.warning("Failed to generate query embedding")
                return []

            return self.vector_search(query_embedding, k=k)

        except Exception as e:
            logger.exception(
                "semantic_search_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return []

    async def hybrid_search(
        self,
        query_text: str,
        k: int = 10,
        embedding_provider_callback: Callable[[str], Any] | None = None,
        min_similarity: float = 0.55,
    ) -> list[tuple[str, float]]:
        """Perform hybrid search: combines keyword and semantic results.

        Args:
            query_text: Text to search for
            k: Number of results to return
            embedding_provider_callback: Async callback for generating embeddings
            min_similarity: Minimum similarity score for semantic results

        Returns:
            List of (id, score) tuples

        """
        try:
            if len(query_text.strip()) < 3:
                return self.keyword_search(query_text, limit=k)

            keyword_results = self.keyword_search(query_text, limit=k)

            semantic_results = await self.semantic_search(
                query_text, k=k, embedding_provider_callback=embedding_provider_callback
            )

            filtered_semantic = [
                (result_id, score)
                for result_id, score in semantic_results
                if score >= min_similarity
            ]

            merged: dict[str, float] = {}
            for result_id, score in keyword_results:
                merged[result_id] = max(merged.get(result_id, 0), score)
            for result_id, score in filtered_semantic:
                merged[result_id] = max(merged.get(result_id, 0), score)

            sorted_results = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:k]

            logger.debug(
                "hybrid_search_merged",
                keyword_count=len(keyword_results),
                semantic_count=len(filtered_semantic),
                merged_count=len(sorted_results),
            )

            return sorted_results

        except Exception as e:
            logger.warning(
                "hybrid_search_failed_fallback_to_keyword",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return self.keyword_search(query_text, limit=k)

    # ========================================================================
    # Result Enrichment
    # ========================================================================

    def enrich_search_results(
        self,
        results: list[tuple[str, float]],
        graph_batch_get_callback: Callable[[list[str]], dict[str, Node]],
        format_type: str = "dict",
    ) -> list[dict]:
        """Enrich search results with full node data.

        Args:
            results: List of (node_id, score) tuples from search
            graph_batch_get_callback: Callback to batch fetch nodes by IDs
            format_type: "dict" returns dicts, "model" returns objects

        Returns:
            List of enriched results with node data and scores

        """
        if not results:
            return []

        node_ids = [node_id for node_id, _ in results]
        nodes_dict = graph_batch_get_callback(node_ids)

        enriched = []
        for node_id, score in results:
            node = nodes_dict.get(node_id)
            if node:
                if format_type == "model":
                    enriched.append({"node": node, "score": score})
                else:
                    enriched.append({"node": node, "score": score, "node_id": node.id})

        logger.debug("search_results_enriched", result_count=len(enriched))
        return enriched

    # ========================================================================
    # Template Search Operations
    # ========================================================================

    def index_template(
        self,
        template_id: str,
        embedding: list[float],
        *,
        session: TransactionalSession | None = None,
    ) -> None:
        """Index a template embedding for semantic search.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            template_id: Template ID to index
            embedding: Embedding vector for the template
            session: Optional caller session to share a transaction with.

        """
        prefixed_id = f"template:{template_id}"
        try:
            with self._acquire_conn(session) as conn:
                self._upsert_vector(conn, prefixed_id, embedding, "template")
            logger.debug("template_indexed", template_id=template_id)
        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "template_indexing_failed",
                template_id=template_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def template_semantic_search(
        self,
        query_embedding: list[float],
        k: int = 10,
        min_similarity: float = 0.5,
    ) -> list[tuple[str, float]]:
        """Search templates by semantic similarity.

        Args:
            query_embedding: Query vector to search with
            k: Maximum number of results to return
            min_similarity: Minimum similarity score to include

        Returns:
            List of (template_id, similarity_score) tuples (ID without prefix)

        """
        results = self.vector_search(query_embedding, k=k, item_type="template")

        template_results = [
            (item_id.replace("template:", ""), score)
            for item_id, score in results
            if score >= min_similarity
        ]

        logger.debug(
            "template_semantic_search",
            query_results=len(results),
            template_results=len(template_results),
            min_similarity=min_similarity,
        )

        return template_results[:k]

    # ========================================================================
    # Maintenance Operations
    # ========================================================================

    def reindex_all_nodes(
        self, nodes: list[Node], *, session: TransactionalSession | None = None
    ) -> None:
        """Reindex all nodes (useful after bulk import or index corruption).

        Delegates to :meth:`clear_all_indices` and :meth:`index_nodes_batch`;
        the ``session`` kwarg flows through unchanged.

        Args:
            nodes: List of all nodes to reindex
            session: Optional caller session to share a transaction with.

        """
        logger.info("reindexing_nodes_started", node_count=len(nodes))

        self.clear_all_indices(session=session)

        self.index_nodes_batch(nodes, session=session)

        logger.info("Reindexing complete")

    def get_index_stats(self) -> dict[str, Any]:
        """Get statistics about the search indexes.

        Returns:
            Dict with fulltext and vector statistics

        """
        from sqlalchemy import text

        try:
            with self._engine.connect() as conn:
                ft_count = conn.execute(text("SELECT COUNT(*) FROM fulltext_content")).fetchone()
                vec_total = 0
                per_type: dict[str, int] = {}
                for item_type, table in _VEC_TABLE_BY_TYPE.items():
                    row = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()
                    type_count = row[0] if row else 0
                    per_type[item_type] = type_count
                    vec_total += type_count

            return {
                "fulltext": {
                    "document_count": ft_count[0] if ft_count else 0,
                },
                "vector": {
                    "vector_count": vec_total,
                    "by_type": per_type,
                    "dimensions": self.vector_dim,
                },
            }
        except Exception as e:
            logger.exception(
                "index_stats_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return {"error": "Index stats unavailable"}

    def clear_all_indices(self, *, session: TransactionalSession | None = None) -> None:
        """Clear all search indices (fulltext and vector).

        WARNING: This operation cannot be undone!
        Indices will need to be rebuilt by reindexing nodes.

        See :meth:`index_node` for the ``session`` contract.

        Args:
            session: Optional caller session to share a transaction with.

        """
        from sqlalchemy import text

        try:
            with self._acquire_conn(session) as conn:
                conn.execute(text("DELETE FROM fulltext_content"))
                for table in _VEC_TABLES:
                    conn.execute(text(f"DELETE FROM {table}"))
            logger.info("all_search_indices_cleared")
        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "clear_indices_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def close(self) -> None:
        """Close resources (no-op for app.db-backed repository).

        The engine is shared with the rest of the application and
        should not be disposed here.

        """
        logger.info("Search repository closed")

    # ========================================================================
    # Internal Helpers
    # ========================================================================

    @contextmanager
    def _acquire_conn(self, session: TransactionalSession | None) -> Iterator[Connection]:
        """Yield a connection for a write, routing through ``session`` when given.

        Without ``session``: opens a fresh pooled connection from the engine,
        commits on clean exit, closes on exit. This is the historical
        standalone path — used when no caller transaction is active.

        With ``session``: yields the connection already bound to the
        caller's session transaction. We do NOT commit or rollback —
        the caller owns transaction lifecycle. After the write block
        exits cleanly, we call ``session.flush()`` so any pending ORM
        state is synchronized; the raw SQL we issue through the
        connection is already sent by that point.

        Exceptions raised inside the ``with`` block escape normally so
        callers can decide how to handle them. The engine-path rolls
        back via SQLAlchemy's context manager on exit; the session-path
        leaves the session in the "needs rollback" state until the
        caller reacts.

        Note: if a future edit adds an ``await`` inside a write method
        whose body runs under ``session.connection()``, two asyncio
        tasks sharing the same session could interleave and corrupt
        session state. This is a general SQLAlchemy hazard, not
        specific to this helper — keep write-method bodies synchronous.
        """
        if session is not None:
            conn = session.connection()
            yield conn
            session.flush()
        else:
            # engine.begin() commits on clean exit, rolls back on exception —
            # replaces the explicit conn.commit() the standalone path used to
            # carry, keeping the file free of raw .commit() calls per the
            # Phase 3 commit-discipline rule.
            with self._engine.begin() as conn:
                yield conn

    def _upsert_vector(
        self,
        conn: Any,
        item_id: str,
        embedding: list[float],
        item_type: str,
    ) -> None:
        """Insert or replace a vector in the per-type vec0 table.

        Args:
            conn: Active SQLAlchemy connection
            item_id: ID of the item (may include prefix)
            embedding: Embedding vector
            item_type: Type of item ("node", "chunk", "template") — selects
                the destination vec0 table.

        """
        from sqlalchemy import text

        if len(embedding) != self.vector_dim:
            logger.warning(
                "embedding_dimension_mismatch",
                item_id=item_id,
                got_dimensions=len(embedding),
                expected_dimensions=self.vector_dim,
            )
            return

        table = _vec_table_for(item_type)
        embedding_bytes = _serialize_float32(embedding)

        # Delete existing entry if any (upsert semantics)
        conn.execute(
            text(f"DELETE FROM {table} WHERE item_id = :item_id"),
            {"item_id": item_id},
        )

        # Insert new entry
        conn.execute(
            text(f"INSERT INTO {table}(embedding, item_id) VALUES (:embedding, :item_id)"),
            {
                "embedding": embedding_bytes,
                "item_id": item_id,
            },
        )

    def _upsert_vectors_bulk(
        self,
        conn: Any,
        embeddings: list[tuple[str, list[float]]],
        item_type: str,
        *,
        chunk_size: int = 500,
    ) -> None:
        """Bulk-upsert a list of vectors in two SQL statements per chunk.

        Uses a single DELETE … WHERE item_id IN (…) followed by a single
        executemany INSERT against the per-type vec0 table, chunked at
        *chunk_size* to stay within SQLite's 999-parameter limit. For
        batches up to 500 items this is exactly 2 statements total
        regardless of N.

        Args:
            conn: Active SQLAlchemy connection.
            embeddings: List of (item_id, embedding) pairs. All embeddings
                must already match ``self.vector_dim`` (callers are
                responsible for filtering mismatches beforehand).
            item_type: Type label ("node", "chunk", "template") — selects
                the destination vec0 table.
            chunk_size: Maximum IDs per DELETE … IN (…) chunk.

        """
        if not embeddings:
            return

        table = _vec_table_for(item_type)
        for offset in range(0, len(embeddings), chunk_size):
            chunk = embeddings[offset : offset + chunk_size]
            ids = [item_id for item_id, _ in chunk]

            # One DELETE per chunk
            placeholders = ",".join(["?"] * len(ids))
            conn.exec_driver_sql(
                f"DELETE FROM {table} WHERE item_id IN ({placeholders})",
                tuple(ids),
            )

            # One executemany INSERT per chunk
            rows = [(item_id, _serialize_float32(emb)) for item_id, emb in chunk]
            conn.exec_driver_sql(
                f"INSERT INTO {table}(item_id, embedding) VALUES (?, ?)",
                rows,
            )

    # ========================================================================
    # Reindex Queue
    # ========================================================================

    @property
    def has_pending_reindex(self) -> bool:
        """Check if there are items queued for re-embedding.

        Returns:
            True if the reindex queue is non-empty.

        """
        return len(self._reindex_queue) > 0

    @property
    def needs_full_reindex(self) -> bool:
        """Check if a full reindex is needed (reads from database).

        This is a live check against the persisted flag in search_metadata,
        so it works correctly across processes (e.g., Cortex reading after
        Neuron clears the flag).

        Returns:
            True if the reindex flag is set in the database.

        """
        from sqlalchemy import text

        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT value FROM search_metadata WHERE key = 'needs_full_reindex'")
                ).fetchone()
                return row is not None and row[0] == "true"
        except Exception:
            return False

    def _set_reindex_flag(self, value: bool) -> None:
        """Persist the needs_full_reindex flag to the database.

        Args:
            value: True to set the flag, False to clear it.

        """
        from sqlalchemy import text

        try:
            with self._engine.begin() as conn:
                if value:
                    conn.execute(
                        text(
                            "INSERT OR REPLACE INTO search_metadata (key, value) "
                            "VALUES ('needs_full_reindex', 'true')"
                        )
                    )
                else:
                    conn.execute(
                        text("DELETE FROM search_metadata WHERE key = 'needs_full_reindex'")
                    )
        except Exception as e:
            logger.warning(
                "set_reindex_flag_failed",
                value=value,
                error=str(e),
            )

    def clear_reindex_flag(self) -> None:
        """Clear the needs_full_reindex flag.

        Called after a successful rebuild with regeneration.
        """
        self._set_reindex_flag(False)

    # ========================================================================
    # Rebuild-in-progress flag
    # ========================================================================

    @property
    def is_rebuilding(self) -> bool:
        """Check if any per-type vec0 table is currently being rebuilt.

        A live check against the persisted ``is_rebuilding`` flag in
        ``search_metadata``. Returns ``True`` between the DROP and the
        re-CREATE of the per-type vec0 tables during an embedding-dimension
        change. Semantic search uses this to fall back to FTS5-only for
        the duration of the rebuild window.

        Returns:
            True if the rebuild flag is set in the database.

        """
        from sqlalchemy import text

        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT value FROM search_metadata WHERE key = 'is_rebuilding'")
                ).fetchone()
                return row is not None and row[0] == "true"
        except Exception:
            return False

    def schedule_reindex(
        self,
        item_id: str,
        text: str,
        item_type: str,
    ) -> None:
        """Queue an item for async re-embedding.

        Called when an embedding has the wrong dimensions or was generated
        by a different model. The item will be re-embedded on the next
        ``flush_reindex()`` call.

        Args:
            item_id: ID of the item (may include prefix like "chunk:xxx")
            text: Source text to re-embed
            item_type: Type of item ("node", "chunk", "template")

        """
        if len(self._reindex_queue) >= self._reindex_queue_max_size:
            logger.warning(
                "reindex_queue_full",
                max_size=self._reindex_queue_max_size,
                dropped_item_id=item_id,
            )
            return
        self._reindex_queue.append(
            {
                "item_id": item_id,
                "text": text,
                "item_type": item_type,
            }
        )

    async def flush_reindex(
        self,
        batch_embed_fn: Callable[[list[str]], Any],
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Re-embed and index all queued items, then clear the queue.

        Items without text are discarded (cannot be re-embedded).

        See :meth:`index_node` for the ``session`` contract. NOTE: the
        ``await batch_embed_fn(...)`` call happens BEFORE we acquire the
        connection — embedding generation must not run under the
        caller's session (see the hazard note in ``_acquire_conn``).

        Args:
            batch_embed_fn: Async callable that takes a list of texts
                and returns a list of embedding vectors (list[list[float]]).
            session: Optional caller session to share a transaction with.

        Returns:
            Number of items successfully re-indexed.

        """
        items_with_text = [item for item in self._reindex_queue if item.get("text")]
        self._reindex_queue.clear()

        if not items_with_text:
            return 0

        try:
            texts = [item["text"] for item in items_with_text]
            embeddings = await batch_embed_fn(texts)

            # Group valid embeddings by item_type for bulk upsert
            from collections import defaultdict

            by_type: dict[str, list[tuple[str, list[float]]]] = defaultdict(list)
            for item, embedding in zip(items_with_text, embeddings, strict=False):
                if len(embedding) != self.vector_dim:
                    logger.warning(
                        "reindex_dimension_still_wrong",
                        item_id=item["item_id"],
                        got=len(embedding),
                        expected=self.vector_dim,
                    )
                    continue
                by_type[item["item_type"]].append((item["item_id"], embedding))

            indexed = 0
            with self._acquire_conn(session) as conn:
                for itype, pairs in by_type.items():
                    self._upsert_vectors_bulk(conn, pairs, itype)
                    indexed += len(pairs)

            if indexed:
                logger.info(
                    "reindex_queue_flushed",
                    indexed=indexed,
                    total=len(items_with_text),
                )
            return indexed

        except Exception as e:
            if session is not None:
                raise
            logger.warning(
                "reindex_flush_failed",
                error_type=type(e).__name__,
                error_message=str(e),
                queued_count=len(items_with_text),
            )
            return 0

    async def flush_reindex_with_service(
        self,
        embedding_service: Any,
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Convenience wrapper that flushes using an embedding provider.

        Delegates to :meth:`flush_reindex`; the ``session`` kwarg flows
        through unchanged.

        Args:
            embedding_service: Embedding provider implementing
                ``EmbeddingProviderProtocol`` with ``batch_embed(texts)``
                method returning an object with ``.embeddings`` attribute.
            session: Optional caller session to share a transaction with.

        Returns:
            Number of items re-indexed.

        """

        async def _batch_embed(texts: list[str]) -> list[list[float]]:
            """Adapt the embedding service to the bare list-of-vectors callback shape."""
            result = await embedding_service.batch_embed(texts)
            return result.embeddings  # type: ignore[no-any-return]

        return await self.flush_reindex(_batch_embed, session=session)
