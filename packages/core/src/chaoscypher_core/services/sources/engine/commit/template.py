# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Commit Handler.

Handles template creation during import commits, including duplicate
checking and property definition conversion.

Extracted from commit_service.py for SRP compliance.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.models import PropertyDefinition, PropertyType, TemplateCreate


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol

logger = structlog.get_logger(__name__)


class TemplateCommitHandler:
    """Handles template creation during import commits.

    Creates new node templates suggested by AI during entity extraction,
    checking for duplicates and building name-to-ID mappings for use
    during entity commit. This handler is part of the commit service's
    Single Responsibility Principle (SRP) refactoring.

    Responsibilities:
    - Create suggested templates from import data
    - Check for existing templates to avoid duplicates
    - Build template name-to-ID mappings for entity matching
    - Convert property definitions to PropertyDefinition objects

    Attributes:
        graph_repository: GraphRepository instance for template CRUD operations

    Example:
        >>> from chaoscypher_core.services.sources.engine.commit.template import TemplateCommitHandler
        >>> from chaoscypher_core.adapters.sqlite.repos import GraphRepository
        >>>
        >>> graph_repo = GraphRepository(graphs_dir="/data/databases/mydb/graphs")
        >>> handler = TemplateCommitHandler(graph_repo)
        >>>
        >>> commit_data = {
        ...     "create_templates": True,
        ...     "suggested_templates": [
        ...         {
        ...             "name": "Research Paper",
        ...             "reason": "Academic publications and research documents",
        ...             "properties": ["title", "authors", "publication_date"]
        ...         }
        ...     ]
        ... }
        >>>
        >>> created_ids, name_mapping, all_used, inserted = await handler.create_suggested_templates(commit_data)
        >>> print(f"Created {len(created_ids)} templates, total used: {len(all_used)}")
        >>> print(f"Mapping: {name_mapping}")
        {"research paper": "template_abc123"}

    Note:
        Template names are normalized to lowercase for duplicate checking.
        Invalid template names (unknown, untitled, n/a, none) are skipped.

    """

    def __init__(self, graph_repository: GraphRepositoryProtocol) -> None:
        """Initialize template commit handler."""
        self.graph_repository = graph_repository

    async def create_suggested_templates(
        self, commit_data: dict, source_id: str | None = None
    ) -> tuple[list[str], dict[str, str], list[str], int]:
        """Create suggested templates from import data.

        Each source gets its own templates (no reuse across sources).
        Templates are linked to the source via source_id for:
        - Filtering by source enabled status
        - Cascade deletion when source is deleted

        Mirrors batch_create_edge_templates: all TemplateCreate objects are
        built first (skipping malformed entries), then a single
        upsert_templates_batch call deduplicates by stable content key in
        one SELECT. This avoids the SafeSession identity-map gap that caused
        duplicate rows when SQLite-busy retry rolled back mid-loop in the old
        sequential upsert_template approach.

        Args:
            commit_data: Commit data containing suggested_templates
            source_id: Source ID for template ownership (enables filtering/cascade delete)

        Returns:
            Tuple of:
            - created_template_ids: List of template IDs (created or reused) in order
            - template_name_to_id: Mapping of template names to IDs
            - all_used_template_ids: ALL template IDs used (same as created_template_ids)
            - inserted_count: Count of rows actually inserted (not counting dedup reuses)

        """
        created_templates: list[str] = []
        template_name_to_id: dict[str, str] = {}
        inserted_count = 0

        if not commit_data.get("create_templates") or not commit_data.get("suggested_templates"):
            return created_templates, template_name_to_id, created_templates, inserted_count

        logger.info(
            "creating_suggested_templates",
            count=len(commit_data["suggested_templates"]),
            source_id=source_id,
        )

        # Build phase — collect all TemplateCreate objects first.
        # Per-template exceptions (malformed AI suggestions) are skippable here
        # because the session is NOT touched during the build loop.
        templates_to_create: list[TemplateCreate] = []

        for template_data in commit_data["suggested_templates"]:
            try:
                template_name = template_data["name"].strip()
                template_description = template_data.get("description", "").strip()
                if not template_description:
                    template_description = template_data.get("reason", "").strip()

                if not template_name or template_name.lower() in [
                    "unknown",
                    "untitled template",
                    "untitled",
                    "n/a",
                    "none",
                ]:
                    logger.warning("template_invalid_name_skipped", template_name=template_name)
                    continue

                properties = []
                if "properties" in template_data and isinstance(template_data["properties"], list):
                    for prop in template_data["properties"]:
                        if isinstance(prop, str):
                            properties.append(
                                PropertyDefinition(
                                    name=prop.lower().replace(" ", "_"),
                                    display_name=prop,
                                    property_type=PropertyType.TEXT,
                                    required=False,
                                )
                            )
                        elif isinstance(prop, dict):
                            properties.append(PropertyDefinition(**prop))

                templates_to_create.append(
                    TemplateCreate(
                        name=template_name,
                        description=template_description,
                        template_type="node",
                        properties=properties,
                        icon=template_data.get("icon"),
                        color=template_data.get("color"),
                        source_id=source_id,
                    )
                )
            except Exception as e:
                logger.exception(
                    "template_build_failed",
                    template_name=template_data.get("name", "Unknown"),
                    error_type=type(e).__name__,
                    error_message=str(e),
                )
                continue

        # Batch upsert phase — single SELECT deduplicates by stable content key,
        # then inserts only genuinely new rows. Errors here poison the SQLAlchemy
        # session; let them propagate so the outer transaction rolls back cleanly.
        if templates_to_create:
            batch_result, inserted_count = await self.graph_repository.upsert_templates_batch(
                templates_to_create
            )

            for template in batch_result:
                created_templates.append(template.id)
                template_name_to_id[template.name.lower()] = template.id
                logger.info(
                    "template_processed",
                    template_id=template.id,
                    template_name=template.name,
                    source_id=source_id,
                )

        logger.info(
            "templates_processed",
            created_count=len(created_templates),
            inserted_count=inserted_count,
            source_id=source_id,
        )
        # Return created_templates for both created and all_used (no reuse anymore)
        return created_templates, template_name_to_id, created_templates, inserted_count
