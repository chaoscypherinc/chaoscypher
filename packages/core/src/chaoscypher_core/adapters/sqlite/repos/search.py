# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Search Repository - Unified Full-Text and Vector Search (SQLite adapter).

Concrete implementation of :class:`chaoscypher_core.ports.search.SearchRepositoryProtocol`
backed by the SQLite adapter. Lives under ``adapters/sqlite/repos/`` because
it issues raw SQL against the SQLite engine and depends on adapter-specific
features (FTS5 virtual tables, sqlite-vec ``vec0`` virtual table).

Provides keyword search (SQLite FTS5) and vector similarity search (sqlite-vec),
both stored in the main app.db database for WAL-mode concurrency safety.

Keyword search covers nodes (``fulltext_index``, written by ``index_node``) and
document chunks (``fulltext_index_chunks``, kept in step with
``document_chunks`` by triggers, so every chunk writer feeds it without
knowing). A query matches any of its significant words, or a quoted phrase
(:func:`_fts5_query`). Vector search stores and queries unit-length vectors in
cosine-metric ``vec0`` tables. Hybrid search fuses the keyword and vector
rankings with reciprocal-rank fusion (:func:`_fuse_rankings`).

Tracks the active embedding model name and vector dimensions in a
``search_metadata`` table.  When the configured model or dimensions
change, sets ``needs_full_reindex`` so callers can trigger background
re-embedding.  Per-item dimension mismatches during indexing are queued
via ``schedule_reindex()`` and flushed asynchronously by the caller.
"""

from __future__ import annotations

import json
import math
import re
import struct
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NamedTuple

import structlog

from chaoscypher_core.adapters.embedding.registry import canonical_embedding_model
from chaoscypher_core.adapters.embedding.vectors import normalize_vector
from chaoscypher_core.adapters.sqlite.repos.source_visibility import (
    ANY_SOURCE_HIDDEN_SQL,
    MEMBERSHIP_TABLES,
    SOURCES_TABLE,
    attested_visible_sql,
    in_source_scope_sql,
    source_visible_sql,
)
from chaoscypher_core.adapters.sqlite.repos.text_indexer import extract_searchable_text
from chaoscypher_core.exceptions import SchemaIntegrityError
from chaoscypher_core.ports.search import SearchHits
from chaoscypher_core.utils.query_text import normalize_query
from chaoscypher_core.utils.retrieval_faults import (
    NEEDS_FULL_REINDEX_UNREADABLE,
    record_retrieval_fault,
)
from chaoscypher_core.utils.rrf import reciprocal_rank_fusion


if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.engine import Connection, Engine

    from chaoscypher_core.models import Node
    from chaoscypher_core.ports.transactional import TransactionalSession

logger = structlog.get_logger(__name__)

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
_CONTENT_ITEM_TYPES: tuple[str, ...] = ("node", "chunk")
"""What a search without an ``item_type`` covers: what a query retrieves (templates only by name)."""

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


# Function words a question carries that say nothing about what it is about.
# Dropped from a keyword query, so "Who is the father of Pierre?" matches on
# ``father`` and ``Pierre`` rather than on every chunk holding "the".
_FTS5_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "about", "after", "again", "all", "also", "am", "an", "and", "any", "are",
        "as", "at", "be", "because", "been", "before", "being", "between", "both", "but",
        "by", "can", "could", "did", "do", "does", "doing", "during", "each", "for",
        "from", "had", "has", "have", "having", "he", "her", "here", "hers", "him",
        "his", "how", "i", "if", "in", "into", "is", "it", "its", "me", "more", "most",
        "my", "no", "nor", "not", "of", "off", "on", "once", "only", "or", "other",
        "our", "out", "over", "own", "same", "she", "should", "so", "some", "such",
        "than", "that", "the", "their", "them", "then", "there", "these", "they",
        "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
        "we", "were", "what", "when", "where", "which", "while", "who", "whom", "whose",
        "why", "will", "with", "would", "you", "your",
    }
)  # fmt: skip

_FTS5_PHRASE = re.compile(r'"([^"]*)"')
_FTS5_WORD = re.compile(r"\w+")


def _tokenizer_form(word: str) -> str:
    """``word`` as the ``unicode61`` tokenizer indexes it: casefolded, accents of Latin letters dropped.

    ``remove_diacritics`` folds a Latin letter's accents (``Bezúkhov`` is
    ``bezukhov``) but not a Cyrillic letter's (``ё`` stays), nor letters
    with no decomposition (``ø``, ``ł``).
    """
    out: list[str] = []
    for char in unicodedata.normalize("NFKD", word.casefold()):
        if unicodedata.combining(char) and out and out[-1].isascii():
            continue
        out.append(char)
    return unicodedata.normalize("NFC", "".join(out))


def _name_rule_form(word: str) -> str | None:
    """``word`` through the name rule's fold, when that is one word the tokenizer would not make.

    The name matcher compares names through
    :func:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.fold`
    (``Фёдор`` -> ``федор``); the keyword arm adds that spelling as one more
    term, so a question spelling a name with ``ё`` finds a text spelling it
    with the plain vowel, as the matcher does (audit wave 4). None when the fold is
    empty, several words, or what the tokenizer makes anyway.
    """
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold

    word = normalize_query(word)
    folded = fold(word)
    if not folded or " " in folded or folded == _tokenizer_form(word):
        return None
    return folded


_VEC_COSINE_PATTERN = re.compile(r"distance_metric\s*=\s*cosine", re.IGNORECASE)


def _vec_table_ddl(table: str, dim: int, *, if_not_exists: bool = False) -> str:
    """The one ``CREATE VIRTUAL TABLE`` of a per-type vec0 table: cosine metric, ``item_id`` aux column.

    Every place that creates a vector table (schema init, a dimension change,
    the L2-to-cosine upgrade) builds it here, so they cannot disagree.
    """
    guard = "IF NOT EXISTS " if if_not_exists else ""
    return (
        f"CREATE VIRTUAL TABLE {guard}{table} USING vec0("
        f"embedding float[{dim}] distance_metric=cosine,"
        f"+item_id TEXT"
        f")"
    )


_NODE_FTS_COLUMNS = ("label", "properties", "searchable_text")
"""The node index's columns (:data:`NODE_FULLTEXT` reads them)."""
_NODE_FTS_UPSERT = (
    "INSERT INTO fulltext_content (node_id, label, properties, searchable_text) "
    "VALUES (:node_id, :label, :properties, :searchable_text) "
    "ON CONFLICT(node_id) DO UPDATE SET label = excluded.label, "
    "properties = excluded.properties, searchable_text = excluded.searchable_text"
)
"""Write one node's full-text row: an upsert, so the update trigger keeps the index in step.

``INSERT OR REPLACE`` deletes the old row without firing the delete trigger
(SQLite fires it only with ``recursive_triggers`` on), so until 2026-10-06
every re-indexed node left its old words in ``fulltext_index`` as an orphan
the plain integrity check does not see (audit wave 2).
"""


def node_fulltext_row(node: Any) -> dict[str, str]:
    """The ``fulltext_content`` row of a node: its id, label, properties and searchable text.

    ``node`` is a graph node, or a mapping with ``id``, ``label`` and
    ``properties`` (the identity resolver's node records).
    """
    if isinstance(node, Mapping):
        node = SimpleNamespace(
            id=node["id"], label=node["label"], properties=dict(node.get("properties") or {})
        )
    return {
        "node_id": node.id,
        "label": node.label,
        "properties": json.dumps(node.properties),
        "searchable_text": extract_searchable_text(node),
    }


def _json_object(value: Any) -> dict[str, Any]:
    """A stored JSON column as a dict: parsed when it is text, ``{}`` when empty or not an object."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _graph_node_records(conn: Any) -> list[dict[str, Any]] | None:
    """Every graph node as stored (``id``, ``label``, ``properties``), by id; None without the table."""
    from sqlalchemy import text

    if not _table_exists(conn, "graph_nodes"):
        return None
    return [
        {"id": nid, "label": label or "", "properties": _json_object(props)}
        for nid, label, props in conn.execute(
            text("SELECT id, label, properties FROM graph_nodes ORDER BY id")
        )
    ]


def _log_node_list_drift(
    given: list[Node | Mapping[str, Any]], graph: list[dict[str, Any]]
) -> None:
    """Log when a caller's node list is not the graph's (what :meth:`SearchRepository.rebuild_node_fulltext` indexes)."""
    listed = {str(n["id"] if isinstance(n, Mapping) else n.id) for n in given}
    stored = {str(n["id"]) for n in graph}
    if listed != stored:
        logger.warning(
            "node_fulltext_caller_list_differs_from_graph",
            missing_from_list=len(stored - listed),
            not_in_graph=len(listed - stored),
            action="indexed the graph's nodes as stored",
        )


_CHUNK_FTS = "fulltext_index_chunks"
"""The chunk full-text index (FTS5, external content :data:`_CHUNK_FTS_ROWS`)."""
_CHUNK_FTS_ROWS = "fulltext_index_chunk_rows"
"""The chunk index's content table: ``item_id`` (``chunk:<id>``) and ``content``.

Both names start with ``fulltext_index`` so the schema-drift detector skips
them like the node index's tables (runtime tables, not SQLModel's).
"""
_CHUNK_FTS_COLUMNS = ("content",)


class FulltextIndex(NamedTuple):
    """One FTS5 index over its external content table.

    What the open-time repair (:meth:`SearchRepository._repair_fulltext_indexes`)
    and the benchmark's index digest
    (:func:`~chaoscypher_core.benchmark.pins.index_digest`) both read, so the
    two never name different tables.
    """

    fts: str
    """The FTS5 virtual table (its token counts live in ``<fts>_docsize``)."""
    content: str
    """The content table it indexes."""
    key: str
    """The content table's item id column (``node_id``, ``item_id``)."""
    rowid: str
    """The content table's column the index's rowid is (``content_rowid``)."""
    columns: tuple[str, ...]
    """The indexed columns."""


NODE_FULLTEXT = FulltextIndex(
    "fulltext_index", "fulltext_content", "node_id", "rowid", _NODE_FTS_COLUMNS
)
CHUNK_FULLTEXT = FulltextIndex(_CHUNK_FTS, _CHUNK_FTS_ROWS, "item_id", "id", _CHUNK_FTS_COLUMNS)
FULLTEXT_INDEXES: tuple[FulltextIndex, ...] = (NODE_FULLTEXT, CHUNK_FULLTEXT)
"""The keyword arm's two indexes: nodes, then chunks."""

_CHUNK_SOURCE_TRIGGERS: tuple[str, ...] = (
    "CREATE TRIGGER IF NOT EXISTS fulltext_chunks_from_document_ai "
    "AFTER INSERT ON document_chunks BEGIN "
    f"INSERT INTO {_CHUNK_FTS_ROWS}(item_id, content) "
    "VALUES ('chunk:' || new.id, coalesce(new.content, '')) "
    "ON CONFLICT(item_id) DO UPDATE SET content = excluded.content; "
    "END",
    "CREATE TRIGGER IF NOT EXISTS fulltext_chunks_from_document_au "
    "AFTER UPDATE OF content ON document_chunks BEGIN "
    f"UPDATE {_CHUNK_FTS_ROWS} SET content = coalesce(new.content, '') "
    "WHERE item_id = 'chunk:' || new.id; "
    "END",
    "CREATE TRIGGER IF NOT EXISTS fulltext_chunks_from_document_ad "
    "AFTER DELETE ON document_chunks BEGIN "
    f"DELETE FROM {_CHUNK_FTS_ROWS} WHERE item_id = 'chunk:' || old.id; "
    "END",
)
"""Keep the chunk index's content table in step with ``document_chunks``."""

_TRIGGER_NAME = re.compile(r"CREATE TRIGGER IF NOT EXISTS (\w+)")
"""The name a ``CREATE TRIGGER IF NOT EXISTS`` statement gives its trigger."""


def _fts_sync_triggers(
    content: str, index: str, columns: tuple[str, ...], *, rowid: str
) -> list[str]:
    """The insert, delete and update triggers keeping an external-content FTS5 index in step.

    Args:
        content: The content table.
        index: The FTS5 table over it.
        columns: The indexed columns.
        rowid: The content table's rowid column.

    Returns:
        Three ``CREATE TRIGGER IF NOT EXISTS`` statements named ``<content>_ai/_ad/_au``.
    """
    cols = ", ".join(columns)
    new_vals = ", ".join(f"new.{c}" for c in columns)
    old_vals = ", ".join(f"old.{c}" for c in columns)
    insert = f"INSERT INTO {index}(rowid, {cols}) VALUES (new.{rowid}, {new_vals}); "
    delete = (
        f"INSERT INTO {index}({index}, rowid, {cols}) VALUES ('delete', old.{rowid}, {old_vals}); "
    )
    return [
        f"CREATE TRIGGER IF NOT EXISTS {content}_ai AFTER INSERT ON {content} BEGIN {insert}END",
        f"CREATE TRIGGER IF NOT EXISTS {content}_ad AFTER DELETE ON {content} BEGIN {delete}END",
        f"CREATE TRIGGER IF NOT EXISTS {content}_au AFTER UPDATE ON {content} BEGIN "
        f"{delete}{insert}END",
    ]


_KEYWORD_SQL: dict[str, str] = {
    "node": (
        "SELECT c.node_id, -bm25(fulltext_index, 3.0, 1.0, 0.5) AS score "
        "FROM fulltext_index fi "
        "JOIN fulltext_content c ON c.rowid = fi.rowid {scope_join}"
        "WHERE fulltext_index MATCH :query {scope_where}"
        "ORDER BY score DESC, c.node_id "
        "LIMIT :limit"
    ),
    "chunk": (
        f"SELECT r.item_id, -bm25({_CHUNK_FTS}) AS score "
        f"FROM {_CHUNK_FTS} fi "
        f"JOIN {_CHUNK_FTS_ROWS} r ON r.id = fi.rowid {{scope_join}}"
        f"WHERE {_CHUNK_FTS} MATCH :query {{scope_where}}"
        "ORDER BY score DESC, r.item_id "
        "LIMIT :limit"
    ),
}
"""The BM25 query of each full-text index, by item type.

Equal scores are ordered by item id: until 2026-10-06 they came in the
index's row order, which another build of the same content need not share
(audit wave 5). ``{scope_join}`` and ``{scope_where}`` take a source scope
(:func:`_scope_clause`), empty for an unscoped search.
"""

_KEYWORD_ID: dict[str, str] = {"node": "c.node_id", "chunk": "r.item_id"}
"""The item id column of each :data:`_KEYWORD_SQL` query (what a source scope joins on)."""

_ITEM_ROWS: dict[str, tuple[str, str]] = {
    "chunk": ("document_chunks", "substr({id}, 7)"),
    "node": ("graph_nodes", "{id}"),
}
"""The table holding each item type's rows, and the row id an index's item id names.

A chunk's index id is ``chunk:<id>``; a node's is its id. Templates come
from no source: they are never scoped, and no switched-off source hides them.
"""

_SCOPE_CONDITION: dict[str, str] = {
    "chunk": "scope_item.source_id IN ({ids})",
    "node": in_source_scope_sql("graph_nodes", "({ids})", "scope_item"),
}

_SCOPE_SQL: dict[str, tuple[str, str]] = {
    item_type: (
        f"JOIN {table} scope_item ON scope_item.id = {key} ",
        _SCOPE_CONDITION[item_type],
    )
    for item_type, (table, key) in _ITEM_ROWS.items()
}
"""How a search of each item type is limited to a source scope: a join and a condition.

A chunk (``chunk:<id>``) is in scope when its ``document_chunks`` row's source
is; a node when a source in scope attests it (its membership, #795), or it
names none, NULL or ``''`` (:func:`~chaoscypher_core.ports.search.item_in_source_scope`,
the GraphRAG handler's own reading of a node's scope).
"""

_SCOPE_KNN_MARGIN = 2.0
"""How many times the expected number of KNN hits a scoped vector search asks for (:meth:`SearchRepository._filtered_knn`)."""

VEC_KNN_MAX = 4096
"""The largest ``k`` sqlite-vec's KNN query accepts (a larger one raises)."""

_VISIBLE_SQL: dict[str, Callable[[str], str]] = {
    "chunk": lambda row: source_visible_sql(f"{row}.source_id"),
    "node": lambda row: attested_visible_sql("graph_nodes", row),
}
"""Whether the row named ``row`` of each item type may answer: a chunk reads its source, a node its members."""

_VISIBILITY_TABLES = (
    SOURCES_TABLE,
    *(table for table, _ in _ITEM_ROWS.values()),
    MEMBERSHIP_TABLES["graph_nodes"][0],
)
"""The tables the source-visibility predicate reads; an index without them hides nothing."""


def _scope_clause(
    item_type: str,
    id_column: str,
    source_ids: Iterable[str] | None,
    *,
    hide_disabled: bool = False,
) -> tuple[str, str, dict[str, str]]:
    """``(join, condition, parameters)`` limiting a search of ``item_type`` to what may answer.

    With a scope, the scope's join and condition (:data:`_SCOPE_SQL`); with
    ``hide_disabled``, a row of a switched-off source is left out too
    (:data:`_VISIBLE_SQL`: a chunk's source, a node's member sources),
    scoped or not. Empty strings and no parameters when neither applies, or
    for an item type no source holds (templates). The one place a search
    reads the scope and the visibility, so the keyword and the vector arm
    cannot disagree on what may answer.
    """
    if item_type not in _SCOPE_SQL:
        return "", "", {}
    if not source_ids:
        if not hide_disabled:
            return "", "", {}
        table, key = _ITEM_ROWS[item_type]
        hidden = (
            f"NOT EXISTS (SELECT 1 FROM {table} vis_item "
            f"WHERE vis_item.id = {key.format(id=id_column)} "
            f"AND NOT {_VISIBLE_SQL[item_type]('vis_item')})"
        )
        return "", hidden, {}
    join, condition = _SCOPE_SQL[item_type]
    params = {f"scope_{i}": sid for i, sid in enumerate(sorted(set(source_ids)))}
    ids = ", ".join(f":{name}" for name in params)
    condition = condition.format(ids=ids)
    if hide_disabled:
        condition = f"{condition} AND {_VISIBLE_SQL[item_type]('scope_item')}"
    return join.format(id=id_column), condition, params


def _scope_count_sql(
    item_type: str, source_ids: Iterable[str] | None, *, hide_disabled: bool
) -> tuple[str, dict[str, str]]:
    """``(sql, parameters)`` counting the rows of ``item_type`` a search in that scope may return.

    Raises:
        ValueError: For an item type no source holds.
    """
    if item_type not in _ITEM_ROWS:
        msg = f"no source scope for item_type {item_type!r}; expected chunk or node"
        raise ValueError(msg)
    table, _ = _ITEM_ROWS[item_type]
    conditions: list[str] = []
    params: dict[str, str] = {}
    if source_ids:
        _, scoped, params = _scope_clause(item_type, "", source_ids)
        conditions.append(scoped)
    if hide_disabled:
        conditions.append(_VISIBLE_SQL[item_type]("scope_item"))
    where = " AND ".join(conditions) or "1"
    return f"SELECT count(*) FROM {table} scope_item WHERE {where}", params


def _table_exists(conn: Any, name: str) -> bool:
    """Whether ``name`` is a table of the connected database."""
    from sqlalchemy import text

    row = conn.execute(
        text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"), {"name": name}
    ).fetchone()
    return row is not None


def install_chunk_source_triggers(conn: Any) -> bool:
    """Install the triggers feeding the chunk index from ``document_chunks``; idempotent.

    Only when both tables exist: the index is the search repository's, and
    ``document_chunks`` is the migrations'. A rebuild that drops
    ``document_chunks`` (an unfinished first build, built again) drops them with it,
    so the rebuild puts them back through here.

    Returns:
        Whether the triggers are in place.
    """
    from sqlalchemy import text

    if not (_table_exists(conn, _CHUNK_FTS_ROWS) and _table_exists(conn, "document_chunks")):
        return False
    for trigger in _CHUNK_SOURCE_TRIGGERS:
        conn.execute(text(trigger))
    return True


def _existing_vec_dim(conn: Any) -> int | None:
    """The dimension the first existing vec0 table declares, or None when there is none."""
    from sqlalchemy import text

    for table in _VEC_TABLES:
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"), {"name": table}
        ).fetchone()
        match = _VEC_DIM_PATTERN.search(row[0]) if row is not None and row[0] else None
        if match:
            return int(match.group(1))
    return None


def _fts_rebuild(index: str) -> str:
    """The statement that rebuilds the FTS5 ``index`` from its content table, whatever state it is in.

    A rewrite of a content table runs it first too: the delete and update
    triggers tell FTS5 to remove each old row's tokens, which an index that
    does not hold them (dropped and recreated empty, emptied, stale) refuses
    as a malformed database, so until 2026-10-06 a rebuild over a broken
    index failed where it was needed most (audit wave 4).
    """
    return f"INSERT INTO {index}({index}) VALUES ('rebuild')"


def _rows_digest(rows: Iterable[Any]) -> str:
    """A SHA-256 of ``(id, text)`` rows in the order given (the caller orders them)."""
    import hashlib

    digest = hashlib.sha256()
    for item_id, content in rows:
        digest.update(f"{item_id}\x1f{content}\x1e".encode())
    return digest.hexdigest()


def _metadata_get(conn: Any, key: str) -> str | None:
    """The ``search_metadata`` value of ``key``, or None."""
    from sqlalchemy import text

    row = conn.execute(
        text("SELECT value FROM search_metadata WHERE key = :key"), {"key": key}
    ).fetchone()
    return None if row is None else str(row[0])


def _metadata_set(conn: Any, key: str, value: str) -> None:
    """Write ``key`` = ``value`` into ``search_metadata``."""
    from sqlalchemy import text

    conn.execute(
        text("INSERT OR REPLACE INTO search_metadata (key, value) VALUES (:key, :value)"),
        {"key": key, "value": value},
    )


def _table_stamp(conn: Any, table: str, length_columns: tuple[str, ...]) -> list[Any]:
    """A cheap fingerprint of a table: its row count, largest rowid and total text length.

    One aggregate query, no rows leave SQLite: what the open-time index
    checks compare against their last full check before hashing every row.
    """
    from sqlalchemy import text

    lengths = " + ".join(f"coalesce(length({c}), 0)" for c in length_columns)
    row = conn.execute(
        text(f"SELECT count(*), coalesce(max(rowid), 0), coalesce(sum({lengths}), 0) FROM {table}")
    ).fetchone()
    return [int(v) for v in row] if row is not None else []


_CHUNK_FTS_CHECKED = "chunk_fulltext_checked"
"""``search_metadata`` key: the table stamps of the last full chunk-index check that found it in step."""
_NODE_FTS_CHECKED = "node_fulltext_checked"
"""``search_metadata`` key: the table stamps of the last full node-index check that found it in step."""
_FTS_VERIFIED = "{fts}_verified"
"""``search_metadata`` key of an FTS5 index: its stamp when a full (rank 1) integrity check last passed."""


def _fts5_query(query: str) -> str:
    """An FTS5 MATCH expression that matches any significant word or quoted phrase of ``query``.

    Every term is double-quoted, so no FTS5 operator or special character in
    the query can break the syntax. Words are OR-ed (BM25 ranks documents
    matching more and rarer words first); a ``"quoted phrase"`` in the query
    stays a phrase; stop words and single characters are dropped; a word
    the name rule folds to another single word than the tokenizer makes
    (``Фёдор``) adds that spelling too (:func:`_name_rule_form`). Until
    2026-10-06 every word was quoted and AND-ed, so a question matched no
    chunk and almost no node (audit R2). The query is composed first
    (:func:`~chaoscypher_core.utils.query_text.normalize_query`): a decomposed
    accent split a word in two (audit wave 5).

    Args:
        query: Raw user query string.

    Returns:
        The MATCH expression, or ``""`` when the query has no significant term.

    """
    query = normalize_query(query)
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        """Quote ``term`` once (case-insensitively) into the expression."""
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            terms.append(f'"{term}"')

    for phrase in _FTS5_PHRASE.findall(query):
        words = _FTS5_WORD.findall(phrase)
        if words:
            add(" ".join(words))
    for word in _FTS5_WORD.findall(_FTS5_PHRASE.sub(" ", query)):
        if len(word) > 1 and word.casefold() not in _FTS5_STOP_WORDS:
            add(word)
            variant = _name_rule_form(word)
            if variant is not None:
                add(variant)
    return " OR ".join(terms)


RRF_K = 60
"""Reciprocal-rank-fusion smoothing constant (Cormack, Clarke & Büttcher 2009)."""


def _fuse_rankings(*rankings: list[tuple[str, float]], limit: int) -> list[tuple[str, float]]:
    """Fuse ranked lists by reciprocal rank, scores scaled to ``[0, 1]``.

    Each item scores ``sum(1 / (RRF_K + rank))`` over the lists it appears in
    (rank from 1), divided by the best score possible over the non-empty
    lists, so the top item of every list scores 1.0 and the top of one list
    of two scores 0.5. Rank, not score, is fused: BM25 and cosine are on
    different scales, and the ``max()`` of the two that hybrid search used
    until 2026-10-06 let the larger scale win outright (audit R2). Ties keep
    first-seen order.

    Args:
        *rankings: Lists of ``(id, score)``, best first.
        limit: Most items to return.

    Returns:
        ``(id, fused score)`` pairs, best first.

    """
    present = [r for r in rankings if r]
    if not present:
        return []
    best = len(present) / (RRF_K + 1)
    fused = reciprocal_rank_fusion(*present, k=RRF_K)[:limit]
    return [(item_id, score / best) for item_id, score in fused]


def _serialize_float32(vector: list[float]) -> bytes:
    """Serialize a vector, scaled to unit length, to float32 bytes for sqlite-vec.

    Every vector the index stores or is queried with passes through here, so
    stored vectors are unit length whatever the provider returned (an MRL cut
    is not) and the index's cosine ranking equals an L2 ranking (audit R7).

    Args:
        vector: List of float values

    Returns:
        Packed bytes in little-endian float32 format

    """
    unit = normalize_vector(vector)
    return struct.pack(f"<{len(unit)}f", *unit)


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
        *,
        vectors: bool = True,
    ) -> None:
        """Initialize search repository with app.db engine.

        Args:
            engine: SQLAlchemy engine connected to app.db
            vector_dim: Dimensionality of embedding vectors (default: 1024)
            embedding_model: Name of the embedding model (e.g. HuggingFace model ID).
                Used to detect model changes and trigger reindexing.
            vectors: False for a keyword-only process (vector search off).
                Such a process never drops, recreates, upgrades or flags
                another process's vectors: the open-time vector checks
                (cosine upgrade, model change, dimension consistency) are
                skipped, and the configured model and dimension are recorded
                only when this open created the vector tables.

        """
        self._engine = engine
        if not isinstance(vector_dim, int) or not (1 <= vector_dim <= 65536):
            msg = f"vector_dim must be an integer 1-65536, got {vector_dim!r}"
            raise ValueError(msg)
        self.vector_dim = vector_dim
        self.embedding_model = embedding_model
        self.vectors = vectors
        self._reindex_queue: list[dict[str, Any]] = []
        self._reindex_queue_max_size = 10_000
        self._visibility_tables_seen = False
        self._init_schema()
        if self.vectors:
            self._check_model_change()
            self._assert_vec_table_dim_consistency()
        self._repair_fulltext_indexes()
        with self._engine.connect() as conn:
            self._hides_sources(conn)  # looks for the visibility tables once, up front

    # ========================================================================
    # Schema Initialization
    # ========================================================================

    def _init_schema(self) -> None:
        """Create per-type vec0 tables, the node and chunk FTS5 tables and their triggers.

        Then checks both full-text indexes against what they index
        (:meth:`_sync_chunk_fulltext`, :meth:`_sync_node_fulltext`). A
        keyword-only repository (``self.vectors`` false) takes the dimension
        the existing vec0 tables have as its ``vector_dim`` (so the vectors it
        reads back from nodes and chunks match, and nothing is queued for a
        re-embed), creates a missing table at that dimension so every node and
        chunk delete keeps working, and never upgrades existing ones; it
        records its model and dimension (:meth:`_store_vector_config`) only
        when it created all of them, so a later vector-on open sees the
        dimension the tables really have.
        """
        from sqlalchemy import text

        chunk_triggers_were_present = self._chunk_source_triggers_present()
        with self._engine.begin() as conn:
            created_vec_tables = not any(_table_exists(conn, t) for t in _VEC_TABLES)
            if not self.vectors:
                existing_dim = _existing_vec_dim(conn)
                if existing_dim is not None:
                    self.vector_dim = existing_dim
            # Per-type sqlite-vec virtual tables — one each for chunks,
            # nodes, and templates. Splitting by type lets every search
            # take the native KNN ``embedding MATCH … AND k = ?`` path
            # instead of the cosine sequential scan that fired when the
            # legacy ``vec_search`` table filtered on ``item_type``.
            for type_table in _VEC_TABLES:
                conn.execute(text(_vec_table_ddl(type_table, self.vector_dim, if_not_exists=True)))

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
            for trigger in _fts_sync_triggers(
                "fulltext_content", "fulltext_index", _NODE_FTS_COLUMNS, rowid="rowid"
            ):
                conn.execute(text(trigger))

            # Chunk full-text index: its own content table (an INTEGER key, so
            # the FTS rowid never moves under VACUUM) fed from document_chunks
            # by triggers, so every chunk writer keeps it in step.
            conn.execute(
                text(
                    f"CREATE TABLE IF NOT EXISTS {_CHUNK_FTS_ROWS} ("
                    "id INTEGER PRIMARY KEY,"
                    "item_id TEXT NOT NULL UNIQUE,"
                    "content TEXT NOT NULL DEFAULT ''"
                    ")"
                )
            )
            conn.execute(
                text(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_CHUNK_FTS} USING fts5("
                    "content,"
                    f"content='{_CHUNK_FTS_ROWS}',"
                    "content_rowid='id',"
                    "tokenize='porter unicode61'"
                    ")"
                )
            )
            for trigger in _fts_sync_triggers(
                _CHUNK_FTS_ROWS, _CHUNK_FTS, _CHUNK_FTS_COLUMNS, rowid="id"
            ):
                conn.execute(text(trigger))
            install_chunk_source_triggers(conn)

            # Search metadata (tracks active model config)
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS search_metadata ("
                    "key TEXT PRIMARY KEY,"
                    "value TEXT NOT NULL"
                    ")"
                )
            )
            if not self.vectors and created_vec_tables:
                self._store_vector_config(conn)

        if self.vectors:
            self._upgrade_vec_tables_to_cosine()
        self._sync_chunk_fulltext(triggers_were_present=chunk_triggers_were_present)
        self._sync_node_fulltext()

        logger.info(
            "search_schema_initialized",
            vector_dim=self.vector_dim,
            backend="sqlite-vec + FTS5 (app.db)",
        )

    def _upgrade_vec_tables_to_cosine(self) -> None:
        """Rebuild any vec0 table created before the cosine metric, its vectors made unit length.

        Tables created before 2026-10-06 have sqlite-vec's default L2 metric
        and hold the providers' raw (for an MRL cut, not unit-length) vectors,
        so their KNN ranking was L2 on non-unit vectors (audit R7). A vec0
        table cannot be altered or renamed with its shadow tables, so each is
        copied out, recreated with ``distance_metric=cosine`` at its own
        dimension, and refilled with the normalised vectors, in one
        transaction.
        """
        import numpy as np
        from sqlalchemy import text

        with self._engine.begin() as conn:
            for table in _VEC_TABLES:
                row = conn.execute(
                    text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
                    {"name": table},
                ).fetchone()
                if row is None or not row[0] or _VEC_COSINE_PATTERN.search(row[0]):
                    continue
                match = _VEC_DIM_PATTERN.search(row[0])
                if not match:
                    continue
                dim = int(match.group(1))
                rows = conn.exec_driver_sql(f"SELECT item_id, embedding FROM {table}").fetchall()
                conn.exec_driver_sql(f"DROP TABLE {table}")
                conn.exec_driver_sql(_vec_table_ddl(table, dim))
                for offset in range(0, len(rows), 500):
                    batch = []
                    for item_id, blob in rows[offset : offset + 500]:
                        vector = np.frombuffer(blob, dtype=np.float32)
                        norm = float(np.linalg.norm(vector))
                        unit = vector / norm if norm else vector
                        batch.append((item_id, unit.astype(np.float32).tobytes()))
                    conn.exec_driver_sql(
                        f"INSERT INTO {table}(item_id, embedding) VALUES (?, ?)", batch
                    )
                logger.info("vec_table_upgraded_to_cosine", table=table, vectors=len(rows))

    def _chunk_source_triggers_present(self) -> bool:
        """Whether every trigger feeding the chunk index from ``document_chunks`` exists now."""
        from sqlalchemy import text

        names = [_TRIGGER_NAME.search(t).group(1) for t in _CHUNK_SOURCE_TRIGGERS]  # type: ignore[union-attr]  # every statement names its trigger
        with self._engine.connect() as conn:
            found = {
                name
                for (name,) in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            }
        return all(n in found for n in names)

    def _chunk_fulltext_stamp(self, conn: Any) -> str:
        """The chunk tables' stamps (:func:`_table_stamp`), as the text the metadata row holds."""
        return json.dumps(
            [
                _table_stamp(conn, "document_chunks", ("id", "content")),
                _table_stamp(conn, _CHUNK_FTS_ROWS, ("item_id", "content")),
            ]
        )

    @contextmanager
    def _write_transaction(self) -> Iterator[Connection]:
        """A connection inside ``BEGIN IMMEDIATE``: SQLite's write lock is held from the first read.

        What a check that reads a table, rewrites an index from what it read
        and stamps the result runs in, so no writer can commit between the
        read and the stamp. Until 2026-10-06 the open-time node check read
        ``graph_nodes`` on a read connection, rebuilt in a second transaction
        and stamped in a third: a node committed in between was deleted from
        the index and the broken state was stamped as checked, so every later
        open skipped it (audit wave 5). Commits on a clean exit, rolls back
        on an exception.
        """
        with self._engine.connect() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.exec_driver_sql("ROLLBACK")
                raise
            conn.exec_driver_sql("COMMIT")

    def _sync_chunk_fulltext(self, *, triggers_were_present: bool = False) -> None:
        """Refill the chunk full-text index when its content differs from ``document_chunks``.

        The triggers keep it in step from the moment they exist; this catches
        the chunks written before (every database made before 2026-10-06, a
        snapshot copied in) and a ``document_chunks`` table rebuilt by a
        migration, which drops its triggers until this repository recreates
        them. The two sides are compared by a digest of every ``(id,
        content)`` pair, not by their counts: until 2026-10-06 a chunk whose
        text changed while the triggers were missing, or one chunk swapped for
        another, kept its stale row (audit wave 2).

        The full comparison hashes every chunk twice (0.77 s on a 179,000-chunk
        database, on every open until 2026-10-06, audit wave 3), so a check
        that finds the index in step records both tables' stamps
        (:func:`_table_stamp`: counts, largest rowids, total lengths) in
        ``search_metadata``; the next open skips the hashing when the
        triggers were already there before it and neither table has moved
        since. A missing trigger or a moved table runs the full comparison,
        the rewrite and the stamp in one write transaction
        (:meth:`_write_transaction`).

        Args:
            triggers_were_present: Whether the ``document_chunks`` triggers
                existed before this open recreated them.
        """
        with self._engine.connect() as conn:
            if not _table_exists(conn, "document_chunks"):
                return
            stamp = self._chunk_fulltext_stamp(conn)
            if triggers_were_present and _metadata_get(conn, _CHUNK_FTS_CHECKED) == stamp:
                return
        with self._write_transaction() as conn:
            source, indexed = self._chunk_fulltext_digests(conn)
            if source != indexed:
                self._write_chunk_fulltext(conn)
            _metadata_set(conn, _CHUNK_FTS_CHECKED, self._chunk_fulltext_stamp(conn))

    @staticmethod
    def _chunk_fulltext_digests(conn: Any) -> tuple[str, str]:
        """Digests of every chunk's ``(id, content)``: as ``document_chunks`` holds it, as the index's content table does."""
        from sqlalchemy import text

        source = _rows_digest(
            conn.execute(
                text(
                    "SELECT 'chunk:' || id, coalesce(content, '') FROM document_chunks "
                    "ORDER BY 'chunk:' || id"
                )
            )
        )
        indexed = _rows_digest(
            conn.execute(text(f"SELECT item_id, content FROM {_CHUNK_FTS_ROWS} ORDER BY item_id"))
        )
        return source, indexed

    def _node_fulltext_stamp(self, conn: Any) -> str:
        """A digest of ``graph_nodes`` as stored and of ``fulltext_content``, as the metadata row holds it.

        Digests of the rows (:func:`_rows_digest`), not the tables' counts and
        lengths: the node index has no triggers, and until 2026-10-06 a label
        edited to another of the same length without touching ``updated_at``
        left every count, length and the newest ``updated_at`` as they were,
        so the open skipped the comparison and served the old name (audit
        wave 4). Hashing the stored rows is cheap next to the comparison it
        guards (which derives every node's full-text row).
        """
        from sqlalchemy import text

        graph = _rows_digest(
            conn.execute(
                text(
                    "SELECT id, json_array(coalesce(label, ''), coalesce(properties, '')) "
                    "FROM graph_nodes ORDER BY id"
                )
            )
        )
        held = _rows_digest(
            conn.execute(
                text(
                    f"SELECT node_id, json_array({', '.join(_NODE_FTS_COLUMNS)}) "
                    "FROM fulltext_content ORDER BY node_id"
                )
            )
        )
        return json.dumps([graph, held])

    def _sync_node_fulltext(self) -> None:
        """Rebuild the node full-text index when it no longer holds the graph's nodes as they are.

        The node index has no triggers: every node writer calls
        ``index_node`` / ``index_nodes_batch``, and a writer that does not
        (a snapshot copied in, a graph edited by hand or by a tool that skips
        the search repository, the resolver before 2026-10-06) leaves it
        stale, and keyword search and keyword seeds then find names the graph
        no longer has. Until 2026-10-06 only the chunk index was checked on
        open (audit wave 3). Like :meth:`_sync_chunk_fulltext`: the rows each
        node would be indexed with (:func:`node_fulltext_row` over
        ``graph_nodes``) are compared with ``fulltext_content`` by a digest,
        and a difference rewrites it (:meth:`_write_node_fulltext`); the
        tables' digests of a check that found it in step are kept in
        ``search_metadata`` (:meth:`_node_fulltext_stamp`), so an open over
        unchanged tables derives no row. The comparison, the rewrite and the
        stamp run in one write transaction (:meth:`_write_transaction`): a
        node committed by another writer is either read and indexed, or
        committed after the stamp and seen by the next open (audit wave 5).
        A database without ``graph_nodes``, or with none in it (an index
        written on its own, as the retrieval sample's), is left alone.
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            if not _table_exists(conn, "graph_nodes"):
                return
            if conn.execute(text("SELECT 1 FROM graph_nodes LIMIT 1")).fetchone() is None:
                return
            if _metadata_get(conn, _NODE_FTS_CHECKED) == self._node_fulltext_stamp(conn):
                return
        with self._write_transaction() as conn:
            if _metadata_get(conn, _NODE_FTS_CHECKED) == self._node_fulltext_stamp(conn):
                return  # another process brought it in step while this one waited
            graph = _graph_node_records(conn) or []
            rows = [node_fulltext_row(node) for node in graph]
            wanted = _rows_digest(
                (row["node_id"], json.dumps([row[c] for c in _NODE_FTS_COLUMNS])) for row in rows
            )
            held = _rows_digest(
                (nid, json.dumps(list(cols)))
                for nid, *cols in conn.execute(
                    text(
                        f"SELECT node_id, {', '.join(_NODE_FTS_COLUMNS)} FROM fulltext_content "
                        "ORDER BY node_id"
                    )
                )
            )
            if wanted != held:
                logger.info("node_fulltext_stale_on_open", nodes=len(rows))
                self._write_node_fulltext(conn, rows)
            _metadata_set(conn, _NODE_FTS_CHECKED, self._node_fulltext_stamp(conn))

    def rebuild_node_fulltext(
        self,
        nodes: Iterable[Node | Mapping[str, Any]] | None = None,
        *,
        session: TransactionalSession | None = None,
    ) -> int:
        """Make the node full-text index hold exactly the graph's nodes: their labels and current aliases.

        Every node's row is rewritten from the node as it is now
        (:func:`node_fulltext_row`: label, properties with the current
        aliases, searchable text), the rows of ids not among the nodes are
        deleted, and the FTS index is rebuilt from its content table, so no
        orphaned or stale entry survives. Vectors are untouched. Called by
        the benchmark's full reindex: until 2026-10-06 a merged-away node
        kept its row and a kept node its old aliases, so keyword search and
        keyword seeds found names the graph no longer had (audit R11, wave
        2). The identity resolver, which knows the nodes it changed, takes
        the scoped :meth:`refresh_node_fulltext` instead: this reads and
        rewrites every row under the writer lock.

        The nodes are read from ``graph_nodes`` inside the rewrite's own write
        transaction, never taken from ``nodes`` on trust: a list the caller
        read earlier misses a node another writer committed since, and until
        2026-10-06 that node's row was deleted and the index stamped as in
        step (audit wave 5). ``nodes`` is checked against the table (a
        difference is logged) and only used when the database holds no graph
        nodes (an index written on its own). Rebuilt from the table, the
        index is stamped as checked (:meth:`_node_fulltext_stamp`), so the
        next open does not compare it again.

        Args:
            nodes: The nodes the caller expects the graph to hold (graph
                nodes, or mappings with ``id``, ``label`` and ``properties``);
                None to read them from the table alone.
            session: Optional caller session to share a transaction with. Its
                transaction holds the write lock from the rewrite's first
                statement (a write), before the table is read.

        Returns:
            The number of rows the index holds afterwards.
        """
        given = None if nodes is None else list(nodes)
        transaction = self._write_transaction() if session is None else self._acquire_conn(session)
        with transaction as conn:
            if session is not None:
                # a write first: a caller's deferred transaction takes the lock before the read
                conn.exec_driver_sql(_fts_rebuild("fulltext_index"))
            graph = _graph_node_records(conn)
            if graph:
                if given is not None:
                    _log_node_list_drift(given, graph)
                count = self._write_node_fulltext(conn, [node_fulltext_row(n) for n in graph])
                _metadata_set(conn, _NODE_FTS_CHECKED, self._node_fulltext_stamp(conn))
            else:
                count = self._write_node_fulltext(conn, [node_fulltext_row(n) for n in given or []])
        return count

    def refresh_node_fulltext(
        self, node_ids: Iterable[str], *, session: TransactionalSession | None = None
    ) -> int:
        """Rewrite the full-text rows of ``node_ids`` from the graph as it is now; nothing else.

        The scoped form of :meth:`rebuild_node_fulltext`, for a caller that
        knows which nodes it changed (the identity resolver after a merge or
        an alias drop). Each id still in ``graph_nodes`` gets its row upserted
        from the node as stored (:func:`node_fulltext_row`, read inside this
        write transaction, so a node rewritten since the caller's read is
        indexed as it is now); an id no longer there loses its row. The
        triggers keep the FTS index in step row by row: no other node's row
        is read or written, and the index is not rebuilt. Until the #795
        follow-up the resolver ran the whole-table rebuild, a read of every
        graph node and a rewrite of every row under the writer lock, on the
        source-delete path too. Vectors are untouched. The open-time check
        stamp (``_NODE_FTS_CHECKED``) is not re-stamped: a scoped write cannot
        vouch for the rows it did not read, so the next repository open
        compares the index with the graph once, as after any commit.

        Args:
            node_ids: The nodes whose rows follow the graph.
            session: Optional caller session to share a transaction with.

        Returns:
            The number of rows written (ids still in the graph).
        """
        from sqlalchemy import text

        ids = sorted(set(node_ids))
        if not ids:
            return 0
        transaction = self._write_transaction() if session is None else self._acquire_conn(session)
        written = 0
        with transaction as conn:
            for offset in range(0, len(ids), 500):
                batch = ids[offset : offset + 500]
                placeholders = ", ".join(f":id_{j}" for j in range(len(batch)))
                params = {f"id_{j}": nid for j, nid in enumerate(batch)}
                rows = [
                    node_fulltext_row(
                        {"id": nid, "label": label or "", "properties": _json_object(props)}
                    )
                    for nid, label, props in conn.execute(
                        text(
                            "SELECT id, label, properties FROM graph_nodes "
                            f"WHERE id IN ({placeholders})"
                        ),
                        params,
                    )
                ]
                gone = set(batch) - {row["node_id"] for row in rows}
                if gone:
                    marks = ", ".join(f":g_{j}" for j in range(len(gone)))
                    conn.execute(
                        text(f"DELETE FROM fulltext_content WHERE node_id IN ({marks})"),
                        {f"g_{j}": nid for j, nid in enumerate(sorted(gone))},
                    )
                if rows:
                    conn.execute(text(_NODE_FTS_UPSERT), rows)
                written += len(rows)
        logger.info("node_fulltext_refreshed", nodes=written, removed=len(ids) - written)
        return written

    @staticmethod
    def _write_node_fulltext(conn: Any, rows: list[dict[str, str]]) -> int:
        """Make ``fulltext_content`` hold exactly ``rows`` and rebuild the FTS index from it; the row count after.

        Runs on the caller's connection, inside its transaction.
        """
        from sqlalchemy import text

        keep = {row["node_id"] for row in rows}
        conn.execute(text(_fts_rebuild("fulltext_index")))  # see _fts_rebuild: before the delete
        stale = [
            node_id
            for (node_id,) in conn.execute(text("SELECT node_id FROM fulltext_content"))
            if node_id not in keep
        ]
        for offset in range(0, len(stale), 500):
            batch = stale[offset : offset + 500]
            placeholders = ", ".join(f":id_{j}" for j in range(len(batch)))
            conn.execute(
                text(f"DELETE FROM fulltext_content WHERE node_id IN ({placeholders})"),
                {f"id_{j}": nid for j, nid in enumerate(batch)},
            )
        if rows:
            conn.execute(text(_NODE_FTS_UPSERT), rows)
        # rebuilt from the content table: drops the orphans INSERT OR REPLACE left
        conn.execute(text(_fts_rebuild("fulltext_index")))
        count = int(conn.execute(text("SELECT COUNT(*) FROM fulltext_content")).scalar() or 0)
        logger.info("node_fulltext_rebuilt", nodes=count, removed=len(stale))
        return count

    def rebuild_chunk_fulltext(self) -> int:
        """Rewrite the chunk full-text index from ``document_chunks`` and stamp it as checked.

        One write transaction (:meth:`_write_transaction`): the chunks are
        read, indexed and stamped under one lock.

        Returns:
            The number of chunks indexed (0 when the table does not exist).
        """
        with self._write_transaction() as conn:
            if not _table_exists(conn, "document_chunks"):
                return 0
            count = self._write_chunk_fulltext(conn)
            _metadata_set(conn, _CHUNK_FTS_CHECKED, self._chunk_fulltext_stamp(conn))
        return count

    @staticmethod
    def _write_chunk_fulltext(conn: Any) -> int:
        """Rewrite the chunk index's content table from ``document_chunks`` and rebuild the index; the count after."""
        from sqlalchemy import text

        conn.execute(text(_fts_rebuild(_CHUNK_FTS)))  # see _fts_rebuild: before the delete
        conn.execute(text(f"DELETE FROM {_CHUNK_FTS_ROWS}"))
        conn.execute(
            text(
                f"INSERT INTO {_CHUNK_FTS_ROWS}(item_id, content) "
                "SELECT 'chunk:' || id, coalesce(content, '') FROM document_chunks"
            )
        )
        conn.execute(text(_fts_rebuild(_CHUNK_FTS)))
        count = int(conn.execute(text(f"SELECT COUNT(*) FROM {_CHUNK_FTS_ROWS}")).scalar() or 0)
        logger.info("chunk_fulltext_rebuilt", chunks=count)
        return count

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
                self._store_vector_config(conn)
                return

            # Check for changes
            dim_changed = stored_dim is not None and stored_dim != self.vector_dim
            # the same curated model under its CPU or Ollama name is one model (registry)
            model_changed = canonical_embedding_model(stored_model) != canonical_embedding_model(
                self.embedding_model
            )

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
                        ddl_conn.execute(text(_vec_table_ddl(type_table, self.vector_dim)))
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
            self._store_vector_config(meta_conn)

        # Set reindex flag via dedicated method (outside the connection)
        if needs_reindex:
            self._set_reindex_flag(True)

    def _store_vector_config(self, conn: Any) -> None:
        """Record the configured embedding model and dimension in ``search_metadata`` on ``conn``.

        The one write of the vector config: the first open, a model change and
        a keyword-only open that created the vector tables all record it here.
        """
        from sqlalchemy import text

        conn.execute(
            text(
                "INSERT OR REPLACE INTO search_metadata (key, value) "
                "VALUES ('embedding_model', :model)"
            ),
            {"model": self.embedding_model},
        )
        conn.execute(
            text("INSERT OR REPLACE INTO search_metadata (key, value) VALUES ('vector_dim', :dim)"),
            {"dim": str(self.vector_dim)},
        )

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
        """Check both full-text indexes against their content tables (:meth:`_fulltext_index_problem`).

        Pre-launch review F3. The ``fulltext_index`` virtual table is
        kept in sync with ``fulltext_content`` by three INSERT / UPDATE
        / DELETE triggers (see ``_init_schema``). If a trigger ever
        fails to fire (lock-timeout mid-trigger, schema rebuild gone
        wrong, manual operator edit), the index silently drifts from
        content and search results go stale until a rebuild.

        ``SELECT COUNT(*) FROM fulltext_index`` does not count the index in
        FTS5's external-content layout: it reads the content table at query
        time, so it agrees with content even when the inverted index is
        stale or empty; the index's own ``_docsize`` table holds one row per
        indexed item, and is what the count compares. The
        canonical health check is the FTS5-specific
        ``INSERT INTO <fts_table>(<fts_table>, rank) VALUES('integrity-check', 1)``
        command: with ``rank`` 1 it also checks the index against the
        content table (the plain form checks the index's own structure
        only and passes orphaned entries), and raises an
        ``OperationalError`` (typically ``SQLITE_CORRUPT_VTAB``) on
        drift / corruption.

        Returns:
            Tuple of ``(is_consistent, error_message)``. ``error_message``
            is ``None`` when consistent.
        """
        for fx in FULLTEXT_INDEXES:
            problem = self._fulltext_index_problem(fx)
            if problem is not None:
                return False, problem
        return True, None

    def _fulltext_index_problem(self, fx: FulltextIndex, *, deep: bool = True) -> str | None:
        """Why the FTS5 index ``fx`` does not hold its content table, or None when it does.

        Two checks: the index's own row count (``<index>_docsize``, one row
        per indexed item) against the content table's, then (``deep``) FTS5's
        ``integrity-check`` with ``rank`` 1 (the index against its content).
        A ``CREATE VIRTUAL TABLE IF NOT EXISTS`` over a dropped index makes
        it anew and empty over an intact content table, and ``delete-all``
        empties an index and keeps its content table: every check of the
        content tables passes both (audit wave 4). The ``rank`` 1 check reads
        every content row and every index entry: 1.13 s of a 1.48 s open at
        179,000 chunks (audit wave 5); the count check is two aggregate reads.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import DatabaseError

        try:
            with self._engine.begin() as conn:
                indexed = conn.execute(text(f"SELECT count(*) FROM {fx.fts}_docsize")).scalar()
                held = conn.execute(text(f"SELECT count(*) FROM {fx.content}")).scalar()
                if indexed != held:
                    return f"{fx.fts} indexes {indexed} of the {held} rows of {fx.content}"
                if deep:
                    # rank 1: also compare the index with its content table; the plain
                    # check passes an index holding orphans of deleted rows (audit wave 2)
                    conn.execute(
                        text(f"INSERT INTO {fx.fts}({fx.fts}, rank) VALUES('integrity-check', 1)")
                    )
        except DatabaseError as e:  # an orphan reads as SQLITE_CORRUPT, not an OperationalError
            return str(e.orig if e.orig is not None else e)
        return None

    def _fulltext_index_stamp(self, fx: FulltextIndex) -> str:
        """The state of the FTS5 index ``fx`` and its content table, as the metadata row holds it.

        Counts and largest ids of the index's ``_docsize`` and ``_data``
        tables and of the content table: one aggregate read each. Every
        write to the index (a trigger, a rebuild, a ``delete-all``, the
        table dropped and recreated) adds, replaces or removes ``_data``
        blocks, so an index written since its last full check has another
        stamp.
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            parts = [
                list(
                    conn.execute(
                        text(f"SELECT count(*), coalesce(max({key}), 0) FROM {table}")
                    ).one()
                )
                for table, key in (
                    (f"{fx.fts}_docsize", "id"),
                    (f"{fx.fts}_data", "id"),
                    (fx.content, fx.rowid),
                )
            ]
        return json.dumps(parts)

    def _fulltext_index_verified(self, fx: FulltextIndex) -> bool:
        """Whether ``fx`` is as its last full check (rank 1) found it in step (:meth:`_fulltext_index_stamp`).

        False when no stamp is recorded or the tables cannot be read (the full check then runs).
        """
        from sqlalchemy.exc import DatabaseError

        try:
            with self._engine.connect() as conn:
                recorded = _metadata_get(conn, _FTS_VERIFIED.format(fts=fx.fts))
            return recorded is not None and recorded == self._fulltext_index_stamp(fx)
        except DatabaseError:
            return False

    def _record_fulltext_index_verified(self, fx: FulltextIndex) -> None:
        """Stamp ``fx`` as found in step by a full check (:meth:`_fulltext_index_stamp`); a failed write is logged."""
        from sqlalchemy.exc import DatabaseError

        try:
            stamp = self._fulltext_index_stamp(fx)
            with self._engine.begin() as conn:
                _metadata_set(conn, _FTS_VERIFIED.format(fts=fx.fts), stamp)
        except DatabaseError:
            logger.warning("search_index_stamp_not_written", index=fx.fts, exc_info=True)

    def _repair_fulltext_indexes(self) -> None:
        """Rebuild, on open, each full-text index that does not hold its content table.

        Called from ``__init__`` after the content tables were brought in
        step (:meth:`_sync_chunk_fulltext`, :meth:`_sync_node_fulltext`). An
        index :meth:`_fulltext_index_problem` finds wanting is rebuilt from
        its content table (FTS5 ``rebuild``, :func:`_fts_rebuild`) and
        checked again; one still wanting is logged as an error and served as
        it is (the process still answers, the operator can rebuild). Until
        2026-10-06 a failed check only logged a warning: a dropped chunk
        index changed all 120 benchmark contexts and a dropped node index 96,
        and no retrieval was refused (audit wave 4).

        The count check runs on every open; the ``rank`` 1 integrity check
        (1.13 s of a 1.48 s open at 179,000 chunks, audit wave 5) only when
        the index has moved since the last full check that passed, or none
        is recorded (:meth:`_fulltext_index_verified`): a passing full check
        stamps the index in ``search_metadata``. A count that disagrees
        rebuilds the index, and the rebuilt index gets the full check.
        """
        from sqlalchemy import text

        for fx in FULLTEXT_INDEXES:
            verified = self._fulltext_index_verified(fx)
            problem = self._fulltext_index_problem(fx, deep=not verified)
            if problem is None:
                if not verified:
                    self._record_fulltext_index_verified(fx)
                continue
            logger.warning(
                "search_index_drift_detected",
                index=fx.fts,
                error=problem,
                action="rebuilding it from its content table",
            )
            with self._engine.begin() as conn:
                conn.execute(text(_fts_rebuild(fx.fts)))
            remaining = self._fulltext_index_problem(fx)
            if remaining is None:
                logger.info("search_index_repaired", index=fx.fts)
                self._record_fulltext_index_verified(fx)
                continue
            logger.error(
                "search_index_drift_unrepaired",
                index=fx.fts,
                error=remaining,
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
            row = node_fulltext_row(node)
            searchable_text = row["searchable_text"]

            with self._acquire_conn(session) as conn:
                # FTS5: upsert content
                conn.execute(text(_NODE_FTS_UPSERT), row)

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
            rows = [node_fulltext_row(node) for node in nodes]

            with self._acquire_conn(session) as conn:
                # Batch FTS5 upsert
                conn.execute(
                    text(_NODE_FTS_UPSERT),
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

    def keyword_search(
        self,
        query: str,
        limit: int = 10,
        item_type: str | None = "node",
        source_ids: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Perform full-text keyword search with BM25 ranking.

        The query matches any of its significant words or quoted phrases
        (:func:`_fts5_query`); equal scores are ordered by item id.

        Args:
            query: Search query string
            limit: Maximum results
            item_type: ``"node"`` (the default: the node index, as before
                chunks had one), ``"chunk"`` (ids ``chunk:<id>``), or None for
                both, fused by reciprocal rank (BM25 scores of two indexes do
                not compare).
            source_ids: A source scope, applied in the query before the
                ``limit`` (:data:`_SCOPE_SQL`); None for every source. A
                switched-off source's items never match, scoped or not
                (:func:`_scope_clause`).

        Returns:
            List of (item_id, score) tuples sorted by relevance

        Raises:
            ValueError: For an item type without a full-text index.

        """
        query = normalize_query(query)
        source_ids = list(source_ids) if source_ids is not None else None
        if item_type is None:
            return _fuse_rankings(
                self.keyword_search(query, limit, "node", source_ids),
                self.keyword_search(query, limit, "chunk", source_ids),
                limit=limit,
            )
        try:
            sql = _KEYWORD_SQL[item_type]
        except KeyError as exc:
            msg = f"no full-text index for item_type {item_type!r}; expected node or chunk"
            raise ValueError(msg) from exc

        from sqlalchemy import text

        match = _fts5_query(query)
        if not match:
            return []
        try:
            with self._engine.connect() as conn:
                join, condition, scope = _scope_clause(
                    item_type,
                    _KEYWORD_ID[item_type],
                    source_ids,
                    hide_disabled=self._hides_sources(conn),
                )
                sql = sql.format(
                    scope_join=join, scope_where=f"AND {condition} " if condition else ""
                )
                result = conn.execute(text(sql), {"query": match, "limit": limit, **scope})
                return [(row[0], row[1]) for row in result]
        except Exception as e:
            record_retrieval_fault(f"keyword_search:{item_type}")
            logger.exception(
                "keyword_search_failed",
                item_type=item_type,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return []

    def vector_search(
        self,
        query_embedding: list[float],
        k: int = 10,
        item_type: str | None = None,
        source_ids: Iterable[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Perform vector similarity search using cosine distance.

        Dispatches to the per-type vec0 table for ``item_type`` so the
        KNN ``embedding MATCH :query AND k = :k`` index path is used.
        When ``item_type`` is None the search runs against the content
        tables (nodes and chunks) and the merged result is the global top-k
        by similarity. Templates are searched only when asked for by name:
        their vectors describe a type, not anything a query retrieves, and
        until 2026-10-06 relation-type templates took GraphRAG seed slots
        (audit R4). A source scope, and the sources switched off, are applied
        before the ``k`` cut (:meth:`_filtered_knn`).

        Args:
            query_embedding: Query vector
            k: Number of results
            item_type: Optional filter by type ("node", "chunk", "template")
            source_ids: A source scope (:data:`_SCOPE_SQL`); None for every source.

        Returns:
            List of (item_id, similarity_score) tuples sorted by similarity
            descending.

        """
        from sqlalchemy import text

        source_ids = list(source_ids) if source_ids is not None else None
        if len(query_embedding) != self.vector_dim:
            record_retrieval_fault("vector_search:dimension_mismatch")
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
            tables = tuple(_vec_table_for(t) for t in _CONTENT_ITEM_TYPES)

        try:
            query_bytes = _serialize_float32(query_embedding)

            merged: list[tuple[str, float]] = []
            types = (item_type,) if item_type is not None else _CONTENT_ITEM_TYPES
            with self._engine.connect() as conn:
                hide = self._hides_sources(conn)
                for table, table_type in zip(tables, types, strict=True):
                    if table_type in _SCOPE_SQL and (source_ids or hide):
                        rows = self._filtered_knn(
                            conn, table, table_type, query_bytes, k, source_ids, hide_disabled=hide
                        )
                    else:
                        sql = (
                            f"SELECT item_id, vec_distance_cosine(embedding, :query) AS distance "
                            f"FROM {table} "
                            f"WHERE embedding MATCH :query "
                            f"AND k = :k"
                        )
                        rows = list(conn.execute(text(sql), {"query": query_bytes, "k": k}))
                    for row in rows:
                        item_id = row[0]
                        distance = row[1]
                        # Cosine distance: 0 = identical, 2 = opposite
                        similarity = max(0.0, min(1.0, 1.0 - distance))
                        merged.append((item_id, similarity))

            merged.sort(key=lambda pair: pair[1], reverse=True)
            return merged[:k]

        except Exception as e:
            record_retrieval_fault("vector_search")
            logger.exception(
                "vector_search_failed",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            return []

    @staticmethod
    def _filtered_knn(
        conn: Any,
        table: str,
        item_type: str,
        query_bytes: bytes,
        k: int,
        source_ids: Iterable[str] | None,
        *,
        hide_disabled: bool,
    ) -> list[Any]:
        """The ``k`` nearest items of ``table`` that may answer: ``(item_id, distance)`` rows, nearest first.

        What may answer is the source scope, less any switched-off source
        (:func:`_scope_clause`). sqlite-vec's KNN takes no filter, so the
        filter is applied to its output. One statement counts the vectors
        and the rows that may answer; the KNN is then asked once for the
        hits a ``k`` that size of the table would hold, with a margin
        (:data:`_SCOPE_KNN_MARGIN`), and the items are scanned exactly when
        that is past sqlite-vec's ``k`` limit (:data:`VEC_KNN_MAX`) or the
        KNN still came up short of ``k``. Until 2026-10-06 the KNN was asked
        again for four times as many until ``k`` were found, each round a
        full scan, so a five-chunk scope on a 179,000-chunk index cost 2.37 s
        against 0.12 s unscoped in eight statements (audit wave 6); before
        that the scope was applied after the cut (audit wave 5). Equal
        distances are ordered by item id.
        """
        from sqlalchemy import text

        if k <= 0:
            return []
        join, condition, scope = _scope_clause(
            item_type, "knn.item_id", source_ids, hide_disabled=hide_disabled
        )
        held_sql, _ = _scope_count_sql(item_type, source_ids, hide_disabled=hide_disabled)
        total, held = conn.execute(
            text(f'SELECT (SELECT count(*) FROM "{table}_rowids"), ({held_sql})'), scope
        ).one()
        total, held = int(total or 0), int(held or 0)
        if total == 0 or (held == 0 and source_ids):
            return []  # nothing indexed, or nothing in the scope (its join needs a row)
        # ``held`` counts rows: an unscoped search keeps vectors with no row
        # (the hidden test needs a row to hide one), so none held reads the table
        fetch = total if held == 0 else math.ceil(k * total / held * _SCOPE_KNN_MARGIN)
        fetch = min(max(fetch, k), total)
        if fetch <= VEC_KNN_MAX:
            knn = (
                # MATERIALIZED: the filter must not reach the KNN as a constraint
                # on its auxiliary item_id column, which sqlite-vec refuses
                "WITH knn AS MATERIALIZED (SELECT item_id, "
                "vec_distance_cosine(embedding, :query) AS distance "
                f"FROM {table} WHERE embedding MATCH :query AND k = :k) "
                f"SELECT knn.item_id, knn.distance FROM knn {join}WHERE {condition} "
                "ORDER BY knn.distance, knn.item_id"
            )
            rows = list(conn.execute(text(knn), {"query": query_bytes, "k": fetch, **scope}))
            if len(rows) >= k or fetch >= total:
                return rows[:k]
        logger.info("vector_search_scope_scanned", table=table, vectors=total, k=k, held=held)
        scan = (
            "SELECT knn.item_id, vec_distance_cosine(knn.embedding, :query) AS distance "
            f"FROM {table} knn {join}WHERE {condition} "
            "ORDER BY distance, knn.item_id LIMIT :k"
        )
        return list(conn.execute(text(scan), {"query": query_bytes, "k": k, **scope}))

    def _hides_sources(self, conn: Any) -> bool:
        """Whether any source is switched off, so a search must apply the visibility predicate.

        False for an index with no ``sources`` (or item) table: nothing can be
        hidden there. Once the tables are seen they are not looked for again.
        """
        from sqlalchemy import text

        if not self._visibility_tables_seen:
            names = ", ".join(f"'{t}'" for t in _VISIBILITY_TABLES)
            found = conn.execute(
                text(
                    f"SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name IN ({names})"
                )
            ).scalar()
            if int(found or 0) < len(_VISIBILITY_TABLES):
                return False
            self._visibility_tables_seen = True
        return bool(conn.execute(text(ANY_SOURCE_HIDDEN_SQL)).scalar())

    def count_in_scope(self, item_type: str, source_ids: Iterable[str] | None = None) -> int:
        """How many items of ``item_type`` (``"chunk"``, ``"node"``) a source scope holds that may answer.

        Every item without a scope; a switched-off source's items never count
        (:func:`_scope_count_sql`, the count the scoped vector search sizes
        its KNN from).

        Raises:
            ValueError: For an item type no source holds.
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            sql, scope = _scope_count_sql(
                item_type, source_ids, hide_disabled=self._hides_sources(conn)
            )
            return int(conn.execute(text(sql), scope).scalar() or 0)

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

    async def _query_vector(
        self, query_text: str, embedding_provider_callback: Callable[[str], Any]
    ) -> list[float]:
        """The query's embedding from the callback (an object, a dict or a bare vector)."""
        result = await embedding_provider_callback(query_text)
        if isinstance(result, dict):
            return list(result.get("embedding", []) or [])
        if hasattr(result, "embedding"):
            return list(result.embedding or [])
        return list(result or [])

    async def semantic_search(
        self,
        query_text: str,
        k: int = 10,
        embedding_provider_callback: Callable[[str], Any] | None = None,
        item_type: str | None = None,
    ) -> list[tuple[str, float]]:
        """Perform semantic search using query text.

        Generates embedding for the query via callback, then performs vector search.

        Args:
            query_text: Text to search for
            k: Number of results to return
            embedding_provider_callback: Async callback that returns embedding
            item_type: Optional filter by type (see :meth:`vector_search`).

        Returns:
            List of (item_id, similarity_score) tuples

        """
        if not embedding_provider_callback:
            logger.warning("semantic_search_no_embedding_provider")
            return []

        query_text = normalize_query(query_text)
        if self.is_rebuilding:
            logger.info(
                "semantic_search_rebuilding_fallback_to_fts",
                query=query_text,
            )
            if item_type == "template":
                return []
            return self.keyword_search(query_text, limit=k, item_type=item_type)

        try:
            query_embedding = await self._query_vector(query_text, embedding_provider_callback)
            if not query_embedding:
                logger.warning("Failed to generate query embedding")
                return []

            return self.vector_search(query_embedding, k=k, item_type=item_type)

        except Exception as e:
            record_retrieval_fault("semantic_search")
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
        item_type: str | None = None,
        source_ids: Iterable[str] | None = None,
    ) -> SearchHits:
        """Hybrid search: the keyword and vector rankings fused by reciprocal rank.

        Both arms search ``item_type`` (None: nodes and chunks) and return up
        to ``k`` hits each, so the budget is filled from the type asked for
        rather than shared with others and filtered afterwards (audit R3).
        The keyword arm is the BM25 ranking of each full-text index searched;
        the vector arm keeps hits of at least ``min_similarity``. The rankings
        are fused by :func:`_fuse_rankings` (reciprocal rank, scaled so the
        top of every arm scores 1.0). While the vector tables are rebuilt the
        vector arm is empty and the keyword ranking alone is returned. A
        source scope is applied inside both arms, before their cut (audit
        wave 5). The query text is composed first (:func:`normalize_query`),
        so its embedding and its keywords are those of the stored spelling.
        The result says how many distinct candidates both arms found before
        the ``k`` cut (keyword hits and vector hits at or above the floor):
        fewer than ``k`` is a scope that holds no more that answers
        (:attr:`~chaoscypher_core.ports.search.SearchHits.scope_exhausted`).

        Args:
            query_text: Text to search for
            k: Number of results to return
            embedding_provider_callback: Async callback for generating embeddings
            min_similarity: Minimum similarity score for semantic results
            item_type: ``"node"``, ``"chunk"``, or None for both.
            source_ids: A source scope (:data:`_SCOPE_SQL`); None for every source.

        Returns:
            The (id, fused score) hits, best first, with the candidates seen
            (:class:`~chaoscypher_core.ports.search.SearchHits`).

        """
        keyword_type = item_type
        if item_type == "template":
            msg = "hybrid search has no keyword index for templates; use vector_search"
            raise ValueError(msg)
        query_text = normalize_query(query_text)
        scope = list(source_ids) if source_ids else None
        try:
            if len(query_text.strip()) < 3:
                alone = self.keyword_search(
                    query_text, limit=k, item_type=keyword_type, source_ids=scope
                )
                return SearchHits(alone, k=k, candidates_seen=len(alone))

            types = (item_type,) if item_type else _CONTENT_ITEM_TYPES
            keyword_arms = [
                self.keyword_search(query_text, limit=k, item_type=t, source_ids=scope)
                for t in types
            ]

            semantic_results: list[tuple[str, float]] = []
            if embedding_provider_callback and self.is_rebuilding:
                record_retrieval_fault("hybrid_search:rebuilding")  # keyword arm alone
            elif embedding_provider_callback:
                query_embedding = await self._query_vector(query_text, embedding_provider_callback)
                if query_embedding:
                    semantic_results = self.vector_search(
                        query_embedding, k=k, item_type=item_type, source_ids=scope
                    )
                else:
                    record_retrieval_fault("hybrid_search:no_query_vector")

            filtered_semantic = [
                (result_id, score)
                for result_id, score in semantic_results
                if score >= min_similarity
            ]

            fused = _fuse_rankings(*keyword_arms, filtered_semantic, limit=k)
            seen = {item for arm in (*keyword_arms, filtered_semantic) for item, _ in arm}

            logger.debug(
                "hybrid_search_merged",
                keyword_count=sum(len(arm) for arm in keyword_arms),
                semantic_count=len(filtered_semantic),
                merged_count=len(fused),
                candidates_seen=len(seen),
            )

            return SearchHits(fused, k=k, candidates_seen=len(seen))

        except Exception as e:
            record_retrieval_fault("hybrid_search")
            logger.warning(
                "hybrid_search_failed_fallback_to_keyword",
                error_type=type(e).__name__,
                error_message=str(e),
            )
            fallback = self.keyword_search(
                query_text, limit=k, item_type=keyword_type, source_ids=scope
            )
            return SearchHits(fallback, k=k, candidates_seen=len(fallback))

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
        the ``session`` kwarg flows through unchanged. A keyword-only
        repository rebuilds the node full-text index alone
        (:meth:`rebuild_node_fulltext`) and leaves the vector tables as they are.

        Args:
            nodes: List of all nodes to reindex
            session: Optional caller session to share a transaction with.

        """
        logger.info("reindexing_nodes_started", node_count=len(nodes))

        if not self.vectors:
            # keyword-only: the full-text half; another process's vectors stay
            self.rebuild_node_fulltext(nodes, session=session)
            logger.info("reindexing_complete_keyword_only", node_count=len(nodes))
            return

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
                chunk_ft = conn.execute(text(f"SELECT COUNT(*) FROM {_CHUNK_FTS_ROWS}")).fetchone()
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
                    "chunk_count": chunk_ft[0] if chunk_ft else 0,
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

    def count_vectors(self, item_type: str) -> int:
        """How many vectors the per-type table for ``item_type`` holds.

        Args:
            item_type: "node", "chunk" or "template".

        Returns:
            The row count of that vec0 table.
        """
        from sqlalchemy import text

        table = _vec_table_for(item_type)
        with self._engine.connect() as conn:
            return int(conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)

    def clear_vectors(self, item_type: str, *, session: TransactionalSession | None = None) -> None:
        """Delete every vector of one type (the full-text indexes are untouched).

        A full re-embed clears first, so a vector the re-embed failed to
        write cannot survive from the previous embedder and be counted as
        written. Errors propagate.

        Args:
            item_type: "node", "chunk" or "template".
            session: Optional caller session to share a transaction with.
        """
        from sqlalchemy import text

        table = _vec_table_for(item_type)
        with self._acquire_conn(session) as conn:
            conn.execute(text(f"DELETE FROM {table}"))

    def clear_all_indices(self, *, session: TransactionalSession | None = None) -> None:
        """Clear all search indices (fulltext and vector).

        WARNING: This operation cannot be undone!
        Indices will need to be rebuilt by reindexing nodes. The chunk
        full-text index is not cleared: it is derived from ``document_chunks``
        by triggers (see :meth:`rebuild_chunk_fulltext`).

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
        except Exception as e:
            # False keeps a chat answering, but the read failure itself must be
            # visible — it fires exactly during the DB contention this flag
            # exists to guard (mirrors _set_reindex_flag's write-side logging).
            # A collecting retrieval records it: until 2026-10-06 an unreadable
            # flag read as an index in step, and the benchmark pinned what it
            # served (audit wave 5).
            logger.warning("needs_full_reindex_read_failed", error=str(e))
            record_retrieval_fault(NEEDS_FULL_REINDEX_UNREADABLE)
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
        except Exception as e:
            # A silent False here sends semantic_search into vec0 tables that
            # may just have been DROPped mid-rebuild — log before defaulting.
            logger.warning("is_rebuilding_read_failed", error=str(e))
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
        if not self.vectors:
            # keyword-only: nothing here would ever flush the queue
            return
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
