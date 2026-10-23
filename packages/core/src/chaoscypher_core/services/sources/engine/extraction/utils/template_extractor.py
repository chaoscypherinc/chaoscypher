# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Extractor.

Generates template suggestions from extracted entities and relationships.

SRP: Single responsibility for template suggestion generation
"""

from typing import Any

import structlog

from chaoscypher_core.services.sources.engine.extraction.domains.core_types import CORE_TYPES
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import committed_type
from chaoscypher_core.templates.visuals import (
    resolve_edge_visuals,
    resolve_node_visuals,
)


logger = structlog.get_logger(__name__)


class TemplateExtractor:
    """Generates template suggestions from extracted entities and relationships.

    Responsibilities:
    - Generate node template suggestions, one per core type present
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
        subtype_visuals: dict[str, dict[str, str | None]] | None = None,
    ) -> list[dict[str, Any]]:
        """One template suggestion per core type the entities carry (at most eight).

        Per-source templates: each source gets its own, created by the commit
        (``commit/template.py``). Spec 2026-10-07 § 6: a node's template is its
        core type's (``Person``), its pack subtype (``character``) rides on the
        node as ``properties.entity_subtype``. A record's type is read with
        :func:`~chaoscypher_core.services.sources.engine.extraction.utils.type_rescue.committed_type`
        (an old ``Character`` counts as Person); a record no core type answers
        gets no template (the matcher's fallback).

        Args:
            entities: Extracted entities with a ``type`` field.
            domain_templates: The floor's node templates (the core types),
                for each suggestion's description, icon and colour.
            subtype_visuals: ``{entity_subtype: {"icon", ...}}`` of the
                source's packs (``DomainStack.subtype_visuals``): a subtype's
                own icon is offered to the UI as ``subtype_icons``.

        Returns:
            Suggestions ``{name, description, reason, entity_count, icon,
            color, subtypes}``, the most common core type first; ``subtypes``
            counts the entities of each subtype, and ``subtype_icons`` (when
            any) the subtypes' own icons.

        Example:
            >>> suggestions = TemplateExtractor.generate_suggestions_from_entities(
            ...     entities=[
            ...         {"type": "Person", "name": "Pierre", "properties": {"entity_subtype": "character"}},
            ...         {"type": "Person", "name": "Tolstoy", "properties": {"entity_subtype": "author"}},
            ...         {"type": "Location", "name": "Moscow"},
            ...     ]
            ... )
            >>> [s["name"] for s in suggestions]
            ['Person', 'Location']

        """
        counts: dict[str, int] = {}
        subtypes: dict[str, dict[str, int]] = {}
        for entity in entities:
            typed = committed_type(entity)
            if typed is None:
                continue
            core, subtype = typed
            counts[core] = counts.get(core, 0) + 1
            if subtype:
                by_subtype = subtypes.setdefault(core, {})
                by_subtype[subtype] = by_subtype.get(subtype, 0) + 1

        domain_descriptions = TemplateExtractor._extract_domain_descriptions(domain_templates)
        domain_visuals = TemplateExtractor._extract_domain_visuals(domain_templates)
        own_icons = {
            key: str(v["icon"]) for key, v in (subtype_visuals or {}).items() if v.get("icon")
        }

        suggestions: list[dict[str, Any]] = []
        for core in sorted(counts, key=lambda c: (-counts[c], CORE_TYPES.index(c))):
            count = counts[core]
            dv = domain_visuals.get(core.lower())
            if dv and (dv.get("icon") or dv.get("color")):
                visuals = {"icon": dv.get("icon"), "color": dv.get("color")}
            else:
                visuals = resolve_node_visuals(core)
            of_core = dict(sorted(subtypes.get(core, {}).items(), key=lambda kv: (-kv[1], kv[0])))
            suggestion: dict[str, Any] = {
                "name": core,
                "description": TemplateExtractor._get_description(core, domain_descriptions),
                "reason": f"{count} {'entity' if count == 1 else 'entities'} extracted",
                "entity_count": count,
                "icon": visuals["icon"],
                "color": visuals["color"],
                "subtypes": of_core,
            }
            icons = {key: own_icons[key] for key in of_core if key in own_icons}
            if icons:
                suggestion["subtype_icons"] = icons
            suggestions.append(suggestion)

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
