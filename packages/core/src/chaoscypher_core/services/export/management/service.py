# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CcxExporter: assemble a CCX 3.0 package from the Chaos Cypher domain.

The exporter is a SERVICE: it reaches storage only through the injected
``graph_repository`` / ``sources_repository`` ports and feeds plain domain
dicts through the pure ``ccx_mapping`` module into ``ccx_format.PackageBuilder``.
It never imports an adapter (CC010/CC012) and raises Core exceptions on
failure (CC031/CC045), never ``HTTPException`` or a bare ``ValueError``.

Layout of a produced package:

* ``ccx``/``knowledge`` default graph — nodes + edges (standard, neutral).
* ``shapes.ttl`` + an extended ``@context`` — generated from templates.
* ``chaoscypher.templates`` named graph — editor metadata (icons/colors/props).
* ``sources.jsonl`` (+ per-source full-text assets / offset selectors).
* embedding descriptor(s) — provenance-only by default; vectors opt-in.
* ``chaoscypher.workflows`` / ``.lenses`` / ``.statistics`` named graphs —
  app-specific, ignored by a neutral CCX reader.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.services.export import ccx_identity, ccx_mapping
from chaoscypher_core.services.export.engine.stats import (
    calculate_knowledge_stats,
    calculate_lens_stats,
    calculate_source_stats,
    calculate_template_stats,
    calculate_workflow_stats,
)


if TYPE_CHECKING:
    from ccx import PackageBuilder

    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


# System template IDs that mark a node/edge as app-internal (lens / workflow)
# rather than portable knowledge. Mirrors the v2.0 classification.
_LENS_TEMPLATE_ID = "system_lens"
_WORKFLOW_TEMPLATE_IDS = frozenset({"system_workflow", "system_workflow_step"})

# Page size for paginated source reads.
_SOURCE_PAGE_SIZE = 500

# Stable manifest path for the optional graph-preview asset. A fixed path
# (rather than a content-addressed one) keeps the preview discoverable by
# neutral readers and matches the v2.0 ``graph_preview.png`` location.
_PREVIEW_ASSET_PATH = "assets/graph_preview.png"
_PREVIEW_MEDIA_TYPE = "image/png"


class CcxExporter:
    """Assemble a CCX 3.0 package from the Chaos Cypher domain via ccx-format."""

    def __init__(
        self,
        *,
        graph_repository: GraphRepositoryProtocol,
        sources_repository: Any = None,
        settings: EngineSettings,
        workflow_db: Any = None,
    ) -> None:
        """Initialize the exporter with the storage ports it reads through.

        Args:
            graph_repository: Graph repository (nodes/edges/templates).
            sources_repository: Source storage port (sources/chunks/citations).
                Required for source export; optional for knowledge-only export.
            settings: Engine settings carrying export/embedding metadata.
            workflow_db: Optional workflow database for trigger export.
        """
        self.graph = graph_repository
        self.sources = sources_repository
        self.settings = settings
        self.workflow_db = workflow_db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export(
        self,
        *,
        include_templates: bool = True,
        include_knowledge: bool = True,
        include_lenses: bool = True,
        include_workflows: bool = True,
        include_sources: bool = True,
        include_embeddings: bool = False,
        lens_id: str | None = None,
        title: str | None = None,
        source_ids: list[str] | None = None,
        preview_png: bytes | None = None,
    ) -> bytes:
        """Build and return the CCX 3.0 package bytes.

        Args:
            include_templates: Emit ``@context`` + ``shapes.ttl`` +
                ``chaoscypher.templates`` from user templates.
            include_knowledge: Emit the knowledge default graph.
            include_lenses: Emit the ``chaoscypher.lenses`` named graph.
            include_workflows: Emit the ``chaoscypher.workflows`` named graph.
            include_sources: Emit ``sources.jsonl`` + full-text assets.
            include_embeddings: Bundle Parquet vector sidecars. Default
                ``False`` → provenance-only descriptor (no vectors).
            lens_id: Optional single-lens filter.
            title: Optional package title.
            source_ids: When given, restrict the export to entities/sources
                of these sources (à-la-carte source-scoped export).
            preview_png: Optional rendered graph-preview PNG bytes. When
                given, the exporter bundles them as the
                ``assets/graph_preview.png`` asset (the CCX 3.0 successor to
                the v2.0 ``graph_preview.png`` bundle entry). When ``None``,
                no preview asset is emitted — the caller (the export
                operation handler / CLI) renders the snapshot and supplies
                the bytes, so a caller without a renderer simply omits it.

        Returns:
            The validated ``.ccx`` package bytes.

        Raises:
            OperationError: When assembly or self-validation fails.
        """
        try:
            from ccx import PackageBuilder

            builder = PackageBuilder(
                name=self._package_name(),
                package_version=self._package_version(),
                license=self.settings.export.export_license,
                base_iri=ccx_identity.BASE_IRI,
                title=title,
                description=self.settings.export.export_description or None,
                author=self.settings.export.export_author,
                tags=self.settings.export.export_tags or None,
                derived_from=self.settings.export.export_derived_from or None,
                dependencies=self.settings.export.export_dependencies or None,
                generator=f"chaoscypher@{self._app_version()}",
            )

            templates = self._templates(include_templates, source_ids)
            templates_by_id = {tmpl["id"]: tmpl for tmpl in templates}

            knowledge_nodes, knowledge_edges = self._read_knowledge(
                include_knowledge, lens_id, source_ids
            )

            # The default knowledge graph is ALWAYS emitted (an empty graph is
            # the à-la-carte sources-only case — the mapping returns
            # {"@graph": []} and the builder requires at least one graph).
            builder.add_graph(
                "ccx",
                "knowledge",
                ccx_mapping.build_knowledge_graph(
                    knowledge_nodes, knowledge_edges, templates_by_id
                ),
                role="default",
            )

            if include_templates and templates:
                builder.extend_context(ccx_mapping.templates_to_context(templates)["@context"])
                builder.add_shapes(ccx_mapping.templates_to_shacl(templates))
                builder.add_graph(
                    "chaoscypher",
                    "templates",
                    ccx_mapping.templates_to_app_graph(templates),
                )

            # Read sources / lens nodes / workflow nodes / triggers ONCE here so
            # the same data feeds BOTH the named graphs (+ sources.jsonl) AND the
            # cached SourceStats / LensStats / WorkflowStats below — no double
            # fetch. Each is gathered only when its data is in scope for the
            # export (so a knowledge-only package never pays for source reads
            # and never emits empty Source/Lens/Workflow stats members).
            source_records: list[dict[str, Any]] = []
            if include_sources and self.sources is not None:
                source_records = self._collect_source_records(source_ids)
                self._add_sources(builder, source_records)
                # Fold the in-scope sources' tags into the package-level manifest
                # tags (the hub's search facet) on top of the configured defaults.
                builder.tags = self._merge_manifest_tags(builder.tags, source_records)

            if include_embeddings:
                self._add_embeddings(builder, source_records, knowledge_nodes)

            triggers: list[dict[str, Any]] = []
            workflow_nodes: list[dict[str, Any]] = []
            if include_workflows and self.workflow_db is not None:
                triggers = self._read_triggers()
                workflow_nodes = self._workflow_nodes(source_ids)
                builder.add_graph(
                    "chaoscypher",
                    "workflows",
                    self._workflows_graph(triggers),
                )

            lens_nodes: list[dict[str, Any]] = []
            if include_lenses:
                lens_nodes = self._lens_nodes(lens_id, source_ids)
                lens_graph = self._lenses_graph(lens_nodes, templates_by_id)
                if lens_graph["@graph"]:
                    builder.add_graph("chaoscypher", "lenses", lens_graph)

            builder.add_graph(
                "chaoscypher",
                "statistics",
                self._statistics_graph(
                    knowledge_nodes,
                    knowledge_edges,
                    source_records=source_records,
                    lens_nodes=lens_nodes,
                    workflow_nodes=workflow_nodes,
                    triggers=triggers,
                    include_embeddings=include_embeddings,
                ),
            )

            if preview_png is not None:
                builder.add_asset(
                    preview_png,
                    _PREVIEW_MEDIA_TYPE,
                    path=_PREVIEW_ASSET_PATH,
                )

            data: bytes = builder.build()
        except OperationError:
            raise
        except Exception as exc:
            logger.exception(
                "ccx_export_failed",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            message = f"CCX export failed: {exc}"
            raise OperationError(message, operation="ccx_export") from exc

        logger.info(
            "ccx_export_complete",
            node_count=len(knowledge_nodes),
            edge_count=len(knowledge_edges),
            template_count=len(templates),
            size_bytes=len(data),
        )
        return data

    def get_export_filename(self) -> str:
        """Generate a timestamped ``.ccx`` export filename."""
        return f"knowledge_export_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.ccx"

    # ------------------------------------------------------------------
    # Knowledge / template readers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_category(node: dict[str, Any]) -> str:
        """Classify a node dict as ``knowledge`` / ``lens`` / ``workflow``."""
        template_id = node.get("template_id")
        if template_id == _LENS_TEMPLATE_ID:
            return "lens"
        if template_id in _WORKFLOW_TEMPLATE_IDS:
            return "workflow"
        return "knowledge"

    def _read_knowledge(
        self,
        include_knowledge: bool,
        lens_id: str | None,
        source_ids: list[str] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read knowledge nodes + edges (dicts carrying ``ccx_iri``).

        System lens / workflow nodes are filtered out so the portable
        knowledge graph stays neutral. Edges are kept only when both
        endpoints are knowledge nodes in the same batch. ``build_knowledge_graph``
        independently skips any remaining dangling edges.
        """
        if not include_knowledge:
            return [], []

        records = self.graph.export_graph_records(source_ids=source_ids)
        knowledge_nodes = [
            node for node in records["nodes"] if self._node_category(node) == "knowledge"
        ]
        knowledge_node_ids = {node["id"] for node in knowledge_nodes}
        knowledge_edges = [
            edge
            for edge in records["edges"]
            if edge.get("source_node_id") in knowledge_node_ids
            and edge.get("target_node_id") in knowledge_node_ids
        ]
        return knowledge_nodes, knowledge_edges

    def _templates(
        self,
        include_templates: bool,
        source_ids: list[str] | None,
    ) -> list[dict[str, Any]]:
        """Return user (non-system) template dicts for context/SHACL/app graph."""
        if not include_templates:
            return []
        templates = [tmpl.model_dump(mode="json") for tmpl in self.graph.list_templates()]
        user_templates = [tmpl for tmpl in templates if not tmpl.get("is_system", False)]
        if source_ids is not None:
            source_id_set = set(source_ids)
            user_templates = [
                tmpl for tmpl in user_templates if tmpl.get("source_id") in source_id_set
            ]
        return user_templates

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    def _list_sources(self, source_ids: list[str] | None) -> list[dict[str, Any]]:
        """Read full source dicts (carrying ``ccx_iri`` / ``full_text``).

        Both the scoped and unscoped paths resolve each source through
        ``get_source`` so the dicts always carry ``full_text`` and
        ``ccx_iri``. ``list_sources`` uses a narrow ``load_only`` projection
        that omits both (it feeds list-view rendering, not export), so the
        unscoped path uses it only to enumerate ids and then re-fetches the
        full row — otherwise a full export would silently lose the offset-
        selector / full-text path that a source-scoped export keeps.
        """
        database_name = self.settings.current_database
        resolved_ids = source_ids if source_ids is not None else self._all_source_ids()
        sources: list[dict[str, Any]] = []
        for source_id in resolved_ids:
            source = self.sources.get_source(source_id, database_name=database_name)
            if source is not None:
                sources.append(source)
        return sources

    def _all_source_ids(self) -> list[str]:
        """Enumerate every source id in the current database (paged)."""
        ids: list[str] = []
        page = 1
        page_size = self.settings.pagination.export_page_size
        while True:
            page_sources, total = self.sources.list_sources(
                page=page,
                page_size=page_size,
                source_type=None,
                status=None,
                search=None,
                tag_id=None,
            )
            ids.extend(source["id"] for source in page_sources)
            if len(ids) >= total or len(page_sources) < page_size:
                break
            page += 1
        return ids

    def _collect_chunks(self, source_id: str) -> list[dict[str, Any]]:
        """Read all chunk dicts for a source (with ``char_start`` / ``char_end``)."""
        chunks: list[dict[str, Any]] = []
        page = 1
        while True:
            page_chunks, total = self.sources.get_chunks_by_source(
                source_id=source_id,
                page=page,
                page_size=_SOURCE_PAGE_SIZE,
                status=None,
                include_embeddings=False,
            )
            if not page_chunks:
                break
            chunks.extend(page_chunks)
            if len(chunks) >= total:
                break
            page += 1
        return chunks

    def _collect_citations_by_chunk(self, source_id: str) -> dict[str, list[dict[str, Any]]]:
        """Group a source's citations by ``chunk_id`` for chunk-record attachment."""
        by_chunk: dict[str, list[dict[str, Any]]] = {}
        page = 1
        while True:
            citations, total = self.sources.get_citations_by_source(
                source_id=source_id,
                page=page,
                page_size=_SOURCE_PAGE_SIZE,
            )
            if not citations:
                break
            for citation in citations:
                chunk_id = citation.get("chunk_id")
                if chunk_id is not None:
                    by_chunk.setdefault(chunk_id, []).append(citation)
            if sum(len(v) for v in by_chunk.values()) >= total:
                break
            page += 1
        return by_chunk

    def _collect_source_records(self, source_ids: list[str] | None) -> list[dict[str, Any]]:
        """Read each in-scope source once, with its chunks + citations attached.

        Returns a list of ``{"source": <source dict>, "chunks": [...]}`` items.
        Each chunk dict carries a per-chunk ``citations`` list (when any), so
        the same gathered data drives BOTH ``sources.jsonl`` assembly
        (:meth:`_add_sources`) and the cached ``SourceStats``
        (:meth:`_source_stats`) without re-reading the source store.
        """
        records: list[dict[str, Any]] = []
        for source in self._list_sources(source_ids):
            source_id = source["id"]
            # Attach tag NAMES so they ride along as ccx:Source ``keywords`` and
            # aggregate into the manifest ``tags`` (hub search indexes tags).
            source["tags"] = [t["name"] for t in self.sources.get_source_tags(source_id)]
            chunks = self._collect_chunks(source_id)
            citations_by_chunk = self._collect_citations_by_chunk(source_id)
            for chunk in chunks:
                chunk_citations = citations_by_chunk.get(chunk["id"])
                if chunk_citations:
                    chunk["citations"] = chunk_citations
            records.append({"source": source, "chunks": chunks})
        return records

    @staticmethod
    def _merge_manifest_tags(
        existing: list[str] | None, source_records: list[dict[str, Any]]
    ) -> list[str] | None:
        """Union the configured manifest tags with every in-scope source's tags."""
        names = set(existing or [])
        for record in source_records:
            names.update(record["source"].get("tags") or [])
        return sorted(names) or None

    def _add_sources(self, builder: PackageBuilder, source_records: list[dict[str, Any]]) -> None:
        """Map sources + chunks into ``sources.jsonl`` records on the builder.

        For a source with ``full_text``, the first record (the ``ccx:Source``)
        carries the ``TEXT_ASSET_PENDING`` sentinel in ``text``; we pop it and
        hand the real bytes to ``add_source(text=...)`` so the builder stores a
        content-addressed asset and rewrites ``text`` to that asset path. Chunk
        records (and full-text-less source records) are added verbatim.

        ``source_records`` is the pre-gathered output of
        :meth:`_collect_source_records` so sources are read from storage once.
        """
        chunking_config = self._chunking_config()
        for record in source_records:
            source = record["source"]
            chunks = record["chunks"]

            records = ccx_mapping.source_records(source, chunks, chunking_config)
            src_rec = records[0]
            full_text = source.get("full_text")
            if full_text and src_rec.get("text") == ccx_mapping.TEXT_ASSET_PENDING:
                # Drop the sentinel so the builder's setdefault populates the
                # real content-addressed asset path.
                src_rec.pop("text", None)
                builder.add_source(
                    src_rec,
                    text=full_text.encode("utf-8"),
                    source_mode="derived-only",
                )
            else:
                builder.add_source(src_rec)

            for chunk_rec in records[1:]:
                builder.add_source(chunk_rec)

    def _chunking_config(self) -> dict[str, Any] | None:
        """Build the ``chunking`` provenance dict from engine settings."""
        chunking = self.settings.chunking
        return {
            "strategy": getattr(chunking, "strategy", "recursive_character"),
            "small_chunk_size": chunking.small_chunk_size,
            "small_chunk_overlap": chunking.small_chunk_overlap,
            "min_chunk_size": chunking.min_chunk_size,
            "max_chunk_size": chunking.max_chunk_size,
            "respect_boundaries": chunking.respect_boundaries,
            "normalize_newlines": chunking.normalize_newlines,
        }

    def _chunking_stats_config(self) -> dict[str, Any]:
        """Build the chunking-settings dict ``calculate_source_stats`` expects.

        Distinct from :meth:`_chunking_config` (the ``sources.jsonl`` provenance
        shape): the stats ``ChunkingConfig`` DTO additionally requires the
        hierarchical-grouping keys (``group_size`` / ``group_overlap`` /
        ``auto_group_size``) and ``separators``, so we source them here from the
        same engine chunking settings.
        """
        chunking = self.settings.chunking
        return {
            **(self._chunking_config() or {}),
            "separators": getattr(chunking, "separators", []),
            "group_size": chunking.group_size,
            "group_overlap": chunking.group_overlap,
            "auto_group_size": getattr(chunking, "auto_group_size", True),
        }

    # ------------------------------------------------------------------
    # Embeddings (provenance-only by default)
    # ------------------------------------------------------------------

    def _add_embeddings(
        self,
        builder: PackageBuilder,
        source_records: list[dict[str, Any]],
        knowledge_nodes: list[dict[str, Any]],
    ) -> None:
        """Bundle a Parquet embedding sidecar + descriptor (vectors included).

        Default exports never call this — they emit no embedding descriptor,
        keeping ``pyarrow`` off the import path. When vector inclusion is
        requested we read node AND chunk embeddings, build a Parquet sidecar,
        and attach an ``included=True`` descriptor — so an importer whose
        embedding model matches can restore them and skip re-embedding.
        """
        rows = self._embedding_rows(knowledge_nodes) + self._chunk_embedding_rows(source_records)
        descriptor = ccx_mapping.embedding_descriptor(
            coverage="full",
            model=self.settings.embedding.model,
            dimensions=self.settings.search.vector_dimensions,
            provider=self.settings.embedding.provider,
            included=bool(rows),
        )
        if not rows:
            # No vectors to bundle — emit a provenance-only descriptor.
            descriptor["included"] = False
            builder.add_embeddings(descriptor)
            return
        sidecar = ccx_mapping.embeddings_to_parquet_bytes(rows)
        builder.add_embeddings(descriptor, sidecar=sidecar)

    def _embedding_rows(self, knowledge_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Read node embeddings as ``{id, vector}`` rows keyed by the node CCX IRI.

        Built from the SAME ``knowledge_nodes`` dicts the knowledge graph is
        emitted from (``export_graph_records``), so a vector's id is
        ``resolve_iri("node", node)`` — IDENTICAL to the node record's ``@id``,
        including a persisted FOREIGN ``ccx_iri`` (which the domain ``Node`` model
        drops, so keying off ``list_nodes`` would mint a diverging id and the
        importer would fail to join a re-exported imported node's vector). Also
        inherits the source-scoping already applied to ``knowledge_nodes`` — a
        by-sources export no longer leaks the whole DB's node vectors.
        """
        rows: list[dict[str, Any]] = []
        for node in knowledge_nodes:
            embedding = node.get("embedding")
            if not embedding:
                continue
            rows.append(
                {
                    "id": ccx_identity.resolve_iri("node", node),
                    "vector": list(embedding),
                }
            )
        return rows

    def _chunk_embedding_rows(self, source_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Read chunk embeddings as ``{id, vector}`` rows keyed by chunk IRI.

        ``DocumentChunk.embedding`` is a base64 float32 BLOB; decode it to the
        ``list[float]`` the Parquet ``vector`` column holds. Keyed by the same
        ``ccx:Chunk`` ``@id`` (:func:`ccx_mapping.chunk_iri`) the source records
        use, so the importer joins vector → chunk by identity.
        """
        if not source_records or self.sources is None:
            return []
        rows: list[dict[str, Any]] = []
        page_size = self.settings.pagination.export_page_size
        for record in source_records:
            src = record["source"]
            src_iri = ccx_identity.resolve_iri("source", src)
            page = 1
            while True:
                chunks, total = self.sources.get_chunks_by_source(
                    src["id"], page=page, page_size=page_size, include_embeddings=True
                )
                for chunk in chunks:
                    blob = chunk.get("embedding")
                    if not blob:
                        continue
                    vector = np.frombuffer(base64.b64decode(blob), dtype=np.float32).tolist()
                    rows.append({"id": ccx_mapping.chunk_iri(src_iri, chunk), "vector": vector})
                if not chunks or page * page_size >= total:
                    break
                page += 1
        return rows

    # ------------------------------------------------------------------
    # Application named graphs
    # ------------------------------------------------------------------

    def _read_triggers(self) -> list[dict[str, Any]]:
        """Read event triggers for the current database (best-effort).

        Shared by the ``chaoscypher.workflows`` named graph and the cached
        ``WorkflowStats`` so triggers are read once. Returns an empty list when
        the trigger store is unavailable (logged, not fatal).
        """
        try:
            return list(self.workflow_db.list_triggers(self.settings.current_database))
        except Exception:
            logger.warning("ccx_export_triggers_unavailable", exc_info=True)
            return []

    def _workflow_nodes(self, source_ids: list[str] | None) -> list[dict[str, Any]]:
        """Read workflow (definition + step) nodes for the current scope.

        These feed ``WorkflowStats`` (the named graph itself currently carries
        only triggers — see :meth:`_workflows_graph`).
        """
        records = self.graph.export_graph_records(source_ids=source_ids)
        return [node for node in records["nodes"] if self._node_category(node) == "workflow"]

    def _lens_nodes(
        self, lens_id: str | None, source_ids: list[str] | None
    ) -> list[dict[str, Any]]:
        """Read lens nodes for the current scope (optionally a single lens)."""
        records = self.graph.export_graph_records(source_ids=source_ids)
        lens_nodes = [node for node in records["nodes"] if self._node_category(node) == "lens"]
        if lens_id is not None:
            lens_nodes = [node for node in lens_nodes if node["id"] == lens_id]
        return lens_nodes

    def _workflows_graph(self, triggers: list[dict[str, Any]]) -> dict[str, Any]:
        """Build the ``chaoscypher.workflows`` named graph from triggers.

        ``triggers`` is pre-read by :meth:`_read_triggers` (scoped to the
        current database). NOTE: this graph carries only the trigger rows — it
        does NOT yet carry the ``Workflow`` definitions the triggers reference,
        so the importer cannot faithfully rebuild a trigger from it (a
        trigger's ``workflow_id`` would dangle). The importer therefore
        surfaces this graph with a warning rather than silently importing
        partial data. See ``internal/TODO.md`` (P2) for the bounded work to
        make workflows round-trip end-to-end.
        """
        return ccx_mapping.app_named_graph(list(triggers))

    def _lenses_graph(
        self,
        lens_nodes: list[dict[str, Any]],
        templates_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the ``chaoscypher.lenses`` named graph from lens nodes.

        Lens nodes are shaped as JSON-LD node objects (the same form the
        knowledge graph uses) so the importer reconstructs them through the
        node path and upserts them by ``@id`` (idempotent re-import). The lens
        system template is NOT exported in ``chaoscypher.templates`` (only user
        templates are), so on import the lens ``@type`` falls back to the
        default node template — the lens node itself still round-trips.

        ``lens_nodes`` is pre-read by :meth:`_lens_nodes`.
        """
        members = [ccx_mapping.node_to_jsonld(node, templates_by_id) for node in lens_nodes]
        return ccx_mapping.app_named_graph(members)

    def _source_stats_input(self, source_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Reshape gathered source records into ``calculate_source_stats`` input.

        ``_collect_source_records`` keeps chunks (with per-chunk ``citations``)
        beside the source dict; the stats calculator wants ``chunks`` +
        ``citations`` flat on each source dict. We flatten per-chunk citations
        into a source-level list here without mutating the records that already
        fed ``sources.jsonl``.
        """
        stats_sources: list[dict[str, Any]] = []
        for record in source_records:
            chunks = record["chunks"]
            citations = [c for chunk in chunks for c in chunk.get("citations", [])]
            stats_sources.append({**record["source"], "chunks": chunks, "citations": citations})
        return stats_sources

    def _statistics_graph(
        self,
        knowledge_nodes: list[dict[str, Any]],
        knowledge_edges: list[dict[str, Any]],
        *,
        source_records: list[dict[str, Any]] | None = None,
        lens_nodes: list[dict[str, Any]] | None = None,
        workflow_nodes: list[dict[str, Any]] | None = None,
        triggers: list[dict[str, Any]] | None = None,
        include_embeddings: bool = False,
    ) -> dict[str, Any]:
        """Build the ``chaoscypher.statistics`` named graph (app-cached stats).

        ``KnowledgeStats`` + ``TemplateStats`` are always emitted. The hub maps
        each member by its ``@type`` onto a stats panel, so we ALSO emit
        ``SourceStats`` / ``LensStats`` / ``WorkflowStats`` — but only when the
        corresponding data is actually present in this export (a knowledge-only
        package emits neither empty Source/Lens/Workflow members nor the panels
        they would drive). Each member is the DTO's flat ``model_dump`` under a
        typed ``@type`` key, matching the fixed hub convention.
        """
        knowledge_stats = calculate_knowledge_stats(
            nodes=knowledge_nodes,
            edges=knowledge_edges,
            settings=self.settings,
            include_embeddings=include_embeddings,
        )
        template_stats = calculate_template_stats(
            [tmpl.model_dump(mode="json") for tmpl in self.graph.list_templates()]
        )
        members: list[dict[str, Any]] = [
            {"@type": "chaoscypher:KnowledgeStats", **knowledge_stats.model_dump(mode="json")},
            {"@type": "chaoscypher:TemplateStats", **template_stats.model_dump(mode="json")},
        ]

        if source_records:
            source_stats = calculate_source_stats(
                sources=self._source_stats_input(source_records),
                chunking_settings=self._chunking_stats_config(),
                include_embeddings=include_embeddings,
            )
            members.append(
                {"@type": "chaoscypher:SourceStats", **source_stats.model_dump(mode="json")}
            )

        if lens_nodes:
            lens_stats = calculate_lens_stats(lens_nodes)
            members.append({"@type": "chaoscypher:LensStats", **lens_stats.model_dump(mode="json")})

        # WorkflowStats is meaningful when there are workflow definitions OR
        # triggers in scope (a package can carry triggers without workflow
        # definition nodes, and vice versa).
        if workflow_nodes or triggers:
            workflow_stats = calculate_workflow_stats(workflow_nodes or [], triggers or [])
            members.append(
                {"@type": "chaoscypher:WorkflowStats", **workflow_stats.model_dump(mode="json")}
            )

        return ccx_mapping.app_named_graph(members)

    # ------------------------------------------------------------------
    # Package metadata
    # ------------------------------------------------------------------

    def _package_name(self) -> str:
        """Resolve the package name (settings override → db-scoped default)."""
        return (
            self.settings.export.export_package_name
            or f"chaoscypher/{self.settings.current_database}"
        )

    def _package_version(self) -> str:
        """Resolve the package semver from settings."""
        return self.settings.export.export_version

    def _app_version(self) -> str:
        """Resolve the running app version for the ``generator`` field."""
        from chaoscypher_core import __version__

        return __version__
