# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Configurable Domain Analyzer.

Domain analyzer driven entirely by JSON-LD configuration files.
No Python code needed for custom domains - just create a domain.jsonld file.

The JSON-LD format provides:
- Keyword-based content detection with weighted groups
- Regex pattern matching for additional signals
- Domain-specific LLM guidance
- Entity and relationship templates
- Type normalization rules

Example JSON-LD structure:
    {
        "@context": {...},
        "@type": "ExtractionDomain",
        "name": "technical",
        "detection": {
            "keywords": {"code": {"terms": ["def ", "class "], "weight": 1.0}},
            "confidence": {"base_score": 0.3, "per_keyword_boost": 0.05}
        },
        "guidance": "...",
        "templates": {...},
        "normalization_rules": {...}
    }
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

import structlog

from chaoscypher_core.plugins import PluginMetadata, metadata_from_dict
from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    ExclusionRule,
)


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class ConfigurableDomain:
    """Domain analyzer driven entirely by JSON-LD configuration.

    Implements the DomainAnalyzer protocol by reading all behavior from
    a JSON-LD config file. This enables users to create custom domains
    without writing Python code.

    Attributes:
        config: The loaded JSON-LD configuration.
        settings: Application settings (optional).
    """

    def __init__(self, config: dict[str, Any], settings: EngineSettings | None = None) -> None:
        """Initialize domain from configuration.

        Args:
            config: Parsed JSON-LD configuration dictionary.
            settings: Application settings (optional).
        """
        self.config = config
        self.settings = settings
        self._compiled_patterns: list[tuple[re.Pattern[str], float]] = []
        self._compile_patterns()
        self._keyword_groups: list[tuple[list[str], list[re.Pattern[str]], float]] = []
        self._preprocess_keywords()
        self._validate_content_exclusions()

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns from config for efficiency."""
        detection = self.config.get("detection", {})
        patterns = detection.get("patterns", [])

        for pattern_spec in patterns:
            regex = pattern_spec.get("regex")
            weight = pattern_spec.get("weight", 1.0)
            if regex:
                try:
                    compiled = re.compile(regex)
                    self._compiled_patterns.append((compiled, weight))
                except re.error as e:
                    logger.warning(
                        "invalid_regex_pattern",
                        domain=self.config.get("name", "unknown"),
                        pattern=regex,
                        error=str(e),
                    )

    def _preprocess_keywords(self) -> None:
        """Separate short terms from long terms and precompile word-boundary regexes.

        Short alphabetic terms (< 4 chars) are matched with word boundaries
        to prevent false positives from substring matching (e.g. "li" in "helium").
        Longer terms use simple substring matching as before.
        """
        detection = self.config.get("detection", {})
        keywords_config = detection.get("keywords", {})

        for group_spec in keywords_config.values():
            terms = group_spec.get("terms", [])
            weight = group_spec.get("weight", 1.0)

            long_terms: list[str] = []
            short_patterns: list[re.Pattern[str]] = []

            for term in terms:
                term_stripped = term.strip()
                if len(term_stripped) < 4 and term_stripped.isalpha():
                    short_patterns.append(
                        re.compile(rf"\b{re.escape(term_stripped)}\b", re.IGNORECASE)
                    )
                else:
                    long_terms.append(term_stripped.lower())

            self._keyword_groups.append((long_terms, short_patterns, weight))

    def _validate_content_exclusions(self) -> None:
        """Validate ``content_exclusions`` against the registry and regex engine.

        Runs at construction time so misconfiguration surfaces as WARNING
        at domain load rather than mid-extraction. Validates both:
        - ``categories``: must all be registered in ``CONTENT_CATEGORIES``.
        - ``custom_patterns``: each regex must be under 512 chars and compile
          cleanly through the sandboxed regex wrapper.

        Invalid entries are reported via WARNING and skipped at resolve
        time — the domain still loads.
        """
        from chaoscypher_core.services.sources.engine.extraction.content_categories import (
            CONTENT_CATEGORIES,
            validate_custom_patterns,
        )

        exclusions = self.config.get("content_exclusions") or {}
        domain_name = self.config.get("name", "unknown")

        category_names = exclusions.get("categories", [])
        if category_names:
            unknown = [n for n in category_names if n not in CONTENT_CATEGORIES]
            if unknown:
                logger.warning(
                    "unknown_content_category",
                    domain=domain_name,
                    unknown=unknown,
                    available=sorted(CONTENT_CATEGORIES),
                )

        custom_patterns = exclusions.get("custom_patterns", [])
        if custom_patterns:
            errors = validate_custom_patterns(custom_patterns)
            for err in errors:
                logger.warning(
                    "invalid_custom_pattern",
                    domain=domain_name,
                    index=err["index"],
                    regex=(err["regex"] or "")[:80],
                    error=err["error"],
                )

    @property
    def name(self) -> str:
        """Domain identifier.

        Returns:
            Domain name from config.
        """
        return cast("str", self.config.get("name", "unknown"))

    @property
    def description(self) -> str:
        """Human-readable description.

        Returns:
            Description from config, or empty string.
        """
        return cast("str", self.config.get("description", ""))

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata from JSON-LD config.

        Returns:
            PluginMetadata instance generated from config.
        """
        return metadata_from_dict(
            {
                "plugin_id": self.name,
                "name": self.name.title(),
                "description": self.description,
                "version": self.config.get("version", "1.0.0"),
                "author": self.config.get("author", ""),
                "category": "domain",
                "builtin": self.config.get("builtin", False),
            }
        )

    def can_analyze(
        self,
        text: str,
        filename: str,
        metadata: dict[str, Any],
    ) -> tuple[bool, float]:
        """Detect if this domain applies using config-based rules.

        Uses weighted keyword matching, file extension checks, doc_type
        detection, and regex patterns to calculate confidence.

        Args:
            text: Sample text from document.
            filename: Original filename.
            metadata: Document metadata (source, doc_type, etc.).

        Returns:
            (can_handle, confidence) tuple.
        """
        detection = self.config.get("detection", {})
        confidence_config = detection.get("confidence", {})

        # Start with base score
        base_score = confidence_config.get("base_score", 0.1)
        per_keyword_boost = confidence_config.get("per_keyword_boost", 0.05)
        extension_boost = confidence_config.get("extension_boost", 0.1)
        doc_type_boost = confidence_config.get("doc_type_boost", 0.2)
        pattern_boost = confidence_config.get("pattern_boost", 0.15)
        min_threshold = confidence_config.get("min_threshold", 0.4)
        # NOTE: max_confidence cap removed - raw scores provide better domain ranking

        text_lower = text.lower()
        confidence = base_score

        # Count keyword matches with weights
        for long_terms, short_patterns, weight in self._keyword_groups:
            matches = sum(1 for term in long_terms if term in text_lower)
            matches += sum(1 for pat in short_patterns if pat.search(text))
            confidence += matches * per_keyword_boost * weight

        # Check file extensions
        file_extensions = detection.get("file_extensions", [])
        if file_extensions:
            filename_lower = filename.lower()
            if any(filename_lower.endswith(ext) for ext in file_extensions):
                confidence += extension_boost

        # Check doc_type metadata
        doc_types = detection.get("doc_types", [])
        if doc_types:
            doc_type = metadata.get("doc_type", "")
            if doc_type in doc_types:
                confidence += doc_type_boost

        # Apply regex patterns
        for pattern, weight in self._compiled_patterns:
            if pattern.search(text):
                confidence += pattern_boost * weight

        # No capping - raw scores used for ranking, min_threshold for filtering
        can_handle = confidence >= min_threshold
        return can_handle, confidence

    def get_guidance(self) -> str:
        """Return domain-specific extraction guidance.

        Returns:
            LLM guidance text from config.
        """
        return cast("str", self.config.get("guidance", ""))

    def get_entity_guidance(self) -> str | None:
        """Get entity-specific extraction guidance.

        Domains can provide separate guidance for entity extraction
        vs relationship extraction.

        Returns:
            Entity-specific guidance if configured, otherwise falls back
            to the general 'guidance' field. Returns None if neither exists.
        """
        # Prefer entity_guidance if configured
        entity_guidance = self.config.get("entity_guidance")
        if entity_guidance:
            return cast("str", entity_guidance)
        # Fall back to general guidance for backwards compatibility
        guidance = self.config.get("guidance")
        return cast("str", guidance) if guidance else None

    def get_relationship_guidance(self) -> str | None:
        """Get relationship-specific extraction guidance.

        Domains can provide separate guidance for relationship extraction.
        This guidance can assume entities have already been extracted and
        are provided as context.

        Returns:
            Relationship-specific guidance if configured, otherwise falls back
            to the general 'guidance' field. Returns None if neither exists.
        """
        # Prefer relationship_guidance if configured
        relationship_guidance = self.config.get("relationship_guidance")
        if relationship_guidance:
            return cast("str", relationship_guidance)
        # Fall back to general guidance for backwards compatibility
        guidance = self.config.get("guidance")
        return cast("str", guidance) if guidance else None

    def get_templates(self) -> dict[str, list[dict[str, Any]]]:
        """Return domain-specific entity and edge templates.

        Returns:
            Templates dict with node_templates and edge_templates lists.
        """
        templates = self.config.get("templates", {})

        # Strip JSON-LD metadata from templates for consumption
        node_templates = templates.get("node_templates", [])
        edge_templates = templates.get("edge_templates", [])

        # Fields on templates that are metadata-only (not for LLM prompts)
        _node_strip_fields = {
            "@type",
            "quality_score",
            "normalization_keywords",
            "compatibility_group",
            "is_structural",
            "is_generic",
        }
        _prop_strip_fields = {"absorbs_types"}

        # Clean templates (remove @type and consolidation metadata)
        cleaned_nodes = []
        for tmpl in node_templates:
            cleaned = {
                "id": tmpl.get("id", ""),
                "name": tmpl.get("name", ""),
                "description": tmpl.get("description", ""),
            }
            # Include properties if defined (strip absorbs_types from each)
            if "properties" in tmpl:
                cleaned["properties"] = [
                    {k: v for k, v in prop.items() if k not in _prop_strip_fields}
                    for prop in tmpl["properties"]
                ]
            # Preserve entity name plausibility flag
            if "requires_named_referent" in tmpl:
                cleaned["requires_named_referent"] = tmpl["requires_named_referent"]
            # Preserve visual identity fields
            if "icon" in tmpl:
                cleaned["icon"] = tmpl["icon"]
            if "color" in tmpl:
                cleaned["color"] = tmpl["color"]
            cleaned_nodes.append(cleaned)

        cleaned_edges = []
        for tmpl in edge_templates:
            cleaned = {
                "id": tmpl.get("id", ""),
                "name": tmpl.get("name", ""),
                "description": tmpl.get("description", ""),
            }
            # Preserve visual identity fields
            if "icon" in tmpl:
                cleaned["icon"] = tmpl["icon"]
            if "color" in tmpl:
                cleaned["color"] = tmpl["color"]
            cleaned_edges.append(cleaned)

        return {
            "node_templates": cleaned_nodes,
            "edge_templates": cleaned_edges,
        }

    def get_normalization_rules(self) -> dict[str, list[str]]:
        """Return type normalization rules.

        Builds the mapping from ``normalization_keywords`` fields on
        individual node templates. Each template's keyword list maps
        back to that template's type name.

        Returns:
            Mapping of target types to trigger keywords.
        """
        templates = self.config.get("templates", {})
        rules: dict[str, list[str]] = {}
        for tmpl in templates.get("node_templates", []):
            name = tmpl.get("name")
            keywords = tmpl.get("normalization_keywords")
            if name and keywords:
                rules[name] = keywords
        return rules

    def get_type_aliases(self) -> dict[str, str]:
        """Return a mapping of alias entity type names to canonical names.

        Domains may declare a ``type_aliases`` block at the top level of
        their ``.jsonld`` config to collapse near-duplicate node templates
        into a single canonical type while preserving the original under
        the entity's ``entity_subtype`` property. See
        ``chaoscypher_core.services.sources.engine.extraction.utils.type_normalizer.apply_type_aliases``
        for the rewrite semantics.

        Example JSON-LD::

            {
              "type_aliases": {
                "Historical Figure": "Character",
                "Historical Event": "Event"
              }
            }

        Returns:
            Mapping of alias type → canonical type. Empty dict when the
            plugin declares no aliases. Non-string values are skipped (the
            JSON-LD is hand-authored; bad shape is a config error).
        """
        raw = self.config.get("type_aliases") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(k): str(v)
            for k, v in raw.items()
            if isinstance(k, str) and isinstance(v, str) and k and v
        }

    def get_examples(self) -> dict[str, list[dict[str, Any]]]:
        """Return domain-specific extraction examples.

        Returns:
            Dictionary with alias_examples, relationship_examples,
            entity_examples lists. Empty dict if no examples configured.
        """
        return cast("dict[str, list[dict[str, Any]]]", self.config.get("examples", {}))

    def get_quality_scoring(self) -> dict[str, Any]:
        """Return quality scoring configuration for this domain.

        Builds type-to-score mappings from ``quality_score`` fields on
        individual node and edge templates. Domain-level defaults are read
        from the ``quality_scoring`` section (``default_entity_score``,
        ``default_relationship_score``).

        Returns:
            Dictionary with keys:
            - ``entity_scores``: ``{type_name: score}`` for node templates
            - ``relationship_scores``: ``{type_name: score}`` for edge templates
            - ``default_entity_score``: Fallback score for unknown entity types
            - ``default_relationship_score``: Fallback score for unknown rel types
            - ``target_density``: Target relationships per entity (if configured)
        """
        templates = self.config.get("templates", {})
        scoring_defaults = self.config.get("quality_scoring", {})

        entity_scores: dict[str, int] = {}
        for tmpl in templates.get("node_templates", []):
            name = tmpl.get("name")
            score = tmpl.get("quality_score")
            if name and score is not None:
                entity_scores[name] = score

        relationship_scores: dict[str, int] = {}
        for tmpl in templates.get("edge_templates", []):
            name = tmpl.get("name")
            score = tmpl.get("quality_score")
            if name and score is not None:
                relationship_scores[name] = score

        result: dict[str, Any] = {
            "entity_scores": entity_scores,
            "relationship_scores": relationship_scores,
            "default_entity_score": scoring_defaults.get("default_entity_score", 18),
            "default_relationship_score": scoring_defaults.get("default_relationship_score", 15),
        }
        if "target_density" in scoring_defaults:
            result["target_density"] = scoring_defaults["target_density"]
        return result

    def get_title_words(self) -> list[str]:
        """Return title/honorific words for deduplication filtering.

        Returns:
            List of lowercase title words from config.
        """
        return cast("list[str]", self.config.get("title_words", []))

    def get_inverse_relationships(self) -> dict[str, str]:
        """Return inverse relationship mapping for this domain.

        Maps each edge type to its inverse for bidirectional graph traversal.
        Reads ``"inverse": "type_name"`` from edge template definitions.
        Symmetric templates (``"symmetric": true``) automatically map to
        themselves.

        Returns:
            Mapping of edge type to inverse edge type.
        """
        templates = self.config.get("templates", {})
        edge_templates = templates.get("edge_templates", [])

        inverse_map: dict[str, str] = {}
        for tmpl in edge_templates:
            name = tmpl.get("name")
            if not name:
                continue
            if tmpl.get("inverse"):
                inverse_map[name] = tmpl["inverse"]
            elif tmpl.get("symmetric") is True:
                inverse_map[name] = name

        return inverse_map

    def get_symmetric_relationships(self) -> list[str]:
        """Return symmetric relationship types for this domain.

        Symmetric relationships are bidirectional — ``(A, B)`` and ``(B, A)``
        are semantically identical. During relationship deduplication, both
        directions are collapsed into one, keeping the highest confidence.

        Reads ``"symmetric": true`` from edge template definitions.

        Returns:
            List of symmetric relationship type names.
        """
        templates = self.config.get("templates", {})
        edge_templates = templates.get("edge_templates", [])
        return [t["name"] for t in edge_templates if t.get("symmetric") is True]

    def get_edge_type_constraints(self) -> dict[str, dict[str, list[str]]]:
        """Return type constraints for edge templates.

        Maps each edge type to its allowed source and target entity types.
        Reads ``source_types``/``target_types`` from edge template definitions.

        Returns:
            Mapping of edge_type_name -> {"source_types": [...], "target_types": [...]}.
            Edge types without constraints are omitted.

        """
        templates = self.config.get("templates", {})
        edge_templates = templates.get("edge_templates", [])

        constraints: dict[str, dict[str, list[str]]] = {}
        for tmpl in edge_templates:
            name = tmpl.get("name")
            if not name:
                continue
            source_types = tmpl.get("source_types", [])
            target_types = tmpl.get("target_types", [])
            if source_types or target_types:
                entry: dict[str, list[str]] = {}
                if source_types:
                    entry["source_types"] = source_types
                if target_types:
                    entry["target_types"] = target_types
                constraints[name] = entry

        return constraints

    def get_entity_exclusions(self) -> list[ExclusionRule]:
        """Return domain-specific entity exclusion rules.

        The plugin config is validated at load time (see
        ``DomainConfigModel``), so each entry is guaranteed to have a
        non-blank description and at least one example. The legacy
        ``list[str]`` form is rejected by the schema.

        Returns:
            Typed exclusion rules, or empty list when the domain
            declares none.
        """
        raw = self.config.get("entity_exclusions", [])
        return [ExclusionRule.model_validate(r) for r in raw]

    def get_type_compatibility(self) -> dict[str, list[str]]:
        """Return domain-specific type compatibility groups for deduplication.

        Builds the group mapping from ``compatibility_group`` fields on
        individual node templates. Templates sharing the same group name
        are collected into a single list.

        Returns:
            Mapping of group name to list of compatible type names.
            Empty dict if no templates carry this field.
        """
        templates = self.config.get("templates", {})
        groups: dict[str, list[str]] = {}
        for tmpl in templates.get("node_templates", []):
            name = tmpl.get("name")
            group = tmpl.get("compatibility_group")
            if name and group:
                groups.setdefault(group, []).append(name)
        return groups

    def get_strict_entity_types(self) -> bool:
        """Return whether entity type enforcement is enabled for this domain.

        When True, the extraction prompt instructs the LLM to ONLY use
        entity types from the domain's node_templates. A code-level filter
        also drops extracted entities whose type doesn't match any template.

        Returns:
            True to enforce strict type matching, False to allow the LLM
            to create new types. Default is False.

        """
        return bool(self.config.get("strict_entity_types", False))

    def get_extraction_limits(self) -> dict[str, float | int]:
        """Return domain-specific relationship extraction limits.

        Different domains have different natural relationship densities.
        Literary texts have dense character webs (higher limits), while
        technical docs have sparser, more structured relationships.

        These limits are enforced in code after LLM extraction. Global
        settings act as fallbacks when a domain doesn't specify limits.

        Returns:
            Dictionary of ``FilteringConfig`` field overrides. The preset
            selector lives on ``get_filtering_mode()`` — never inlined here.
            Empty dict uses global defaults.
        """
        return cast("dict[str, float | int]", dict(self.config.get("extraction_limits", {})))

    def get_filtering_mode(self) -> str | None:
        """Return the preset selector (``extraction_filtering_mode``) from the jsonld."""
        mode = self.config.get("extraction_filtering_mode")
        return cast("str | None", mode) if mode else None

    def get_evidence_validation_mode(self) -> str | None:
        """Return the evidence validation mode for this domain.

        Allows domains to override the global ``evidence_validation_mode``
        setting on a per-domain basis.

        Returns:
            ``"strict"``, ``"standard"``, ``"relaxed"``, or ``None`` to use
            the global default from ``ExtractionSettings``.
        """
        mode = self.config.get("evidence_validation_mode")
        return cast("str | None", mode)

    def get_property_type_mapping(self) -> dict[str, dict[str, str]]:
        """Return property-type mapping for the type rescue system.

        Builds the mapping from ``absorbs_types`` arrays on individual
        node template property definitions. Each absorbed type maps back
        to the owning template's type name and the property name.

        Returns:
            Mapping of invalid_type -> {"target_type": ..., "property": ...}.
            Empty dict if no templates carry absorbs_types.
        """
        templates = self.config.get("templates", {})
        mapping: dict[str, dict[str, str]] = {}
        for tmpl in templates.get("node_templates", []):
            target_type = tmpl.get("name")
            if not target_type:
                continue
            for prop in tmpl.get("properties", []):
                prop_name = prop.get("name")
                for absorbed in prop.get("absorbs_types", []):
                    mapping[absorbed] = {
                        "target_type": target_type,
                        "property": prop_name,
                    }
        return mapping

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
            Density factor from config (default 1.0).
        """
        return cast("float", self.config.get("extraction_density", 1.0))

    def get_content_exclusions(self) -> dict[str, Any]:
        """Return content exclusion configuration for this domain.

        Domains can specify which built-in content categories to exclude
        and add custom regex patterns for domain-specific filtering.
        Content exclusions are applied before entity extraction to skip
        non-essential content (TOC, changelogs, copyright, etc.).

        Returns:
            Dictionary with optional keys:
            - ``categories``: List of built-in category names (e.g. ["toc", "changelog"])
            - ``custom_patterns``: List of custom pattern dicts with keys:
              regex, mode, threshold, description
            Empty dict if not configured.
        """
        return cast("dict[str, Any]", self.config.get("content_exclusions", {}))

    def get_system_prompt_override(self) -> str | None:
        """Return the per-domain system-prompt override if configured.

        Allows a domain JSONLD config to replace the global
        ``ExtractionSettings.system_prompt`` for all extraction calls
        on this domain.  ``None`` means "no override — use the global default".

        Example JSONLD::

            {
              "extraction_overrides": {
                "system_prompt": "You are a biomedical entity extractor. ..."
              }
            }

        Returns:
            Replacement system prompt string, or ``None`` when absent.
        """
        overrides = self.config.get("extraction_overrides")
        if not isinstance(overrides, dict):
            return None
        return cast("str | None", overrides.get("system_prompt"))


def load_domain_config(path: Path) -> dict[str, Any]:
    """Load domain configuration from JSON-LD or JSON file.

    Args:
        path: Path to domain.jsonld or domain.json file.

    Returns:
        Parsed configuration dictionary.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        json.JSONDecodeError: If config file is invalid JSON.
    """
    import json

    content = path.read_text(encoding="utf-8")
    return cast("dict[str, Any]", json.loads(content))


__all__ = ["ConfigurableDomain", "load_domain_config"]
