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
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from chaoscypher_core.exceptions import DataIntegrityError, ValidationError
from chaoscypher_core.models import EdgeCreate, NodeCreate, PropertyDefinition, TemplateCreate
from chaoscypher_core.services.export import ccx_identity, ccx_mapping
from chaoscypher_core.services.graph.resolution.apply import (
    edge_create_snapshot,
    node_create_snapshot,
)
from chaoscypher_core.services.package.importer import ccx_import_mapping
from chaoscypher_core.services.package.importer.core_types import (
    DEFAULT_NODE_TEMPLATE_NAME,
    CoreTypeMapping,
    bundle_packs,
)
from chaoscypher_core.services.package.importer.models import ImportOptions, ImportStats
from chaoscypher_core.services.workflows.tools.engine.memory_render import MEMORY_SOURCE_TYPE


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.settings import EngineSettings


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
_DEFAULT_NODE_TEMPLATE_NAME = DEFAULT_NODE_TEMPLATE_NAME

# ``sources.source_type`` of a source an imported package wrote; a memory keeps its own.
_IMPORTED_SOURCE_TYPE = "imported"


KEY_SIGNATURE_FORMATS = frozenset({"ed25519"})
"""The signature kinds the importer refuses on failure: a key signature over the manifest bytes."""


def refuse_failed_signatures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Raise on a failed key signature; return the results the library could not verify (skipped).

    ``ccx.Package.verify_signatures`` reports every declared signature as
    ``{path, format, verified, error}``. A failed ed25519 signature means the
    manifest bytes changed since signing: refused. A format this install cannot
    verify (a sigstore bundle without its extra) is not evidence of tampering:
    logged and skipped, so such packages still import as they did before.
    """
    skipped: list[dict[str, Any]] = []
    for result in results:
        if result.get("verified"):
            continue
        if result.get("format") in KEY_SIGNATURE_FORMATS:
            raise ValidationError("package signature failed", details=result)
        logger.warning("package_signature_unverifiable", **{k: str(v) for k, v in result.items()})
        skipped.append(result)
    return skipped


@dataclass(frozen=True)
class _ImportedEdge:
    """One edge this import wrote: what its relationship citations need to name it."""

    id: str
    label: str
    template_id: str
    source_label: str
    target_label: str


@dataclass
class _ImportedEdges:
    """The edges this import wrote, by the relation they state, and what the package wrote for each.

    A relationship citation or a closure names its relation by its ends and predicate
    (``ccx_mapping.edge_reference``); ``by_key`` resolves that to the local edge.
    ``readings`` is each edge's membership snapshot (``edge_create_snapshot``).
    """

    by_key: dict[tuple[str, str, str], _ImportedEdge] = field(default_factory=dict)
    readings: dict[str, dict[str, Any]] = field(default_factory=dict)


def _core_template_create(core_type: str) -> TemplateCreate:
    """The node template of ``core_type`` as the commit creates it: the floor's suggestion, built once.

    ``TemplateExtractor.generate_suggestions_from_entities`` gives the floor
    template's description, icon and colour; ``template_from_suggestion`` is
    the commit's one builder of it.
    """
    from chaoscypher_core.services.sources.engine.commit.template import (
        template_from_suggestion,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
        FLOOR_NAME,
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.factory import (
        get_domain_registry,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.template_extractor import (
        TemplateExtractor,
    )

    floor = get_domain_registry().get_domain(FLOOR_NAME)
    floor_templates = floor.get_templates().get("node_templates") if floor is not None else None
    suggestions = TemplateExtractor.generate_suggestions_from_entities(
        [{"type": core_type}], domain_templates=floor_templates
    )
    template = template_from_suggestion(suggestions[0]) if suggestions else None
    if template is None:  # pragma: no cover - a core type always has a suggestion
        return TemplateCreate(name=core_type, template_type="node")
    return template


class CcxImporter:
    """Ingest a CCX 3.0 package into Chaos Cypher via upsert-by-IRI."""

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        sources_repository: Any | None = None,
        workflow_db: Any | None = None,
        settings: EngineSettings | None = None,
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
            settings: Engine settings the rebuild of a shared node ranks
                survivors by; None reads the configured ones.
        """
        self.graph = graph_repository
        self.sources = sources_repository
        self.settings = settings
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
        if stats.core_types_mapped:
            logger.info(
                "ccx_import_core_types_mapped",
                database=options.database_name,
                nodes=sum(row["count"] for row in stats.core_types_mapped.values()),
                mapped=stats.core_types_mapped,
                templates_repointed=stats.templates_repointed,
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

        # 0. Signatures: a key signature the manifest declares must verify over the
        #    manifest bytes the package holds (tampering); a package with none verifies
        #    nothing; a kind the library cannot check here is logged, not refused.
        refuse_failed_signatures(pkg.verify_signatures())

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

        # ``graph_documents()`` decompresses and json-parses EVERY graph on each
        # call (no cache in ccx-format), so parse once here and hand the list to
        # each pass instead of re-parsing the knowledge graph per app-graph lookup.
        graph_docs = pkg.graph_documents()

        database_name = options.database_name

        # The bundle onto the core types (a database from before them is
        # exported, the product updated, the export imported): read with the
        # packs its sources were extracted with.
        source_records: list[dict[str, Any]] = list(pkg.sources())
        mapping = CoreTypeMapping(packs=bundle_packs(source_records))

        # 2. Templates first (FK: nodes/edges reference template_id).
        template_name_to_id: dict[str, str] = {}
        template_iri_to_id: dict[str, str] = {}
        if options.import_templates:
            self._import_templates(
                graph_docs, database_name, stats, template_name_to_id, template_iri_to_id, mapping
            )

        # 3. Default knowledge graph → nodes (+ buffered edges).
        node_iri_to_id: dict[str, str] = {}
        node_iri_to_label: dict[str, str] = {}
        # entity_iri -> entity_type, filled by the same node pass. Citations
        # read the cited node's type from here instead of issuing a full-row
        # ``get_node_by_ccx_iri`` SELECT per citation.
        node_iri_to_type: dict[str, str | None] = {}
        # local node id -> what this package wrote for it (its membership snapshot)
        node_readings: dict[str, dict[str, Any]] = {}
        chunk_iri_to_id: dict[str, str] = {}
        relationship_edges: list[dict[str, Any]] = []
        plain_triple_edges: list[dict[str, Any]] = []
        imported_edges = _ImportedEdges()
        if options.import_knowledge:
            self._import_nodes(
                graph_docs,
                database_name,
                stats,
                template_name_to_id,
                node_iri_to_id,
                node_iri_to_label,
                node_iri_to_type,
                relationship_edges,
                plain_triple_edges,
                template_iri_to_id=template_iri_to_id,
                mapping=mapping,
                node_readings=node_readings,
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
                imported_edges=imported_edges,
                node_iri_to_label=node_iri_to_label,
            )

        stats.core_types_mapped = mapping.as_dict()
        stats.templates_repointed = dict(sorted(mapping.templates.items()))

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
            graph_docs,
            database_name,
            stats,
            template_name_to_id,
            node_iri_to_id,
            node_iri_to_label,
            node_iri_to_type,
            template_iri_to_id=template_iri_to_id,
        )

        # 4b. Workflows app graph (triggers). The current export shape is
        #     insufficient to rebuild a working trigger (it references a
        #     workflow that is not itself exported), so this does not silently
        #     ignore the graph: it logs a clear warning with a count and
        #     records it on the stats. See internal/TODO.md (P2).
        if options.import_workflows:
            self._import_workflows(graph_docs, database_name, stats)

        # 5. Sources / chunks / citations.
        rebuilt: frozenset[str] = frozenset()
        if options.import_sources and self.sources is not None:
            rebuilt = self._import_sources(
                pkg,
                database_name,
                stats,
                node_iri_to_id,
                node_iri_to_label,
                node_iri_to_type,
                chunk_iri_to_id,
                records=source_records,
                node_readings=node_readings,
                imported_edges=imported_edges,
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

        # 8. A shared node the finalize rebuilt reads differently from the vector it
        #    carries (its previous one, or the package's just restored): cleared, so
        #    the import's indexing, which embeds only vectorless nodes, re-embeds it.
        if rebuilt:
            self.graph.clear_node_embeddings(sorted(rebuilt))
            logger.info("ccx_import_shared_nodes_vectors_cleared", nodes=len(rebuilt))

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    def _import_templates(
        self,
        graph_docs: list[Any],
        database_name: str,
        stats: ImportStats,
        template_name_to_id: dict[str, str],
        template_iri_to_id: dict[str, str],
        mapping: CoreTypeMapping | None = None,
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
        refs. A node template named by an old type (``Character``) is not
        created when ``mapping`` is given: its nodes take their core type's
        template (``CoreTypeMapping.skip_template``).
        """
        templates_doc = self._find_app_graph(graph_docs, _TEMPLATES_GRAPH)
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
            if mapping is not None and mapping.skip_template(member):
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
        graph_docs: list[Any],
        database_name: str,
        stats: ImportStats,
        template_name_to_id: dict[str, str],
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
        node_iri_to_type: dict[str, str | None],
        relationship_edges: list[dict[str, Any]],
        plain_triple_edges: list[dict[str, Any]],
        template_iri_to_id: dict[str, str] | None = None,
        mapping: CoreTypeMapping | None = None,
        node_readings: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Upsert nodes from the default knowledge graph; buffer edges.

        Each ``@graph`` member is classified: a ``ccx:Relationship`` resource
        is buffered for edge import; everything else is a node, which is
        upserted by IRI (on its core type, through ``mapping``) and whose
        plain-triple object-references are buffered as deterministic edges.
        """
        knowledge_doc = self._find_knowledge_graph(graph_docs)
        if knowledge_doc is None:
            return

        for member in knowledge_doc.get("@graph", []):
            if member.get("@type") == _RELATIONSHIP_TYPE:
                relationship_edges.append(ccx_import_mapping.relationship_to_edge(member))
                continue

            self._upsert_jsonld_node(
                member,
                database_name,
                template_name_to_id,
                node_iri_to_id,
                node_iri_to_label,
                node_iri_to_type,
                template_iri_to_id=template_iri_to_id,
                mapping=mapping,
                node_readings=node_readings,
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
        node_iri_to_type: dict[str, str | None],
        template_iri_to_id: dict[str, str] | None = None,
        mapping: CoreTypeMapping | None = None,
        node_readings: dict[str, dict[str, Any]] | None = None,
    ) -> str:
        """Upsert one JSON-LD node member by IRI; record id + label + type maps.

        Shared by the default-knowledge-graph and ``chaoscypher.lenses``
        importers so a lens node is reconstructed exactly like a knowledge
        node (upsert-by-IRI → idempotent re-import). Returns the local id.
        With ``mapping`` (knowledge nodes) the node is stored on its core
        type, and a node of a re-pointed template takes its core type's.
        ``node_readings`` gets the entity as this package wrote it
        (``node_create_snapshot``): the snapshot the source that cites it is
        recorded with, whatever the row reads once other sources share it.
        """
        ccx_iri, node_kwargs = ccx_import_mapping.jsonld_entity_to_node(member)
        core_template = None
        if mapping is not None:
            node_kwargs = mapping.place(node_kwargs)
            core_template = mapping.core_template_type(node_kwargs, template_iri_to_id or {})
        if core_template is not None:
            template_id = self._core_template_id(core_template, template_name_to_id)
        else:
            template_id = self._resolve_node_template(
                node_kwargs.get("type_term"),
                template_name_to_id,
                database_name,
                template_iri=node_kwargs.get("template_iri"),
                template_iri_to_id=template_iri_to_id,
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
        if node_readings is not None:
            node_readings[local_id] = node_create_snapshot(local_id, node_create)
        node_iri_to_id[ccx_iri] = local_id
        node_iri_to_label[ccx_iri] = label
        # Recorded from the persisted row (not the payload) so it is exactly
        # what a ``get_node_by_ccx_iri`` read-back would have returned — the
        # citation importer reads its ``entity_type`` from here.
        node_iri_to_type[ccx_iri] = row.get("entity_type")
        return local_id

    def _resolve_node_template(
        self,
        type_term: str | None,
        template_name_to_id: dict[str, str],
        database_name: str,
        *,
        template_iri: str | None = None,
        template_iri_to_id: dict[str, str] | None = None,
    ) -> str:
        """Resolve a node's template id.

        An explicit ``cc:template`` reference (the exporter records the
        node's template IRI) wins, so an extracted node keeps its generic
        system template while ``@type`` carries its specific entity type.
        Otherwise a ``@type`` term that matches an imported template name
        uses that template. Otherwise (a bare entity, or a type term with no
        template — common for neutral CCX packages) a single shared default
        node template is synthesized/selected and cached in
        ``template_name_to_id`` under :data:`_DEFAULT_NODE_TEMPLATE_NAME`.
        """
        if template_iri and template_iri_to_id and template_iri in template_iri_to_id:
            return template_iri_to_id[template_iri]
        if type_term and type_term in template_name_to_id:
            return template_name_to_id[type_term]
        return self._default_node_template_id(template_name_to_id, database_name)

    def _default_node_template_id(
        self,
        template_name_to_id: dict[str, str],
        database_name: str,
    ) -> str:
        """Return (creating once) the shared default node-template id."""
        return self._named_node_template_id(
            _DEFAULT_NODE_TEMPLATE_NAME,
            template_name_to_id,
            lambda: TemplateCreate(
                name=_DEFAULT_NODE_TEMPLATE_NAME,
                template_type="node",
                description="Default template for imported entities without a CCX template.",
            ),
        )

    def _core_template_id(self, core_type: str, template_name_to_id: dict[str, str]) -> str:
        """Return (creating once) the node template of ``core_type``: the one a re-pointed node takes.

        The bundle's own template of that core type when it carries one, else
        the database's, else one created as the commit creates it
        (``template_from_suggestion`` over the floor's suggestion for the core
        type: its description, icon and colour).
        """
        return self._named_node_template_id(
            core_type, template_name_to_id, lambda: _core_template_create(core_type)
        )

    def _named_node_template_id(
        self,
        name: str,
        template_name_to_id: dict[str, str],
        build: Callable[[], TemplateCreate],
    ) -> str:
        """The id of the node template named ``name``: this import's, else the database's, else ``build()`` created.

        Cached in ``template_name_to_id`` (idempotent across a re-import and
        across packages: an existing template of the name is reused).
        """
        cached = template_name_to_id.get(name)
        if cached is not None:
            return cached

        for tmpl in self.graph.list_templates(include_disabled_sources=True):
            if tmpl.name == name and tmpl.template_type == "node":
                template_name_to_id[name] = tmpl.id
                return tmpl.id

        created = self.graph.create_template(build())
        template_name_to_id[name] = created.id
        return created.id

    # ------------------------------------------------------------------
    # Lenses (chaoscypher.lenses app graph)
    # ------------------------------------------------------------------

    def _import_lenses(
        self,
        graph_docs: list[Any],
        database_name: str,
        stats: ImportStats,
        template_name_to_id: dict[str, str],
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
        node_iri_to_type: dict[str, str | None],
        template_iri_to_id: dict[str, str] | None = None,
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
        lenses_doc = self._find_app_graph(graph_docs, _LENSES_GRAPH)
        if lenses_doc is None:
            return

        for member in lenses_doc.get("@graph", []):
            if not isinstance(member, dict) or not member.get("@id"):
                stats.warnings.append(f"Skipping malformed lens member: {member!r}")
                continue
            self._upsert_jsonld_node(
                member,
                database_name,
                template_name_to_id,
                node_iri_to_id,
                node_iri_to_label,
                node_iri_to_type,
                template_iri_to_id=template_iri_to_id,
            )
            stats.nodes_imported += 1

    # ------------------------------------------------------------------
    # Workflows (chaoscypher.workflows app graph)
    # ------------------------------------------------------------------

    def _import_workflows(
        self, graph_docs: list[Any], database_name: str, stats: ImportStats
    ) -> None:
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
        workflows_doc = self._find_app_graph(graph_docs, _WORKFLOWS_GRAPH)
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
        *,
        imported_edges: _ImportedEdges | None = None,
        node_iri_to_label: dict[str, str] | None = None,
    ) -> None:
        """Upsert reified-relationship and plain-triple edges by IRI.

        Each written edge is recorded in ``imported_edges`` under the relation it
        states, for the relationship citations and closures the sources carry.
        """
        edge_template_by_predicate: dict[str, str] = {}
        written = imported_edges if imported_edges is not None else _ImportedEdges()
        labels = node_iri_to_label or {}

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
                imported_edges=written,
                node_iri_to_label=labels,
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
                imported_edges=written,
                node_iri_to_label=labels,
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
        imported_edges: _ImportedEdges,
        node_iri_to_label: dict[str, str],
    ) -> None:
        """Resolve endpoints to local node ids and upsert one edge by IRI.

        A dangling edge (an endpoint IRI that resolves to no imported node)
        is skipped with a warning rather than aborting the import — the
        endpoint node is absent from this à-la-carte package. A written edge
        is recorded in ``imported_edges`` with the snapshot it was written from.
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
        row = self.graph.upsert_edge_by_ccx_iri(ccx_iri, edge_create, database_name)
        stats.edges_imported += 1
        edge_id = row.get("id") if isinstance(row, dict) else None
        if not isinstance(edge_id, str) or subject_iri is None or object_iri is None:
            return
        imported_edges.by_key[(subject_iri, edge_create.label, object_iri)] = _ImportedEdge(
            id=edge_id,
            label=edge_create.label,
            template_id=template_id,
            source_label=node_iri_to_label.get(subject_iri) or subject_iri,
            target_label=node_iri_to_label.get(object_iri) or object_iri,
        )
        imported_edges.readings[edge_id] = edge_create_snapshot(edge_id, edge_create)

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
        node_iri_to_type: dict[str, str | None],
        chunk_iri_to_id: dict[str, str],
        records: list[dict[str, Any]] | None = None,
        node_readings: dict[str, dict[str, Any]] | None = None,
        imported_edges: _ImportedEdges | None = None,
    ) -> frozenset[str]:
        """Upsert sources, then their chunks and citations.

        ``pkg.sources()`` returns a flat list of ``ccx:Source`` and
        ``ccx:Chunk`` records (``records`` when the caller read them already).
        Sources are upserted first (with ``full_text`` recovered from the
        ``text`` asset when present), then each chunk is created and its
        citations attached, then the relationship citations (a sentence may
        name a later chunk) and the edges' memberships and closures
        (:meth:`_link_imported_edges`). Returns the shared nodes the finalize
        rebuilt with a changed text (:meth:`_finalize_imported_sources`).
        """
        # The caller (`_import_sync`) only invokes this when a source repo is
        # wired; assert so the type checker narrows the storage calls below.
        assert self.sources is not None

        if records is None:
            records = pkg.sources()
        source_records = [r for r in records if r.get("@type") == "ccx:Source"]
        chunk_records = [r for r in records if r.get("@type") == "ccx:Chunk"]

        # source_iri -> (local source id, full_text)
        source_ctx: dict[str, tuple[str, str | None]] = {}
        closures: list[tuple[str, dict[str, Any]]] = []
        for src_rec in source_records:
            local_id, full_text = self._upsert_source(pkg, src_rec, database_name)
            source_ctx[src_rec["@id"]] = (local_id, full_text)
            stats.sources_imported += 1
            stats.imported_source_ids.append(local_id)
            closures.extend(
                (local_id, closed)
                for closed in src_rec.get(ccx_mapping.CLOSES_KEY) or []
                if isinstance(closed, dict)
            )

        # local node id -> the local source ids citing it (first citer first),
        # accumulated from citations so the importer can back-fill
        # ``graph_nodes.source_id`` and the memberships once everything is in.
        node_source_links: dict[str, list[str]] = {}
        cited_relations: list[tuple[dict[str, Any], str]] = []
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
                node_iri_to_type,
                node_source_links,
                chunk_iri_to_id,
            )
            if chunk_rec.get(ccx_mapping.RELATIONSHIP_CITATION_KEY):
                cited_relations.append((chunk_rec, local_source_id))

        edges = imported_edges if imported_edges is not None else _ImportedEdges()
        edge_source_links = self._import_relationship_citations(
            cited_relations, chunk_iri_to_id, edges, database_name, stats
        )

        # Finalize: link nodes back to their source and set the denormalized
        # counters the source-detail UI reads, so a complete import no longer
        # presents as an empty/"pending" source.
        rebuilt = self._finalize_imported_sources(
            database_name,
            source_ctx,
            chunk_records,
            node_source_links,
            stats.imported_node_ids,
            node_readings or {},
        )
        self._link_imported_edges(
            database_name,
            [local_id for local_id, _text in source_ctx.values()],
            edge_source_links,
            edges,
            closures,
            stats,
        )
        return rebuilt

    def _import_relationship_citations(
        self,
        cited: list[tuple[dict[str, Any], str]],
        chunk_iri_to_id: dict[str, str],
        edges: _ImportedEdges,
        database_name: str,
        stats: ImportStats,
    ) -> dict[str, list[str]]:
        """Write each chunk's relationship citations; return each cited edge's citing sources.

        ``cited`` pairs a chunk record carrying ``ccx_mapping.RELATIONSHIP_CITATION_KEY``
        with its local source id. Each citation names its relation by its ends and
        predicate; one this import wrote no edge for is skipped with a warning. A
        cited sentence keeps its text and chunk-local index, and its chunk is the
        one it names, else the citation's own. The ids are stable per chunk, edge
        and position, so a re-import writes the same rows.
        """
        assert self.sources is not None
        rows: list[dict[str, Any]] = []
        links: dict[str, list[str]] = {}
        for chunk_rec, local_source_id in cited:
            chunk_id = chunk_iri_to_id.get(str(chunk_rec.get("@id")))
            if chunk_id is None:
                continue
            for ordinal, citation in enumerate(
                chunk_rec.get(ccx_mapping.RELATIONSHIP_CITATION_KEY) or []
            ):
                key = ccx_import_mapping.edge_reference_key(citation)
                edge = edges.by_key.get(key) if key is not None else None
                if edge is None:
                    stats.warnings.append(
                        f"Skipping relationship citation on chunk {chunk_id}: unresolved relation {key}"
                    )
                    continue
                citers = links.setdefault(edge.id, [])
                if local_source_id not in citers:
                    citers.append(local_source_id)
                sentences = ccx_import_mapping.cited_sentences(citation, chunk_iri_to_id, chunk_id)
                rows.append(
                    {
                        "id": self._relationship_citation_id(chunk_id, edge.id, ordinal),
                        "database_name": database_name,
                        "edge_id": edge.id,
                        "edge_label": edge.label,
                        "edge_type": edge.template_id,
                        "source_entity_label": edge.source_label,
                        "target_entity_label": edge.target_label,
                        "source_id": local_source_id,
                        "chunk_id": chunk_id,
                        "extraction_method": "imported",
                        "justification": citation.get("cc:justification"),
                        "citation_metadata": {"sentences": sentences} if sentences else None,
                    }
                )
        if rows:
            self.sources.create_relationship_citations_batch(rows)
        return links

    @staticmethod
    def _relationship_citation_id(chunk_id: str, edge_id: str, ordinal: int) -> str:
        """A stable relationship citation id: the chunk, the edge, the entry's position."""
        digest = hashlib.sha256(f"{chunk_id}|{edge_id}|{ordinal}".encode()).hexdigest()[:24]
        return f"relcitation_{digest}"

    def _link_imported_edges(
        self,
        database_name: str,
        source_ids: list[str],
        edge_source_links: dict[str, list[str]],
        edges: _ImportedEdges,
        closures: list[tuple[str, dict[str, Any]]],
        stats: ImportStats,
    ) -> None:
        """Give the imported edges their member sources, rebuild them, and restore the closures.

        A single-source import attests every edge it wrote, as it does every
        node; a bundle attests an edge from each source citing it. Each member's
        snapshot is the package's reading of the edge. The member rebuild
        (``release.rebuild_from_members``, the one a commit and a release run)
        then writes each edge's ``valid_from`` from its members (a memory's) and
        re-homes it. Last, each closure a memory carries (``ccx_mapping.CLOSES_KEY``)
        closes its edge at its date, closed by that memory. One unit of work.
        """
        from chaoscypher_core.services.graph.management.release import (
            graph_conventions,
            rebuild_from_members,
        )

        assert self.sources is not None
        if len(source_ids) == 1:
            members = {edge_id: [source_ids[0]] for edge_id in edges.readings}
        else:
            members = {e: s for e, s in edge_source_links.items() if e in edges.readings}
        if not members and not closures:
            return
        rows = [
            (edge_id, source_id, edges.readings[edge_id])
            for edge_id, sids in members.items()
            for source_id in sids
        ]
        with self.graph.write_locked():
            if rows:
                self.graph.record_edge_sources(rows)
                conventions = graph_conventions(
                    self.graph, self.sources, database_name, self._engine_settings()
                )
                rebuild_from_members(self.graph, [], sorted(members), conventions)
            for closer_id, closed in closures:
                self._restore_closure(closer_id, closed, edges, stats)

    def _restore_closure(
        self,
        closer_id: str,
        closed: dict[str, Any],
        edges: _ImportedEdges,
        stats: ImportStats,
    ) -> None:
        """Close the edge ``closed`` names at its ``cc:validTo``, closed by ``closer_id``."""
        key = ccx_import_mapping.edge_reference_key(closed)
        edge = edges.by_key.get(key) if key is not None else None
        valid_to = ccx_import_mapping.iso_date(closed.get(ccx_mapping.VALID_TO_KEY))
        current = self.graph.get_edge(edge.id) if edge is not None else None
        if current is None or valid_to is None:
            stats.warnings.append(
                f"Skipping closure by source {closer_id}: unresolved relation {key}"
            )
            return
        self.graph.set_edge_validity(
            current.id,
            valid_from=current.valid_from,
            valid_to=valid_to,
            closed_by_source_id=closer_id,
        )

    def _finalize_imported_sources(
        self,
        database_name: str,
        source_ctx: dict[str, tuple[str, str | None]],
        chunk_records: list[dict[str, Any]],
        node_source_links: dict[str, list[str]],
        all_node_ids: list[str],
        node_readings: dict[str, dict[str, Any]],
    ) -> frozenset[str]:
        """Back-fill node->source links and the denormalized source counters.

        Mirrors the finalize step a normal extraction->commit runs after its
        write transaction. The importer otherwise leaves ``chunk_count`` and
        ``commit_nodes_created`` at their defaults and every imported node's
        ``source_id`` NULL — which makes a complete import read as an empty,
        unattributed source whose delete would release none of its nodes.

        ``all_node_ids`` are this import's knowledge nodes. A SINGLE-source
        import owns all of them, so they are all linked (an entity never cited
        in a chunk would otherwise keep ``source_id`` NULL → invisible to search
        and kept on delete). A multi-source bundle can only attribute a node
        via its citation lineage: its first citer is its home, every citer a member.

        Each member's snapshot is the package's own entity (``node_readings``),
        not the node's row: on a node another source already shares, the row
        holds every member's names, and a later release would rebuild the node
        from them (#795 follow-up M5). A node the package has no entity for
        falls back to its own pre-merge reading (``assign_source_to_nodes``).
        Every linked node a source from outside this import attests too is
        then rebuilt from its members (:func:`_rebuild_shared_nodes`): the
        upsert wrote this package's content over it, and its row is the fold
        of every reading. A node only this import's sources attest is left as
        written: they all hold the package's one reading of it.

        An upserted node this import does not link whose sole member is one
        of its sources (a re-import of the package that wrote it, or an
        entity no chunk of the bundle cites) has that source's reading
        re-recorded from the package: the upsert names no writer and touches
        no membership, since the same IRI may be another package's. Returns
        the shared nodes the rebuild changed.
        """
        assert self.sources is not None

        # Chunks per source (by source IRI) → chunk_count.
        chunks_by_source_iri: dict[str, int] = {}
        for chunk_rec in chunk_records:
            source_iri = (chunk_rec.get("source") or {}).get("@id")
            if isinstance(source_iri, str):
                chunks_by_source_iri[source_iri] = chunks_by_source_iri.get(source_iri, 0) + 1

        # Node ids grouped by their first citing (local) source → source_id
        # back-fill + commit_nodes_created; every later citer is a member too.
        node_ids_by_source: dict[str, list[str]] = {}
        also_cited_by: dict[str, list[str]] = {}
        for node_id, src_ids in node_source_links.items():
            node_ids_by_source.setdefault(src_ids[0], []).append(node_id)
            for other in src_ids[1:]:
                also_cited_by.setdefault(other, []).append(node_id)

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
                self.graph.assign_source_to_nodes(
                    node_ids, local_source_id, database_name, readings=node_readings
                )
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
        # A node the bundle cites from several sources is attested by each: the
        # home stays with the first, the others become members (#795), so
        # deleting the home source keeps the node the others still cite.
        for src_id, node_ids in also_cited_by.items():
            self.graph.assign_source_to_nodes(
                node_ids, src_id, database_name, readings=node_readings
            )
        imported = {local_id for local_id, _text in source_ctx.values()}
        linked_ids = set(node_source_links) | (set(all_node_ids) if single_source else set())
        members = self.graph.node_member_sources([*node_readings, *linked_ids])
        self._refresh_own_readings(database_name, node_readings, members, linked_ids, imported)
        return self._rebuild_shared_nodes(
            database_name, {n: members.get(n, set()) for n in linked_ids}, imported
        )

    def _refresh_own_readings(
        self,
        database_name: str,
        node_readings: dict[str, dict[str, Any]],
        members: dict[str, set[str]],
        linked: set[str],
        imported: set[str],
    ) -> None:
        """Re-record the package reading of each unlinked node only one of ``imported`` attests."""
        by_source: dict[str, list[str]] = {}
        for node_id in node_readings:
            sids = members.get(node_id, set())
            if node_id not in linked and len(sids) == 1 and sids <= imported:
                by_source.setdefault(next(iter(sids)), []).append(node_id)
        for source_id, node_ids in by_source.items():
            self.graph.assign_source_to_nodes(
                node_ids, source_id, database_name, readings=node_readings
            )

    def _rebuild_shared_nodes(
        self, database_name: str, members: dict[str, set[str]], imported: set[str]
    ) -> frozenset[str]:
        """Rebuild each linked node a source outside this import attests too, from its members.

        ``upsert_node_by_ccx_iri`` writes the package's content over an
        existing row; when a source outside ``imported`` (this import's
        sources) attests the node too (a merge folded it in), the row must read
        as the fold of every member's reading, as after a commit or a release
        (``release.rebuild_from_members``, under the graph's conventions and
        the engine settings, as a release ranks). A node only ``imported``
        attests holds one reading, the package's, and is left as written: a
        fresh multi-source bundle keeps its first citer as home.

        ``members`` maps each linked node to its member sources (one batched
        read by the caller). Returns the nodes whose text the rebuild changed:
        the import clears their vectors once the bundle's are restored, so its
        indexing, which embeds only vectorless nodes, re-embeds them from the
        new text. Their full-text rows follow at indexing too.
        """
        from chaoscypher_core.services.graph.management.release import (
            graph_conventions,
            rebuild_from_members,
        )

        shared = sorted(nid for nid, sids in members.items() if sids - imported)
        if not shared:
            return frozenset()
        assert self.sources is not None  # called from the source finalize, which has them
        conventions = graph_conventions(
            self.graph, self.sources, database_name, self._engine_settings()
        )
        rebuilt = rebuild_from_members(self.graph, shared, [], conventions)
        logger.info(
            "ccx_import_shared_nodes_rebuilt",
            nodes=len(shared),
            changed=len(rebuilt.changed_node_ids),
            sample=sorted(rebuilt.changed_node_ids)[:10],
        )
        return rebuilt.changed_node_ids

    def _engine_settings(self) -> EngineSettings:
        """The engine settings this importer ranks by: the caller's, else the configured ones."""
        if self.settings is not None:
            return self.settings
        from chaoscypher_core.app_config import get_settings
        from chaoscypher_core.app_config.engine_factory import build_engine_settings

        return build_engine_settings(get_settings())

    def _link_templates_to_source(
        self,
        database_name: str,
        template_name_to_id: dict[str, str],
        template_iri_to_id: dict[str, str],
        imported_source_ids: list[str],
    ) -> None:
        """Own this import's templates by its source so its release takes them.

        Templates are source-scoped: releasing a source deletes its templates
        nothing else uses (``release_source``, D6). The importer creates templates
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
        from chaoscypher_core.utils.id import generate_hex_id, generate_id

        assert self.sources is not None
        ccx_iri = src_rec["@id"]
        full_text = self._read_source_text(pkg, src_rec)
        local_id = ccx_identity.local_id_from_iri(ccx_iri)
        display_name = src_rec.get("title") or local_id or ccx_iri
        provenance = ccx_import_mapping.source_provenance(src_rec)
        is_memory = provenance.get("source_type") == MEMORY_SOURCE_TYPE

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
        # A memory comes back as a memory (spec 2026-10-20 § 8b): its type, its
        # ``by``, scope and ``valid_from``, and an id minted as ``remember``
        # mints one, so its handle reads like any memory's. Any other source is
        # an imported one, as before.
        source_dict: dict[str, Any] = {
            "id": generate_hex_id() if is_memory else generate_id("source"),
            "title": src_rec.get("title"),
            "filename": display_name,
            "filepath": "",
            "source_type": MEMORY_SOURCE_TYPE if is_memory else _IMPORTED_SOURCE_TYPE,
            "status": "committed",
            "full_text": full_text,
        }
        if is_memory:
            source_dict.update({k: v for k, v in provenance.items() if k != "source_type"})
        extracted_by = ccx_import_mapping.extracted_by_text(src_rec.get("extractedBy"))
        if extracted_by:
            source_dict["extraction_mode"] = extracted_by
        # Restore the extraction domain so the graph view renders this imported
        # source's group icon exactly as an extracted source's (the domain →
        # icon mapping lives in the cortex graph API + the registry).
        if src_rec.get("extractionDomain"):
            source_dict["extraction_domain"] = src_rec["extractionDomain"]
        # The packs in their stack order (the label above is sorted); a bundle
        # from before the packs has none, and the label is read for them.
        if isinstance(src_rec.get("extractionPacks"), list):
            source_dict["extraction_packs"] = list(src_rec["extractionPacks"])

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
        node_iri_to_type: dict[str, str | None],
        node_source_links: dict[str, list[str]],
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
        if self.sources.chunk_exists(chunk_id, database_name):
            self._import_citations(
                chunk.get("citations") or [],
                chunk_id,
                local_source_id,
                database_name,
                stats,
                node_iri_to_id,
                node_iri_to_label,
                node_iri_to_type,
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
            node_iri_to_type,
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
            self.sources.update_chunk_embeddings_batch(
                {
                    chunk_id: base64.b64encode(np.array(vector, dtype=np.float32).tobytes()).decode(
                        "utf-8"
                    )
                    for chunk_id, vector in chunk_updates.items()
                },
                embedding_model=model,
                embedding_dimensions=dims,
                status="indexed",
            )
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

    @staticmethod
    def _citation_id(chunk_id: str, local_node_id: str, ordinal: int) -> str:
        """Derive a stable citation id for a chunk's citation entry.

        ``create_citations_batch`` dedups by id ("content-addressed stable
        IDs") — a random id per row made every re-import insert a full
        duplicate citation set, violating the importer's idempotency
        contract. Hash the chunk, cited node, and the entry's position in
        the chunk's citation list so identical package bytes always land on
        the same rows while distinct citations (even duplicates of the same
        entity within one chunk) keep distinct ids.
        """
        import hashlib

        digest = hashlib.sha256(f"{chunk_id}|{local_node_id}|{ordinal}".encode()).hexdigest()[:24]
        return f"citation_{digest}"

    def _import_citations(
        self,
        citations: list[dict[str, Any]],
        chunk_id: str,
        local_source_id: str,
        database_name: str,
        stats: ImportStats,
        node_iri_to_id: dict[str, str],
        node_iri_to_label: dict[str, str],
        node_iri_to_type: dict[str, str | None],
        node_source_links: dict[str, list[str]],
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

        Written with ONE ``create_citations_batch`` call per chunk rather than
        the single-row ``create_citation`` (which commits per row — the
        importer opens no ``transaction()``, so that was one COMMIT per
        citation across the whole package).
        """
        assert self.sources is not None
        citation_rows: list[dict[str, Any]] = []
        for ordinal, citation in enumerate(citations):
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
            # ``graph_nodes.source_id`` and the node's memberships after every
            # source/chunk lands (the node was created before its source was
            # known). The first citer becomes the home; every citer a member.
            citers = node_source_links.setdefault(local_node_id, [])
            if local_source_id not in citers:
                citers.append(local_source_id)

            # Carry the cited node's ``entity_type`` onto the citation. The
            # source's Entity Distribution panel groups citations by
            # ``entity_type``; without this every imported entity falls into a
            # single "Unknown" bucket even though the node itself is typed.
            # Read from the map the node pass filled — a per-citation
            # ``get_node_by_ccx_iri`` was a full-row SELECT (embedding vector +
            # properties JSON) to fetch this one scalar.
            entity_type = node_iri_to_type.get(entity_iri)

            citation_data: dict[str, Any] = {
                "id": self._citation_id(chunk_id, local_node_id, ordinal),
                "database_name": database_name,
                "entity_uri": local_node_id,
                "entity_label": node_iri_to_label.get(entity_iri) or entity_iri,
                "entity_type": entity_type,
                "source_id": local_source_id,
                "chunk_id": chunk_id,
                "confidence": citation.get("ccx:confidence", 1.0),
                "extraction_method": citation.get("ccx:extractionMethod") or "imported",
            }
            citation_rows.append(citation_data)

        if citation_rows:
            self.sources.create_citations_batch(citation_rows)
            stats.citations_imported += len(citation_rows)

    # ------------------------------------------------------------------
    # Named-graph lookup helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_knowledge_graph(graph_docs: list[Any]) -> dict[str, Any] | None:
        """Return the default (``role == "default"``) graph document dict."""
        for doc in graph_docs:
            if doc.role == _KNOWLEDGE_ROLE:
                graph: dict[str, Any] = doc.doc
                return graph
        return None

    @staticmethod
    def _find_app_graph(graph_docs: list[Any], name: str) -> dict[str, Any] | None:
        """Return a ``chaoscypher.<name>`` named-graph document dict, or None."""
        for doc in graph_docs:
            if doc.namespace == _APP_NAMESPACE and doc.name == name:
                graph: dict[str, Any] = doc.doc
                return graph
        return None


__all__ = ["CcxImporter"]
