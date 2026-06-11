# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Relationship Commit Handler.

Handles relationship edge creation during import commits, including
edge template creation and batch edge processing.

Extracted from commit_service.py for SRP compliance.
"""

import re
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import (
    EdgeCreate,
    TemplateCreate,
)
from chaoscypher_core.settings import GraphSettings
from chaoscypher_core.templates.visuals import (
    resolve_edge_visuals,
)


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)

_LABEL_NONWORD = re.compile(r"[\s_]+")


def _canonicalize_edge_label(raw: str | None) -> str:
    """Collapse spaces, underscores, and case into a single canonical form.

    'Confides In', 'confides_in', and '  CONFIDES   IN  ' all map to
    'confides_in'. Used to normalize the persisted label column so the
    same fact across chunks reads consistently.

    Args:
        raw: Raw label string from the extraction pipeline.

    Returns:
        Normalized label with spaces and underscores collapsed to single
        underscores and all characters lowercased. Returns an empty string
        for ``None`` or empty input.

    """
    if not raw:
        return ""
    return _LABEL_NONWORD.sub("_", raw.strip().lower()).strip("_")


# Load graph settings for defaults (inverse map now comes from domain configs)
_GRAPH = GraphSettings()
_DEFAULT_RELATIONSHIP_TYPE = _GRAPH.default_relationship_type
_DEFAULT_EDGE_TEMPLATE = _GRAPH.default_edge_template


def _build_edge_properties(rel: dict[str, Any]) -> dict[str, Any]:
    """Build edge properties dict from a relationship record.

    Extracts confidence, justification, evidence references, and any
    custom properties from the relationship dict.

    Args:
        rel: Relationship dict from extraction pipeline.

    Returns:
        Properties dict for RDF edge creation.

    """
    props = rel.get("properties", {}).copy() if rel.get("properties") else {}
    props["confidence"] = rel.get("confidence", 0.0)

    if rel.get("justification"):
        props["justification"] = rel["justification"]
    if rel.get("sent_ref"):
        props["sent_ref"] = rel["sent_ref"]
    if rel.get("chunk_index") is not None:
        props["chunk_index"] = rel["chunk_index"]

    return props


class RelationshipCommitHandler:
    """Handles relationship edge creation during import commits.

    Creates relationship edges from extraction results, including automatic
    edge template creation, support for both name-based (NLP workflow) and
    index-based (standard workflow) relationships, and batch edge creation.
    This handler is part of the commit service's Single Responsibility
    Principle (SRP) refactoring.

    Responsibilities:
    - Batch create edge templates for unique relationship types
    - Prepare relationship edges from extraction results
    - Handle both name-based and index-based relationships
    - Add AI metadata (confidence, justification) to edges
    - Create inverse relationships for bidirectional graph traversal
    - Batch create edges for performance

    Attributes:
        graph_repository: GraphRepository instance for edge/template operations

    Example:
        >>> from chaoscypher_core.services.sources.engine.commit.relation import RelationshipCommitHandler
        >>> from chaoscypher_core.adapters.sqlite.repos import GraphRepository
        >>>
        >>> graph_repo = GraphRepository(graphs_dir="/data/databases/mydb/graphs")
        >>> handler = RelationshipCommitHandler(graph_repo)
        >>>
        >>> # Name-based relationships (NLP workflow)
        >>> relationships = [
        ...     {
        ...         "from": "Einstein",
        ...         "to": "Relativity",
        ...         "type": "developed",
        ...         "confidence": 0.95,
        ...         "justification": "Einstein developed the theory of relativity"
        ...     }
        ... ]
        >>> entity_name_to_id = {"Einstein": "node_1", "Relativity": "node_2"}
        >>> entity_index_to_id = {}
        >>>
        >>> edges = await handler.prepare_relationship_edges(
        ...     relationships=relationships,
        ...     entity_name_to_node_id=entity_name_to_id,
        ...     entity_index_to_node_id=entity_index_to_id,
        ... )
        >>>
        >>> created_ids = await handler.batch_create_edges(edges)

    Note:
        Edge templates are batch created upfront for all unique relationship
        types to improve performance. Missing templates are auto-generated
        with descriptive names.

    """

    def __init__(self, graph_repository: GraphRepositoryProtocol):
        """Initialize relationship commit handler."""
        self.graph_repository = graph_repository

    @staticmethod
    def _resolve_node_ids(
        rel: dict,
        entity_name_to_node_id: dict[str, str],
        entity_index_to_node_id: dict[int, str],
    ) -> tuple[str, str] | None:
        """Resolve from/to node IDs from a relationship dict.

        Handles both name-based (NLP workflow) and index-based (standard
        workflow) relationships.

        Args:
            rel: Relationship dictionary with either from/to or source/target.
            entity_name_to_node_id: Mapping from entity names to node IDs.
            entity_index_to_node_id: Mapping from entity indices to node IDs.

        Returns:
            Tuple of (from_node_id, to_node_id) if resolved, None otherwise.

        """
        # Handle name-based relationships (NLP workflow)
        if "from" in rel and "to" in rel:
            from_name = rel["from"]
            to_name = rel["to"]
            from_node_id = entity_name_to_node_id.get(from_name)
            to_node_id = entity_name_to_node_id.get(to_name)
            if not from_node_id or not to_node_id:
                logger.warning(
                    "relationship_entities_not_found", from_entity=from_name, to_entity=to_name
                )
                return None
            return from_node_id, to_node_id

        # Handle index-based relationships (standard workflow)
        if "source" in rel and "target" in rel:
            from_idx = rel.get("source")
            to_idx = rel.get("target")
            if from_idx is None or to_idx is None:
                logger.warning("Skipping relationship with missing indices")
                return None
            from_node_id = entity_index_to_node_id.get(from_idx)
            to_node_id = entity_index_to_node_id.get(to_idx)
            if not from_node_id or not to_node_id:
                logger.warning(
                    "relationship_nodes_not_found_for_indices",
                    from_index=from_idx,
                    to_index=to_idx,
                )
                return None
            return from_node_id, to_node_id

        logger.warning("Skipping relationship: missing both names and indices")
        return None

    async def prepare_relationship_edges(
        self,
        relationships: list[dict],
        entity_name_to_node_id: dict[str, str],
        entity_index_to_node_id: dict[int, str],
        source_id: str | None = None,
        edge_descriptions: dict[str, str] | None = None,
        edge_visuals: dict[str, dict[str, str | None]] | None = None,
        inverse_relationships: dict[str, str] | None = None,
        *,
        enable_inverse_relationships: bool = True,
    ) -> tuple[list[EdgeCreate], list[str], list[str], int]:
        """Create edge objects from relationship data.

        Args:
            relationships: List of relationship data
            entity_name_to_node_id: Mapping from entity names to node IDs (for NLP workflow)
            entity_index_to_node_id: Mapping from entity indices to node IDs (for legacy workflow)
            source_id: Source ID for linking edges to source (for enabled filtering)
            edge_descriptions: Domain-aware edge type descriptions from extraction
            edge_visuals: Domain-aware edge visuals (icon/color) from extraction
            inverse_relationships: Domain-specific inverse relationship map
            enable_inverse_relationships: When False, inverse edges are never
                created regardless of ``inverse_relationships``.

        Returns:
            Tuple of (edges_to_create, created_edge_template_ids, all_used_edge_template_ids, edge_templates_inserted):
            - edges_to_create: List of EdgeCreate objects ready for batch creation
            - created_edge_template_ids: List of IDs for all edge templates (created or reused)
            - all_used_edge_template_ids: ALL edge template IDs used (same as created)
            - edge_templates_inserted: Count of edge template rows actually inserted

        """
        edges_to_create: list[Any] = []
        created_edge_template_ids: list[str] = []
        all_used_edge_template_ids: list[str] = []
        edge_templates_inserted = 0
        inverse_map = inverse_relationships or {}
        descriptions = edge_descriptions or {}

        if not relationships:
            return (
                edges_to_create,
                created_edge_template_ids,
                all_used_edge_template_ids,
                edge_templates_inserted,
            )

        logger.info("processing_relationships", relationship_count=len(relationships))

        # Batch create all missing edge templates upfront (including inverse types)
        unique_edge_types = set()
        for rel in relationships:
            edge_type = rel.get("type", _DEFAULT_RELATIONSHIP_TYPE)
            if not rel.get("template_id"):
                unique_edge_types.add(edge_type)
                # Also add inverse type if it exists and the toggle is on
                if enable_inverse_relationships:
                    inverse_type = inverse_map.get(edge_type)
                    if inverse_type and inverse_type != edge_type:  # Skip symmetric
                        unique_edge_types.add(inverse_type)

        # Batch create all templates and get the mapping
        # Per-source templates: Always create new templates for this source
        edge_type_to_template_id: dict[str, str] = {}
        if unique_edge_types:
            logger.info("batch_creating_edge_templates", template_count=len(unique_edge_types))
            (
                edge_type_to_template_id,
                created_edge_template_ids,
                all_used_edge_template_ids,
                edge_templates_inserted,
            ) = await self.batch_create_edge_templates(
                list(unique_edge_types),
                source_id=source_id,
                edge_descriptions=descriptions,
                edge_visuals=edge_visuals,
            )
            logger.info("edge_templates_batch_created", template_count=len(unique_edge_types))

        for rel in relationships:
            node_ids = self._resolve_node_ids(rel, entity_name_to_node_id, entity_index_to_node_id)
            if node_ids is None:
                continue
            from_node_id, to_node_id = node_ids

            # Get edge template from batch-created mapping
            edge_type = rel.get("type", _DEFAULT_RELATIONSHIP_TYPE)
            template_id = rel.get("template_id")

            if not template_id:
                # Use pre-created template from batch operation
                template_id = edge_type_to_template_id.get(edge_type)
                if not template_id:
                    # Fallback: shouldn't happen but handle it
                    logger.warning(
                        "edge_template_fallback_triggered",
                        edge_type=edge_type,
                        available_templates=list(edge_type_to_template_id.keys()),
                    )
                    template_id = self._create_edge_template(
                        edge_type, source_id=source_id, edge_descriptions=descriptions
                    )
                    # Track this template for source linking (fixes orphan detection bug)
                    if (
                        template_id
                        and template_id != _DEFAULT_EDGE_TEMPLATE
                        and template_id not in all_used_edge_template_ids
                    ):
                        all_used_edge_template_ids.append(template_id)
                        logger.info(
                            "edge_template_fallback_tracked",
                            template_id=template_id,
                            edge_type=edge_type,
                        )

            edge_label = edge_type
            edge_properties = _build_edge_properties(rel)

            edges_to_create.append(
                EdgeCreate(
                    template_id=template_id,
                    source_node_id=from_node_id,
                    target_node_id=to_node_id,
                    label=_canonicalize_edge_label(edge_label),
                    properties=edge_properties,
                    source_id=source_id,
                )
            )

            # Create inverse edge for bidirectional graph traversal
            # Phase 6 (2026-05-08): gated by enable_inverse_relationships toggle.
            inverse_type = inverse_map.get(edge_type) if enable_inverse_relationships else None
            if inverse_type and inverse_type != edge_type:  # Skip symmetric relationships
                inverse_template_id = edge_type_to_template_id.get(inverse_type)
                if not inverse_template_id:
                    # Create template for inverse if needed
                    inverse_template_id = self._create_edge_template(
                        inverse_type, source_id=source_id, edge_descriptions=descriptions
                    )
                    edge_type_to_template_id[inverse_type] = inverse_template_id

                # Create inverse edge properties (same confidence/justification)
                inverse_properties = edge_properties.copy()
                inverse_properties["inverse_of"] = edge_type

                edges_to_create.append(
                    EdgeCreate(
                        template_id=inverse_template_id,
                        source_node_id=to_node_id,  # Swapped
                        target_node_id=from_node_id,  # Swapped
                        label=_canonicalize_edge_label(inverse_type),
                        properties=inverse_properties,
                        source_id=source_id,
                    )
                )

        logger.info(
            "relationship_edges_prepared",
            edges_prepared=len(edges_to_create),
            relationships_processed=len(relationships),
            edge_templates_created=len(created_edge_template_ids),
            edge_templates_used=len(all_used_edge_template_ids),
            edge_templates_inserted=edge_templates_inserted,
        )
        return (
            edges_to_create,
            created_edge_template_ids,
            all_used_edge_template_ids,
            edge_templates_inserted,
        )

    async def batch_create_edge_templates(
        self,
        edge_types: list[str],
        source_id: str | None = None,
        edge_descriptions: dict[str, str] | None = None,
        edge_visuals: dict[str, dict[str, str | None]] | None = None,
    ) -> tuple[dict[str, str], list[str], list[str], int]:
        """Batch create edge templates for multiple relationship types.

        Per-Source Templates: Always creates new templates for this source,
        no cross-source template reuse.

        Args:
            edge_types: List of unique edge types needing templates
            source_id: Source ID for linking templates to source
            edge_descriptions: Domain-aware edge type descriptions
            edge_visuals: Domain-aware edge visuals (icon/color) from extraction

        Returns:
            Tuple of (edge_type_mapping, created_template_ids, all_used_template_ids, inserted_count):
            - edge_type_mapping: Dict mapping edge_type -> template_id
            - created_template_ids: List of IDs for all templates (created or reused)
            - all_used_template_ids: Same as created_template_ids (no reuse in per-source)
            - inserted_count: Count of rows actually inserted (not counting dedup reuses)

        """
        # Per-source templates: Always create new templates for this source
        edge_type_mapping: dict[str, str] = {}
        created_template_ids: list[str] = []
        descriptions = edge_descriptions or {}
        domain_visuals = edge_visuals or {}

        templates_to_create = []
        for edge_type in edge_types:
            # Use domain visuals first, fall back to generic mapping table
            dv = domain_visuals.get(edge_type.lower())
            if dv and (dv.get("icon") or dv.get("color")):
                visuals = dv
            else:
                visuals = resolve_edge_visuals(edge_type)
            templates_to_create.append(
                TemplateCreate(
                    name=edge_type,
                    description=descriptions.get(
                        edge_type.lower(), edge_type.replace("_", " ").strip().title()
                    ),
                    template_type="edge",
                    properties=[],
                    icon=visuals.get("icon"),
                    color=visuals.get("color"),
                    source_id=source_id,
                )
            )

        # Batch create all templates
        edge_templates_inserted = 0
        if templates_to_create:
            logger.info(
                "creating_edge_templates",
                template_count=len(templates_to_create),
                source_id=source_id,
            )
            try:
                # Idempotent UPSERT: re-running commit for the same
                # source reuses existing edge-template rows instead of
                # creating a fresh UUID duplicate on every attempt.
                (
                    created_templates,
                    edge_templates_inserted,
                ) = await self.graph_repository.upsert_templates_batch(templates_to_create)

                # Build mapping from created templates
                for template in created_templates:
                    edge_type_mapping[template.name] = template.id
                    created_template_ids.append(template.id)

                logger.info(
                    "edge_templates_batch_created_success",
                    created_count=len(created_templates),
                    inserted_count=edge_templates_inserted,
                    source_id=source_id,
                    template_ids=created_template_ids,
                )

            except Exception as e:
                logger.exception(
                    "edge_templates_batch_create_failed",
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                # Fallback path used system template; nothing newly tracked.
                edge_templates_inserted = 0
                # Fallback to system template
                for template_create in templates_to_create:
                    if template_create.name not in edge_type_mapping:
                        edge_type_mapping[template_create.name] = _DEFAULT_EDGE_TEMPLATE

        # For per-source templates, all_used = created (no reuse)
        return (
            edge_type_mapping,
            created_template_ids,
            created_template_ids,
            edge_templates_inserted,
        )

    def _create_edge_template(
        self,
        edge_type: str,
        source_id: str | None = None,
        edge_descriptions: dict[str, str] | None = None,
    ) -> str:
        """Create an edge template for a relationship type (fallback method).

        Per-Source Templates: Always creates a new template for this source.

        Args:
            edge_type: Relationship type
            source_id: Source ID for linking template to source
            edge_descriptions: Domain-aware edge type descriptions

        Returns:
            Template ID (or system_template_link on failure)

        """
        descriptions = edge_descriptions or {}
        logger.info("creating_edge_template", edge_type=edge_type, source_id=source_id)
        try:
            visuals = resolve_edge_visuals(edge_type)
            edge_template = self.graph_repository.create_template(
                TemplateCreate(
                    name=edge_type,
                    description=descriptions.get(
                        edge_type.lower(), edge_type.replace("_", " ").strip().title()
                    ),
                    template_type="edge",
                    properties=[],
                    icon=visuals["icon"],
                    color=visuals["color"],
                    source_id=source_id,
                )
            )
            logger.info("edge_template_created", template_id=edge_template.id, source_id=source_id)
            return edge_template.id
        except Exception:
            logger.exception(
                "commit_edge_template_create_failed_used_default",
                edge_type=edge_type,
                source_id=source_id,
            )
            return _DEFAULT_EDGE_TEMPLATE

    async def batch_create_edges(self, edges_to_create: list[EdgeCreate]) -> tuple[list[str], int]:
        """Batch create edges.

        Args:
            edges_to_create: List of EdgeCreate objects

        Returns:
            Tuple of:
            - List of edge IDs (created or pre-existing) in input order.
            - Count of rows actually inserted (not counting dedup reuses).
              Use this for commit_edges_created so the counter reflects true
              insertions, not the input list size.

        """
        if not edges_to_create:
            return [], 0

        logger.info("creating_relationship_edges", edge_count=len(edges_to_create))
        # Idempotent UPSERT: resumability entry point on the
        # edge side. Relies on upsert_nodes_batch having produced
        # stable endpoint IDs earlier in the commit pass.
        batch_created_edges, inserted_count = await self.graph_repository.upsert_edges_batch(
            edges_to_create
        )

        created_edges = [edge.id for edge in batch_created_edges]
        logger.info("relationship_edges_created", edge_count=len(batch_created_edges))

        return created_edges, inserted_count
