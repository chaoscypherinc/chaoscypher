# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity Commit Handler.

Handles entity node creation during import commits, including
batch creation and source tracking via entity properties.

Extracted from commit_service.py for SRP compliance.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.constants import is_system_template_id
from chaoscypher_core.models import (
    Node,
    NodeCreate,
)
from chaoscypher_core.services.sources.engine.deduplication.name_identity import (
    GIVEN_ALIASES_KEY,
    NAME_RECORD_KEYS,
)
from chaoscypher_core.services.sources.engine.extraction.utils.offered_types import (
    take_offered_template,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    SUBTYPES_PROPERTY,
    core_typed,
)
from chaoscypher_core.settings import GraphSettings


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.storage_embeddings import EntityEmbeddingStorageProtocol
    from chaoscypher_core.services.sources.engine.commit.matcher import EntityTemplateMatcher

logger = structlog.get_logger(__name__)

# Load graph settings for template IDs and labels
_GRAPH = GraphSettings()
_DEFAULT_NODE_TEMPLATE = _GRAPH.default_node_template


class EntityCommitHandler:
    """Handles entity node creation during import commits.

    Creates entity nodes from extraction results, including
    template matching, property normalization, and batch creation
    with optional embeddings. Source provenance is tracked via entity
    properties (source_document_id, source_document_name) and
    SourceCitation records rather than graph edges.

    Responsibilities:
    - Prepare entity nodes for batch creation
    - Match entities to templates using EntityTemplateMatcher
    - Batch create nodes with embeddings
    - Build entity index/name mappings for relationship creation

    """

    def __init__(
        self,
        graph_repository: GraphRepositoryProtocol,
        source_repository: EntityEmbeddingStorageProtocol,
        entity_matcher: EntityTemplateMatcher,
        database_name: str,
    ):
        """Initialize entity commit handler."""
        self.graph_repository = graph_repository
        self.source_repository = source_repository
        self.entity_matcher = entity_matcher
        self.database_name = database_name

    def prepare_entity_nodes(
        self,
        entities: list[dict],
        all_templates: list[Any],
        suggested_templates: list[dict] | None,
        template_name_to_id: dict[str, str],
        file_info: Any,
        file_id: str,
        source_id: str | None = None,
        offered_templates: Mapping[str, str] | None = None,
    ) -> tuple[list[NodeCreate], list[dict], list[str]]:
        """Prepare entity nodes for batch creation.

        Args:
            entities: List of entity data from commit
            all_templates: All available node templates
            suggested_templates: Suggested templates from analysis
            template_name_to_id: Mapping of template names to IDs
            file_info: Import file info dict
            file_id: Import file ID
            source_id: Source ID for linking nodes to source (for enabled filtering)
            offered_templates: A memory's offered templates (folded name -> id, D26): an
                entity stamped with one (``offered_types.OFFERED_TEMPLATE_KEY``) whose
                template still exists binds to it; the stamp is never stored. Every other
                entity goes through the per-source matcher.

        Returns:
            Tuple of (nodes_to_create, entity_data_list, all_entity_template_ids):
            - nodes_to_create: List of NodeCreate objects
            - entity_data_list: List of entity data dicts
            - all_entity_template_ids: ALL template IDs used by entities (for source linking)

        """
        # Use source_id if provided, fallback to file_id for backwards compat
        embeddings_map = self._load_embeddings(file_id, source_id or file_id)

        nodes_to_create = []
        entity_data_list = []
        all_entity_template_ids: list[str] = []
        # Index once: ``all_templates`` holds every node template in the
        # database (per-source ids, so it grows with the library) and the
        # property lookup below runs once per entity.
        templates_by_id = {t.id: t for t in all_templates}

        for entity in entities:
            # Skip if explicitly marked as not-create
            if entity.get("action") and entity.get("action") != "create":
                continue

            # Handle both nested and flat entity formats. graph_nodes.entity_type
            # stores the core type (spec 2026-10-07 § 6), placed once
            # (``core_typed``): an old per-type name (``Character``) is its core
            # type with the subtype in ``entity_subtype``; a type no core type
            # answers is kept as ``Concept`` flagged ``unplaced_type`` (the type
            # rescue's rule), so the template match below reads the core type.
            entity_data, offered = take_offered_template(
                core_typed(entity.get("entity_data", entity)), offered_templates or {}
            )
            entity_name = entity_data.get("name", "Untitled")

            # Resolve template: an offered one it binds to while it exists, else the matcher
            template_id = (
                offered
                if offered is not None and offered in templates_by_id
                else self._resolve_template_id(
                    entity, entity_data, all_templates, suggested_templates, template_name_to_id
                )
            )

            # Track this template for source linking (ensures proper orphan detection)
            # Only track user templates, not system templates
            if (
                template_id
                and not is_system_template_id(template_id)
                and template_id not in all_entity_template_ids
            ):
                all_entity_template_ids.append(template_id)

            # Build properties
            properties = self._build_entity_properties(
                entity_data, entity_name, template_id, templates_by_id, file_info, file_id
            )

            # Get embedding for this entity if available.
            # Join by the stable ``entity_id`` (stamped by ``normalize_entities``
            # and persisted alongside every stored vector) rather than by list
            # position: ``flag_orphan_entities`` compacts the entity list before
            # this runs, so a positional join would assign every survivor after a
            # dropped orphan a DIFFERENT entity's vector. Fall back to the entity
            # dict itself — deduplication persists embeddings directly on entities
            # so they survive index-changing pipeline steps (hierarchical merge).
            entity_id = entity_data.get("id")
            embedding = (
                embeddings_map.get(entity_id) if entity_id is not None else None
            ) or entity_data.get("embedding")

            # The placed core type, read off the extracted ``type`` (RawEntity)
            # without going through the source_citations.entity_label↔
            # graph_nodes.label join; the pack subtype stays in the properties
            # (``entity_subtype``, and a merge's ``entity_subtypes``).
            entity_type = entity_data.get("type")

            nodes_to_create.append(
                NodeCreate(
                    template_id=template_id,
                    label=entity_name,
                    entity_type=entity_type,
                    properties=properties,
                    embedding=embedding,
                    source_id=source_id,
                )
            )
            entity_data_list.append(entity_data)

        logger.info(
            "entity_nodes_prepared",
            count=len(nodes_to_create),
            template_ids_tracked=len(all_entity_template_ids),
        )
        return nodes_to_create, entity_data_list, all_entity_template_ids

    def _load_embeddings(self, file_id: str, source_id: str) -> dict[str, Any]:
        """Load embeddings map for entities, keyed by stable ``entity_id``.

        Keying by ``entity_id`` (not the stored positional ``entity_index``)
        keeps the join correct after ``flag_orphan_entities`` compacts and
        re-indexes the entity list. Rows without an ``entity_id`` are skipped
        (a missing embedding is safe; a mis-joined one silently corrupts the
        vector index).

        Args:
            file_id: Import file ID
            source_id: Source ID

        Returns:
            Dict mapping ``entity_id`` to embedding

        """
        embeddings_map: dict[str, Any] = {}
        try:
            entity_embeddings = self.source_repository.get_entity_embeddings(source_id)
            if entity_embeddings:
                embeddings_map = {
                    e["entity_id"]: e["embedding"]
                    for e in entity_embeddings
                    if e.get("entity_id") is not None
                }
                logger.info("embeddings_loaded_for_import", count=len(embeddings_map))
        except Exception:
            logger.exception(
                "commit_entity_embeddings_load_failed",
                file_id=file_id,
                source_id=source_id,
            )
        return embeddings_map

    def _resolve_template_id(
        self,
        entity: dict,
        entity_data: dict,
        all_templates: list[Any],
        suggested_templates: list[dict] | None,
        template_name_to_id: dict[str, str],
    ) -> str:
        """Resolve template ID for an entity.

        Per-source templates: Always use this source's templates (from template_name_to_id).
        Ignores any template_id from extraction phase since those could reference
        templates from other sources.

        Args:
            entity: Raw entity dict (template_id ignored for per-source templates)
            entity_data: Entity data dict
            all_templates: All available templates (used for property lookup only)
            suggested_templates: Suggested templates from analysis (not used)
            template_name_to_id: Name to ID mapping for THIS source's templates

        Returns:
            Template ID to use (from this source or system_template_item fallback)

        """
        # Per-source templates: always use matcher which only matches THIS source's templates
        # Don't trust template_id from extraction - it could be from another source
        return self.entity_matcher.match(
            entity_data, all_templates, suggested_templates, template_name_to_id
        )

    def _build_entity_properties(
        self,
        entity_data: dict,
        entity_name: str,
        template_id: str,
        templates_by_id: dict[str, Any],
        file_info: Any,
        file_id: str,
    ) -> dict[str, Any]:
        """Build properties dict for an entity node.

        Args:
            entity_data: Entity data dict
            entity_name: Entity name
            template_id: Resolved template ID
            templates_by_id: All available templates keyed by template ID
            file_info: Import file info
            file_id: Import file ID

        Returns:
            Properties dict for the node

        """
        # Start with entity properties
        properties = (
            entity_data.get("properties", {}).copy() if entity_data.get("properties") else {}
        )

        # Store aliases if extracted (for alias resolution in search)
        aliases = entity_data.get("aliases", [])
        if aliases and isinstance(aliases, list) and len(aliases) > 0:
            properties["aliases"] = aliases
        # What the name rule reads besides the name and aliases (``NAME_RECORD_KEYS``): which
        # aliases were given under this name (the resolver's alias evidence; an empty list
        # says none was), the names a merge folded in (the resolver guards the node by them),
        # the spellings the corpus writes (its survivor ranking) and the descriptors.
        for key in NAME_RECORD_KEYS:
            value = entity_data.get(key)
            if isinstance(value, list) and (value or key == GIVEN_ALIASES_KEY):
                properties[key] = list(value)

        # Normalize property values
        self._normalize_properties(properties)

        # Ensure required properties
        self._ensure_required_properties(
            properties, entity_data, entity_name, template_id, templates_by_id
        )

        # Add source tracking
        self._add_source_tracking(properties, file_info, file_id)

        return properties

    def _normalize_properties(self, properties: dict[str, Any]) -> None:
        """Normalize property values to strings where needed.

        Preserves list properties for: aliases, nicknames, tags
        (these are useful for search and alias resolution)

        Args:
            properties: Properties dict to modify in place

        """
        # Properties to keep as lists (used by search/alias resolution)
        preserve_as_list = {
            "aliases",
            *NAME_RECORD_KEYS,
            "nicknames",
            "tags",
            "also_known_as",
            SUBTYPES_PROPERTY,  # a merge's subtypes (Person/character + Person/author)
        }

        for key, value in list(properties.items()):
            if isinstance(value, list):
                # Preserve certain properties as lists for better querying
                if key in preserve_as_list:
                    properties[key] = [str(v) for v in value if v]
                else:
                    properties[key] = ", ".join(str(v) for v in value)
            elif not isinstance(value, (str, int, float, bool)) and value is not None:
                properties[key] = str(value)

    def _ensure_required_properties(
        self,
        properties: dict[str, Any],
        entity_data: dict,
        entity_name: str,
        template_id: str,
        templates_by_id: dict[str, Any],
    ) -> None:
        """Ensure required properties are present for the template.

        Args:
            properties: Properties dict to modify in place
            entity_data: Entity data dict
            entity_name: Entity name
            template_id: Template ID
            templates_by_id: All available templates keyed by template ID

        """
        # Ensure 'definition' for system_template_item
        if template_id == _DEFAULT_NODE_TEMPLATE and "definition" not in properties:
            context = entity_data.get("context") or entity_data.get("description")
            if isinstance(context, list):
                context = ", ".join(str(c) for c in context)
            properties["definition"] = context or f"Entity: {entity_name}"

        # Get template for property checks
        template = templates_by_id.get(template_id)
        if not template or not template.properties:
            return

        # Ensure 'title' if required
        if "title" not in properties:
            title_prop = next((p for p in template.properties if p.name == "title"), None)
            if title_prop and title_prop.required:
                properties["title"] = entity_name

        # Ensure 'content' if required
        if "content" not in properties:
            content_prop = next((p for p in template.properties if p.name == "content"), None)
            if content_prop and content_prop.required:
                content = (
                    entity_data.get("context")
                    or entity_data.get("description")
                    or entity_data.get("definition")
                )
                if isinstance(content, list):
                    content = ", ".join(str(c) for c in content)
                properties["content"] = content or f"Entity: {entity_name}"

    def _add_source_tracking(
        self, properties: dict[str, Any], file_info: Any, file_id: str
    ) -> None:
        """Add source tracking properties.

        Args:
            properties: Properties dict to modify in place
            file_info: Import file info
            file_id: Import file ID

        """
        properties["source_document_id"] = file_id
        properties["source_document_name"] = file_info.get("filename", "")
        properties["source_type"] = "source processing"
        properties["ingested_at"] = datetime.now(UTC).isoformat()

    async def batch_create_nodes(
        self,
        nodes_to_create: list[NodeCreate],
        entity_data_list: list[dict],
        *,
        reattested: set[str] | None = None,
    ) -> tuple[list[str], dict[int, str], dict[str, str], dict[int, Node], int]:
        """Batch create nodes and build index/name mappings.

        Args:
            nodes_to_create: List of NodeCreate objects
            entity_data_list: List of entity data
            reattested: Filled with the reused node ids this source attests
                again (``upsert_nodes_batch(reattested=)``), for the rebuild.

        Returns:
            Tuple of:
            - created_node_ids: List of node IDs (created or pre-existing) in input order
            - entity_index_to_node_id: Mapping from entity index to node ID
            - entity_name_to_node_id: Mapping from entity name to node ID
            - entity_index_to_node: Mapping from entity index to Node object
            - nodes_actually_inserted: Count of rows newly inserted (not dedup reuses)

        """
        created_nodes: list[str] = []
        entity_index_to_node_id: dict[int, str] = {}
        entity_name_to_node_id: dict[str, str] = {}
        entity_index_to_node: dict[int, Node] = {}

        if not nodes_to_create:
            return (
                created_nodes,
                entity_index_to_node_id,
                entity_name_to_node_id,
                entity_index_to_node,
                0,
            )

        logger.info("batch_node_creation_started", count=len(nodes_to_create))
        # Idempotent UPSERT: re-running commit for the same source
        # reuses existing rows instead of creating duplicates. This
        # is the resumability entry point on the node side.
        batch_created_nodes, inserted_count = await self.graph_repository.upsert_nodes_batch(
            nodes_to_create, reattested=reattested
        )

        for idx, node in enumerate(batch_created_nodes):
            created_nodes.append(node.id)
            entity_index_to_node_id[idx] = node.id
            entity_index_to_node[idx] = node  # Store full Node object for later use

            # Build name→node_id mapping for NLP workflow relationships
            entity_data = entity_data_list[idx]
            entity_name = entity_data.get("name")
            if entity_name:
                entity_name_to_node_id[entity_name] = node.id

            logger.debug("node_created", node_id=node.id, node_label=node.label)

        logger.info("batch_nodes_created", count=len(batch_created_nodes))
        return (
            created_nodes,
            entity_index_to_node_id,
            entity_name_to_node_id,
            entity_index_to_node,
            inserted_count,
        )
