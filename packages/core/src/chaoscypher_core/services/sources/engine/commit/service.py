# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source processing Commit Service.

Orchestrates the source processing commit process - converting analyzed entities
and relationships into permanent graph nodes and edges.

Extracted from commit_service.py for SRP compliance.
"""

import base64
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

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


from chaoscypher_core.models import TemplateUpdate
from chaoscypher_core.services.sources.engine.commit.entity import (
    EntityCommitHandler,
)
from chaoscypher_core.services.sources.engine.commit.matcher import (
    EntityTemplateMatcher,
)
from chaoscypher_core.services.sources.engine.commit.relation import (
    RelationshipCommitHandler,
)
from chaoscypher_core.services.sources.engine.commit.template import (
    TemplateCommitHandler,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    resolve_filtering_config,
)


logger = structlog.get_logger(__name__)


def drop_orphan_entities(
    entities: list[dict],
    relationships: list[dict],
    *,
    enabled: bool,
) -> tuple[list[dict], list[dict], int]:
    """Filter entities not referenced by any relationship, when enabled.

    Honors ``FilteringConfig.protect_orphans``. Returns
    ``(kept_entities, remapped_relationships, dropped_count)``
    preserving input order.

    The upstream extraction pipeline emits relationships keyed by
    integer indices into the ``entities`` list — each relationship
    has ``source: int`` and ``target: int`` whose values position into
    ``entities``. The downstream consumers in this commit pipeline
    (``commit/relation.py:157`` and ``commit/service.py:1120``) resolve
    endpoints via those indices, so this filter — which runs *before*
    node creation, when no entity IDs exist yet — must use the same
    contract.

    When the filter drops entity at index ``k``, every entity after it
    shifts down by one. Without remapping, relationships into kept-but-
    shifted entities silently disappear at commit time (the index they
    reference is now out of range, or — worse — points at the wrong
    surviving entity). The fix borrows
    ``EntityProcessor.remap_relationship_indices`` (the canonical remap
    pattern already used by dedup and type-rescue).

    Malformed endpoints (non-integer ``source``/``target``) are dropped
    by ``remap_relationship_indices`` so a typo in one relationship
    cannot poison the surviving edge set.

    Args:
        entities: Entities pending commit (in extraction order — index
            position is the join key).
        relationships: Relationships pending commit, keyed by integer
            ``source``/``target`` indices into ``entities``.
        enabled: When False, returns ``(entities, relationships, 0)``
            unchanged.

    Returns:
        Tuple of ``(kept_entities, remapped_relationships, dropped_count)``.
        ``dropped_count`` is the number of orphan entities removed; it
        feeds the ``ORPHAN_ENTITIES_FILTERED`` quality counter at the
        caller. Relationships into removed entities are filtered out by
        the canonical remap helper (so the returned list is always a
        subset of the input list).
    """
    from chaoscypher_core.services.sources.engine.deduplication.service import (
        EntityProcessor,
    )

    if not enabled:
        return list(entities), list(relationships), 0

    referenced: set[int] = set()
    for rel in relationships:
        for key in ("source", "target"):
            idx = rel.get(key)
            if isinstance(idx, int) and idx >= 0:
                referenced.add(idx)

    # Build mapping: old_index -> new_index (or None if dropped) and the
    # surviving entity list at the same time, preserving extraction order.
    new_idx = 0
    index_mapping: dict[int, int | None] = {}
    kept: list[dict] = []
    for old_idx, entity in enumerate(entities):
        if old_idx in referenced:
            index_mapping[old_idx] = new_idx
            kept.append(entity)
            new_idx += 1
        else:
            index_mapping[old_idx] = None

    dropped_count = len(entities) - len(kept)
    if dropped_count == 0:
        return kept, list(relationships), 0

    remapped = EntityProcessor.remap_relationship_indices(relationships, index_mapping)
    return kept, remapped, dropped_count


def normalize_relationship_endpoints(
    entities: list[dict],
    relationships: list[dict],
) -> list[dict]:
    """Convert string entity-id ``source``/``target`` endpoints to integer indices.

    The commit contract (``drop_orphan_entities`` and ``relation.py``'s
    ``_resolve_node_ids`` index path) expects each relationship's ``source`` /
    ``target`` to be an INTEGER index into ``entities``. The in-memory
    finalizer path honors that. But the relational-store reload path —
    ``list_source_relationships`` → ``_relationship_row_to_dict`` (migration
    0042) — projects ``source``/``target`` as STRING entity IDs
    (``source_entity_id`` / ``target_entity_id``). Feeding those straight to
    commit made the integer check in ``drop_orphan_entities`` fail for every
    relationship, so the referenced set was empty and 100% of entities were
    dropped as false orphans (empty graph for every committed source via the
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
                flush and template embedding; constructed lazily from
                ``settings`` on first use when omitted.

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

        Args:
            engine: Engine instance with storage_adapter, graph_repository,
                search_repository, and settings.

        Returns:
            SourceCommitService with all dependencies injected.

        """
        return cls(
            graph_repository=engine.graph_repository,
            source_repository=engine.storage_adapter,
            sources_repository=engine.storage_adapter,
            indexing_repository=engine.storage_adapter,
            search_repository=engine.search_repository,
            settings=engine.settings,
        )

    def _get_embedding_provider(self) -> EmbeddingProviderProtocol:
        """Return the injected embedding provider, constructing one on first use.

        Lazily builds a default embedding provider from ``self.settings`` when
        the constructor did not receive an explicit one. Caches the result so
        subsequent calls reuse the same instance within this service.
        """
        if self._embedding_provider is None:
            from chaoscypher_core.adapters.embedding import create_embedding_provider

            self._embedding_provider = create_embedding_provider(self.settings)
        return self._embedding_provider

    def _cleanup_previous_commit(self, file_id: str, source_id: str) -> dict[str, Any]:
        """Clean up any previously committed graph data for idempotent re-commit.

        Deletes graph nodes/edges/templates, search index entries, and citations
        created by a prior commit for this source. Resets chunk status to 'indexed'
        so they can be re-promoted during the fresh commit.

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
            # Step 1: Delete graph data (edges, nodes, templates) for this source
            graph_stats = self.graph_repository.delete_graph_data_by_source(source_id)
            deleted_node_ids: list[str] = graph_stats.get("deleted_node_ids", [])

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

        had_previous_data = (
            graph_stats.get("nodes_deleted", 0) > 0
            or citation_stats.get("entity_citations_deleted", 0) > 0
        )

        stats = {
            "had_previous_data": had_previous_data,
            "graph": graph_stats,
            "search_removed": search_removed,
            "citations": citation_stats,
        }

        if had_previous_data:
            logger.info(
                "idempotent_cleanup_completed",
                source_id=source_id,
                nodes_deleted=graph_stats.get("nodes_deleted", 0),
                edges_deleted=graph_stats.get("edges_deleted", 0),
                templates_deleted=graph_stats.get("templates_deleted", 0),
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
        chunk_index_to_id: dict[int, str] = {}
        for chunk in all_chunks:
            idx = chunk.get("chunk_index")
            if idx is not None:
                chunk_index_to_id[idx] = chunk["id"]

        # === WRITE PHASE (inside transaction — no LLM calls) ===
        # Declare before the block so post-transaction code can access them.
        all_created_templates: list[str] = []
        created_nodes: list[Any] = []
        created_edges: list[Any] = []

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
                if cleanup_stats["had_previous_data"]:
                    logger.info("commit_retry_detected", file_id=file_id, source_id=source_id)
            else:
                logger.debug(
                    "commit_first_attempt_cleanup_skipped",
                    file_id=file_id,
                    source_id=source_id,
                )

            # Step 1: Create suggested templates (with source_id for per-source templates)
            self._update_progress(file_id, 1, 8, "Creating templates")
            (
                created_templates,
                template_name_to_id,
                _all_used_templates,
                node_templates_inserted,
            ) = await self.template_handler.create_suggested_templates(
                commit_data, source_id=source_id
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
            # First apply orphan filter (entities with no relationships are dropped
            # when FilteringConfig.protect_orphans is False for the active mode).
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
            _raw_mode: str | None = file_info.get("filtering_mode")
            _filtering_mode: str = _raw_mode or self.settings.extraction.extraction_filtering_mode
            _filtering_config = resolve_filtering_config(mode=_filtering_mode)
            (
                _entities_for_commit,
                _relationships_for_commit,
                _dropped_orphan_count,
            ) = drop_orphan_entities(
                _raw_entities,
                _raw_relationships,
                enabled=not _filtering_config.protect_orphans,
            )
            if _dropped_orphan_count:
                from chaoscypher_core.services.quality.counters import (
                    QualityCounter,
                    increment_quality_counter,
                )

                await increment_quality_counter(
                    adapter=self.source_repository,
                    source_id=file_id,
                    database_name=self.database_name,
                    counter=QualityCounter.ORPHAN_ENTITIES_FILTERED,
                    n=_dropped_orphan_count,
                )
                logger.info(
                    "orphan_entities_dropped",
                    file_id=file_id,
                    count=_dropped_orphan_count,
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
                    commit_data.get("suggested_templates"),
                    template_name_to_id,
                    file_info,
                    file_id,
                    source_id,
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
            ) = await self.entity_handler.batch_create_nodes(nodes_to_create, entity_data_list)

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
                chunk_index_to_id=chunk_index_to_id,
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
            ) = await self.relationship_handler.batch_create_edges(edges_to_create)

            # Step 7b: Create relationship citations linking edges to source chunks
            await self._create_relationship_citations(
                file_id=file_id,
                source_id=source_id,
                created_edges=created_edges,
                edges_to_create=edges_to_create,
                relationships=_relationships_for_commit,
                entity_index_to_node=entity_index_to_node,
                chunk_index_to_id=chunk_index_to_id,
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
                self._enqueue_search_retry(
                    [n.id for n in nodes_to_index],
                    source_id=source_id,
                    kind="node",
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
            self._enqueue_search_retry(
                [file_id],
                source_id=source_id,
                kind="chunk",
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
        }

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

        with self.adapter.transaction():
            self.source_repository.start_commit(file_id)
            if had_prior_commit_data:
                self._cleanup_previous_commit(file_id, source_id)

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
            self._enqueue_search_retry(
                [file_id],
                source_id=source_id,
                kind="chunk",
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
        chunk_index_to_id: dict[int, str],
    ) -> None:
        """Create source_citations linking entities to source chunks.

        Args:
            file_id: Import file ID
            source_id: Created source ID
            entity_index_to_node_id: Mapping from entity index to created node ID
            entity_index_to_node: Mapping from entity index to Node object (avoids lookups)
            commit_data: Commit data containing chunk_ids and entity details
            chunk_index_to_id: Pre-built mapping from chunk index to chunk ID

        """
        # Get entities from commit_data (they have chunk_index from extraction)
        entities = commit_data.get("entities", [])

        if not chunk_index_to_id:
            logger.warning("no_chunks_found_skipping_source_citations", file_id=file_id)
            return

        logger.info(
            "creating_source_citations",
            entity_count=len(entity_index_to_node_id),
            chunk_count=len(chunk_index_to_id),
        )

        # Create citations: each entity is linked to the chunk it was extracted from
        citations_to_create = []
        citations_skipped_no_chunk_index = 0
        citations_skipped_index_not_mapped = 0

        for entity_idx, node_id in entity_index_to_node_id.items():
            # Get entity's chunk_index (set during extraction)
            if entity_idx >= len(entities):
                continue

            entity = entities[entity_idx]
            chunk_index = entity.get("chunk_index")
            if chunk_index is None:
                # Upstream merge collapsed the chunk_index — entity was never
                # tagged during extraction.
                citations_skipped_no_chunk_index += 1
                continue

            # Look up the chunk_id from chunk_index
            chunk_id = chunk_index_to_id.get(chunk_index)
            if not chunk_id:
                # chunk_index exists but doesn't map to a stored chunk —
                # commit-pipeline drift (e.g. chunk pruned after extraction).
                citations_skipped_index_not_mapped += 1
                continue

            # Get entity details from already-created nodes (no lookup needed)
            node = entity_index_to_node.get(entity_idx)
            entity_label = node.label if node else "Unknown"

            # Store the type name (not template_id) for human-readable stats
            entity_type = entity.get("type")

            # Get entity confidence (default to 1.0 if not provided by LLM)
            entity_confidence = entity.get("confidence", 1.0)

            # Populate context_snippet from sentence evidence when available
            context_snippet = None
            sent_ref = entity.get("sent_ref")
            chunk_sentences = commit_data.get("chunk_sentences")
            if sent_ref and chunk_sentences:
                from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
                    get_referenced_sentences,
                )

                # chunk_sentences is parallel to chunk indices
                if chunk_index is not None and chunk_index < len(chunk_sentences):
                    sentences_for_chunk = chunk_sentences[chunk_index]
                    if sentences_for_chunk:
                        ref_sentences = get_referenced_sentences(sent_ref, sentences_for_chunk)
                        if ref_sentences:
                            context_snippet = " ".join(ref_sentences)

            citation = {
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
                "context_snippet": context_snippet,
                "created_at": datetime.now(UTC),
                "citation_metadata": {"sent_ref": sent_ref} if sent_ref else None,
            }
            citations_to_create.append(citation)

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
        chunk_index_to_id: dict[int, str],
    ) -> None:
        """Create relationship_citations linking edges to source chunks.

        Args:
            file_id: Import file ID
            source_id: Created source ID
            created_edges: List of created edge IDs (in same order as edges_to_create)
            edges_to_create: List of EdgeCreate objects with source/target info
            relationships: Original relationships from commit_data (have chunk_index)
            entity_index_to_node: Mapping from entity index to Node object
            chunk_index_to_id: Pre-built mapping from chunk index to chunk ID

        """
        if not created_edges:
            logger.debug("no_edges_created_skipping_relationship_citations", file_id=file_id)
            return

        if not chunk_index_to_id:
            logger.warning("no_chunks_found_skipping_relationship_citations", file_id=file_id)
            return

        logger.info(
            "creating_relationship_citations",
            edge_count=len(created_edges),
            relationship_count=len(relationships),
            chunk_count=len(chunk_index_to_id),
        )

        # Build a lookup for relationships by source/target entity indices
        # This helps match EdgeCreate objects back to original relationships
        # Handle both formats: "source"/"target" and "source_index"/"target_index"
        rel_by_entities: dict[tuple[int, int], dict] = {}
        for rel in relationships:
            # Check both key formats (extraction uses "source"/"target")
            source_idx = rel.get("source_index")
            if source_idx is None:
                source_idx = rel.get("source")
            target_idx = rel.get("target_index")
            if target_idx is None:
                target_idx = rel.get("target")
            if source_idx is not None and target_idx is not None:
                rel_by_entities[(source_idx, target_idx)] = rel

        # Build O(1) lookup: (source_node_id, target_node_id) -> relationship dict
        node_pair_to_rel: dict[tuple[str, str], dict] = {}
        for (src_idx, tgt_idx), rel in rel_by_entities.items():
            src_node = entity_index_to_node.get(src_idx)
            tgt_node = entity_index_to_node.get(tgt_idx)
            if src_node and tgt_node:
                node_pair_to_rel[(src_node.id, tgt_node.id)] = rel

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
            # tagged with ``inverse_of``) mirror a forward relationship that
            # already gets its own citation. Their (source, target) pair is the
            # reversed key, never present in ``node_pair_to_rel``, so they would
            # otherwise be miscounted as "skipped — no chunk index" and inflate
            # that quality counter by one phantom skip per inverse edge. They
            # carry no independent chunk provenance, so skip them outright.
            if edge.properties.get("inverse_of"):
                continue

            # O(1) lookup for matching relationship by node pair
            matching_rel = node_pair_to_rel.get((edge.source_node_id, edge.target_node_id))
            chunk_index = matching_rel.get("chunk_index") if matching_rel else None

            if chunk_index is None:
                # Upstream merge collapsed the chunk_index — relationship was
                # never tagged during extraction.
                rel_citations_skipped_no_chunk_index += 1
                continue

            chunk_id = chunk_index_to_id.get(chunk_index)
            if not chunk_id:
                # chunk_index exists but doesn't map to a stored chunk —
                # commit-pipeline drift (e.g. chunk pruned after extraction).
                rel_citations_skipped_index_not_mapped += 1
                continue

            # O(1) lookup for entity labels
            source_node = node_id_to_node.get(edge.source_node_id)
            target_node = node_id_to_node.get(edge.target_node_id)
            source_label = source_node.label if source_node else "Unknown"
            target_label = target_node.label if target_node else "Unknown"

            # Build citation metadata with sent_ref if available
            rel_sent_ref = matching_rel.get("sent_ref") if matching_rel else None
            citation_meta = {"sent_ref": rel_sent_ref} if rel_sent_ref else None

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
                "citation_metadata": citation_meta,
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

            embedding_provider = self._get_embedding_provider()
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
