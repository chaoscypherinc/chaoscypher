# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source processing Commit Service.

Orchestrates the source processing commit process - converting analyzed entities
and relationships into permanent graph nodes and edges.

Extracted from commit_service.py for SRP compliance.
"""

import base64
import dataclasses
import hashlib
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

import numpy as np
import structlog


def _stable_citation_id(
    *,
    database_name: str,
    source_id: str,
    chunk_id: str,
    entity_uri: str,
) -> str:
    """Derive a content-addressed citation ID for (source, chunk, entity).

    Within a given source, a citation is uniquely identified by the
    (chunk, entity) pair — one citation per distinct entity-mention
    in that chunk. Hashing these three scopes gives a key that stays
    identical across crash-and-resume commit attempts, so the
    ``create_citations_batch`` INSERT can be replayed safely.

    Args:
        database_name: Active database name.
        source_id: Source the citation belongs to.
        chunk_id: Chunk where the entity was mentioned.
        entity_uri: URI of the extracted entity (content-addressed
            from ``upsert_nodes_batch``).

    Returns:
        Deterministic string of the form ``cite_<24-hex-chars>``.
    """
    raw = f"{database_name}:{source_id}:{chunk_id}:{entity_uri}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"cite_{digest}"


def _stable_relationship_citation_id(
    *,
    database_name: str,
    source_id: str,
    chunk_id: str,
    edge_id: str,
) -> str:
    """Derive a content-addressed citation ID for (source, chunk, edge).

    Parallel to ``_stable_citation_id`` but for edge citations. The
    edge_id here is the stable hash from ``upsert_edges_batch``, so
    the full citation key cascades all the way down from the raw
    commit inputs.

    Args:
        database_name: Active database name.
        source_id: Source the citation belongs to.
        chunk_id: Chunk where the relationship was mentioned.
        edge_id: Stable ID of the extracted edge.

    Returns:
        Deterministic string of the form ``relcite_<24-hex-chars>``.
    """
    raw = f"{database_name}:{source_id}:{chunk_id}:{edge_id}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"relcite_{digest}"


from chaoscypher_core.models import ReleasedRows, TemplateUpdate
from chaoscypher_core.services.graph.memory.offered_nodes import OFFERED_NODES_KEY
from chaoscypher_core.services.sources.engine.commit.entity import (
    EntityCommitHandler,
)
from chaoscypher_core.services.sources.engine.commit.matcher import (
    EntityTemplateMatcher,
)
from chaoscypher_core.services.sources.engine.commit.relation import (
    RelationshipCommitHandler,
    relationship_edge_label,
)
from chaoscypher_core.services.sources.engine.commit.template import (
    TemplateCommitHandler,
    drop_bound_suggestions,
    live_offered_templates,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    INVERSE_OF_KEY,
    ORPHAN_KEY,
    is_orphan_record,
)
from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    carries_evidence,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    resolve_filtering_config,
)
from chaoscypher_core.services.sources.engine.extraction.utils.offered_types import (
    OFFERED_TEMPLATES_KEY,
)
from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
    evidence_snippet,
)


logger = structlog.get_logger(__name__)


def _citation_metadata(
    sent_ref: str | None,
    evidence: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build the JSON citation metadata for an entity or relationship citation.

    Args:
        sent_ref: The model's sentence reference (numbers the group text).
        evidence: Evidence resolved at aggregation (``resolve_evidence``).

    Returns:
        ``{"sent_ref", "sentences"}`` where ``sentences`` lists each cited
        sentence's ``text``, ``chunk_id`` and chunk-local ``local_index``;
        ``None`` when there is neither.
    """
    if not sent_ref and not evidence:
        return None
    return {
        "sent_ref": sent_ref,
        "sentences": [
            {
                "text": e.get("text"),
                "chunk_id": e.get("chunk_id"),
                "local_index": e.get("local_index"),
            }
            for e in evidence
        ],
    }


def entity_citation_chunks(entity: dict[str, Any]) -> list[str]:
    """Every distinct chunk ``entity`` is cited in: its ``cited_chunk_id``, then its evidence chunks.

    Args:
        entity: An extracted (possibly deduplicated) entity dict.

    Returns:
        Chunk ids in first-cited order, without repeats; empty when the
        entity names no chunk.
    """
    out: list[str] = []
    candidates = [entity.get("cited_chunk_id")]
    candidates.extend(
        e.get("chunk_id") for e in entity.get("evidence") or [] if isinstance(e, dict)
    )
    for cid in candidates:
        if cid and str(cid) not in out:
            out.append(str(cid))
    return out


class OrphanCommit(NamedTuple):
    """What the commit's orphan rule (:func:`flag_orphan_entities`) leaves to commit."""

    entities: list[dict]
    """The entities to commit, in extraction order, every orphan kept flagged (``ORPHAN_KEY``)."""
    relationships: list[dict]
    """The relationships, remapped onto ``entities``' positions."""
    flagged: int
    """Orphans kept with the flag (the ``orphan_entities_filtered`` counter)."""
    dropped: int
    """Orphans dropped for carrying nothing (the ``evidence_less_entities_dropped`` counter)."""


def _entity_record(entity: dict) -> dict:
    """The record an entity's fields live on: its ``entity_data`` when nested, else itself."""
    nested = entity.get("entity_data")
    return nested if isinstance(nested, dict) else entity


def _with_orphan_flag(entity: dict, *, orphan: bool) -> dict:
    """``entity`` with the orphan flag set (``orphan``) or a stale ``True`` cleared; a copy when it changes.

    Only the flag's own value is touched: an extracted ``orphan`` property of
    any other value (``"yes"``) is what the text says and stays.
    """
    record = _entity_record(entity)
    stored = record.get("properties")
    props: dict[str, Any] = stored if isinstance(stored, dict) else {}
    if orphan == is_orphan_record(props):
        return entity
    new_props = (
        {**props, ORPHAN_KEY: True}
        if orphan
        else {k: v for k, v in props.items() if k != ORPHAN_KEY}
    )
    new_record = {**record, "properties": new_props}
    return {**entity, "entity_data": new_record} if record is not entity else new_record


def flag_orphan_entities(
    entities: list[dict],
    relationships: list[dict],
    *,
    keep_evidence_less: bool,
) -> OrphanCommit:
    """Keep every orphan that carries evidence, flagged; drop only the ones carrying nothing.

    Keep, flag, never delete (2026-10-07): an orphan is an entity no
    relationship references. Until then the commit deleted every orphan when
    ``FilteringConfig.protect_orphans`` was off (every preset but
    ``minimal``/``unfiltered``), description and all: 20 to 36 entities per
    Book One run, among them "Márya Lvóvna Karágina" with the fact that she
    is Julie's mother (benchmark q035, q128), because the relationship pass
    happened not to write her edge. Now an orphan that carries evidence
    (:func:`~chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner.carries_evidence`:
    a description or an evidence sentence) is committed with
    ``properties.orphan: true``
    (:data:`~chaoscypher_core.services.sources.engine.deduplication.name_identity.ORPHAN_KEY`),
    and only one carrying neither is dropped, unless ``keep_evidence_less``
    (``protect_orphans``) keeps it too, flagged. The consumers decide:
    retrieval ranks a flagged node after the connected ones
    (``graphrag_handlers._by_standing``), the graph view groups it, the
    resolver may connect it later. The flag is the commit's: a connected
    entity loses a stale ``True``.

    The extraction emits relationships keyed by integer indices into
    ``entities`` (``source``/``target``), and the commit's consumers
    (``commit/relation.py`` and the citation helpers) resolve endpoints by
    those positions, so a dropped entity's successors shift down: the
    relationships are remapped by the canonical
    ``EntityProcessor.remap_relationship_indices`` (an edge into a kept but
    shifted entity would otherwise vanish or hit the wrong one). Malformed
    endpoints (non-integer ``source``/``target``) are dropped by that remap
    when anything is dropped, and count as no reference.

    Args:
        entities: Entities pending commit (in extraction order: the position
            is the join key).
        relationships: Relationships pending commit, keyed by integer
            ``source``/``target`` indices into ``entities``.
        keep_evidence_less: Keep (flagged) an orphan that carries nothing
            too (``FilteringConfig.protect_orphans``).

    Returns:
        :class:`OrphanCommit`: the kept entities (input order), the remapped
        relationships (always a subset of the input), and the flagged and
        dropped orphan counts.
    """
    from chaoscypher_core.services.sources.engine.deduplication.service import (
        EntityProcessor,
    )

    referenced: set[int] = set()
    for rel in relationships:
        for key in ("source", "target"):
            idx = rel.get(key)
            if isinstance(idx, int) and idx >= 0:
                referenced.add(idx)

    # old_index -> new_index (None when dropped) and the kept list, in extraction order.
    index_mapping: dict[int, int | None] = {}
    kept: list[dict] = []
    flagged = 0
    for old_idx, entity in enumerate(entities):
        orphan = old_idx not in referenced
        if orphan and not keep_evidence_less and not carries_evidence(_entity_record(entity)):
            index_mapping[old_idx] = None
            continue
        flagged += orphan
        index_mapping[old_idx] = len(kept)
        kept.append(_with_orphan_flag(entity, orphan=orphan))

    dropped = len(entities) - len(kept)
    if dropped == 0:
        return OrphanCommit(kept, list(relationships), flagged, 0)
    remapped = EntityProcessor.remap_relationship_indices(relationships, index_mapping)
    return OrphanCommit(kept, remapped, flagged, dropped)


def normalize_relationship_endpoints(
    entities: list[dict],
    relationships: list[dict],
) -> list[dict]:
    """Convert string entity-id ``source``/``target`` endpoints to integer indices.

    The commit contract (``flag_orphan_entities`` and ``relation.py``'s
    ``_resolve_node_ids`` index path) expects each relationship's ``source`` /
    ``target`` to be an INTEGER index into ``entities``. The in-memory
    finalizer path honors that. But the relational-store reload path —
    ``list_source_relationships`` → ``_relationship_row_to_dict`` (migration
    0042) — projects ``source``/``target`` as STRING entity IDs
    (``source_entity_id`` / ``target_entity_id``). Feeding those straight to
    commit made the integer check in the orphan filter (now
    ``flag_orphan_entities``) fail for every relationship, so the referenced
    set was empty and 100% of entities were dropped as false orphans (empty graph for every committed source via the
    CLI ``source add`` and recovery re-commit paths).

    This maps id-keyed endpoints to the entity's position in ``entities``.
    Integer endpoints pass through unchanged, so the finalizer path is a
    no-op. An endpoint id not present in ``entities`` is left as-is — a single
    dangling reference must not silently re-index the whole relationship.

    Args:
        entities: Commit entities in order (position is the index contract).
        relationships: Relationships that may key endpoints by string id.

    Returns:
        A new relationship list with string-id ``source``/``target`` rewritten
        to integer indices; every other key (including ``from``/``to`` names)
        is preserved.
    """
    id_to_index: dict[str, int] = {
        entity["id"]: idx
        for idx, entity in enumerate(entities)
        if isinstance(entity, dict) and entity.get("id") is not None
    }
    if not id_to_index:
        return list(relationships)

    normalized: list[dict] = []
    for rel in relationships:
        new_rel = dict(rel)
        for key in ("source", "target"):
            value = new_rel.get(key)
            if isinstance(value, str) and value in id_to_index:
                new_rel[key] = id_to_index[value]
        normalized.append(new_rel)
    return normalized


if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from typing import Any, Protocol

    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.index import IndexingProtocol
    from chaoscypher_core.ports.retry import RetryPolicyPort
    from chaoscypher_core.ports.search import SearchRepositoryProtocol
    from chaoscypher_core.ports.storage_citations import CitationStorageProtocol
    from chaoscypher_core.ports.storage_sources import SourceStorageProtocol
    from chaoscypher_core.settings import EngineSettings

    class SourcesProtocol(SourceStorageProtocol, CitationStorageProtocol, Protocol):
        """Combined protocol for SourceCommitService — covers CRUD and citations."""

    class _CommitAdapterProtocol(Protocol):
        """Local protocol for the adapter surface used by SourceCommitService.

        Combines ``transaction()`` with the live session attribute and the
        post-commit search-retry queue. ``SqliteAdapter`` satisfies all three
        structurally; this protocol stays local because the broader
        ``TransactionalAdapterProtocol`` deliberately stays minimal.
        """

        session: Any  # SafeSession when inside transaction(), None otherwise.

        def transaction(self) -> AbstractContextManager[None]:
            """Open a unit-of-work; commits on exit unless an exception escapes."""
            ...

        def enqueue_pending_search_index(self, *, rows: list[dict[str, Any]]) -> None:
            """Append rows to the post-commit search-index retry queue."""
            ...


class SourceCommitService:
    """Orchestrates the source processing commit process.

    Coordinates:
    - Template creation from suggestions
    - Entity node creation with embeddings
    - Relationship edge creation
    - Source record creation
    - Citation tracking
    - Chunk promotion and indexing
    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        source_repository: SourceStorageProtocol,
        sources_repository: SourcesProtocol,
        indexing_repository: IndexingProtocol,
        search_repository: SearchRepositoryProtocol,
        settings: EngineSettings,
        reload_callback: Callable[[], None] | None = None,
        retry_policy: RetryPolicyPort | None = None,
        embedding_provider: EmbeddingProviderProtocol | None = None,
    ):
        """Initialize import commit service.

        Args:
            graph_repository: GraphRepository implementation
            source_repository: SourceStorageProtocol implementation
            sources_repository: SourcesProtocol implementation
            indexing_repository: IndexingProtocol implementation
            search_repository: SearchRepository for vector search indexing
            settings: Engine settings (provides current_database, batching config)
            reload_callback: Optional callback to trigger backend reload (backend-specific)
            retry_policy: Retry policy for SQLite-lock-sensitive commit path;
                defaults to :class:`DbLockRetryPolicy` when omitted.
            embedding_provider: Embedding provider for pre-commit reindex
                flush and template embedding; resolved lazily from
                ``settings`` on first use when omitted
                (:func:`~chaoscypher_core.adapters.embedding.embedding_provider_for`,
                so None with vector search off).

        """
        from chaoscypher_core.utils.retry import DbLockRetryPolicy

        self.graph_repository = graph_repository
        self.source_repository = source_repository
        self.sources_repository = sources_repository
        self.indexing_repository = indexing_repository
        self.search_repository = search_repository
        self.settings = settings
        self.database_name = settings.current_database
        self.reload_callback = reload_callback
        self._retry_policy: RetryPolicyPort = retry_policy or DbLockRetryPolicy()
        self._embedding_provider: EmbeddingProviderProtocol | None = embedding_provider
        self._embedding_provider_resolved: bool = embedding_provider is not None
        # Adapter reference for transaction() context manager.
        # At all production call sites, source_repository IS the SqliteAdapter
        # which implements both SourceStorageProtocol and the local
        # _CommitAdapterProtocol (transaction() boundary, live session,
        # search-retry queue). mypy cannot infer that from the
        # SourceStorageProtocol annotation, hence the ignore.
        self.adapter: _CommitAdapterProtocol = source_repository  # type: ignore[assignment]

        # Initialize handlers. ``EntityCommitHandler`` requires
        # ``EntityEmbeddingStorageProtocol`` (a wider surface that also
        # spans entity-embedding writes). At every production wire-site
        # ``source_repository`` is the ``SqliteAdapter`` which implements
        # both; mypy cannot prove that through the narrower
        # ``SourceStorageProtocol`` annotation, so we cast.
        entity_matcher = EntityTemplateMatcher(graph_repository)
        self.template_handler = TemplateCommitHandler(graph_repository)
        self.entity_handler = EntityCommitHandler(
            graph_repository,
            source_repository,  # type: ignore[arg-type]
            entity_matcher,
            self.database_name,
        )
        self.relationship_handler = RelationshipCommitHandler(graph_repository)

    @classmethod
    def from_engine(cls, engine: Any) -> SourceCommitService:
        """Create a SourceCommitService wired from an Engine instance.

        The one wiring of a commit service on an Engine (``Engine.commit_service``
        builds its own here). It takes the Engine's embedding provider: left to
        resolve its own from the settings, a second service built a second
        provider, and with the local provider a second copy of the model.

        Args:
            engine: Engine instance with storage_adapter, search_repository,
                settings, retry_policy and embedding_service (None when vector
                search is off).

        Returns:
            SourceCommitService with all dependencies injected.

        """
        from chaoscypher_core.repo_factories import get_transaction_graph_repository

        return cls(
            # on the adapter's session, never the Engine's own graph session:
            # the commit's graph and storage writes share one transaction
            graph_repository=get_transaction_graph_repository(
                engine.storage_adapter, engine.settings.current_database
            ),
            source_repository=engine.storage_adapter,
            sources_repository=engine.storage_adapter,
            indexing_repository=engine.storage_adapter,
            search_repository=engine.search_repository,
            settings=engine.settings,
            retry_policy=engine.retry_policy,
            embedding_provider=engine.embedding_service,
        )

    def _get_embedding_provider(self) -> EmbeddingProviderProtocol | None:
        """Return the injected embedding provider, or the one the settings call for.

        When the constructor received none, resolves once with
        ``embedding_provider_for(self.settings)`` (None when vector search is
        off: the commit then runs keyword-only) and caches the answer, so
        subsequent calls reuse it within this service.
        """
        if not self._embedding_provider_resolved:
            from chaoscypher_core.adapters.embedding import embedding_provider_for

            self._embedding_provider = embedding_provider_for(self.settings)
            self._embedding_provider_resolved = True
        return self._embedding_provider

    def _cleanup_previous_commit(self, file_id: str, source_id: str) -> dict[str, Any]:
        """Clean up any previously committed graph data for idempotent re-commit.

        Releases the source from the graph (``release_source_graph``, #795):
        its memberships go, and the nodes and edges no other source attests
        are deleted, with their search index entries. Nodes and edges another
        source still attests (merged across sources by identity resolution)
        survive, rebuilt from the sources they still have, so the re-commit
        writes this source's rows again beside them. ``stats["released"]``
        carries the release for :meth:`_refresh_released` after the commit.
        Also deletes the source's citations and resets chunk status to
        'indexed' so they can be re-promoted during the fresh commit.

        Args:
            file_id: Import file ID.
            source_id: Source ID (same as file_id in unified schema).

        Returns:
            Dict with had_previous_data flag and cleanup stats.

        """
        # Steps 1-3 are wrapped in a single transaction so that a failure in
        # any of the three deletes rolls back all of them. Without this, a
        # search-index failure after the graph delete would leave orphan
        # FTS5/vec rows for nodes that no longer exist.
        with self.adapter.transaction():
            # Step 1: Release this source's graph rows, on the adapter's own
            # session whatever repository the service was given (#795)
            from chaoscypher_core.repo_factories import get_transaction_graph_repository
            from chaoscypher_core.services.graph.management.release import release_source_graph

            released = release_source_graph(
                get_transaction_graph_repository(
                    self.adapter,  # type: ignore[arg-type]
                    self.database_name,
                ),
                source_id,
                sources=self.sources_repository,
                database_name=self.database_name,
                settings=self.settings,
            )
            deleted_node_ids = sorted(released.deleted_node_ids)

            # Step 2: Clean search indexes for deleted nodes.
            # Pass session= so the search delete joins the adapter's transaction
            # instead of opening its own connection and committing independently.
            search_removed = 0
            if deleted_node_ids:
                search_removed = self.search_repository.delete_nodes_batch(
                    deleted_node_ids, session=self.adapter.session
                )

            # Step 3: Delete citations for this source
            citation_stats = self.sources_repository.delete_citations_by_source(source_id)

        # Step 4: Reset chunk status back to 'indexed' for re-promotion
        self.indexing_repository.update_chunk_status(file_id, "indexed")

        graph_stats = {
            "nodes_deleted": len(released.deleted_node_ids),
            "edges_deleted": len(released.deleted_edge_ids),
            "nodes_kept": len(released.touched_node_ids),
            "edges_kept": len(released.touched_edge_ids),
            "nodes_rebuilt": len(released.stale_node_ids),
            "deleted_node_ids": deleted_node_ids,
        }
        had_previous_data = (
            bool(released.deleted_node_ids or released.touched_node_ids)
            or citation_stats.get("entity_citations_deleted", 0) > 0
        )

        stats = {
            "had_previous_data": had_previous_data,
            "released": released,
            "graph": graph_stats,
            "search_removed": search_removed,
            "citations": citation_stats,
        }

        if had_previous_data:
            logger.info(
                "idempotent_cleanup_completed",
                source_id=source_id,
                nodes_deleted=graph_stats["nodes_deleted"],
                edges_deleted=graph_stats["edges_deleted"],
                nodes_kept=graph_stats["nodes_kept"],
                edges_kept=graph_stats["edges_kept"],
                search_removed=search_removed,
                entity_citations_deleted=citation_stats.get("entity_citations_deleted", 0),
                relationship_citations_deleted=citation_stats.get(
                    "relationship_citations_deleted", 0
                ),
            )

        return stats

    def _update_progress(self, file_id: str, step: int, total: int, description: str) -> None:
        """Update processing progress for UI display.

        Liveness heartbeats for the source reconciler are NOT emitted
        here — they're handled centrally by the
        ``source_heartbeat`` async context manager that wraps the
        commit handler. This keeps the commit service free of recovery
        concerns and ensures any future caller (CLI, future handler)
        gets the same heartbeat behavior automatically.

        Args:
            file_id: Source processing file identifier
            step: Current step number (1-indexed)
            total: Total number of steps
            description: Human-readable description of current step

        """
        self.source_repository.update_step_progress(file_id, step, total, description)

    async def commit(
        self,
        file_id: str,
        commit_data: dict,
        file_info: dict[str, Any],
        auto_enable: bool = True,
    ) -> dict[str, Any]:
        """Public commit entry point with retry-on-db-lock.

        Structured into three phases:

        1. **PREP** (outside transaction): DB reads and LLM-adjacent calls are
           allowed here.  Chunk data is pre-fetched so the write phase only
           performs fast local writes.

        2. **WRITE** (inside ``adapter.transaction()``): All DB writes from
           ``start_commit`` through ``complete_commit`` are grouped into a
           single atomic transaction.  No LLM calls happen in this phase.
           If any write fails the transaction rolls back and the source row
           returns to its pre-commit state (``status='extracted'``), allowing
           the worker retry machinery to re-dispatch cleanly.

        3. **POST-TRANSACTION** (outside transaction): Template embedding runs
           after the transaction commits.  It is non-fatal — templates are
           already created; they just won't be semantically searchable until a
           background reindex runs if this step fails.

        The actual commit logic lives in ``_commit_impl``; this wrapper retries
        the whole idempotent operation if ``SQLITE_BUSY`` fires inside the
        ``adapter.transaction()`` block. ``SafeSession`` retries the final
        commit call, but busy errors can also occur earlier in the
        transactional write sequence.

        Args:
            file_id: Import file ID
            commit_data: Commit data with entities, relationships, templates
            file_info: Import file info dict
            auto_enable: Whether to enable the source immediately (visible in graph/search)

        Returns:
            ImportCommitResult dictionary with created node/edge/template IDs

        """
        return await self._retry_policy.run_async(
            self._commit_impl,
            file_id=file_id,
            commit_data=commit_data,
            file_info=file_info,
            auto_enable=auto_enable,
            operation_name="source_commit",
        )

    async def _commit_impl(  # noqa: PLR0915
        self,
        file_id: str,
        commit_data: dict,
        file_info: dict[str, Any],
        auto_enable: bool = True,
    ) -> dict[str, Any]:
        """Inner commit body. Must be idempotent — may be retried on lock.

        Args:
            file_id: Import file ID
            commit_data: Commit data with entities, relationships, templates
            file_info: Import file info dict
            auto_enable: Whether to enable the source immediately (visible in graph/search)

        Returns:
            ImportCommitResult dictionary with created node/edge/template IDs

        """
        # Use file_id as source_id (unified schema - source already exists from upload)
        source_id = file_id

        # Two-caller distinction for empty-entity payloads
        # (must happen BEFORE the transaction block — fast read-only checks).
        #
        # Caller B (stale recovery re-dispatch): source is already committed.
        #   commit_complete=True means the previous commit ran to completion.
        #   Skip without touching anything to preserve Cluster A's safety
        #   property: never delete prior graph data on a stale re-dispatch.
        #
        # Caller A (legitimate empty extraction): source is in extracted state,
        #   commit_complete=False, extraction genuinely produced 0 entities.
        #   Route to _commit_empty which does a zero-graph commit so the source
        #   transitions to committed and chunks remain visible for RAG/search.
        source_record = self.sources_repository.get_source(file_id, self.database_name)
        if source_record is None:
            logger.warning("commit_source_not_found", file_id=file_id)
            return {
                "skipped": "source_not_found",
                "created_nodes": [],
                "created_edges": [],
                "created_templates": [],
                "followups_complete": True,
            }

        # Caller B: already committed. Stale re-dispatch -- skip without touching anything.
        if source_record.get("commit_complete"):
            logger.info(
                "commit_skipped_already_committed",
                file_id=file_id,
                status=source_record.get("status"),
            )
            return {
                "skipped": "already_committed",
                "created_nodes": [],
                "created_edges": [],
                "created_templates": [],
                "followups_complete": True,
            }

        # Caller A: legitimate empty extraction. Do a zero-graph commit.
        if not commit_data.get("entities"):
            logger.info(
                "commit_empty_extraction",
                file_id=file_id,
            )
            return await self._commit_empty(
                file_id=file_id,
                file_info=file_info,
                auto_enable=auto_enable,
                had_prior_commit_data=(source_record.get("commit_nodes_created") or 0) > 0,
            )

        # === PREP PHASE (outside transaction — LLM calls allowed) ===

        # Drain any pending re-embedding queue before the transaction.
        # Must run outside the transaction because it makes LLM calls.
        await self._flush_pending_reindex()

        # Read chunk fetch limit from settings (respects user config)
        chunk_fetch_limit = self.settings.batching.chunk_fetch_limit

        # Pre-fetch chunks ONCE for all sub-methods that need them
        # (avoids 4 separate DB round-trips for the same data)
        _chunk_result = self.indexing_repository.get_chunks_by_source(
            file_id, page=1, page_size=chunk_fetch_limit
        )
        all_chunks = _chunk_result[0] if isinstance(_chunk_result, tuple) else _chunk_result

        # Build shared mappings used by multiple sub-methods
        total_content_length = sum(len(c.get("content", "")) for c in all_chunks)
        # Citations name their chunk directly (``cited_chunk_id``, resolved at
        # aggregation); the commit only checks it is one of this source's
        # stored chunks. ``chunk_index`` on an extracted item is an
        # extraction-group position and is never looked up as a chunk index.
        stored_chunk_ids: set[str] = {c["id"] for c in all_chunks if c.get("id")}

        # === WRITE PHASE (inside transaction — no LLM calls) ===
        # Declare before the block so post-transaction code can access them.
        all_created_templates: list[str] = []
        created_nodes: list[Any] = []
        created_edges: list[Any] = []
        released: ReleasedRows | None = None
        reattested_nodes: set[str] = set()
        reattested_edges: set[str] = set()

        # Only run idempotent cleanup when a prior commit attempt actually
        # wrote graph data. For first-attempt commits the three DELETE
        # statements in _cleanup_previous_commit are no-ops, but they still
        # fight the SQLite writer lock under concurrent load and have been
        # observed to fail commit even on fresh sources. commit_nodes_created
        # is only incremented inside complete_commit (last write of a
        # successful commit transaction), so a non-zero value is the only
        # signal that a previous attempt left data behind.
        had_prior_commit_data = (source_record.get("commit_nodes_created") or 0) > 0

        # Progress updates (_update_progress) inside the transaction flush but
        # don't commit. The UI polls from a separate connection and won't see
        # step-by-step progress until the transaction completes. This is an
        # accepted UX regression in exchange for atomicity; a follow-up cluster
        # should route progress writes through a separate session so they
        # commit eagerly.
        with self.adapter.transaction():
            # Step 0: Mark commit stage as started
            self.source_repository.start_commit(file_id)

            # Idempotent cleanup: only run on re-commit. Skipped for first
            # attempts so the hot path never issues DELETEs that could
            # contend with concurrent extraction writes.
            if had_prior_commit_data:
                cleanup_stats = self._cleanup_previous_commit(file_id, source_id)
                released = cleanup_stats["released"]
                if cleanup_stats["had_previous_data"]:
                    logger.info("commit_retry_detected", file_id=file_id, source_id=source_id)
            else:
                logger.debug(
                    "commit_first_attempt_cleanup_skipped",
                    file_id=file_id,
                    source_id=source_id,
                )

            # Step 1: Create suggested templates (with source_id for per-source templates).
            # A memory's offered templates that still exist bind its stamped entities in
            # step 4; the suggestions only those entities needed are not created (D26).
            offered_templates = live_offered_templates(
                self.graph_repository, commit_data.get(OFFERED_TEMPLATES_KEY) or {}
            )
            suggested_templates = drop_bound_suggestions(commit_data, offered_templates)
            self._update_progress(file_id, 1, 8, "Creating templates")
            (
                created_templates,
                template_name_to_id,
                _all_used_templates,
                node_templates_inserted,
            ) = await self.template_handler.create_suggested_templates(
                {**commit_data, "suggested_templates": suggested_templates}, source_id=source_id
            )

            # Step 2: Reload all templates (including newly-created ones)
            self._update_progress(file_id, 2, 8, "Loading templates")
            all_templates = self.graph_repository.list_templates(template_type="node")
            logger.info(
                "node_templates_loaded_for_entity_matching", template_count=len(all_templates)
            )

            # Step 3: Create Source record first (before nodes, to satisfy FK constraint)
            # Nodes/edges will reference this source_id for enabled filtering
            self._update_progress(file_id, 3, 8, "Creating source record")
            await self._create_source_and_promote_chunks(
                file_id=file_id,
                source_id=source_id,
                enabled=auto_enable,
                total_content_length=total_content_length,
                chunk_count=len(all_chunks),
            )

            # Step 4: Prepare and batch create entity nodes.
            # First the orphan rule (``flag_orphan_entities``): an entity no relationship
            # references is kept flagged ``orphan`` when it carries evidence, and dropped
            # only when it carries nothing and FilteringConfig.protect_orphans is off.
            _raw_entities = commit_data.get("entities", [])
            # Reload paths (CLI ``source add``, recovery re-commit) rebuild
            # commit_data straight from ``list_source_relationships``, whose
            # ``source``/``target`` carry STRING entity ids. The orphan filter
            # and edge resolver below key on INTEGER indices, so normalize
            # id-keyed endpoints first — otherwise every entity is dropped as a
            # false orphan and the graph commits empty.
            _raw_relationships = normalize_relationship_endpoints(
                _raw_entities, commit_data.get("relationships", [])
            )
            # The queued commit task's ``file_info`` is ``adapter.get_file(...)``
            # (``extraction_finalizer._queue_commit_phase``), whose narrow
            # ``load_only`` projection carries neither ``filtering_mode`` nor
            # ``protect_orphans`` — and recovery's rebuilt file_info carries
            # neither either. Reading the mode from ``file_info`` alone
            # therefore always fell through to the engine default, so a
            # ``minimal``/``unfiltered`` source (or one with
            # ``protect_orphans=True``) had its orphans dropped here even
            # though the finalizer honoured the row. Mirror the finalizer's
            # cascade instead: payload > source row > engine default for the
            # mode, and the row's explicit ``protect_orphans`` choice beats
            # the preset's value (``source_record`` is ``get_source``, every
            # non-heavy column).
            _raw_mode: str | None = file_info.get("filtering_mode") or source_record.get(
                "filtering_mode"
            )
            _filtering_mode: str = _raw_mode or self.settings.extraction.extraction_filtering_mode
            _row_protect_orphans = source_record.get("protect_orphans")
            _source_overrides: dict[str, Any] | None = (
                {"protect_orphans": _row_protect_orphans}
                if isinstance(_row_protect_orphans, bool)
                else None
            )
            _filtering_config = resolve_filtering_config(
                mode=_filtering_mode, source_overrides=_source_overrides
            )
            _orphans = flag_orphan_entities(
                _raw_entities,
                _raw_relationships,
                keep_evidence_less=_filtering_config.protect_orphans,
            )
            _entities_for_commit = _orphans.entities
            _relationships_for_commit = _orphans.relationships
            if _orphans.flagged or _orphans.dropped:
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                if _orphans.flagged:  # kept, flagged (until 2026-10-07: the ones deleted)
                    await increment_quality_counter(
                        adapter=self.source_repository,
                        source_id=file_id,
                        database_name=self.database_name,
                        counter=QualityCounter.ORPHAN_ENTITIES_FILTERED,
                        n=_orphans.flagged,
                    )
                if _orphans.dropped:  # carrying neither a description nor an evidence sentence
                    await increment_quality_counter(
                        adapter=self.source_repository,
                        source_id=file_id,
                        database_name=self.database_name,
                        counter=QualityCounter.EVIDENCE_LESS_ENTITIES_DROPPED,
                        n=_orphans.dropped,
                    )
                logger.info(
                    "orphan_entities_committed",
                    file_id=file_id,
                    flagged=_orphans.flagged,
                    dropped_evidence_less=_orphans.dropped,
                    kept=len(_entities_for_commit),
                    pre_filter=len(_raw_entities),
                    mode=_filtering_mode,
                )

            # Build an effective commit_data view with the filtered entity list AND the
            # remapped relationships so that citation helpers and edge prep — which key
            # into entities by positional index — stay aligned with the node creation
            # order produced by prepare_entity_nodes below. Workstream 3, Tasks 3.3+3.4:
            # without remapping, an edge into kept-but-shifted entities silently
            # disappeared at commit time.
            _commit_data_for_citations = {
                **commit_data,
                "entities": _entities_for_commit,
                "relationships": _relationships_for_commit,
            }

            self._update_progress(file_id, 4, 8, "Preparing entities")
            nodes_to_create, entity_data_list, _all_entity_template_ids = (
                self.entity_handler.prepare_entity_nodes(
                    _entities_for_commit,
                    all_templates,
                    suggested_templates,
                    template_name_to_id,
                    file_info,
                    file_id,
                    source_id,
                    offered_templates=offered_templates,
                )
            )

            # Step 5: Batch create nodes and build mappings
            self._update_progress(file_id, 5, 8, "Creating nodes")
            (
                created_nodes,
                entity_index_to_node_id,
                entity_name_to_node_id,
                entity_index_to_node,  # Node objects for citation creation (avoids redundant lookups)
                nodes_actually_inserted,
            ) = await self.entity_handler.batch_create_nodes(
                nodes_to_create, entity_data_list, reattested=reattested_nodes
            )

            # Collect nodes for post-transaction search indexing.
            # SearchRepository uses its own engine connections rather than the
            # adapter's session, so writing to FTS5 / sqlite-vec inside this
            # transaction would self-deadlock on the writer lock we already
            # hold — every such write waits the full busy_timeout and then
            # silently fails. Indexing is already best-effort (errors are
            # swallowed); defer it to the POST-TRANSACTION PHASE below.
            nodes_to_index = list(entity_index_to_node.values())

            # Step 5b: Create source_citations linking entities to source chunks.
            # Pass _commit_data_for_citations (entities replaced with filtered list) so
            # positional indices from entity_index_to_node_id align with the entity
            # objects that _create_source_citations reads chunk_index from.
            await self._create_source_citations(
                file_id=file_id,
                source_id=source_id,
                entity_index_to_node_id=entity_index_to_node_id,
                entity_index_to_node=entity_index_to_node,
                commit_data=_commit_data_for_citations,
                stored_chunk_ids=stored_chunk_ids,
            )

            # Step 6: Create relationship edges (also returns any newly created edge templates)
            self._update_progress(file_id, 6, 8, "Preparing relationships")

            # Build edge description and visuals maps from suggested_edge_templates (domain-aware)
            edge_descriptions: dict[str, str] = {}
            edge_visuals: dict[str, dict[str, str | None]] = {}
            for et in commit_data.get("suggested_edge_templates", []):
                name = et.get("name", "").lower()
                desc = et.get("description", "")
                if name and desc:
                    edge_descriptions[name] = desc
                if name and (et.get("icon") or et.get("color")):
                    edge_visuals[name] = {"icon": et.get("icon"), "color": et.get("color")}

            # Get inverse relationship map from extraction results (domain-specific)
            inverse_relationships: dict[str, str] = commit_data.get("inverse_relationships", {})

            # One fact, one edge: orient symmetric and inverse-pair relationships
            # by node id (the rule the relationship dedup applies), so a fact the
            # LLM wrote both ways in two groups commits as one edge. Edge creation
            # and the citation match below both read the oriented list.
            _relationships_for_commit = RelationshipCommitHandler.orient_relationships_for_commit(
                _relationships_for_commit,
                symmetric_relationships=commit_data.get("symmetric_relationships"),
                inverse_relationships=inverse_relationships,
                entity_name_to_node_id=entity_name_to_node_id,
                entity_index_to_node_id=entity_index_to_node_id,
            )

            # Phase 6 (2026-05-08): resolve enable_inverse_relationships via 3-layer cascade.
            # Cascade: per-source (nullable bool on source row) → global ExtractionSettings.
            # NULL on the source row means "use the global default" (True by default).
            _row_inverse: bool | None = (
                source_record.get("enable_inverse_relationships")
                if source_record is not None
                else None
            )
            _effective_inverse_relationships: bool = (
                _row_inverse
                if isinstance(_row_inverse, bool)
                else self.settings.extraction.enable_inverse_relationships
            )

            (
                edges_to_create,
                created_edge_templates,
                _all_used_edge_templates,
                edge_templates_inserted,
            ) = await self.relationship_handler.prepare_relationship_edges(
                _relationships_for_commit,
                entity_name_to_node_id,
                entity_index_to_node_id,
                source_id,
                edge_descriptions=edge_descriptions,
                edge_visuals=edge_visuals,
                inverse_relationships=inverse_relationships,
                enable_inverse_relationships=_effective_inverse_relationships,
            )

            # Step 7: Batch create edges
            self._update_progress(file_id, 7, 8, "Creating relationships")
            (
                created_edges,
                edges_actually_inserted,
            ) = await self.relationship_handler.batch_create_edges(
                edges_to_create, reattested=reattested_edges
            )

            # Step 7a: the reused rows this source attests again (the kept side of a
            # cross-source merge that outlived this source's release) fold its reading
            # back in, through the release's own rebuild (#795).
            released = self._rebuild_reattested(
                source_id, reattested_nodes, reattested_edges, released
            )

            # Step 7b: Create relationship citations linking edges to source chunks
            await self._create_relationship_citations(
                file_id=file_id,
                source_id=source_id,
                created_edges=created_edges,
                edges_to_create=edges_to_create,
                relationships=_relationships_for_commit,
                entity_index_to_node=entity_index_to_node,
                stored_chunk_ids=stored_chunk_ids,
                entity_index_to_node_id=entity_index_to_node_id,
                entity_name_to_node_id=entity_name_to_node_id,
            )

            # Step 8: Update chunk status to 'committed'. Vector-search
            # indexing of the chunks is deferred to the post-transaction
            # phase for the same reason as node indexing above.
            self._update_progress(file_id, 8, 8, "Finalizing commit")
            await self._update_chunk_status(file_id, "committed")

            # Combine all created templates for reporting (used by complete_commit and
            # the post-transaction embedding step)
            all_created_templates = created_templates + created_edge_templates

            # Mark commit stage as complete — atomic with all preceding writes.
            templates_actually_inserted = node_templates_inserted + edge_templates_inserted
            self.source_repository.complete_commit(
                source_id=file_id,  # file_id == source_id in unified schema
                nodes_created=nodes_actually_inserted,
                edges_created=edges_actually_inserted,
                templates_created=templates_actually_inserted,
            )

            # Clear commit_payload as the LAST write inside the transaction so
            # the payload-discard and the status flip to COMMITTED ride one
            # SQLite commit. Folding this in here removes the need for the
            # outer transaction at import_service._run_commit (the 2026-05-20
            # writer-lock-contention root cause): keeping the payload-clear
            # inside the inner txn preserves the "succeed-together / fail-
            # together with commit writes" invariant without holding the
            # writer lock across the post-transaction LLM embedding await.
            self.source_repository.clear_source_commit_payload(file_id, self.database_name)
        # Transaction committed atomically here. Any exception that escapes
        # the with-block propagates to the caller (the neuron worker's retry
        # handler) with the database rolled back to its pre-transaction state.

        # === POST-TRANSACTION PHASE (outside transaction — LLM calls allowed) ===

        # Index nodes and chunks in search (FTS5 + sqlite-vec). Run outside
        # the commit transaction because SearchRepository opens its own
        # SQLite connections via engine.connect() and cannot participate in
        # the adapter's session. Running these inside the transaction made
        # each write wait the full busy_timeout against the writer lock we
        # held ourselves. Best-effort: a failure here means search indexes
        # lag until the next rebuild task, but the graph data is safe.
        #
        # We pass session=self.adapter.session so search writes share
        # the adapter's connection. Under concurrent load this converts
        # SQLite busy_timeout waits (60s per stall) into asyncio queuing
        # (microseconds). We also explicitly commit after each block so
        # the writes land in SQLite immediately instead of lingering in
        # the session's auto-begun transaction — and rollback on failure
        # so a bad search write doesn't poison the session for the code
        # that runs next (e.g. _update_progress).
        import contextlib

        from chaoscypher_core.services.quality.counters import (
            mark_search_indexing_degraded,
            mark_search_indexing_indexed,
            mark_search_indexing_pending,
        )

        session = self.adapter.session

        # Confirm pending status as we enter the indexing phase. Covers
        # the re-commit path where the row may carry a stale ``indexed``
        # or ``failed`` from a prior attempt.
        mark_search_indexing_pending(
            adapter=self.sources_repository,
            source_id=source_id,
            database_name=self.database_name,
        )

        # Track failures across both indexing calls so the final status
        # write reflects the worst outcome (indexed iff both succeed).
        indexing_failed = False

        if nodes_to_index:
            try:
                self.search_repository.index_nodes_batch(nodes_to_index, session=session)
                session.commit()
                logger.info(
                    "nodes_indexed_for_search",
                    count=len(nodes_to_index),
                    file_id=file_id,
                )
            except Exception:
                with contextlib.suppress(Exception):
                    session.rollback()
                logger.exception(
                    "nodes_search_indexing_failed_enqueuing_retry",
                    count=len(nodes_to_index),
                    file_id=file_id,
                )
                # The enqueue opens a fresh transaction right after a
                # rollback, under the same lock contention that just broke
                # indexing — if it raises, the exception would escape
                # _commit_impl, the DbLockRetryPolicy retry would hit the
                # commit_complete early-return, and the retry row (plus the
                # degraded mark below) would be lost forever, leaving the
                # source stuck at vector_indexing_status="pending".
                try:
                    self._enqueue_search_retry(
                        [n.id for n in nodes_to_index],
                        source_id=source_id,
                        kind="node",
                    )
                except Exception:
                    logger.exception(
                        "node_search_retry_enqueue_failed",
                        file_id=file_id,
                    )
                indexing_failed = True

        try:
            await self._index_chunks_to_vector_search(file_id, session=session)
            session.commit()
        except Exception:
            with contextlib.suppress(Exception):
                session.rollback()
            logger.exception(
                "chunks_vector_indexing_failed_enqueuing_retry",
                file_id=file_id,
            )
            # Same guard as the node block above — a failed enqueue must
            # not escape past the commit_complete point.
            try:
                self._enqueue_search_retry(
                    [file_id],
                    source_id=source_id,
                    kind="chunk",
                )
            except Exception:
                logger.exception(
                    "chunk_search_retry_enqueue_failed",
                    file_id=file_id,
                )
            indexing_failed = True

        if indexing_failed:
            mark_search_indexing_degraded(
                adapter=self.sources_repository,
                source_id=source_id,
                database_name=self.database_name,
            )
        else:
            mark_search_indexing_indexed(
                adapter=self.sources_repository,
                source_id=source_id,
                database_name=self.database_name,
            )

        # Generate embeddings for newly created templates (node + edge) so they
        # are semantically searchable immediately after commit. Non-fatal: a
        # failure here only means templates won't appear in semantic search
        # until a background reindex runs. The embedding LLM calls happen
        # before any session-mode writes inside _embed_created_templates,
        # so no transaction is held across the await.
        if all_created_templates:
            try:
                await self._embed_created_templates(all_created_templates, session=session)
                session.commit()
            except Exception:
                with contextlib.suppress(Exception):
                    session.rollback()
                logger.exception(
                    "template_embedding_failed_non_fatal",
                    count=len(all_created_templates),
                    file_id=file_id,
                )

        scope = {str(nid) for nid in entity_index_to_node_id.values() if nid}
        if released is not None:
            if self.settings.graph.resolve_identities:
                # The release's scoped resolution joins this commit's own pass below,
                # under conventions that read this source's packs again.
                scope |= released.resolve_node_ids
                released = dataclasses.replace(released, resolve_node_ids=frozenset())
            await self._refresh_released(released)

        # Best-effort follow-ups: the commit stands when either fails, and the result
        # says so, so a memory's link step can finish them (``finish_memory``).
        followups_complete = True
        if self.settings.graph.resolve_identities:
            followups_complete = await self._resolve_identities(
                file_id, scope=scope, join=commit_data.get(OFFERED_NODES_KEY) or ()
            )
        followups_complete = self._supersede_memory(file_id) and followups_complete

        # Clear progress after completion
        self._update_progress(file_id, 0, 0, "")

        # Trigger graph reload in backend (if callback provided)
        if self.reload_callback:
            try:
                self.reload_callback()
            except Exception:
                logger.exception("graph_reload_failed_after_commit", file_id=file_id)

        # Return results using standardized keys matching the cortex caller
        # (import_service.py) and the empty-entities early-return above.
        return {
            "created_nodes": created_nodes,
            "created_edges": created_edges,
            "created_templates": all_created_templates,
            "followups_complete": followups_complete,
        }

    def _graph_packs(self, file_id: str) -> tuple[str, ...]:
        """The union of the packs the graph's sources extracted with, ``file_id``'s included, sorted.

        :func:`~chaoscypher_core.services.graph.management.release.graph_packs`,
        the lookup the rebuild after a source release shares.
        """
        from chaoscypher_core.services.graph.management.release import graph_packs

        return graph_packs(
            self.graph_repository,
            self.sources_repository,
            self.database_name,
            include=[file_id],
        )

    def _rebuild_reattested(
        self,
        source_id: str,
        nodes: set[str],
        edges: set[str],
        released: ReleasedRows | None,
    ) -> ReleasedRows | None:
        """Rebuild the reused rows ``source_id`` attests again; fold the changed ones into ``released``.

        ``release.rebuild_from_members`` under the graph's conventions (this
        source's included), in the commit's transaction. The nodes whose text
        changed join ``stale_node_ids`` for :meth:`_refresh_released`; the
        post-commit identity pass already covers them (they are this commit's
        nodes).
        """
        if not (nodes or edges):
            return released
        from chaoscypher_core.services.graph.management.release import (
            graph_conventions,
            rebuild_from_members,
        )

        conventions = graph_conventions(
            self.graph_repository,
            self.sources_repository,
            self.database_name,
            self.settings,
            include=[source_id],
        )
        rebuilt = rebuild_from_members(self.graph_repository, nodes, edges, conventions)
        logger.info(
            "commit_reattested_rows_rebuilt",
            source_id=source_id,
            nodes=len(nodes),
            edges=len(edges),
            nodes_changed=len(rebuilt.changed_node_ids),
        )
        base = released or ReleasedRows()
        return dataclasses.replace(
            base, stale_node_ids=base.stale_node_ids | rebuilt.changed_node_ids
        )

    async def _refresh_released(self, released: ReleasedRows) -> None:
        """After the commit: the re-commit's release resolved, re-embedded and re-indexed.

        ``refresh_released_graph``: best-effort, logged; the commit stands.
        """
        from chaoscypher_core.services.graph.management.release import refresh_released_graph

        if not (released.stale_node_ids or released.resolve_node_ids or released.deleted_node_ids):
            return
        try:
            embedder = self._get_embedding_provider()
        except Exception:
            logger.exception("released_graph_embedder_unavailable_non_fatal")
            embedder = None
        await refresh_released_graph(
            self.graph_repository,
            released,
            embedder=embedder,
            search=self.search_repository,
        )

    async def finish_memory(self, source_id: str, *, join: Collection[str] = ()) -> bool:
        """Run a committed memory's follow-ups again: the identity pass over its nodes, then supersession.

        For a memory whose commit stood but whose follow-ups failed or never ran (the
        process ended after the commit). Both steps are idempotent. The identity pass
        is scoped to the nodes the memory attests and runs only when
        ``settings.graph.resolve_identities`` is on, as after a commit; ``join`` are
        the offered nodes with no type it joins by name (:meth:`_resolve_identities`).
        True when both ran without failing.
        """
        resolved = True
        if self.settings.graph.resolve_identities:
            scope = set(self.graph_repository.member_node_ids(source_id))
            resolved = await self._resolve_identities(source_id, scope=scope, join=join)
        return self._supersede_memory(source_id) and resolved

    def _supersede_memory(self, file_id: str) -> bool:
        """The supersession step of a memory source (spec 2026-10-20 decision 6), after the identity pass.

        Read from the source row's ``source_type``: a document runs nothing. The
        single-valued relations are the memory path's set plus the graph's
        vocabulary flags. Non-fatal: the commit stands if the step fails. False
        when the step raised; True when it ran, and for a document.
        """
        from chaoscypher_core.models import is_memory_source
        from chaoscypher_core.services.graph.memory.supersession import (
            single_valued_labels,
            supersede_memory_edges,
        )

        source = self.source_repository.get_source(file_id, self.database_name)
        if not source or not is_memory_source(source):
            return True
        try:
            closures = supersede_memory_edges(
                self.graph_repository,
                file_id,
                single_valued=single_valued_labels(self._relation_vocabulary()),
            )
        except Exception:
            logger.exception("memory_supersession_failed", source_id=file_id)
            return False
        if closures:
            logger.info("memory_supersession_done", source_id=file_id, closures=len(closures))
        return True

    def _relation_vocabulary(self) -> Any:
        """The graph's relation vocabulary (its sources' packs), for the single-valued flags."""
        from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
            EMPTY_RELATION_VOCABULARY,
        )

        return EMPTY_RELATION_VOCABULARY

    async def _resolve_identities(
        self, file_id: str, scope: set[str] | None = None, *, join: Collection[str] = ()
    ) -> bool:
        """The post-commit identity pass (``settings.graph.resolve_identities``).

        ``join`` are the nodes with no type a memory's extraction was offered: after
        the resolver, a node the source attests whose label names one of them exactly
        is folded into it (``offered_nodes.join_offered_nodes``; the resolver's type
        gate never compares a node with no type). Empty for a document.

        Runs the resolver under the naming conventions of the graph's
        sources (the floor's tables and those of :meth:`_graph_packs`, the
        union of every source's packs: the ones a one-source graph's
        deduplication used), over ``scope`` (the nodes the commit wrote) and
        every node sharing a naming word, a label or an alias with them
        (``plan_resolution(..., scope=)``; the plan reads the whole graph so
        every guard counts every holder; None resolves the whole graph), then
        re-embeds the nodes whose text changed (``release.refresh_node_search``,
        the step the rebuild after a source release shares) so the vector
        index follows; with vector search off it re-indexes them for keywords.

        A graph whose sources chose different packs is resolved under the
        union of their packs (spec 2026-10-07 § 6): a pack only adds titles,
        forms and words to the floor's and cannot contradict it, so the union
        is one coherent set of tables. Until
        2026-10-07 such a graph was skipped
        (``identity_resolution_skipped_mixed_domains``). Non-fatal: the commit
        stands if the pass fails, and the next commit retries it. False when
        the pass or its re-embedding raised.
        """
        from chaoscypher_core.services.graph.management.release import refresh_node_search
        from chaoscypher_core.services.graph.memory.offered_nodes import join_offered_nodes
        from chaoscypher_core.services.graph.resolution import resolve_graph
        from chaoscypher_core.services.graph.resolution.apply import ResolutionReport
        from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
            conventions_of_packs,
        )

        report = ResolutionReport()
        reembedded = 0
        packs: tuple[str, ...] = ()
        failed = False
        try:
            packs = self._graph_packs(file_id)
            resolve_graph(
                self.graph_repository,
                conventions=conventions_of_packs(packs, self.settings),
                search=self.search_repository,
                scope=scope,
                report=report,
            )
            join_offered_nodes(
                self.graph_repository,
                file_id,
                join,
                search=self.search_repository,
                report=report,
            )
        except Exception:
            # the merge groups that committed before the failure are in the graph (and
            # out of the index): their kept nodes are still re-embedded below
            logger.exception("identity_resolution_failed_non_fatal", file_id=file_id)
            failed = True
        try:
            if report.changed_node_ids:
                reembedded = await refresh_node_search(
                    self.graph_repository,
                    sorted(report.changed_node_ids),
                    self._get_embedding_provider(),
                    search=self.search_repository,  # the vector rows follow too
                )
        except Exception:
            logger.exception("identity_resolution_reembed_failed_non_fatal", file_id=file_id)
            return False
        if failed:
            return False
        logger.info(
            "identity_resolution_applied",
            file_id=file_id,
            packs=list(packs),
            scoped=scope is not None,
            merged=len(report.merged),
            edges_moved=report.edges_moved,
            edges_dropped=report.edges_dropped,
            edges_folded=report.edges_folded,
            aliases_dropped=len(report.aliases_dropped),
            flagged=report.flagged,
            reembedded=reembedded,
        )
        return True

    async def _commit_empty(
        self,
        file_id: str,
        file_info: dict[str, Any],
        auto_enable: bool,
        had_prior_commit_data: bool = False,
    ) -> dict[str, Any]:
        """Commit a source with zero entities extracted.

        Used when extraction legitimately found no entities (pure prose,
        math-heavy doc, etc.). Transitions the source to committed with
        no graph writes. Chunks are still promoted so they remain visible
        in RAG/search.

        Idempotent: cleanup runs only when a prior attempt actually wrote
        graph data (``had_prior_commit_data=True``). First-attempt commits
        skip cleanup to avoid the DELETEs fighting the SQLite writer lock
        under concurrent load.

        Args:
            file_id: Source file ID (same as source_id in unified schema).
            file_info: File info dict (used for any callers that need it downstream).
            auto_enable: Whether to enable the source immediately.
            had_prior_commit_data: True when source_record.commit_nodes_created > 0,
                meaning a previous commit attempt left graph data behind.

        Returns:
            Commit result dict with empty lists and empty_extraction=True.

        """
        source_id = file_id

        chunk_fetch_limit = self.settings.batching.chunk_fetch_limit
        _chunk_result = self.indexing_repository.get_chunks_by_source(
            file_id, page=1, page_size=chunk_fetch_limit
        )
        all_chunks = _chunk_result[0] if isinstance(_chunk_result, tuple) else _chunk_result
        total_content_length = sum(len(c.get("content", "")) for c in all_chunks)

        released: ReleasedRows | None = None
        with self.adapter.transaction():
            self.source_repository.start_commit(file_id)
            if had_prior_commit_data:
                released = self._cleanup_previous_commit(file_id, source_id)["released"]

            await self._create_source_and_promote_chunks(
                file_id=file_id,
                source_id=source_id,
                enabled=auto_enable,
                total_content_length=total_content_length,
                chunk_count=len(all_chunks),
            )

            await self._update_chunk_status(file_id, "committed")

            self.source_repository.complete_commit(
                source_id=file_id,
                nodes_created=0,
                edges_created=0,
                templates_created=0,
            )

            # Atomic with complete_commit — see the matching comment in
            # ``_commit_impl``. Folds payload-clear into the inner txn so the
            # outer transaction at ``import_service._run_commit`` can be
            # removed (2026-05-20 writer-lock-contention root fix).
            self.source_repository.clear_source_commit_payload(file_id, self.database_name)

        if released is not None:
            await self._refresh_released(released)

        # Vector-search indexing runs outside the transaction, but passes
        # the adapter's session so the write shares the connection
        # (converting SQLite busy_timeout waits into asyncio queuing
        # under concurrent load). Explicit commit lands the write; explicit
        # rollback on failure clears any dirty session state before the
        # later _update_progress call.
        import contextlib

        from chaoscypher_core.services.quality.counters import (
            mark_search_indexing_degraded,
            mark_search_indexing_indexed,
            mark_search_indexing_pending,
        )

        session = self.adapter.session

        # Confirm pending status as we enter the indexing phase (covers
        # the re-commit path where the row may carry stale state).
        mark_search_indexing_pending(
            adapter=self.sources_repository,
            source_id=source_id,
            database_name=self.database_name,
        )

        try:
            await self._index_chunks_to_vector_search(file_id, session=session)
            session.commit()
            mark_search_indexing_indexed(
                adapter=self.sources_repository,
                source_id=source_id,
                database_name=self.database_name,
            )
        except Exception:
            with contextlib.suppress(Exception):
                session.rollback()
            logger.exception(
                "commit_empty_chunk_vector_indexing_failed_enqueuing_retry",
                file_id=file_id,
            )
            # Same guard as _commit_impl's indexing blocks — a failed
            # enqueue must not escape past the commit_complete point, or
            # the retrying commit() hits the early-return and the source
            # is stuck at vector_indexing_status="pending" forever.
            try:
                self._enqueue_search_retry(
                    [file_id],
                    source_id=source_id,
                    kind="chunk",
                )
            except Exception:
                logger.exception(
                    "commit_empty_search_retry_enqueue_failed",
                    file_id=file_id,
                )
            mark_search_indexing_degraded(
                adapter=self.sources_repository,
                source_id=source_id,
                database_name=self.database_name,
            )

        logger.info(
            "commit_empty_succeeded",
            file_id=file_id,
            chunk_count=len(all_chunks),
        )

        # Clear progress after completion
        self._update_progress(file_id, 0, 0, "")

        # Trigger graph reload in backend (if callback provided)
        if self.reload_callback:
            try:
                self.reload_callback()
            except Exception:
                logger.exception("commit_empty_graph_reload_callback_failed", file_id=file_id)

        return {
            "created_nodes": [],
            "created_edges": [],
            "created_templates": [],
            "empty_extraction": True,
            "followups_complete": True,
        }

    async def _create_source_and_promote_chunks(
        self,
        file_id: str,
        source_id: str,
        enabled: bool = True,
        total_content_length: int = 0,
        chunk_count: int = 0,
    ) -> None:
        """Update the Source record for commit (unified schema - source already exists).

        With unified schema, the source was created at upload and already has all metadata.
        This method just updates commit-related fields: total_content_length, enabled.

        Args:
            file_id: Import file ID (same as source_id in unified schema)
            source_id: Source ID (same as file_id in unified schema)
            enabled: Whether the source is enabled (visible in graph/search)
            total_content_length: Pre-computed total content length from chunks
            chunk_count: Pre-computed chunk count

        """
        logger.info("updating_source_for_commit", file_id=file_id, source_id=source_id)

        if chunk_count == 0:
            logger.warning("no_chunks_found_for_file", file_id=file_id)

        # Update existing source with commit-related fields
        # (source_id == file_id in unified schema)
        source_update = {
            "id": source_id,
            "total_content_length": total_content_length,
            "enabled": enabled,
        }
        self.sources_repository.update_source(source_id, source_update)

        logger.info(
            "source_updated_for_commit",
            source_id=source_id,
            chunk_count=chunk_count,
            enabled=enabled,
        )

    async def _create_source_citations(
        self,
        file_id: str,
        source_id: str,
        entity_index_to_node_id: dict[int, str],
        entity_index_to_node: dict[int, Any],
        commit_data: dict,
        stored_chunk_ids: set[str],
    ) -> None:
        """Create source_citations linking entities to the chunks that cite them.

        One row per distinct chunk an entity is cited in (:func:`entity_citation_chunks`):
        a deduplicated entity carries the union of its mentions' evidence, so
        an entity cited in three groups gets three rows and the provenance
        arm reaches every chunk, not only the first. Each row's snippet and
        metadata are that chunk's cited sentences; the model's ``sent_ref``
        numbers one group's text and rides on the ``cited_chunk_id`` row only.

        Args:
            file_id: Import file ID
            source_id: Created source ID
            entity_index_to_node_id: Mapping from entity index to created node ID
            entity_index_to_node: Mapping from entity index to Node object (avoids lookups)
            commit_data: Commit data containing the extracted entities
            stored_chunk_ids: Ids of this source's stored chunks

        """
        entities = commit_data.get("entities", [])

        if not stored_chunk_ids:
            logger.warning("no_chunks_found_skipping_source_citations", file_id=file_id)
            return

        logger.info(
            "creating_source_citations",
            entity_count=len(entity_index_to_node_id),
            chunk_count=len(stored_chunk_ids),
        )

        # Create citations: one per (entity, chunk its evidence is in)
        citations_to_create = []
        citations_skipped_no_chunk_index = 0
        citations_skipped_index_not_mapped = 0

        for entity_idx, node_id in entity_index_to_node_id.items():
            if entity_idx >= len(entities):
                continue

            entity = entities[entity_idx]
            chunk_ids = entity_citation_chunks(entity)
            if not chunk_ids:
                # No evidence chunk was resolved at aggregation (item never
                # tagged with its group, or a payload from before evidence).
                citations_skipped_no_chunk_index += 1
                continue

            # Get entity details from already-created nodes (no lookup needed)
            node = entity_index_to_node.get(entity_idx)
            entity_label = node.label if node else "Unknown"
            # Store the type name (not template_id) for human-readable stats
            entity_type = entity.get("type")
            # Get entity confidence (default to 1.0 if not provided by LLM)
            entity_confidence = entity.get("confidence", 1.0)
            evidence = entity.get("evidence") or []
            primary = entity.get("cited_chunk_id")

            for chunk_id in chunk_ids:
                if chunk_id not in stored_chunk_ids:
                    # The cited chunk is not one of this source's stored chunks —
                    # commit-pipeline drift (e.g. chunk pruned after extraction).
                    citations_skipped_index_not_mapped += 1
                    continue
                chunk_evidence = [
                    e for e in evidence if isinstance(e, dict) and e.get("chunk_id") == chunk_id
                ] or [e for e in evidence if isinstance(e, dict) and not e.get("chunk_id")]
                citations_to_create.append(
                    {
                        "id": _stable_citation_id(
                            database_name=self.database_name,
                            source_id=source_id,
                            chunk_id=chunk_id,
                            entity_uri=node_id,
                        ),
                        "database_name": self.database_name,
                        "entity_uri": node_id,
                        "entity_label": entity_label,
                        "entity_type": entity_type,
                        "source_id": source_id,
                        "chunk_id": chunk_id,
                        "confidence": entity_confidence,
                        "extraction_method": "ai_extraction",
                        "context_snippet": evidence_snippet(evidence, chunk_id),
                        "created_at": datetime.now(UTC),
                        "citation_metadata": _citation_metadata(
                            entity.get("sent_ref") if chunk_id == primary else None,
                            chunk_evidence,
                        ),
                    }
                )

        # Batch create citations (single transaction)
        if citations_to_create:
            self.sources_repository.create_citations_batch(citations_to_create)

            logger.info(
                "source_citations_created",
                citation_count=len(citations_to_create),
                source_id=source_id,
            )
        else:
            logger.warning("no_source_citations_created", source_id=source_id)

        # Workstream 2 (2026-05-08): surface entity citations dropped because
        # the extraction phase didn't tag them with a chunk_index (no_chunk_index)
        # or the index doesn't map to a stored chunk (index_not_mapped).
        # Two distinct failure modes → two separate counters.  Best-effort —
        # never block commit on a counter UPDATE.
        if citations_skipped_no_chunk_index > 0 or citations_skipped_index_not_mapped > 0:
            from chaoscypher_core.services.quality.counters import (
                QualityCounter,
                increment_quality_counter,
            )

            if citations_skipped_no_chunk_index > 0:
                await increment_quality_counter(
                    adapter=self.sources_repository,
                    source_id=source_id,
                    database_name=self.database_name,
                    counter=QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX,
                    n=citations_skipped_no_chunk_index,
                )
            if citations_skipped_index_not_mapped > 0:
                await increment_quality_counter(
                    adapter=self.sources_repository,
                    source_id=source_id,
                    database_name=self.database_name,
                    counter=QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED,
                    n=citations_skipped_index_not_mapped,
                )

    async def _create_relationship_citations(
        self,
        file_id: str,
        source_id: str,
        created_edges: list[str],
        edges_to_create: list,
        relationships: list[dict],
        entity_index_to_node: dict[int, Any],
        stored_chunk_ids: set[str],
        entity_index_to_node_id: dict[int, str] | None = None,
        entity_name_to_node_id: dict[str, str] | None = None,
    ) -> None:
        """Create relationship_citations linking edges to the chunks that cite them.

        Each forward edge is matched back to the relationship it was built
        from by ``(source node, target node, edge label)``, resolving the
        endpoints with the same resolver edge creation uses; relationships
        sharing a key are consumed in order, so two edges on one node pair
        keep their own evidence. The citation reads ``cited_chunk_id`` and
        ``evidence`` resolved at aggregation.

        Args:
            file_id: Import file ID
            source_id: Created source ID
            created_edges: List of created edge IDs (in same order as edges_to_create)
            edges_to_create: List of EdgeCreate objects with source/target info
            relationships: Relationships from commit_data (carry the evidence)
            entity_index_to_node: Mapping from entity index to Node object
            stored_chunk_ids: Ids of this source's stored chunks
            entity_index_to_node_id: Mapping from entity index to node ID
                (derived from ``entity_index_to_node`` when omitted)
            entity_name_to_node_id: Mapping from entity name to node ID, for
                name-keyed relationships

        """
        if not created_edges:
            logger.debug("no_edges_created_skipping_relationship_citations", file_id=file_id)
            return

        if not stored_chunk_ids:
            logger.warning("no_chunks_found_skipping_relationship_citations", file_id=file_id)
            return

        logger.info(
            "creating_relationship_citations",
            edge_count=len(created_edges),
            relationship_count=len(relationships),
            chunk_count=len(stored_chunk_ids),
        )

        index_to_node_id = (
            entity_index_to_node_id
            if entity_index_to_node_id is not None
            else {idx: node.id for idx, node in entity_index_to_node.items()}
        )
        name_to_node_id = entity_name_to_node_id or {}

        # (source_node_id, target_node_id, edge label) -> relationships in
        # order. Keyed by type as well as endpoints so edges sharing a node
        # pair never borrow another relationship's evidence.
        rels_by_edge_key: dict[tuple[str, str, str], list[dict]] = {}
        for rel in relationships:
            node_ids = RelationshipCommitHandler._resolve_node_ids(  # noqa: SLF001 - the citation match must resolve endpoints exactly as edge creation did
                rel, name_to_node_id, index_to_node_id
            )
            if node_ids is None:
                continue
            key = (node_ids[0], node_ids[1], relationship_edge_label(rel))
            rels_by_edge_key.setdefault(key, []).append(rel)
        consumed: dict[tuple[str, str, str], int] = {}

        # Build O(1) lookup: node_id -> node (for label resolution)
        node_id_to_node = {node.id: node for node in entity_index_to_node.values()}

        citations_to_create = []
        rel_citations_skipped_no_chunk_index = 0
        rel_citations_skipped_index_not_mapped = 0

        # edges_to_create and created_edges should be in the same order
        for edge_idx, edge_id in enumerate(created_edges):
            if edge_idx >= len(edges_to_create):
                continue

            edge = edges_to_create[edge_idx]

            # Synthetic inverse edges (created for bidirectional traversal and
            # tagged with ``INVERSE_OF_KEY``) mirror a forward relationship that
            # already gets its own citation. They carry no independent chunk
            # provenance and would otherwise be miscounted as skipped, so skip
            # them outright.
            if edge.properties.get(INVERSE_OF_KEY):
                continue

            key = (edge.source_node_id, edge.target_node_id, edge.label)
            candidates = rels_by_edge_key.get(key) or []
            position = consumed.get(key, 0)
            matching_rel = candidates[min(position, len(candidates) - 1)] if candidates else None
            consumed[key] = position + 1
            chunk_id = matching_rel.get("cited_chunk_id") if matching_rel else None

            if not chunk_id:
                # No evidence chunk was resolved at aggregation for this edge.
                rel_citations_skipped_no_chunk_index += 1
                continue

            if chunk_id not in stored_chunk_ids:
                # The cited chunk is not one of this source's stored chunks —
                # commit-pipeline drift (e.g. chunk pruned after extraction).
                rel_citations_skipped_index_not_mapped += 1
                continue

            # O(1) lookup for entity labels
            source_node = node_id_to_node.get(edge.source_node_id)
            target_node = node_id_to_node.get(edge.target_node_id)
            source_label = source_node.label if source_node else "Unknown"
            target_label = target_node.label if target_node else "Unknown"

            evidence = (matching_rel.get("evidence") if matching_rel else None) or []
            citation = {
                "id": _stable_relationship_citation_id(
                    database_name=self.database_name,
                    source_id=source_id,
                    chunk_id=chunk_id,
                    edge_id=edge_id,
                ),
                "database_name": self.database_name,
                "edge_id": edge_id,
                "edge_label": edge.label,
                "edge_type": edge.template_id,
                "source_entity_label": source_label,
                "target_entity_label": target_label,
                "source_id": source_id,
                "chunk_id": chunk_id,
                "confidence": matching_rel.get("confidence", 1.0) if matching_rel else 1.0,
                "extraction_method": "ai_extraction",
                "justification": matching_rel.get("justification") if matching_rel else None,
                "created_at": datetime.now(UTC),
                "citation_metadata": _citation_metadata(
                    matching_rel.get("sent_ref") if matching_rel else None, evidence
                ),
            }
            citations_to_create.append(citation)

        # Batch create relationship citations
        if citations_to_create:
            self.sources_repository.create_relationship_citations_batch(citations_to_create)

            logger.info(
                "relationship_citations_created",
                citation_count=len(citations_to_create),
                source_id=source_id,
            )
        else:
            logger.debug("no_relationship_citations_created", source_id=source_id)

        # Workstream 2 (2026-05-08): surface relationship citations dropped
        # because extraction didn't tag the rel with a chunk_index (no_chunk_index)
        # or the index doesn't map to a stored chunk (index_not_mapped).
        # Two distinct failure modes → two separate counters.  Best-effort —
        # never block commit on a counter UPDATE.
        if rel_citations_skipped_no_chunk_index > 0 or rel_citations_skipped_index_not_mapped > 0:
            from chaoscypher_core.services.quality.counters import (
                QualityCounter,
                increment_quality_counter,
            )

            if rel_citations_skipped_no_chunk_index > 0:
                await increment_quality_counter(
                    adapter=self.sources_repository,
                    source_id=source_id,
                    database_name=self.database_name,
                    counter=QualityCounter.CITATIONS_SKIPPED_NO_CHUNK_INDEX,
                    n=rel_citations_skipped_no_chunk_index,
                )
            if rel_citations_skipped_index_not_mapped > 0:
                await increment_quality_counter(
                    adapter=self.sources_repository,
                    source_id=source_id,
                    database_name=self.database_name,
                    counter=QualityCounter.CITATIONS_SKIPPED_INDEX_NOT_MAPPED,
                    n=rel_citations_skipped_index_not_mapped,
                )

    async def _update_chunk_status(self, file_id: str, status: str) -> None:
        """Update status for all chunks of an import file.

        Args:
            file_id: Import file ID
            status: New status ('staged' | 'committed' | 'rejected')

        """
        count = self.indexing_repository.update_chunk_status(file_id, status)

        logger.info("chunk_status_updated", chunk_count=count, new_status=status, file_id=file_id)

    async def _index_chunks_to_vector_search(
        self, file_id: str, *, session: Any | None = None
    ) -> None:
        """Index document chunks to vector search index.

        Uses chunk ID prefix "chunk:" to distinguish from graph nodes.
        Passes ``session`` through to SearchRepository so the write
        shares the caller's connection when one is provided.

        Args:
            file_id: Import file ID
            session: Optional caller session (SqlModel Session) to share
                a transaction with. See SearchRepository.index_node.

        """
        try:
            # Get chunks with embeddings (must include_embeddings=True for vector indexing)
            _chunk_fetch_limit = self.settings.batching.chunk_fetch_limit
            _result = self.indexing_repository.get_chunks_by_source(
                file_id, page=1, page_size=_chunk_fetch_limit, include_embeddings=True
            )
            chunks = _result[0] if isinstance(_result, tuple) else _result

            if not chunks:
                logger.warning("no_chunks_found_skipping_vector_indexing", file_id=file_id)
                return

            # Prepare embeddings and text lookup for batch indexing
            embeddings_to_index = []
            text_lookup: dict[str, str] = {}
            skipped_count = 0
            for chunk in chunks:
                if not chunk.get("embedding"):
                    skipped_count += 1
                    continue

                # Decode embedding from base64
                embedding_bytes = base64.b64decode(chunk["embedding"])
                embedding_array = np.frombuffer(embedding_bytes, dtype=np.float32)
                embedding_list = embedding_array.tolist()

                # Use "chunk:" prefix to distinguish from nodes
                chunk_id = f"chunk:{chunk['id']}"
                embeddings_to_index.append((chunk_id, embedding_list))

                # Store text for re-embedding on dimension mismatch
                chunk_text = chunk.get("content", "")
                if chunk_text:
                    text_lookup[chunk_id] = chunk_text

            # Batch index all embeddings (single disk write)
            indexed_count = 0
            if embeddings_to_index:
                indexed_count = self.search_repository.index_embeddings_batch(
                    embeddings_to_index,
                    item_type="chunk",
                    text_lookup=text_lookup,
                    session=session,
                )

            if skipped_count > 0:
                logger.warning(
                    "chunks_missing_embeddings_skipped",
                    skipped_count=skipped_count,
                    file_id=file_id,
                )

            logger.info(
                "chunks_indexed_to_vector_search", indexed_count=indexed_count, file_id=file_id
            )

        except Exception as e:
            logger.exception(
                "vector_indexing_failed",
                file_id=file_id,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            # Re-raise so the commit step reports the error.
            # Chunks can be recovered via POST /api/v1/search/indexes rebuild.
            raise

    def _enqueue_search_retry(
        self,
        ids: list[str],
        source_id: str,
        kind: str,
    ) -> None:
        """Persist pending search indexing for later retry by the orphan-sweep worker.

        Called by the commit pipeline when post-transaction FTS5/vec indexing
        fails. Rows are drained by the orphan-sweep worker at its next
        cycle. Delegates to the adapter-owned
        ``SearchRetryQueueProtocol`` which uses INSERT OR IGNORE so
        repeated failure-and-retry of the same source produces exactly one
        pending row per item rather than a unique-constraint violation.

        Args:
            ids: Item IDs to enqueue (node IDs, or ``[file_id]`` for chunks).
            source_id: Source these items belong to (used by sweep for bulk
                chunk re-indexing).
            kind: Row discriminator -- ``"node"``, ``"chunk"``, or
                ``"template"``.
        """
        if not ids:
            return
        rows = [{"item_id": item_id, "kind": kind, "source_id": source_id} for item_id in ids]
        with self.adapter.transaction():
            self.adapter.enqueue_pending_search_index(rows=rows)

    async def _flush_pending_reindex(self) -> None:
        """Drain any pending re-embedding queue before the commit transaction.

        Must run OUTSIDE the commit transaction because it makes LLM
        calls. Rare path — only fires when embedding dimensions change
        (e.g. model upgrade).
        """
        if not self.search_repository.has_pending_reindex:
            return

        embedding_provider = self._get_embedding_provider()
        if embedding_provider is None:
            logger.info("reindex_flush_skipped_keyword_only")
            return
        try:
            reindexed = await self.search_repository.flush_reindex_with_service(embedding_provider)
            if reindexed:
                logger.info("reindex_queue_flushed_pre_commit", count=reindexed)
        except Exception:
            logger.exception("reindex_flush_failed_non_fatal")

    async def _embed_created_templates(
        self, template_ids: list[str], *, session: Any | None = None
    ) -> None:
        """Generate and store embeddings for newly created templates.

        Embeds templates inline during commit so they are immediately
        available for semantic search without requiring a separate worker task.

        Writer-lock discipline (2026-05-20 writer-lock-contention root fix
        — second iteration): SQLAlchemy's autobegin semantics mean any read
        or write on the shared ``session`` opens an implicit transaction
        that stays open until the next explicit ``session.commit()``. Naive
        in-loop pattern of "read template → await LLM → write embedding"
        holds the implicit transaction — and therefore the SQLite writer
        lock — across the entire per-template LLM HTTP call. With ~15
        templates and a slow embedding model that's ~60s of contention,
        which sibling handlers hit as ``OperationalError("database is
        locked")``.

        Fix: read all templates first and commit to release the read-side
        implicit txn before any await; compute embeddings outside any
        open transaction; then write each embedding followed by an
        explicit per-iteration ``session.commit()`` so the writer lock is
        held only for the duration of each write (microseconds), never
        across the LLM await.

        When ``session`` is provided the search-index write joins the
        caller's connection. Exceptions propagate to the caller so the
        outer wrapper at ``_commit_impl`` can roll back; the caller's
        wrapper also calls ``session.rollback()`` on failure to clear
        any half-written state out of the session.

        Args:
            template_ids: List of template IDs to embed.
            session: Optional caller session (SqlModel Session) to share
                a connection with. See SearchRepository.index_node.

        """
        from chaoscypher_core.services.graph.management.embedding import (
            TemplateEmbeddingService,
        )

        try:
            embedding_provider = self._get_embedding_provider()
            if embedding_provider is None:
                logger.info(
                    "commit_template_embedding_skipped_keyword_only", templates=len(template_ids)
                )
                return

            # Phase 1: load all templates first and commit so the implicit
            # read transaction is closed before any LLM await opens a
            # window of writer-lock contention.
            loaded_templates: list[tuple[str, Any]] = []
            for template_id in template_ids:
                template = self.graph_repository.get_template(template_id)
                if not template:
                    logger.warning(
                        "template_not_found_for_embedding",
                        template_id=template_id,
                    )
                    continue
                loaded_templates.append((template_id, template))
            if session is not None:
                session.commit()

            template_service = TemplateEmbeddingService(embedding_provider)

            # Phase 2: per template — await the LLM with NO transaction
            # held, then write and commit immediately so the writer lock
            # never spans the next iteration's HTTP call.
            embedded_count = 0
            for template_id, template in loaded_templates:
                embedding = await template_service.generate_embedding(
                    template.name, template.description
                )
                if embedding:
                    self.graph_repository.update_template(
                        template_id,
                        TemplateUpdate(
                            embedding=embedding,
                            embedding_model=template_service.get_embedding_model(),
                            embedding_dimensions=len(embedding),
                        ),
                    )
                    self.search_repository.index_template(template_id, embedding, session=session)
                    if session is not None:
                        session.commit()
                    embedded_count += 1

            logger.info(
                "commit_template_embeddings_generated",
                embedded_count=embedded_count,
                total_templates=len(template_ids),
            )

        except Exception:
            # Non-fatal: templates are created but won't be semantically
            # searchable until the next regenerate_template_embeddings task.
            #
            # When session is provided, the caller (in _commit_impl) wraps
            # this call in its own try/except that also calls
            # session.rollback(). We re-raise here so that wrapper fires.
            if session is not None:
                raise
            logger.exception(
                "commit_template_embedding_failed",
                template_count=len(template_ids),
            )
