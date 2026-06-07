# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared post-extraction pipeline helpers.

The structural-filter + type-normalize pair runs in three places — the
standalone ``extract_entities_from_groups`` helper, the production
``ExtractionService.finalize_distributed_extraction`` (Cortex / CLI),
and the worker ``_finalize_extraction_inner`` path (Neuron). Keeping
all three in lockstep is the difference between "same source produces
the same graph" and the W3 parity drift this module exists to prevent.

This module collects the shared sequence so each caller threads the
same inputs (resolved ``FilteringConfig``, resolved domain, normalization
rules) and gets the same outputs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer import (
    apply_type_aliases,
    filter_structural_entities,
    normalize_entity_types,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        FilteringConfig,
    )


logger = structlog.get_logger(__name__)


def get_domain_structural_filters(
    domain: Any | None,
) -> tuple[set[str], set[str]]:
    """Resolve ``(structural_types, generic_types)`` for a domain.

    Reads ``is_structural`` / ``is_generic`` flags from the domain's node
    templates when the domain exposes the raw ``config`` attribute (the
    JSON-LD-driven ``ConfigurableDomain``). Returns empty sets when the
    domain is ``None`` or when its templates carry no flags — the type
    normalizer and structural filter then fall back to their module-level
    defaults (``_DEFAULT_STRUCTURAL_ENTITY_TYPES`` /
    ``_DEFAULT_GENERIC_TYPES``).

    Args:
        domain: Resolved domain object (typically a ``ConfigurableDomain``)
            or ``None``.

    Returns:
        Tuple of ``(structural_types, generic_types)`` — both ``set[str]``
        of type names. Either may be empty.
    """
    if domain is None:
        return set(), set()

    # ConfigurableDomain stashes the raw JSON-LD on ``config``; reach in
    # rather than adding a public getter, since this is the only caller
    # that needs the structural / generic flags.
    config = getattr(domain, "config", None)
    if not isinstance(config, dict):
        return set(), set()

    templates = config.get("templates", {})
    node_templates = templates.get("node_templates", []) if isinstance(templates, dict) else []

    structural_types: set[str] = set()
    generic_types: set[str] = set()
    for tmpl in node_templates:
        if not isinstance(tmpl, dict):
            continue
        name = tmpl.get("name")
        if not name:
            continue
        if tmpl.get("is_structural"):
            structural_types.add(name)
        if tmpl.get("is_generic"):
            generic_types.add(name)

    return structural_types, generic_types


def apply_domain_type_aliases(
    entities: list[dict[str, Any]],
    domain: Any | None,
) -> int:
    """Apply the domain's ``type_aliases`` map to a list of entities.

    Shared wrapper used by all three finalize sites (standalone
    ``extract_entities_from_groups``, ``ExtractionService.finalize_distributed_extraction``,
    and the Neuron worker's ``_finalize_extraction_inner``). Resolves the
    alias mapping from the domain, then delegates to
    ``apply_type_aliases`` for the in-place rewrite. Must run BEFORE
    dedup and BEFORE the relationship-type constraint validator so:

    * dedup sees canonical types and merges name variants split across
      alias types (e.g. ``Historical Figure: Pierre`` +
      ``Character: Pierre`` collapse to one node);
    * the type validator sees canonical endpoints and applies edge
      templates against the canonical type list.

    Defensive throughout: a ``None`` domain, a domain without
    ``get_type_aliases``, an empty alias map, or a raising accessor all
    fall through as a no-op. Finalization is critical — an aliasing bug
    must not block commit.

    Args:
        entities: Entity dicts (modified in-place when aliases apply).
        domain: Resolved domain object, or ``None``.

    Returns:
        Count of entities whose type was rewritten. Zero on any
        defensive fallthrough.
    """
    if not entities or domain is None:
        return 0

    accessor = getattr(domain, "get_type_aliases", None)
    if not callable(accessor):
        return 0

    try:
        aliases = accessor()
    except Exception:
        logger.warning(
            "type_aliases_accessor_raised",
            domain=getattr(domain, "name", "unknown"),
            exc_info=True,
        )
        return 0

    if not isinstance(aliases, dict) or not aliases:
        return 0

    rewritten = apply_type_aliases(entities, aliases)
    if rewritten:
        logger.info(
            "type_aliases_applied_in_finalize",
            domain=getattr(domain, "name", "unknown"),
            rewritten=rewritten,
            alias_count=len(aliases),
        )
    return rewritten


def apply_structural_and_normalization(
    entities: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
    *,
    domain: Any | None,
    filtering_config: FilteringConfig,
    normalization_rules: dict[str, list[str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Apply structural-entity filter and type normalization in lockstep.

    The structural filter is gated on
    ``filtering_config.enable_structural_filter`` so ``minimal`` and
    ``unfiltered`` modes (which set the flag to ``False``) skip it
    entirely — matching the standalone path's behaviour. Type
    normalization always runs, but a ``None`` / empty rule mapping
    short-circuits inside ``normalize_entity_types``.

    Both ``structural_types`` and ``generic_types`` are resolved once
    via ``get_domain_structural_filters`` so the normalizer honours
    custom-domain generic types (e.g. ``Notion``, ``Idea``) instead of
    silently falling back to the default ``Item``/``Concept``/``Unknown``
    set.

    Args:
        entities: Entities to filter and re-type.
        relationships: Relationships keyed by integer indices into
            ``entities``. Re-mapped if the structural filter removes
            entities.
        domain: Resolved domain object (or ``None``). Drives
            structural / generic type resolution.
        filtering_config: Resolved ``FilteringConfig``. Gates whether
            the structural filter runs at all.
        normalization_rules: Mapping of target type to keyword list,
            from ``domain.get_normalization_rules()``. Empty / ``None``
            short-circuits normalization.

    Returns:
        ``(entities, relationships, structural_filtered_count)``. The
        count is the number of entities removed by the structural
        filter — zero when the filter was gated off or no entities
        matched. Callers (worker path) increment a quality counter
        with this value.
    """
    structural_types, generic_types = get_domain_structural_filters(domain)

    structural_filtered = 0
    if filtering_config.enable_structural_filter:
        pre_count = len(entities)
        entities, relationships, _ = filter_structural_entities(
            entities,
            relationships,
            structural_entity_types=structural_types or None,
        )
        structural_filtered = pre_count - len(entities)

    if normalization_rules:
        entities = normalize_entity_types(
            entities,
            normalization_rules,
            generic_types=generic_types or None,
        )

    return entities, relationships, structural_filtered


__all__ = [
    "apply_domain_type_aliases",
    "apply_structural_and_normalization",
    "get_domain_structural_filters",
]
