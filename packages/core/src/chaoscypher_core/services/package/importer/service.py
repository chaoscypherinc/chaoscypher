# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""CcxImporter: ingest a CCX 3.0 package via upsert-by-IRI.

The importer is a SERVICE: it reaches storage only through the injected
``graph_repository`` / ``sources_repository`` ports and feeds the JSON-LD
objects + ``sources.jsonl`` records a CCX 3.0 package carries through the
pure ``ccx_import_mapping`` module into the storage layer. It never imports
an adapter (CC010/CC012) and raises Core exceptions on integrity failure
(CC031/CC045), never ``HTTPException`` or a bare ``ValueError``.

Identity & idempotency
----------------------
Every entity is upserted by its stable CCX IRI (``upsert_*_by_ccx_iri``):
re-importing the same bytes updates the same rows rather than duplicating
them. Plain-triple edges (which have no own ``@id``) get a deterministic IRI
from ``(subject, predicate, object)`` so they round-trip idempotently too.

FK ordering
-----------
Templates → nodes → edges. ``graph_*.template_id`` is a RESTRICT FK, so a
node/edge template must exist before the node/edge that references it. The
importer rebuilds templates from the authoritative ``chaoscypher.templates``
named graph first, then resolves each node's ``@type`` term to a template id.

Conflict policy (design §3.2/§4)
--------------------------------
Incoming-wins within the same ``package_version`` (the upsert overwrites
mutable columns); a higher ``package_version`` supersedes a lower one. The
upsert is unconditional incoming-wins; the package version is recorded on
the stats for provenance/logging.

Example:
    from chaoscypher_core.services.package.importer import (
        CcxImporter,
        ImportOptions,
    )

    importer = CcxImporter(graph_repository=graph_repo, sources_repository=src_repo)
    stats = await importer.import_from_bytes(data, ImportOptions())
    if stats.errors:
        ...  # fail-closed: validation rejected the package
"""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from chaoscypher_core.exceptions import DataIntegrityError
from chaoscypher_core.models import EdgeCreate, NodeCreate, PropertyDefinition, TemplateCreate
from chaoscypher_core.services.export import ccx_identity
from chaoscypher_core.services.package.importer import ccx_import_mapping
from chaoscypher_core.services.package.importer.models import ImportOptions, ImportStats


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.ports.graph import GraphRepositoryProtocol


logger = structlog.get_logger(__name__)

# Named-graph identity the exporter emits (``builder.add_graph(namespace, name, ...)``).
_KNOWLEDGE_ROLE = "default"
_APP_NAMESPACE = "chaoscypher"
_TEMPLATES_GRAPH = "templates"
_LENSES_GRAPH = "lenses"
_WORKFLOWS_GRAPH = "workflows"

# The reified-relationship sentinel ``@type``.
_RELATIONSHIP_TYPE = "ccx:Relationship"

# Default node-template name synthesized for bare entities (a node whose
# ``@type`` matches no imported template). A neutral CCX package may carry
# entities with no Chaos-Cypher template; they still need *a* node template
# because ``graph_nodes.template_id`` is NOT NULL + a RESTRICT FK.
_DEFAULT_NODE_TEMPLATE_NAME = "Imported Entity"


class CcxImporter:
    """Ingest a CCX 3.0 package into Chaos Cypher via upsert-by-IRI."""

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        sources_repository: Any | None = None,
        workflow_db: Any | None = None,
    ) -> None:
        """Initialize the importer with the storage ports it writes through.

        Args:
            graph_repository: Graph repository (templates/nodes/edges) — the
                upsert-by-IRI primitives live here.
            sources_repository: Optional source storage port
                (sources/chunks/citations). ``None`` skips source import
                (e.g. the CLI, which has no sources repo).
            workflow_db: Optional workflow database for trigger/workflow
                import. ``None`` skips the ``chaoscypher.workflows`` graph.
        """
        self.graph = graph_repository
        self.sources = sources_repository
        self.workflow_db = workflow_db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def import_from_bytes(
        self,
        data: bytes,
        options: ImportOptions | None = None,
    ) -> ImportStats:
        """Import a CCX 3.0 package from raw bytes.

        Opens + validates the package (fail-closed: a non-OK validation
        report raises), then persists templates → nodes → edges → sources
        in FK order, upserting every entity by its stable CCX IRI.

        Args:
            data: The ``.ccx`` package bytes.
            options: Import options (defaults if not provided).

        Returns:
            ``ImportStats`` with per-kind counts, the conformance classes,
            and any warnings. A validation failure raises rather than
            returning a stats object with errors.

        Raises:
            DataIntegrityError: When ``ccx-format`` validation fails
                (Core integrity is mandatory / fail-closed).
        """
        options = options or ImportOptions()
        stats = ImportStats()

        # All ccx-format reads + the synchronous storage upserts are blocking;
        # run the whole pipeline off the event loop so the worker stays
        # responsive (mirrors the v2.0 importer's to_thread offload).
        await asyncio.to_thread(self._import_sync, data, options, stats)

        logger.info(
            "ccx_import_completed",
            database=options.database_name,
            nodes=stats.nodes_imported,
            edges=stats.edges_imported,
            sources=stats.sources_imported,
            chunks=stats.chunks_imported,
            templates=stats.templates_imported,
            conformance_classes=stats.conformance_classes,
            warnings=len(stats.warnings),
        )
        return stats

    async def import_from_path(
        self,
        path: Path,
        options: ImportOptions | None = None,
    ) -> ImportStats:
        """Import a CCX 3.0 package from a file path.

        Reads the bytes off the event loop, then delegates to
        :meth:`import_from_bytes`.

        Args:
            path: Path to the ``.ccx`` package file.
            options: Import options.

        Returns:
            ``ImportStats`` from :meth:`import_from_bytes`.
        """
        data = await asyncio.to_thread(path.read_bytes)
        return await self.import_from_bytes(data, options)

    # ------------------------------------------------------------------
    # Synchronous pipeline (runs in a worker thread)
    # ------------------------------------------------------------------

    def _import_sync(self, data: bytes, options: ImportOptions, stats: ImportStats) -> None:
        """Open + validate the package, then persist its contents.

        Storage upserts and ccx-format reads are synchronous, so the whole
        flow runs in one thread (called via ``asyncio.to_thread``).
        """
        import ccx

        pkg = ccx.open_package(data)

        # 1. Fail-closed validation. Core integrity is mandatory: a package
        #    that does not validate is rejected outright.
        report = pkg.validate()
        stats.conformance_classes = list(report.classes)
        stats.warnings.extend(str(warning) for warning in report.warnings)
        if not report.ok:
            errors = "; ".join(str(error) for error in report.errors) or "unknown error"
            message = f"CCX package failed validation: {errors}"
            raise DataIntegrityError(message, details={"errors": list(report.errors)})

        manifest = pkg.manifest
        stats.package_version = getattr(manifest, "package_version", None)
        stats.checksum_verified = True  # ccx-format validates integrity by construction

        database_name = options.database_name

        # 2. Templates first (FK: nodes/edges reference template_id).
        template_name_to_id: dict[str, str] = {}
        template_iri_to_id: dict[str, str] = {}
        if options.import_templates:
            self._import_templates(
                pkg, database_name, stats, template_name_to_id, template_iri_to_id
            )

        # 3. Default knowledge graph → nodes (+ buffered edges).
        node_iri_to_id: dict[str, str] = {}
        node_iri_to_label: dict[str, str] = {}
        chunk_iri_to_id: dict[str, str] = {}
        relationship_edges: list[dict[str, Any]] = []
        plain_triple_edges: list[dict[str, Any]] = []
        if options.import_knowledge:
            self._import_nodes(
                pkg,
                database_name,
                stats,
                template_name_to_id,
                node_iri_to_id,
                node_iri_to_label,
                relationship_edges,
                plain_triple_edges,
            )
            # 4. Edges (after every node IRI is resolvable).
            self._import_edges(
                database_name,
                stats,
                template_name_to_id,
                template_iri_to_id,
                node_iri_to_id,
                relationship_edges,
                plain_triple_edges,
            )

        # Record the KNOWLEDGE nodes (captured before lens app-graph nodes are
        # added to the IRI map below). These are what the search-indexing step
        # re-embeds + indexes, and what a single-source import links to its
        # source — lens nodes are saved views, not source-owned entities.
        stats.imported_node_ids = list(node_iri_to_id.values())

        # 3b. Lens nodes (the chaoscypher.lenses app graph). Imported as
        #     knowledge-shaped nodes, upserted by ccx_iri exactly like default
        #     graph nodes, so re-import is idempotent. Independent of
        #     import_knowledge so a lens-only package still lands its lenses.
        self._import_lenses(
            pkg,
            database_name,
            stats,
            template_name_to_id,
            node_iri_to_id,
            node_iri_to_label,
        )

        # 4b. Workflows app graph (triggers). The current export shape is
        #     insufficient to rebuild a working trigger (it references a
        #     workflow that is not itself exported), so this does not silently
        #     ignore the graph: it logs a clear warning with a count and
        #     records it on the stats. See internal/TODO.md (P2).
        if options.import_workflows:
            self._import_workflows(pkg, database_name, stats)

        # 5. Sources / chunks / citations.
        if options.import_sources and self.sources is not None:
            self._import_sources(
                pkg, database_name, stats, node_iri_to_id, node_iri_to_label, chunk_iri_to_id
            )
            # 6. Own this import's templates by its source so they cascade-delete
            #    with it (mirrors extraction's source-scoped templates).
            self._link_templates_to_source(
                database_name, template_name_to_id, template_iri_to_id, stats.imported_source_ids
            )

        # 7. Restore bundled embeddings (node + chunk vectors) when the package's
        #    embedding model matches this machine — pure storage writes, so the
        #    later index op skips re-embedding (and a model-less import still
        #    becomes searchable). A model mismatch leaves them to be re-embedded.
        self._restore_embeddings(pkg, database_name, node_iri_to_id, chunk_iri_to_id, stats)

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def _import_templates(
        self,
        pkg: Any,
        database_name: str,
        stats: ImportStats,
        template_name_to_id: dict[str, str],
        template_iri_to_id: dict[str, str],
    ) -> None:
        """Rebuild templates from the ``chaoscypher.templates`` named graph.

        The named graph is the authoritative template record: each member
        carries the minted template IRI, ``name``, ``template_type``,
        ``properties`` (PropertyDefinitions), ``icon``, ``color``. Templates
        have NO ``ccx_iri`` column, so idempotency keys on the recovered
        local id (``custom_id``), falling back to a ``(name, template_type)``
        match for foreign packages.

        A MISSING templates graph is NOT an error (à-la-carte packages).
        Builds ``template_name -> id`` and ``template_iri -> id`` maps so the
        node/edge importer can resolve type terms and relationship-template
        refs.
        """
        templates_doc = self._find_app_graph(pkg, _TEMPLATES_GRAPH)
        if templates_doc is None:
            return

        existing = self.graph.list_templates(include_disabled_sources=True)
        existing_by_id = {tmpl.id: tmpl for tmpl in existing}
        existing_by_key = {(tmpl.name, tmpl.template_type): tmpl for tmpl in existing}

        for member in templates_doc.get("@graph", []):
            iri = member.get("@id")
            name = member.get("name")
            template_type = member.get("template_type") or "node"
            if not iri or not name:
                stats.warnings.append(f"Skipping template with missing @id/name: {member!r}")
                continue

            local_id = ccx_identity.local_id_from_iri(iri)

            # Idempotency: an existing template (by recovered local id, then
            # by name+type) is reused rather than re-created.
            reused = None
            if local_id is not None and local_id in existing_by_id:
                reused = existing_by_id[local_id]
            elif (name, template_type) in existing_by_key:
                reused = existing_by_key[(name, template_type)]

            if reused is not None:
                template_name_to_id[name] = reused.id
                template_iri_to_id[iri] = reused.id
                stats.templates_skipped += 1
                continue

            template_create = TemplateCreate(
                name=name,
                template_type=template_type,
                description=member.get("description"),
                properties=self._property_definitions(member.get("properties")),
                icon=member.get("icon"),
                color=member.get("color"),
            )
            # Idempotency for templates keys on the target database's
            # ``(name, template_type)`` (handled by the reuse branch above),
            # NOT on the recovered local id: ``graph_templates.id`` is a
            # GLOBAL primary key, so reusing the original id as ``custom_id``
            # would collide with a same-id row in another database (e.g.
            # importing into the same DB file the package was exported from).
            # Let the repo mint a fresh id.
            created = self.graph.create_template(template_create)
            template_name_to_id[name] = created.id
            template_iri_to_id[iri] = created.id
            # Register so duplicate members within the same package reuse it.
            existing_by_id[created.id] = created
            existing_by_key[(name, template_type)] = created
            stats.templates_imported += 1

    @staticmethod
    def _property_definitions(raw: Any) -> list[PropertyDefinition]:
        """Coerce raw template-property dicts into ``PropertyDefinition``s.

        Malformed entries are skipped (a property that fails validation must
        not abort the whole template import); the rest pass through.
        """
        if not isinstance(raw, list):
            return []
        definitions: list[PropertyDefinition] = []
        for prop in raw:
            if isinstance(prop, PropertyDefinition):
                definitions.append(prop)
            elif isinstance(prop, dict):
                try:
                    definitions.append(PropertyDefinition(**prop))
                except Exception:  # pragma: no cover - defensive against drift
                    logger.warning("ccx_import_skip_bad_property", property=prop)
        return definitions

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    def _import_nodes(
        self,
        pkg: Any,
        database_name: str,
        stats: ImportStats,
        template_name_to_id: dict[str, str],
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
        relationship_edges: list[dict[str, Any]],
        plain_triple_edges: list[dict[str, Any]],
    ) -> None:
        """Upsert nodes from the default knowledge graph; buffer edges.

        Each ``@graph`` member is classified: a ``ccx:Relationship`` resource
        is buffered for edge import; everything else is a node, which is
        upserted by IRI and whose plain-triple object-references are buffered
        as deterministic edges.
        """
        knowledge_doc = self._find_knowledge_graph(pkg)
        if knowledge_doc is None:
            return

        for member in knowledge_doc.get("@graph", []):
            if member.get("@type") == _RELATIONSHIP_TYPE:
                relationship_edges.append(ccx_import_mapping.relationship_to_edge(member))
                continue

            self._upsert_jsonld_node(
                member, database_name, template_name_to_id, node_iri_to_id, node_iri_to_label
            )
            stats.nodes_imported += 1

            plain_triple_edges.extend(ccx_import_mapping.plain_triples_to_edges(member))

    def _upsert_jsonld_node(
        self,
        member: dict[str, Any],
        database_name: str,
        template_name_to_id: dict[str, str],
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
    ) -> str:
        """Upsert one JSON-LD node member by IRI; record id + label maps.

        Shared by the default-knowledge-graph and ``chaoscypher.lenses``
        importers so a lens node is reconstructed exactly like a knowledge
        node (upsert-by-IRI → idempotent re-import). Returns the local id.
        """
        ccx_iri, node_kwargs = ccx_import_mapping.jsonld_entity_to_node(member)
        template_id = self._resolve_node_template(
            node_kwargs.get("type_term"), template_name_to_id, database_name
        )
        label = node_kwargs.get("label") or ccx_iri
        node_create = NodeCreate(
            template_id=template_id,
            label=label,
            entity_type=node_kwargs.get("entity_type"),
            properties=node_kwargs.get("properties") or {},
        )
        row = self.graph.upsert_node_by_ccx_iri(ccx_iri, node_create, database_name)
        local_id: str = row["id"]
        node_iri_to_id[ccx_iri] = local_id
        node_iri_to_label[ccx_iri] = label
        return local_id

    def _resolve_node_template(
        self,
        type_term: str | None,
        template_name_to_id: dict[str, str],
        database_name: str,
    ) -> str:
        """Resolve a node's ``@type`` term to a template id.

        A type term that matches an imported template name uses that
        template. Otherwise (a bare entity, or a type term with no template —
        common for neutral CCX packages) a single shared default node
        template is synthesized/selected and cached in
        ``template_name_to_id`` under :data:`_DEFAULT_NODE_TEMPLATE_NAME`.
        """
        if type_term and type_term in template_name_to_id:
            return template_name_to_id[type_term]
        return self._default_node_template_id(template_name_to_id, database_name)

    def _default_node_template_id(
        self,
        template_name_to_id: dict[str, str],
        database_name: str,
    ) -> str:
        """Return (creating once) the shared default node-template id."""
        cached = template_name_to_id.get(_DEFAULT_NODE_TEMPLATE_NAME)
        if cached is not None:
            return cached

        # Reuse an existing default template if one already exists (idempotent
        # re-import / a prior package).
        for tmpl in self.graph.list_templates(include_disabled_sources=True):
            if tmpl.name == _DEFAULT_NODE_TEMPLATE_NAME and tmpl.template_type == "node":
                template_name_to_id[_DEFAULT_NODE_TEMPLATE_NAME] = tmpl.id
                return tmpl.id

        created = self.graph.create_template(
            TemplateCreate(
                name=_DEFAULT_NODE_TEMPLATE_NAME,
                template_type="node",
                description="Default template for imported entities without a CCX template.",
            )
        )
        template_name_to_id[_DEFAULT_NODE_TEMPLATE_NAME] = created.id
        return created.id

    # ------------------------------------------------------------------
    # Lenses (chaoscypher.lenses app graph)
    # ------------------------------------------------------------------

    def _import_lenses(
        self,
        pkg: Any,
        database_name: str,
        stats: ImportStats,
        template_name_to_id: dict[str, str],
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
    ) -> None:
        """Import lens nodes from the ``chaoscypher.lenses`` named graph.

        The exporter routes lens nodes (``system_lens``) out of the neutral
        knowledge graph into this app graph, shaped as JSON-LD node objects.
        Each member is upserted by its ``@id`` exactly like a knowledge node,
        so a lens round-trips and a re-import is idempotent. A MISSING lenses
        graph is not an error (most packages carry none). The recovered local
        ids feed ``node_iri_to_id`` so a citation pointing at a lens still
        resolves.
        """
        lenses_doc = self._find_app_graph(pkg, _LENSES_GRAPH)
        if lenses_doc is None:
            return

        for member in lenses_doc.get("@graph", []):
            if not isinstance(member, dict) or not member.get("@id"):
                stats.warnings.append(f"Skipping malformed lens member: {member!r}")
                continue
            self._upsert_jsonld_node(
                member, database_name, template_name_to_id, node_iri_to_id, node_iri_to_label
            )
            stats.nodes_imported += 1

    # ------------------------------------------------------------------
    # Workflows (chaoscypher.workflows app graph)
    # ------------------------------------------------------------------

    def _import_workflows(self, pkg: Any, database_name: str, stats: ImportStats) -> None:
        """Surface (but do not silently drop) the ``chaoscypher.workflows`` graph.

        The current export shape is insufficient to faithfully rebuild a
        workflow trigger: ``service._workflows_graph`` emits trigger rows whose
        NOT-NULL ``workflow_id`` FK points at a ``workflows`` row that the
        exporter never includes in the package, so a ``create_trigger`` would
        violate the FK. Rather than silently ignore the graph (the original
        data-loss bug), we count its members, emit a clear warning, and record
        it on the stats so the gap is visible. See ``internal/TODO.md`` (P2)
        for the bounded work needed to make workflows round-trip.
        """
        workflows_doc = self._find_app_graph(pkg, _WORKFLOWS_GRAPH)
        if workflows_doc is None:
            return
        members = workflows_doc.get("@graph", [])
        count = len(members)
        if count == 0:
            return
        logger.warning(
            "ccx_import_workflows_not_reconstructed",
            workflow_members=count,
            reason="export shape lacks workflow definitions; triggers reference unexported workflows",
        )
        stats.warnings.append(
            f"Skipping {count} chaoscypher.workflows member(s): the export shape does not "
            "carry the workflow definitions needed to rebuild triggers (see internal/TODO.md)."
        )

    # ------------------------------------------------------------------
    # Edges
    # ------------------------------------------------------------------

    def _import_edges(
        self,
        database_name: str,
        stats: ImportStats,
        template_name_to_id: dict[str, str],
        template_iri_to_id: dict[str, str],
        node_iri_to_id: dict[str, str],
        relationship_edges: list[dict[str, Any]],
        plain_triple_edges: list[dict[str, Any]],
    ) -> None:
        """Upsert reified-relationship and plain-triple edges by IRI."""
        edge_template_by_predicate: dict[str, str] = {}

        for edge in relationship_edges:
            self._upsert_edge(
                ccx_iri=edge["ccx_iri"],
                subject_iri=edge.get("subject_iri"),
                object_iri=edge.get("object_iri"),
                predicate=edge.get("predicate"),
                properties=edge.get("properties") or {},
                template_id=self._resolve_relationship_template(
                    edge.get("template_iri"),
                    edge.get("predicate"),
                    template_iri_to_id,
                    template_name_to_id,
                    edge_template_by_predicate,
                    database_name,
                ),
                database_name=database_name,
                node_iri_to_id=node_iri_to_id,
                stats=stats,
            )

        for edge in plain_triple_edges:
            predicate = edge.get("predicate")
            self._upsert_edge(
                ccx_iri=edge["ccx_iri"],
                subject_iri=edge.get("subject_iri"),
                object_iri=edge.get("object_iri"),
                predicate=predicate,
                properties={},
                template_id=self._edge_template_for_predicate(
                    predicate,
                    template_name_to_id,
                    edge_template_by_predicate,
                    database_name,
                ),
                database_name=database_name,
                node_iri_to_id=node_iri_to_id,
                stats=stats,
            )

    def _upsert_edge(
        self,
        *,
        ccx_iri: str,
        subject_iri: str | None,
        object_iri: str | None,
        predicate: str | None,
        properties: dict[str, Any],
        template_id: str,
        database_name: str,
        node_iri_to_id: dict[str, str],
        stats: ImportStats,
    ) -> None:
        """Resolve endpoints to local node ids and upsert one edge by IRI.

        A dangling edge (an endpoint IRI that resolves to no imported node)
        is skipped with a warning rather than aborting the import — the
        endpoint node is absent from this à-la-carte package.
        """
        source_node_id = node_iri_to_id.get(subject_iri) if subject_iri else None
        target_node_id = node_iri_to_id.get(object_iri) if object_iri else None
        if source_node_id is None or target_node_id is None:
            stats.warnings.append(
                f"Skipping dangling edge {ccx_iri}: unresolved endpoint "
                f"(subject={subject_iri}, object={object_iri})"
            )
            return

        edge_create = EdgeCreate(
            template_id=template_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            label=predicate or "related_to",
            properties=properties,
        )
        self.graph.upsert_edge_by_ccx_iri(ccx_iri, edge_create, database_name)
        stats.edges_imported += 1

    def _resolve_relationship_template(
        self,
        template_iri: str | None,
        predicate: str | None,
        template_iri_to_id: dict[str, str],
        template_name_to_id: dict[str, str],
        edge_template_by_predicate: dict[str, str],
        database_name: str,
    ) -> str:
        """Resolve a reified relationship's edge template.

        Prefers the explicit ``ccx:relationshipTemplate`` ref (via the
        ``template_iri -> id`` map, then the recovered local id). Falls back
        to a per-predicate edge template (existing whose name == predicate,
        else synthesized).
        """
        if template_iri is not None:
            mapped = template_iri_to_id.get(template_iri)
            if mapped is not None:
                return mapped
            local_id = ccx_identity.local_id_from_iri(template_iri)
            if local_id is not None:
                tmpl = self.graph.get_template(local_id)
                if tmpl is not None:
                    template_iri_to_id[template_iri] = tmpl.id
                    return tmpl.id
        return self._edge_template_for_predicate(
            predicate, template_name_to_id, edge_template_by_predicate, database_name
        )

    def _edge_template_for_predicate(
        self,
        predicate: str | None,
        template_name_to_id: dict[str, str],
        edge_template_by_predicate: dict[str, str],
        database_name: str,
    ) -> str:
        """Return (creating once) an edge template keyed by predicate label.

        Picks an existing edge template whose name == the predicate, else
        synthesizes one. Cached per predicate so all edges with the same
        predicate share a template (and re-import is idempotent).
        """
        key = predicate or "related_to"
        cached = edge_template_by_predicate.get(key)
        if cached is not None:
            return cached

        # An imported edge template whose name matches the predicate wins.
        mapped = template_name_to_id.get(key)
        if mapped is not None:
            tmpl = self.graph.get_template(mapped)
            if tmpl is not None and tmpl.template_type == "edge":
                edge_template_by_predicate[key] = mapped
                return mapped

        for tmpl in self.graph.list_templates(template_type="edge", include_disabled_sources=True):
            if tmpl.name == key:
                edge_template_by_predicate[key] = tmpl.id
                return tmpl.id

        created = self.graph.create_template(
            TemplateCreate(
                name=key,
                template_type="edge",
                description=f"Synthesized edge template for predicate '{key}'.",
            )
        )
        edge_template_by_predicate[key] = created.id
        return created.id

    # ------------------------------------------------------------------
    # Sources / chunks / citations
    # ------------------------------------------------------------------

    def _import_sources(
        self,
        pkg: Any,
        database_name: str,
        stats: ImportStats,
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
        chunk_iri_to_id: dict[str, str],
    ) -> None:
        """Upsert sources, then their chunks and citations.

        ``pkg.sources()`` returns a flat list of ``ccx:Source`` and
        ``ccx:Chunk`` records. Sources are upserted first (with ``full_text``
        recovered from the ``text`` asset when present), then each chunk is
        created and its citations attached.
        """
        # The caller (`_import_sync`) only invokes this when a source repo is
        # wired; assert so the type checker narrows the storage calls below.
        assert self.sources is not None

        records = pkg.sources()
        source_records = [r for r in records if r.get("@type") == "ccx:Source"]
        chunk_records = [r for r in records if r.get("@type") == "ccx:Chunk"]

        # source_iri -> (local source id, full_text)
        source_ctx: dict[str, tuple[str, str | None]] = {}
        for src_rec in source_records:
            local_id, full_text = self._upsert_source(pkg, src_rec, database_name)
            source_ctx[src_rec["@id"]] = (local_id, full_text)
            stats.sources_imported += 1
            stats.imported_source_ids.append(local_id)

        # local node id -> local source id, accumulated from citations so the
        # importer can back-fill ``graph_nodes.source_id`` once everything is in.
        node_source_links: dict[str, str] = {}
        for chunk_rec in chunk_records:
            source_iri = (chunk_rec.get("source") or {}).get("@id")
            ctx = source_ctx.get(source_iri) if isinstance(source_iri, str) else None
            if ctx is None:
                stats.warnings.append(
                    f"Skipping chunk {chunk_rec.get('@id')}: unknown source {source_iri}"
                )
                continue
            local_source_id, full_text = ctx
            self._import_chunk(
                chunk_rec,
                local_source_id,
                full_text,
                database_name,
                stats,
                node_iri_to_id,
                node_iri_to_label,
                node_source_links,
                chunk_iri_to_id,
            )

        # Finalize: link nodes back to their source and set the denormalized
        # counters the source-detail UI reads, so a complete import no longer
        # presents as an empty/"pending" source.
        self._finalize_imported_sources(
            database_name, source_ctx, chunk_records, node_source_links, stats.imported_node_ids
        )

    def _finalize_imported_sources(
        self,
        database_name: str,
        source_ctx: dict[str, tuple[str, str | None]],
        chunk_records: list[dict[str, Any]],
        node_source_links: dict[str, str],
        all_node_ids: list[str],
    ) -> None:
        """Back-fill node->source links and the denormalized source counters.

        Mirrors the finalize step a normal extraction->commit runs after its
        write transaction. The importer otherwise leaves ``chunk_count`` and
        ``commit_nodes_created`` at their defaults and every imported node's
        ``source_id`` NULL — which makes a complete import read as an empty,
        unattributed source and orphans its nodes from ``ON DELETE CASCADE``.

        ``all_node_ids`` are this import's knowledge nodes. A SINGLE-source
        import owns all of them, so they are all linked (an entity never cited
        in a chunk would otherwise keep ``source_id`` NULL → invisible to search
        and orphaned on delete). A multi-source bundle can only attribute a node
        via its citation lineage.
        """
        assert self.sources is not None

        # Chunks per source (by source IRI) → chunk_count.
        chunks_by_source_iri: dict[str, int] = {}
        for chunk_rec in chunk_records:
            source_iri = (chunk_rec.get("source") or {}).get("@id")
            if isinstance(source_iri, str):
                chunks_by_source_iri[source_iri] = chunks_by_source_iri.get(source_iri, 0) + 1

        # Node ids grouped by their (local) source id → source_id back-fill +
        # commit_nodes_created.
        node_ids_by_source: dict[str, list[str]] = {}
        for node_id, src_id in node_source_links.items():
            node_ids_by_source.setdefault(src_id, []).append(node_id)

        single_source = len(source_ctx) == 1
        for source_iri, (local_source_id, _full_text) in source_ctx.items():
            node_ids = (
                all_node_ids if single_source else node_ids_by_source.get(local_source_id, [])
            )
            # Count nodes ACTUALLY attributed to this source (assign only links
            # rows whose source_id was NULL), not len(node_ids) — an entity that
            # already belongs to another source is not this source's creation, so
            # the denormalized counter must not over-report it.
            linked = (
                self.graph.assign_source_to_nodes(node_ids, local_source_id, database_name)
                if node_ids
                else 0
            )
            self.sources.update_source_columns(
                source_id=local_source_id,
                database_name=database_name,
                updates={
                    "chunk_count": chunks_by_source_iri.get(source_iri, 0),
                    "commit_nodes_created": linked,
                    # Imports arrive fully committed; never auto-kick extraction
                    # on them (an embed job, if ever enqueued, reads this flag).
                    "auto_analyze": False,
                },
            )

    def _link_templates_to_source(
        self,
        database_name: str,
        template_name_to_id: dict[str, str],
        template_iri_to_id: dict[str, str],
        imported_source_ids: list[str],
    ) -> None:
        """Own this import's templates by its source so they cascade-delete.

        Templates are source-scoped: a source's templates cascade-delete with it
        via the ``graph_templates.source_id`` FK. The importer creates templates
        before the source (FK order), leaving ``source_id`` NULL, so an imported
        source's templates would survive its delete — unlike extracted ones.
        Link them once the source lands.

        Only a single-source import can unambiguously own its templates (a
        multi-source bundle shares one template set); multi-source imports are
        left unlinked. System templates (shared defaults the importer may have
        matched by name) are excluded so a source never re-owns them.
        """
        if len(imported_source_ids) != 1:
            return
        source_id = imported_source_ids[0]
        template_ids = set(template_name_to_id.values()) | set(template_iri_to_id.values())
        owned = [tid for tid in template_ids if not tid.startswith("system_template_")]
        if owned:
            self.graph.assign_source_to_templates(owned, source_id, database_name)

    def _upsert_source(
        self,
        pkg: Any,
        src_rec: dict[str, Any],
        database_name: str,
    ) -> tuple[str, str | None]:
        """Upsert one ``ccx:Source`` record; return ``(local id, full_text)``.

        ``full_text`` is read from the ``text`` asset (a content-addressed
        path) via ``pkg.asset_bytes`` when the record carries one; absent
        when the package used inline chunk content.
        """
        from chaoscypher_core.utils.id import generate_id

        assert self.sources is not None
        ccx_iri = src_rec["@id"]
        full_text = self._read_source_text(pkg, src_rec)
        local_id = ccx_identity.local_id_from_iri(ccx_iri)
        display_name = src_rec.get("title") or local_id or ccx_iri

        # ``id`` is minted fresh (not the recovered local id): ``sources.id``
        # is a GLOBAL primary key, so reusing the original id collides when
        # importing into the same DB file. ``upsert_source_by_ccx_iri`` only
        # uses this id on CREATE; on re-import it matches by ``(db, ccx_iri)``
        # and the supplied id is ignored. ``filename`` carries the display name;
        # ``filepath`` is EMPTY because an imported source has no on-disk staged
        # file — giving it a bare display name as a "path" made
        # ``delete_source_files`` resolve ``Path(name).parent`` to the process
        # CWD and rmtree it (the source-delete frontend-wipe bug). Both columns
        # are NOT NULL; "" satisfies that without naming a file that isn't there.
        source_dict: dict[str, Any] = {
            "id": generate_id("source"),
            "title": src_rec.get("title"),
            "filename": display_name,
            "filepath": "",
            "source_type": "imported",
            "status": "committed",
            "full_text": full_text,
        }
        if src_rec.get("extractedBy"):
            source_dict["extraction_mode"] = src_rec["extractedBy"]
        # Restore the extraction domain so the graph view renders this imported
        # source's group icon exactly as an extracted source's (the domain →
        # icon mapping lives in the cortex graph API + the registry).
        if src_rec.get("extractionDomain"):
            source_dict["extraction_domain"] = src_rec["extractionDomain"]

        row = self.sources.upsert_source_by_ccx_iri(ccx_iri, source_dict, database_name)
        self._restore_source_tags(row["id"], src_rec.get("keywords"), database_name)
        return row["id"], full_text

    def _restore_source_tags(self, source_id: str, keywords: Any, database_name: str) -> None:
        """Re-create + assign a source's tags from its CCX ``keywords``.

        Find-or-create each tag by name in the target database (so tags shared
        across sources collapse to one row), then assign it. Idempotent:
        ``assign_tag`` no-ops an existing assignment and the name lookup reuses
        an existing tag, so a re-import doesn't duplicate.
        """
        if not keywords or self.sources is None:
            return
        from chaoscypher_core.utils.id import generate_id

        existing = {t["name"]: t["id"] for t in self.sources.list_tags(database_name)}
        for name in keywords:
            if not isinstance(name, str) or not name.strip():
                continue
            tag_id = existing.get(name)
            if tag_id is None:
                created = self.sources.create_tag(
                    {"id": generate_id("tag"), "name": name, "database_name": database_name}
                )
                tag_id = created["id"]
                existing[name] = tag_id
            self.sources.assign_tag(source_id, tag_id, database_name)

    @staticmethod
    def _read_source_text(pkg: Any, src_rec: dict[str, Any]) -> str | None:
        """Resolve a source's full text from its ``text`` asset, if any."""
        text_ref = src_rec.get("text")
        if not isinstance(text_ref, str) or not text_ref:
            return None
        try:
            decoded: str = pkg.asset_bytes(text_ref).decode("utf-8")
        except Exception:  # pragma: no cover - defensive against asset drift
            logger.warning("ccx_import_source_text_unreadable", asset=text_ref)
            return None
        return decoded

    def _import_chunk(
        self,
        chunk_rec: dict[str, Any],
        local_source_id: str,
        full_text: str | None,
        database_name: str,
        stats: ImportStats,
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
        node_source_links: dict[str, str],
        chunk_iri_to_id: dict[str, str],
    ) -> None:
        """Create one chunk (and its citations) from a ``ccx:Chunk`` record."""
        assert self.sources is not None
        chunk = ccx_import_mapping.ccx_chunk_to_chunk(chunk_rec, full_text)
        ccx_iri = chunk.get("ccx_iri")
        local_chunk_id = ccx_identity.local_id_from_iri(ccx_iri) if ccx_iri else None
        chunk_id = local_chunk_id or self._chunk_id_from_iri(ccx_iri)
        # Map the chunk IRI -> local id so the embedding restore can join a
        # bundled chunk vector (keyed by the same IRI) to this row. Recorded
        # before the idempotent skip below so a re-import still maps it.
        if ccx_iri:
            chunk_iri_to_id[ccx_iri] = chunk_id

        chunk_data: dict[str, Any] = {
            "id": chunk_id,
            "database_name": database_name,
            "source_id": local_source_id,
            # Recover the ORIGINAL chunk index from the chunk IRI fragment
            # (``<source-iri>#chunk-<index>``) so the round-tripped index
            # matches the source. A running import counter would re-number
            # chunks and silently lose their position.
            "chunk_index": self._chunk_index_from_iri(ccx_iri, stats.chunks_imported),
            "content": chunk.get("content") or "",
            "char_start": chunk.get("char_start"),
            "char_end": chunk.get("char_end"),
            "status": "committed",
        }
        # Idempotency: a chunk row keyed on the deterministic id already
        # present means a prior import created it — skip the duplicate insert
        # (chunks have no ccx_iri column to upsert on).
        if self.sources.get_chunk(chunk_id, database_name) is not None:
            self._import_citations(
                chunk.get("citations") or [],
                chunk_id,
                local_source_id,
                database_name,
                stats,
                node_iri_to_id,
                node_iri_to_label,
                node_source_links,
            )
            return

        self.sources.create_chunk(chunk_data)
        stats.chunks_imported += 1
        self._import_citations(
            chunk.get("citations") or [],
            chunk_id,
            local_source_id,
            database_name,
            stats,
            node_iri_to_id,
            node_iri_to_label,
            node_source_links,
        )

    def _restore_embeddings(
        self,
        pkg: Any,
        database_name: str,
        node_iri_to_id: dict[str, str],
        chunk_iri_to_id: dict[str, str],
        stats: ImportStats,
    ) -> None:
        """Restore node + chunk vectors from the package's embedding sidecar.

        ONLY when the package's embedding model + dimensions match this machine —
        cross-model vectors live in a different space, so restoring them would
        silently corrupt search; a mismatch is recorded and left for the index
        op to re-embed. Pure storage writes (no embedding provider), so a package
        with matching vectors imports searchable even on a model-less machine:
        the post-import index op then finds nodes already at the right dimension
        and chunks already ``embedded_at``-stamped, skips re-embedding, and just
        pushes the restored vectors into the search index.
        """
        from chaoscypher_core.app_config import get_settings

        descriptor = next(
            (d for d in pkg.embeddings() if d.get("included") and d.get("path")), None
        )
        if descriptor is None:
            return

        settings = get_settings()
        if (
            descriptor.get("model") != settings.embedding.model
            or int(descriptor.get("dimensions", 0)) != settings.search.vector_dimensions
        ):
            stats.embeddings_need_regeneration = True
            stats.embedding_mismatch_reason = (
                f"package embeddings {descriptor.get('model')!r}/"
                f"{descriptor.get('dimensions')}d do not match local "
                f"{settings.embedding.model!r}/{settings.search.vector_dimensions}d"
            )
            return

        try:
            table = pkg.read_embeddings(descriptor)
        except Exception:  # pragma: no cover - defensive (missing pyarrow / drift)
            logger.warning("ccx_import_embeddings_unreadable", exc_info=True)
            return

        ids = table.column("id").to_pylist()
        vectors = table.column("vector").to_pylist()
        node_updates: dict[str, list[float]] = {}
        chunk_updates: dict[str, list[float]] = {}
        for iri, vector in zip(ids, vectors, strict=False):
            if not vector:
                continue
            if iri in node_iri_to_id:
                node_updates[node_iri_to_id[iri]] = list(vector)
            elif iri in chunk_iri_to_id:
                chunk_updates[chunk_iri_to_id[iri]] = list(vector)

        if node_updates:
            self.graph.update_node_embeddings_batch(node_updates)
        if chunk_updates and self.sources is not None:
            model = descriptor["model"]
            dims = int(descriptor["dimensions"])
            for chunk_id, vector in chunk_updates.items():
                b64 = base64.b64encode(np.array(vector, dtype=np.float32).tobytes()).decode("utf-8")
                self.sources.update_chunk_embedding(chunk_id, b64, model, dims, "indexed")
            # Stamp embedded_at so the index op's list_unembedded_chunks gate
            # excludes them — otherwise it re-embeds despite the restored vector.
            self.sources.mark_chunks_embedded(
                chunk_ids=list(chunk_updates),
                embedded_at=datetime.now(UTC),
                database_name=database_name,
            )
        stats.embeddings_restored = len(node_updates) + len(chunk_updates)

    @staticmethod
    def _chunk_index_from_iri(ccx_iri: str | None, fallback: int) -> int:
        """Recover the original chunk index from a ``...#chunk-<index>`` IRI.

        The exporter encodes the source chunk index in the chunk IRI fragment
        (see ``ccx_mapping.source_records``). Recovering it here keeps the
        round-tripped ``chunk_index`` aligned with the source rather than a
        re-numbered import counter. Falls back to ``fallback`` (the running
        counter) when the fragment is absent or non-numeric — e.g. a chunk
        whose IRI used the chunk id rather than an integer index.
        """
        if not ccx_iri:
            return fallback
        fragment = ccx_iri.rpartition("#chunk-")[2]
        if fragment and fragment.lstrip("-").isdigit():
            return int(fragment)
        return fallback

    @staticmethod
    def _chunk_id_from_iri(ccx_iri: str | None) -> str:
        """Derive a stable chunk id from a foreign chunk IRI.

        Chunk IRIs are ``<source_iri>#chunk-<fragment>``. For foreign
        sources (no recoverable local id) hash the IRI to a deterministic
        ``chunk_<...>`` id so re-import lands on the same row.
        """
        import hashlib

        from chaoscypher_core.utils.id import generate_id

        if not ccx_iri:
            return generate_id("chunk")
        digest = hashlib.sha256(ccx_iri.encode("utf-8")).hexdigest()[:24]
        return f"chunk_{digest}"

    def _import_citations(
        self,
        citations: list[dict[str, Any]],
        chunk_id: str,
        local_source_id: str,
        database_name: str,
        stats: ImportStats,
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
        node_source_links: dict[str, str],
    ) -> None:
        """Create entity citations attached to a chunk.

        Each ``ccx:citation`` entry references a cited entity (a node IRI),
        a confidence, and an extraction method. The entity reference is
        resolved against the node IRI→id map the node importer built earlier in
        the same run; for a normal round-trip the cited node was imported
        first, so this resolves and the skip-with-warning fallback does NOT
        fire. ``entity_uri`` stores the resolved LOCAL node id (NOT the CCX
        IRI) — a normal extraction stores the local id there, and consumers
        (the source's Entity Distribution panel, and the graph's source-group
        node, which the frontend places by matching entity ids to graph node
        ids) resolve against the graph's local ids, not the package IRIs.
        ``entity_label`` is recovered from the imported node's label, falling
        back to the IRI.
        """
        assert self.sources is not None
        for citation in citations:
            entity_ref = citation.get("ccx:citation")
            entity_iri = entity_ref.get("@id") if isinstance(entity_ref, dict) else None
            local_node_id = node_iri_to_id.get(entity_iri) if entity_iri else None
            if entity_iri is None or local_node_id is None:
                # Citation without a resolvable in-package entity — record a
                # warning but keep importing.
                stats.warnings.append(
                    f"Skipping citation on chunk {chunk_id}: unresolved entity {entity_iri}"
                )
                continue

            # Record the cited node's source so the importer can back-fill
            # ``graph_nodes.source_id`` after every source/chunk lands (the
            # node was created before its source was known). First source wins.
            node_source_links.setdefault(local_node_id, local_source_id)

            # Carry the cited node's ``entity_type`` onto the citation. The
            # source's Entity Distribution panel groups citations by
            # ``entity_type``; without this every imported entity falls into a
            # single "Unknown" bucket even though the node itself is typed.
            cited_node = self.graph.get_node_by_ccx_iri(entity_iri, database_name)
            entity_type = cited_node.get("entity_type") if cited_node else None

            from chaoscypher_core.utils.id import generate_id

            citation_data: dict[str, Any] = {
                "id": generate_id("citation"),
                "database_name": database_name,
                "entity_uri": local_node_id,
                "entity_label": node_iri_to_label.get(entity_iri) or entity_iri,
                "entity_type": entity_type,
                "source_id": local_source_id,
                "chunk_id": chunk_id,
                "confidence": citation.get("ccx:confidence", 1.0),
                "extraction_method": citation.get("ccx:extractionMethod") or "imported",
            }
            self.sources.create_citation(citation_data)
            stats.citations_imported += 1

    # ------------------------------------------------------------------
    # Named-graph lookup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_knowledge_graph(pkg: Any) -> dict[str, Any] | None:
        """Return the default (``role == "default"``) graph document dict."""
        for doc in pkg.graph_documents():
            if doc.role == _KNOWLEDGE_ROLE:
                graph: dict[str, Any] = doc.doc
                return graph
        return None

    @staticmethod
    def _find_app_graph(pkg: Any, name: str) -> dict[str, Any] | None:
        """Return a ``chaoscypher.<name>`` named-graph document dict, or None."""
        for doc in pkg.graph_documents():
            if doc.namespace == _APP_NAMESPACE and doc.name == name:
                graph: dict[str, Any] = doc.doc
                return graph
        return None


__all__ = ["CcxImporter"]
