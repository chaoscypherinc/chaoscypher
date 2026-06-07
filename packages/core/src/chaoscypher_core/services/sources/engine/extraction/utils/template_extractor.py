# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Extractor.

Generates template suggestions from extracted entities and relationships.

SRP: Single responsibility for template suggestion generation
"""

from typing import Any

import structlog

from chaoscypher_core.templates.visuals import (
    resolve_edge_visuals,
    resolve_node_visuals,
)


logger = structlog.get_logger(__name__)


class TemplateExtractor:
    """Generates template suggestions from extracted entities and relationships.

    Responsibilities:
    - Generate node template suggestions from entity types
    - Generate edge template suggestions from relationship types
    """

    @staticmethod
    def _extract_domain_descriptions(
        domain_templates: list[dict[str, Any]] | None,
    ) -> dict[str, str]:
        """Extract description lookup dict from domain templates."""
        domain_descriptions: dict[str, str] = {}
        if not domain_templates:
            return domain_descriptions
        for dt in domain_templates:
            name = dt.get("name", "").lower()
            desc = dt.get("description", "")
            if name and desc:
                domain_descriptions[name] = desc
        return domain_descriptions

    @staticmethod
    def _extract_domain_visuals(
        domain_templates: list[dict[str, Any]] | None,
    ) -> dict[str, dict[str, str | None]]:
        """Extract icon/color lookup dict from domain templates."""
        visuals: dict[str, dict[str, str | None]] = {}
        if not domain_templates:
            return visuals
        for dt in domain_templates:
            name = dt.get("name", "").lower()
            icon = dt.get("icon")
            color = dt.get("color")
            if name and (icon or color):
                visuals[name] = {"icon": icon, "color": color}
        return visuals

    @staticmethod
    def _get_description(
        entity_type: str,
        domain_descriptions: dict[str, str],
    ) -> str:
        """Get description for entity type from domain config or generate fallback."""
        type_lower = entity_type.lower()
        if type_lower in domain_descriptions:
            return domain_descriptions[type_lower]
        # Generate readable fallback from name
        return entity_type.replace("_", " ").strip().title()

    @staticmethod
    def generate_suggestions_from_entities(
        entities: list[dict[str, Any]],
        domain_templates: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate template suggestions from extracted entity types.

        Per-Source Templates: Generates suggestions for ALL entity types found,
        regardless of templates in other sources. Each source gets its own templates.

        Args:
            entities: List of extracted entities with 'type' field
            domain_templates: Optional list of domain-specific template definitions
                with 'name' and 'description' fields (from domain config)

        Returns:
            List of suggested templates with name, description, reason, and entity_count

        Example:
            >>> suggestions = TemplateExtractor.generate_suggestions_from_entities(
            ...     entities=[
            ...         {"type": "Module", "name": "os"},
            ...         {"type": "Module", "name": "sys"},
            ...         {"type": "Class", "name": "Path"},
            ...     ]
            ... )
            >>> print(suggestions)
            [
                {"name": "Module", "description": "Python module or code package", ...},
                {"name": "Class", "description": "Object-oriented class definition", ...},
            ]

        """
        # Aggregate entity types and counts
        type_counts: dict[str, int] = {}
        skip_types = ("unknown", "item")
        for entity in entities:
            entity_type = entity.get("type", "").strip()
            if entity_type and entity_type.lower() not in skip_types:
                normalized_type = entity_type.title()
                type_counts[normalized_type] = type_counts.get(normalized_type, 0) + 1

        # Build domain template lookups (lowercase keys)
        domain_descriptions = TemplateExtractor._extract_domain_descriptions(domain_templates)
        domain_visuals = TemplateExtractor._extract_domain_visuals(domain_templates)

        # Types to skip in suggestions (too generic)
        generic_types = ("item", "unknown", "thing", "object", "entity")

        # Generate suggestions for all entity types (per-source templates)
        suggestions: list[dict[str, Any]] = []
        for entity_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            type_lower = entity_type.lower()
            if type_lower in generic_types:
                continue

            description = TemplateExtractor._get_description(entity_type, domain_descriptions)
            reason = f"{count} {'entity' if count == 1 else 'entities'} extracted"

            # Check domain config first, then fall back to generic mapping table
            dv = domain_visuals.get(type_lower)
            if dv and (dv.get("icon") or dv.get("color")):
                visuals = {"icon": dv.get("icon"), "color": dv.get("color")}
            else:
                visuals = resolve_node_visuals(entity_type)

            suggestions.append(
                {
                    "name": entity_type,
                    "description": description,
                    "reason": reason,
                    "entity_count": count,
                    "icon": visuals["icon"],
                    "color": visuals["color"],
                }
            )

        if suggestions:
            logger.info(
                "template_suggestions_from_entities",
                suggestion_count=len(suggestions),
                types=[s["name"] for s in suggestions],
            )

        return suggestions

    @staticmethod
    def generate_suggestions_from_relationships(
        relationships: list[dict[str, Any]],
        domain_templates: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate edge template suggestions from extracted relationship types.

        Per-Source Templates: Generates suggestions for ALL relationship types found,
        regardless of templates in other sources. Each source gets its own templates.

        Args:
            relationships: List of extracted relationships with 'type' field
            domain_templates: Optional list of domain-specific edge template definitions

        Returns:
            List of suggested edge templates with name, description, reason, and count

        """
        # Aggregate relationship types and counts
        type_counts: dict[str, int] = {}
        for rel in relationships:
            rel_type = rel.get("type", "").strip()
            if rel_type:
                # Normalize to snake_case
                normalized_type = rel_type.lower().replace(" ", "_")
                type_counts[normalized_type] = type_counts.get(normalized_type, 0) + 1

        # Build domain template lookups
        domain_descriptions = TemplateExtractor._extract_domain_descriptions(domain_templates)
        domain_visuals = TemplateExtractor._extract_domain_visuals(domain_templates)

        # Types to skip in suggestions (too generic)
        generic_types = ("link", "unknown", "relationship", "connection", "related")

        # Generate suggestions for all relationship types (per-source templates)
        suggestions: list[dict[str, Any]] = []
        for rel_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            if rel_type in generic_types:
                continue

            # Get description from domain or generate fallback
            description = TemplateExtractor._get_edge_description(rel_type, domain_descriptions)
            reason = f"{count} {'relationship' if count == 1 else 'relationships'} extracted"

            # Check domain config first, then fall back to generic mapping table
            dv = domain_visuals.get(rel_type)
            if dv and (dv.get("icon") or dv.get("color")):
                visuals = {"icon": dv.get("icon"), "color": dv.get("color")}
            else:
                visuals = resolve_edge_visuals(rel_type)

            suggestions.append(
                {
                    "name": rel_type,
                    "description": description,
                    "reason": reason,
                    "relationship_count": count,
                    "template_type": "edge",
                    "icon": visuals["icon"],
                    "color": visuals["color"],
                }
            )

        if suggestions:
            logger.info(
                "edge_template_suggestions_from_relationships",
                suggestion_count=len(suggestions),
                types=[s["name"] for s in suggestions[:10]],
            )

        return suggestions

    @staticmethod
    def _get_edge_description(
        rel_type: str,
        domain_descriptions: dict[str, str],
    ) -> str:
        """Get description for relationship type from domain config or generate fallback."""
        type_lower = rel_type.lower()
        if type_lower in domain_descriptions:
            return domain_descriptions[type_lower]
        # Generate readable fallback from name
        return rel_type.replace("_", " ").strip().title()
