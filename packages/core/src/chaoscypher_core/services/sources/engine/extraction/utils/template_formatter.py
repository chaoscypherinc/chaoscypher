# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Formatting Utilities for Domain-Based Extraction.

Provides formatted template lists for AI extraction prompts using domain configurations.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

import structlog

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.extraction.utils.prompts import subtype_clause


logger = structlog.get_logger(__name__)


# ============================================================================
# Helper Functions
# ============================================================================


def template_name_to_snake_case(name: str) -> str:
    """Convert template name to snake_case for relationship types.

    Examples:
        "Works At" → "works_at"
        "Located In" → "located_in"
        "Collaborates With" → "collaborates_with"

    """
    # Convert to lowercase and replace spaces/hyphens with underscores
    snake = re.sub(r"[\s-]+", "_", name.lower())
    # Remove any non-alphanumeric characters except underscores
    snake = re.sub(r"[^a-z0-9_]", "", snake)
    # Remove leading/trailing underscores
    return snake.strip("_")


# ============================================================================
# Fallback Functions
# ============================================================================


def _get_fallback_node_templates() -> str:
    """Fallback node template list if domain templates unavailable."""
    return """- Note: General note or observation
- Item: Core subject—a person, place, or specific idea
- Person: Biographical information about individuals
- Organization: Companies, institutions, groups
- Concept: Ideas, theories, principles, abstract notions
- Event: Historical events, occurrences, milestones
- Location: Places, geographical areas
- Document: Other documents referenced
- Topic: Subject areas, themes, domains"""


def _get_fallback_edge_templates() -> str:
    """Fallback edge template list if domain templates unavailable."""
    return """- link (Link): Generic relationship between items
- related_to (Related To): Generic relationship when no specific type applies
- parent_of (Parent Of): Biological or adoptive parent relationship
- child_of (Child Of): Biological or adoptive child relationship
- sibling_of (Sibling Of): Brother or sister relationship
- spouse_of (Spouse Of): Married to or in partnership with
- married_to (Married To): Marriage relationship
- friend_of (Friend Of): Friendship or close social relationship
- knows (Knows): Person knows or is acquainted with another person
- member_of (Member Of): Person belongs to a group, organization, or family
- works_at (Works At): Person works at an organization
- works_for (Works For): Person is employed by or serves another
- located_in (Located In): Entity is located in a place
- part_of (Part Of): Component is part of a larger whole
- owns (Owns): Person or entity owns something
- created_by (Created By): Work or artifact created by a person/organization
- authored_by (Authored By): Written or composed by
- influences (Influences): Entity influences or affects another entity
- similar_to (Similar To): Entities share similarities or are comparable
- derived_from (Derived From): Concept or entity derived from another
- mentions (Mentions): Document or text mentions an entity
- collaborates_with (Collaborates With): Person collaborates with another person
- instance_of (Instance Of): This item is an example of a more general concept
- contains (Contains): This item contains or possesses another item
- attended (Attended): Person attended an event or institution
- participated_in (Participated In): Person participated in an event or activity"""


# ============================================================================
# Domain-Based Template Formatting
# ============================================================================


def format_node_line(
    template: Mapping[str, Any],
    *,
    subtypes: Sequence[str] = (),
    extra_properties: Sequence[Mapping[str, Any]] = (),
) -> str:
    """One entity type's line in the prompt's type list.

    ``- Person: An individual human being (subtypes: author, character —
    write Person/author) (properties: title=Title/Role, ...)``: the subtypes
    as the slugs the model writes, ``Core/subtype`` (``prompts.subtype_clause``).
    The property hints show the key
    and its display name, so the model knows which key a ``P|`` line uses;
    ``extra_properties`` (a pack's ``core_properties``) follow the
    template's own, a key the template already has being skipped.

    Args:
        template: A node template (``name``, ``description``, ``properties``).
        subtypes: The subtype values offered under this type, in order.
        extra_properties: Further property definitions for this type.

    Returns:
        The line, without a trailing newline.
    """
    name = template.get("name", "Unknown")
    desc = template.get("description", "No description provided")
    line = f"- {name}: {desc}"
    if subtypes:
        line += f" {subtype_clause(str(name), subtypes)}"
    seen: set[str] = set()
    prop_hints = []
    for prop in [*(template.get("properties") or []), *extra_properties]:
        key = prop.get("name")
        if key and key not in seen:
            seen.add(key)
            prop_hints.append(f"{key}={prop.get('display_name', key)}")
    if prop_hints:
        line += f" (properties: {', '.join(prop_hints)})"
    return line


def format_edge_line(template: Mapping[str, Any]) -> str:
    """One relationship type's line in the prompt's type list.

    ``- slug: description``; a template whose name is not already its slug
    (``Works At``) shows the name too: ``- works_at (Works At): description``.
    """
    name = str(template.get("name", "unknown"))
    desc = template.get("description", "Generic relationship")
    slug = template_name_to_snake_case(name)
    return f"- {slug}: {desc}" if slug == name else f"- {slug} ({name}): {desc}"


def format_domain_node_templates(
    templates: dict[str, list[dict]],
    *,
    allow_template_fallback: bool = True,
) -> str:
    """Format node templates from domain configuration for LLM prompt.

    Includes property hints when templates have property definitions,
    enabling the LLM to extract structured properties.

    Args:
        templates: Dictionary with 'node_templates' and 'edge_templates' lists
                  from domain.get_templates()
        allow_template_fallback: When True (legacy default), an empty
            node-template list silently falls back to the built-in generic
            templates. When False (opt-in), an empty list raises
            ``ValidationError`` so operators see a misconfigured domain
            immediately rather than silently getting generic output.

    Returns:
        Formatted string for LLM prompt

    Raises:
        ValidationError: When ``allow_template_fallback=False`` and the
            domain has no node templates configured.

    """
    node_templates = templates.get("node_templates", [])

    if not node_templates:
        if not allow_template_fallback:
            raise ValidationError(
                "Domain has no node templates configured and "
                "allow_template_fallback=False. Add entity templates to the "
                "domain config or set allow_template_fallback=True to use "
                "the built-in generic fallback.",
                field="node_templates",
            )
        logger.warning("domain_node_templates_empty_using_fallback")
        return _get_fallback_node_templates()

    formatted_lines = [format_node_line(template) for template in node_templates]
    logger.debug("domain_node_templates_formatted", template_count=len(node_templates))
    return "\n".join(formatted_lines)


def format_domain_edge_templates(
    templates: dict[str, list[dict]],
    *,
    allow_template_fallback: bool = True,
) -> str:
    """Format edge templates from domain configuration for LLM prompt.

    Args:
        templates: Dictionary with 'node_templates' and 'edge_templates' lists
                  from domain.get_templates()
        allow_template_fallback: When True (legacy default), an empty
            edge-template list silently falls back to built-in generic
            templates. When False, raises ``ValidationError``.

    Returns:
        Formatted string for LLM prompt

    Raises:
        ValidationError: When ``allow_template_fallback=False`` and the
            domain has no edge templates configured.

    """
    edge_templates = templates.get("edge_templates", [])

    if not edge_templates:
        if not allow_template_fallback:
            raise ValidationError(
                "Domain has no edge templates configured and "
                "allow_template_fallback=False. Add relationship templates "
                "to the domain config or set allow_template_fallback=True.",
                field="edge_templates",
            )
        logger.warning("domain_edge_templates_empty_using_fallback")
        return _get_fallback_edge_templates()

    formatted_lines = [format_edge_line(template) for template in edge_templates]
    logger.debug("domain_edge_templates_formatted", template_count=len(edge_templates))
    return "\n".join(formatted_lines)


# ============================================================================
# Domain Examples Formatting
# ============================================================================


def format_domain_examples(  # noqa: C901, PLR0912
    examples: dict[str, list[dict]],
    max_chars: int | None = None,
) -> str:
    """Format domain-specific extraction examples for LLM prompt.

    Examples help the LLM understand domain-specific patterns like
    alias recognition, relationship typing, and entity extraction.

    Args:
        examples: Dictionary with optional keys:
            - alias_examples: List of alias example dicts
            - relationship_examples: List of relationship example dicts
            - entity_examples: List of entity extraction example dicts
        max_chars: Maximum characters for output (truncates at line boundary)

    Returns:
        Formatted string for LLM prompt, or empty string if no examples.

    """
    if not examples:
        return ""

    sections = []

    # Alias examples (most impactful for entity resolution)
    alias_examples = examples.get("alias_examples", [])
    if alias_examples:
        lines = ["Alias examples:"]
        for ex in alias_examples:
            canonical = ex.get("canonical", "")
            aliases = ex.get("aliases", [])
            if canonical and aliases:
                line = f"  {canonical} <- [{', '.join(aliases)}]"
                if ex.get("note"):
                    line += f" ({ex['note']})"
                lines.append(line)
        if len(lines) > 1:  # Has content beyond header
            sections.append("\n".join(lines))

    # Relationship examples
    rel_examples = examples.get("relationship_examples", [])
    if rel_examples:
        lines = ["Relationship examples:"]
        for ex in rel_examples:
            source = ex.get("source", "")
            target = ex.get("target", "")
            rel_type = ex.get("type", "")
            if source and target and rel_type:
                line = f"  {source} --[{rel_type}]--> {target}"
                if ex.get("note"):
                    line += f" ({ex['note']})"
                lines.append(line)
        if len(lines) > 1:
            sections.append("\n".join(lines))

    # Entity examples
    entity_examples = examples.get("entity_examples", [])
    if entity_examples:
        lines = ["Entity examples:"]
        for ex in entity_examples:
            entity = ex.get("entity", "")
            entity_type = ex.get("type", "")
            text = ex.get("text", "")
            if entity and entity_type:
                line = f'  "{text}" -> {entity} ({entity_type})'
                if ex.get("aliases"):
                    line += f" [aliases: {', '.join(ex['aliases'])}]"
                elif ex.get("note"):
                    line += f" ({ex['note']})"
                lines.append(line)
        if len(lines) > 1:
            sections.append("\n".join(lines))

    if not sections:
        return ""

    result = "\n\n".join(sections)

    # Truncate at line boundary if needed
    if max_chars and len(result) > max_chars:
        logger.warning(
            "domain_examples_truncated",
            original_chars=len(result),
            max_chars=max_chars,
        )
        result = result[:max_chars].rsplit("\n", 1)[0]

    logger.debug("domain_examples_formatted", chars=len(result))
    return result


def format_entity_examples(
    examples: dict[str, list[dict]],
    max_chars: int | None = None,
) -> str:
    """Format entity-specific examples for LLM prompt.

    Formats only alias_examples and entity_examples, excluding relationship
    examples.

    Args:
        examples: Dictionary with optional keys:
            - alias_examples: List of alias example dicts
            - entity_examples: List of entity extraction example dicts
        max_chars: Maximum characters for output (truncates at line boundary)

    Returns:
        Formatted string for LLM prompt, or empty string if no examples.

    """
    if not examples:
        return ""

    sections = []

    # Alias examples (most impactful for entity resolution)
    alias_examples = examples.get("alias_examples", [])
    if alias_examples:
        lines = ["Alias examples:"]
        for ex in alias_examples:
            canonical = ex.get("canonical", "")
            aliases = ex.get("aliases", [])
            if canonical and aliases:
                line = f"  {canonical} <- [{', '.join(aliases)}]"
                if ex.get("note"):
                    line += f" ({ex['note']})"
                lines.append(line)
        if len(lines) > 1:  # Has content beyond header
            sections.append("\n".join(lines))

    # Entity examples
    entity_examples = examples.get("entity_examples", [])
    if entity_examples:
        lines = ["Entity examples:"]
        for ex in entity_examples:
            entity = ex.get("entity", "")
            entity_type = ex.get("type", "")
            text = ex.get("text", "")
            if entity and entity_type:
                line = f'  "{text}" -> {entity} ({entity_type})'
                if ex.get("aliases"):
                    line += f" [aliases: {', '.join(ex['aliases'])}]"
                elif ex.get("note"):
                    line += f" ({ex['note']})"
                lines.append(line)
        if len(lines) > 1:
            sections.append("\n".join(lines))

    if not sections:
        return ""

    result = "\n\n".join(sections)

    # Truncate at line boundary if needed
    if max_chars and len(result) > max_chars:
        logger.warning(
            "entity_examples_truncated",
            original_chars=len(result),
            max_chars=max_chars,
        )
        result = result[:max_chars].rsplit("\n", 1)[0]

    logger.debug("entity_examples_formatted", chars=len(result))
    return result


def format_relationship_examples(
    examples: dict[str, list[dict]],
    max_chars: int | None = None,
) -> str:
    """Format relationship-specific examples for LLM prompt.

    Formats only relationship_examples.

    Args:
        examples: Dictionary with optional keys:
            - relationship_examples: List of relationship example dicts
        max_chars: Maximum characters for output (truncates at line boundary)

    Returns:
        Formatted string for LLM prompt, or empty string if no examples.

    """
    if not examples:
        return ""

    sections = []

    # Relationship examples
    rel_examples = examples.get("relationship_examples", [])
    if rel_examples:
        lines = ["Relationship examples:"]
        for ex in rel_examples:
            source = ex.get("source", "")
            target = ex.get("target", "")
            rel_type = ex.get("type", "")
            if source and target and rel_type:
                line = f"  {source} --[{rel_type}]--> {target}"
                if ex.get("note"):
                    line += f" ({ex['note']})"
                lines.append(line)
        if len(lines) > 1:
            sections.append("\n".join(lines))

    if not sections:
        return ""

    result = "\n\n".join(sections)

    # Truncate at line boundary if needed
    if max_chars and len(result) > max_chars:
        logger.warning(
            "relationship_examples_truncated",
            original_chars=len(result),
            max_chars=max_chars,
        )
        result = result[:max_chars].rsplit("\n", 1)[0]

    logger.debug("relationship_examples_formatted", chars=len(result))
    return result
