# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Domain Analyzer Protocol.

Defines the interface for domain-specific extraction analyzers.
Similar to BaseLoader protocol for document loaders.

Implementations are auto-discovered via domain.jsonld marker files in:
- plugins/: Built-in domains shipped with the application
- per-database domains/: User-added domains (drop-in folder)

Example:
    from chaoscypher_core.plugins import PluginMetadata

    class TechnicalDomain:
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                plugin_id="technical",
                name="Technical",
                description="Technical/API documentation",
                category="domain",
            )

        @property
        def name(self) -> str:
            return "technical"

        def can_analyze(self, text, filename, metadata) -> tuple[bool, float]:
            # Detect technical documentation
            ...

        def get_guidance(self) -> str:
            return "Use Module, Class, Function types..."
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from chaoscypher_core.plugins import PluginMetadata
    from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
        ExclusionRule,
    )


@runtime_checkable
class DomainAnalyzer(Protocol):
    """Protocol for domain-specific extraction analyzers.

    Each domain analyzer provides:
    - Plugin metadata (metadata)
    - Content detection (can_analyze)
    - LLM guidance for entity extraction (get_guidance)
    - Domain-specific templates (get_templates)
    - Type normalization rules (get_normalization_rules)

    Implementations are auto-discovered from plugins/ and per-database
    domains/ directories via domain.jsonld marker files.
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata (optional).

        Returns:
            PluginMetadata instance with domain information.

        Note:
            This property is optional for backwards compatibility.
            ConfigurableDomain generates it from JSON-LD config.
        """
        ...

    @property
    def name(self) -> str:
        """Unique domain identifier.

        Returns:
            Domain name (e.g., 'technical', 'legal', 'medical').
        """
        ...

    @property
    def description(self) -> str:
        """Human-readable description of this domain.

        Returns:
            Description string.
        """
        ...

    def can_analyze(
        self,
        text: str,
        filename: str,
        metadata: dict[str, Any],
    ) -> tuple[bool, float]:
        """Check if this domain applies to the content.

        Args:
            text: Sample text from document (first ~2000 chars).
            filename: Original filename.
            metadata: Document metadata (source, doc_type, etc.).

        Returns:
            Tuple of (can_handle, confidence):
            - can_handle: True if this domain applies
            - confidence: 0.0-1.0 indicating match strength
        """
        ...

    def get_guidance(self) -> str:
        """Return domain-specific extraction guidance for the LLM prompt.

        This guidance is appended to the extraction prompt when this
        domain is selected for a document.

        Returns:
            Guidance text for the LLM, or empty string for no guidance.
        """
        ...

    def get_templates(self) -> dict[str, list[dict[str, Any]]]:
        """Return domain-specific entity and edge templates.

        These templates are domain-specific suggestions for entity types.

        Returns:
            Dictionary with keys:
            - "node_templates": List of entity template dicts
            - "edge_templates": List of relationship template dicts

            Each template dict has: id, name, description
        """
        ...

    def get_normalization_rules(self) -> dict[str, list[str]]:
        """Return type normalization rules for this domain.

        Used to fix entity types post-extraction by matching
        description keywords to target types.

        Returns:
            Mapping of target_type to list of trigger keywords.
            Example: {"Class": ["a class", "class that", ...]}
        """
        ...

    def get_type_aliases(self) -> dict[str, str]:
        """Return alias entity types that should be rewritten to a canonical type.

        Optional. Lets a domain collapse near-duplicate node templates
        (e.g. literary's ``Historical Figure`` → ``Character``) into a
        single canonical type post-extraction. The original type is
        preserved on the entity under ``properties.entity_subtype`` so
        the refinement signal isn't lost. Apply via
        ``apply_type_aliases`` in ``type_normalizer``.

        Returns:
            Mapping of alias type name → canonical type name. Empty dict
            when the domain declares no aliases.
        """
        ...

    def get_examples(self) -> dict[str, list[dict[str, Any]]]:
        """Return domain-specific extraction examples for the LLM prompt.

        Examples help the LLM understand domain-specific patterns like:
        - Alias recognition (character names with titles)
        - Relationship typing (spouse_of vs related_to)
        - Entity extraction from context

        Returns:
            Dictionary with optional keys:
            - "alias_examples": List of alias example dicts
            - "relationship_examples": List of relationship example dicts
            - "entity_examples": List of entity extraction example dicts

            Empty dict if no examples configured.
        """
        ...

    def get_title_words(self) -> list[str]:
        """Return title/honorific words to filter during entity deduplication.

        These words are excluded from significant-word extraction and
        skipped when adding alias names during entity merging, preventing
        generic titles from becoming entity aliases.

        Returns:
            List of lowercase title words for this domain.
        """
        ...

    def get_extraction_density(self) -> float:
        """Return extraction density factor for this domain.

        Extraction density indicates how many entities/relationships are
        typically extracted per chunk for this domain. Higher values mean
        denser content that produces more extraction output.

        Examples:
            - scientific: 1.3 (structured data with hypothesis, methods, findings)
            - technical: 1.2 (code templates, classes, functions)
            - news: 0.95 (straightforward narrative)
            - generic: 1.0 (baseline)

        Returns:
            Density factor (default 1.0). Higher = more entities per chunk.
        """
        ...

    def get_entity_exclusions(self) -> list[ExclusionRule]:
        """Return domain-specific entity exclusion rules.

        Each rule pairs a human-readable description with a non-empty
        list of example names. The description goes into the LLM prompt
        ("SKIP these — they are NOT entities"); the examples drive the
        post-extraction code-level filter.

        Returns:
            Typed exclusion rules. Empty list means no domain-specific
            exclusions (only generic prompt defaults apply).
        """
        ...

    def get_type_compatibility(self) -> dict[str, list[str]]:
        """Return domain-specific type compatibility groups for deduplication.

        Types within the same group are considered compatible for entity
        merging during deduplication. For example, an entity appearing as
        "Medical Condition" in one chunk and "Plot Element" in another can
        be merged if both types appear in the same compatibility group.

        Returns:
            Mapping of group name to list of compatible type names.
            Empty dict means no domain-specific compatibility rules.
        """
        ...

    def get_strict_entity_types(self) -> bool:
        """Return whether entity type enforcement is enabled for this domain.

        When True, the extraction prompt instructs the LLM to ONLY use
        entity types from the domain's node_templates. A code-level filter
        also drops extracted entities whose type doesn't match any template.

        Returns:
            True to enforce strict type matching, False to allow the LLM
            to create new types. Default is False.
        """
        ...

    def get_extraction_limits(self) -> dict[str, float | int]:
        """Return domain-specific relationship extraction limits.

        Different domains have different natural relationship densities.
        Literary texts have dense character webs (higher limits), while
        technical docs have sparser, more structured relationships.

        These limits are enforced in code after LLM extraction. Global
        settings act as fallbacks when a domain doesn't specify limits.

        Keys (all optional — missing keys use global settings fallback):
            - ``max_relationship_ratio``: Max relationships as multiple of
              entity count (e.g., 3.0 = at most 3x entities).
            - ``max_entity_degree``: Max relationships per entity (source +
              target combined).
            - ``max_same_source_type``: Max relationships with the same
              (source_index, type) pair.

        Returns:
            Dictionary of limit overrides — only ``FilteringConfig`` field
            names are valid keys. The preset selector is returned by
            ``get_filtering_mode()`` instead and must never appear here.
            Empty dict uses global defaults.
        """
        ...

    def get_filtering_mode(self) -> str | None:
        """Return the domain's preferred filtering preset name, if any.

        This is the preset *selector* (``"strict"``, ``"balanced"``, …)
        and lives in its own accessor — distinct from ``get_extraction_limits``,
        which returns per-field overrides on top of the chosen preset.
        Keeping the two apart avoids the historical bug where the
        selector was inlined into the overrides dict and then handed to
        ``resolve_filtering_config(domain_overrides=…)``, which only
        understands ``FilteringConfig`` field names.

        Returns:
            A preset name from
            ``chaoscypher_core.services.sources.engine.extraction.utils.filtering_config.VALID_PRESETS``,
            or ``None`` when the domain does not pin a preset (callers fall
            back to the engine-level default).
        """
        ...

    def get_property_type_mapping(self) -> dict[str, dict[str, str]]:
        """Return property-type mapping for the type rescue system.

        Maps invalid entity type names to a target entity type and a property
        name on that target. Used to absorb entities that are really properties
        of another entity (e.g., "Personality Trait" -> Character.personality_traits).

        Returns:
            Mapping of invalid_type -> {"target_type": ..., "property": ...}.
            Empty dict if not configured.
        """
        ...

    def get_evidence_validation_mode(self) -> str | None:
        """Return the evidence validation mode for this domain.

        Allows domains to override the global ``evidence_validation_mode``
        setting on a per-domain basis.

        Returns:
            ``"strict"``, ``"standard"``, ``"relaxed"``, or ``None`` to use
            the global default from ``ExtractionSettings``.
        """
        ...


__all__ = ["DomainAnalyzer"]
