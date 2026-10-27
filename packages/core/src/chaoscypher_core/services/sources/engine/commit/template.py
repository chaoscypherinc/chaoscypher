# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Commit Handler.

Handles template creation during import commits, including duplicate
checking and property definition conversion.

Extracted from commit_service.py for SRP compliance.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import PropertyDefinition, PropertyType, TemplateCreate
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import committed_type


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
        ...             "name": "Work",
        ...             "reason": "Books, papers and other created works",
        ...             "properties": ["title", "authors", "publication_date"]
        ...         }
        ...     ]
        ... }
        >>>
        >>> created_ids, name_mapping, all_used, inserted = await handler.create_suggested_templates(commit_data)
        >>> print(f"Created {len(created_ids)} templates, total used: {len(all_used)}")
        >>> print(f"Mapping: {name_mapping}")
        {"work": "template_abc123"}

    Note:
        Templates are one per core type: a suggestion named by an old
        per-type name (``Character``) is created as its core type's
        (``Person``), once. Template names are normalized to lowercase for
        duplicate checking.
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
        seen: set[str] = set()

        for template_data in commit_data["suggested_templates"]:
            try:
                built = template_from_suggestion(template_data, source_id=source_id)
                if built is None:
                    logger.warning(
                        "template_invalid_name_skipped",
                        template_name=str(template_data.get("name") or ""),
                    )
                    continue
                if built.name.lower() in seen:
                    continue
                seen.add(built.name.lower())
                templates_to_create.append(built)
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


_INVALID_TEMPLATE_NAMES = frozenset({"unknown", "untitled template", "untitled", "n/a", "none"})
"""Suggested template names that name no template (a model's placeholder): skipped."""


def template_from_suggestion(
    template_data: Mapping[str, Any], source_id: str | None = None
) -> TemplateCreate | None:
    """The node template a suggestion (``TemplateExtractor`` shape) is created as; None for a placeholder name.

    The one builder of a core type's template, for the commit
    (:meth:`TemplateCommitHandler.create_suggested_templates`) and the ccx
    import of a bundle from before the core types (``CcxImporter``). One
    template per core type (spec 2026-10-07 § 6): a suggestion named by an
    old per-type name (a payload cached before the restructure:
    ``Character``) is its core type's (``Person``, ``committed_type``). The
    description falls back to the suggestion's ``reason``; ``properties``
    may be names (a text property each) or ``PropertyDefinition`` dicts.

    Raises:
        KeyError: When the suggestion has no ``name``.
        pydantic.ValidationError: When a property dict is malformed.
    """
    template_name = str(template_data["name"]).strip()
    typed = committed_type({"type": template_name})
    if typed is not None:
        template_name = typed[0]
    if not template_name or template_name.lower() in _INVALID_TEMPLATE_NAMES:
        return None
    description = str(template_data.get("description") or "").strip()
    if not description:
        description = str(template_data.get("reason") or "").strip()

    properties: list[PropertyDefinition] = []
    raw_properties = template_data.get("properties")
    if isinstance(raw_properties, list):
        for prop in raw_properties:
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

    return TemplateCreate(
        name=template_name,
        description=description,
        template_type="node",
        properties=properties,
        icon=template_data.get("icon"),
        color=template_data.get("color"),
        source_id=source_id,
    )


def live_offered_templates(
    graph_repository: GraphRepositoryProtocol, offered_templates: Mapping[str, str]
) -> dict[str, str]:
    """The offered templates (folded name -> id) that still exist.

    One deleted since the extraction offered it binds nothing, and its entities fall
    back to the matcher.
    """
    return {
        name: template_id
        for name, template_id in offered_templates.items()
        if graph_repository.get_template(template_id) is not None
    }


def drop_bound_suggestions(
    commit_data: Mapping[str, Any], offered_templates: Mapping[str, str]
) -> list[dict[str, Any]]:
    """The suggested templates the commit still needs once offered templates bind (D26).

    With ``offered_templates`` (a memory's, live: :func:`live_offered_templates`), a
    suggestion is kept only when its committed name (:func:`template_from_suggestion`) is
    the core type of an entity that will not bind; a memory whose every entity binds
    creates no template of its own. With none, every suggestion stands (documents).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.offered_types import (
        take_offered_template,
    )
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        core_typed,
    )

    suggestions = [dict(s) for s in commit_data.get("suggested_templates") or []]
    if not offered_templates:
        return suggestions
    needed: set[str] = set()
    for entity in commit_data.get("entities") or []:
        entity_data, bound = take_offered_template(
            core_typed(entity.get("entity_data", entity)), offered_templates
        )
        if bound is None:
            needed.add(str(entity_data.get("type") or "").strip().lower())
    kept: list[dict[str, Any]] = []
    for suggestion in suggestions:
        try:
            built = template_from_suggestion(suggestion)
        except KeyError, ValueError:  # malformed: the creator skips and logs it
            kept.append(suggestion)
            continue
        if built is None or built.name.lower() in needed:
            kept.append(suggestion)
    return kept
