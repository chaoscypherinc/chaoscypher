# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Loader - Imports templates from CCX packages.

Handles importing templates from templates.jsonld files with support
for skipping existing templates by name.

Example:
    from chaoscypher_core.services.package.importer.loaders import TemplateLoader

    loader = TemplateLoader(graph_repository)
    loader.load(templates_data, mapper, stats, "default")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.models import PropertyDefinition, TemplateCreate
from chaoscypher_core.services.package.importer.loaders.base import PackageLoaderBase


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.repos import GraphRepository
    from chaoscypher_core.services.package.importer.models import IdMapper, ImportStats


logger = structlog.get_logger(__name__)


class TemplateLoader(PackageLoaderBase):
    """Loads templates from CCX packages.

    Handles importing templates from templates.jsonld files. Supports
    skipping templates that already exist by name to avoid duplicates.

    Attributes:
        graph_repository: Graph repository for template operations.
        skip_existing: Whether to skip templates that already exist by name.
    """

    def __init__(
        self,
        graph_repository: GraphRepository,
        skip_existing: bool = False,
    ) -> None:
        """Initialize template loader.

        Args:
            graph_repository: Graph repository for template operations.
            skip_existing: Whether to skip templates that already exist by name.
                Defaults to False — CCX imports are self-contained and always
                mint fresh templates.
        """
        self.graph_repository = graph_repository
        self.skip_existing = skip_existing

    def load(
        self,
        data: dict[str, Any] | list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load templates from parsed templates.jsonld data.

        Args:
            data: Parsed templates.jsonld data {"templates": [...]}.
            mapper: IdMapper for tracking ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name (not used for templates).

        Raises:
            ValueError: If data format is invalid.
        """
        if not isinstance(data, dict) or "templates" not in data:
            stats.errors.append("Invalid templates.jsonld format: missing 'templates' key")
            return

        templates_data = data["templates"]
        if not isinstance(templates_data, list):
            stats.errors.append("Invalid templates.jsonld format: 'templates' must be a list")
            return

        logger.info(
            "loading_templates",
            template_count=len(templates_data),
            skip_existing=self.skip_existing,
        )

        # Get existing templates by name if skipping duplicates
        existing_names: set[str] = set()
        existing_templates_list: list = []
        if self.skip_existing:
            existing_templates_list = self.graph_repository.list_templates()
            existing_names = {t.name for t in existing_templates_list}

        # (TemplateCreate, original_id_from_package_or_None) pairs. Paired
        # rather than zipped because the loop below skips entries without
        # names or with invalid properties, so the indices would otherwise
        # desync from ``templates_data``.
        templates_to_create: list[tuple[TemplateCreate, str | None]] = []

        for template_data in templates_data:
            template_name = template_data.get("name")
            if not template_name:
                stats.warnings.append("Skipping template without name")
                continue

            # Skip if already exists
            if self.skip_existing and template_name in existing_names:
                stats.templates_skipped += 1
                # Map existing template for reference
                existing = next(
                    (t for t in existing_templates_list if t.name == template_name),
                    None,
                )
                if existing:
                    mapper.map_template(template_name, existing.id)
                logger.debug("skipping_existing_template", name=template_name)
                continue

            # Parse properties
            properties = []
            for prop_data in template_data.get("properties", []):
                try:
                    name = prop_data.get("name", "")
                    prop = PropertyDefinition(
                        name=name,
                        display_name=prop_data.get("display_name", name),  # Use name as fallback
                        property_type=prop_data.get("property_type", "string"),
                        description=prop_data.get("description"),
                        required=prop_data.get("required", False),
                        default_value=prop_data.get("default_value"),
                    )
                    properties.append(prop)
                except Exception as e:
                    stats.warnings.append(f"Invalid property in template '{template_name}': {e}")

            # Create template data
            try:
                template_create = TemplateCreate(
                    name=template_name,
                    template_type=template_data.get("template_type", "node"),
                    description=template_data.get("description"),
                    properties=properties,
                )
                original_id = template_data.get("id")
                templates_to_create.append(
                    (
                        template_create,
                        original_id if isinstance(original_id, str) else None,
                    )
                )
            except Exception as e:
                stats.errors.append(f"Invalid template '{template_name}': {e}")

        # Batch create templates. Preserve original IDs from the package
        # so that nodes/edges referencing ``template_id`` survive an
        # export → import roundtrip without going through name-based
        # remapping. ``GraphRepository.create_template`` mints a fresh
        # ID when ``custom_id`` is None, matching the pre-CCX behavior
        # for callers that don't have an original ID to preserve.
        if templates_to_create:
            try:
                for template_create, original_id in templates_to_create:
                    created = self.graph_repository.create_template(
                        template_create,
                        custom_id=original_id,
                    )
                    mapper.map_template(created.name, created.id)
                    stats.templates_imported += 1
                    logger.debug("template_imported", name=created.name, id=created.id)
            except Exception as e:
                stats.errors.append(f"Failed to create templates: {e}")
                logger.exception("template_batch_create_failed", error=str(e))

        logger.info(
            "templates_loaded",
            imported=stats.templates_imported,
            skipped=stats.templates_skipped,
        )


__all__ = ["TemplateLoader"]
