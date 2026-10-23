# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Configurable Domain Analyzer.

Domain analyzer driven entirely by JSON-LD configuration files.
No Python code needed for custom domains - just create a domain.jsonld file.

The JSON-LD format provides:
- Keyword-based content detection with weighted groups
- Regex pattern matching for additional signals
- Domain-specific LLM guidance
- Entity and relationship templates (the floor) or subtypes and extra labels (a pack)

Example JSON-LD structure:
    {
        "@context": {...},
        "@type": "ExtractionDomain",
        "name": "technical",
        "detection": {
            "keywords": {"code": {"terms": ["def ", "class "], "weight": 1.0}},
            "confidence": {"base_score": 0.3, "per_keyword_boost": 0.05}
        },
        "role": "pack",
        "extends": "core",
        "entity_guidance": "...",
        "subtypes": [{"name": "Character", "of": "Person", "description": "..."}],
        "templates": {"edge_templates": [...]}
    }

Since 2026-10-07 (spec "additive domains") a config is the one floor
(``generic``: the core types and labels) or a pack that only adds to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog

from chaoscypher_core.plugins import PluginMetadata, metadata_from_dict
from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    ExclusionRule,
)
from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    FLOOR_LIMITS,
    subtype_key,
)
from chaoscypher_core.services.sources.engine.extraction.safe_user_regex import (
    PatternTooLongError,
    SafeUserRegex,
    compile_safe,
)


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


IDENTITY_TABLES = (
    "title_words",
    "feminine_titles",
    "masculine_titles",
    "generation_words",
    "name_forms",
    "title_equivalents",
    "relation_vocabulary",
    "role_words",
)
"""The naming and relation tables a domain may declare (the name rule, the evidence validator).

A table a domain leaves out is the universal one (``NameConventions.build``);
:meth:`ConfigurableDomain.get_identity_tables` returns the declared ones.
"""

DEFAULT_DETECTION_BASE_SCORE = 0.1
"""The score a domain starts from before any keyword, extension or pattern hit."""


def detection_base_score(config: dict[str, Any]) -> float:
    """The base score a domain config's detection starts from (no evidence yet).

    The one reading of ``detection.confidence.base_score``: :meth:`ConfigurableDomain.can_analyze`
    starts every score here, and the registry's detection floor measures a
    winner's evidence as its score minus this value.
    """
    confidence = (config.get("detection") or {}).get("confidence") or {}
    return float(confidence.get("base_score", DEFAULT_DETECTION_BASE_SCORE))


DEFAULT_DETECTION_MAX_CONFIDENCE = 1.0
"""The most confidence a detection reports when a domain declares no ``max_confidence``."""


def detection_max_confidence(config: dict[str, Any]) -> float:
    """The most confidence a domain config's detection reports (``detection.confidence.max_confidence``).

    Raw scores rank the candidates and decide the evidence floor and the
    runner-up margin (they are not on any common scale and grow with the
    sample: Book One whole scores literary 1.62); the confidence a detection
    reports is capped here (``DomainRegistry.get_best_domain``), so a
    confidence is never above 1 and never above what the domain declares.
    """
    confidence = (config.get("detection") or {}).get("confidence") or {}
    declared = float(confidence.get("max_confidence", DEFAULT_DETECTION_MAX_CONFIDENCE))
    return min(declared, DEFAULT_DETECTION_MAX_CONFIDENCE)


KEYWORD_INFLECTIONS = (
    "s", "es", "d", "ed", "ing", "er", "ers", "or", "ors", "al", "ally", "ic", "ical", "ian",
    "ians", "ly", "ism", "ist", "ists", "ity", "ment", "ments", "ion", "ions", "ual",
)  # fmt: skip
"""Endings a detection keyword may carry and still be the keyword ("exploit" in "exploited")."""

_INFLECTION = "(?:" + "|".join(sorted(KEYWORD_INFLECTIONS, key=len, reverse=True)) + ")?"


def keyword_pattern(term: str) -> re.Pattern[str]:
    """How a detection keyword matches: as a word or an inflection of it, never inside another word.

    Until 2026-10-06 a term of four letters or more matched anywhere
    (``nist`` in "minister", ``verse`` in "conversation", ``import`` in
    "important", ``plea`` in "pleasure"), so a novel scored as
    cybersecurity, technical or investigation prose (audit wave 3). Now:

    * a term written in capitals (``MORI``, ``FLIR``, ``NIST``) is an
      acronym: matched case-sensitively, as a whole word (a plural ``s``
      allowed), so "Morio" and "flirt" are not markings;
    * a short term (under four letters) matches as a whole word, any case;
    * any other term matches as a whole word, any case, with one of the
      :data:`KEYWORD_INFLECTIONS` (its last letter may double: "wiretapping"),
      so "teach" counts in "teaching" and "spirit" in "spiritual", and
      "import" not in "important".
    """
    stripped = term.strip()
    escaped = re.escape(stripped)
    letters = [c for c in stripped if c.isalpha()]
    if len(letters) >= 2 and all(c.isupper() for c in letters):
        return re.compile(rf"(?<!\w){escaped}s?(?!\w)")
    if len(stripped) < 4 and stripped.isalpha():
        return re.compile(rf"\b{escaped}\b", re.IGNORECASE)
    start = r"\b" if stripped[:1].isalnum() else ""
    if not stripped[-1:].isalnum():
        return re.compile(rf"{start}{escaped}", re.IGNORECASE)
    double = re.escape(stripped[-1]) + "?" if stripped[-1].isalpha() else ""
    return re.compile(rf"{start}{escaped}{double}{_INFLECTION}\b", re.IGNORECASE)


@dataclass(frozen=True)
class SubtypeSpec:
    """A pack's subtype of a core type, as :meth:`ConfigurableDomain.subtypes_of` returns it."""

    name: str
    """The name the pack shows ('Military Unit')."""
    of: str
    """The core type it is a kind of ('Organization')."""
    description: str = ""
    properties: tuple[dict[str, Any], ...] = field(default=())
    names_a_person: bool = False
    icon: str | None = None
    color: str | None = None
    rescue_keywords: tuple[str, ...] = ()
    """Words that rescue a written type to this subtype (the old template's ``normalization_keywords``)."""

    @property
    def key(self) -> str:
        """The ``entity_subtype`` value a node of this subtype stores ('military_unit')."""
        return subtype_key(self.name)


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
        self._compiled_patterns: list[tuple[SafeUserRegex, float]] = []
        self._compile_patterns()
        self._keyword_groups: list[tuple[list[re.Pattern[str]], float]] = []
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
                # flags=0 keeps the stdlib `re.compile(regex)` semantics this
                # call had: compile_safe defaults to IGNORECASE|MULTILINE for
                # parity with compile_custom_patterns, but the shipped
                # detection patterns are case-sensitive by design (the
                # `reference` domain matches RFC-2119 MUST/SHALL, `literary`
                # anchors ^chapter), so the defaults would change what matches.
                try:
                    compiled = compile_safe(regex, flags=0)
                    self._compiled_patterns.append((compiled, weight))
                except PatternTooLongError as e:
                    logger.warning(
                        "detection_pattern_too_long",
                        domain=self.config.get("name", "unknown"),
                        length=len(regex),
                        error=str(e),
                    )
                except Exception as e:  # covers regex.error and any wrapper error
                    logger.warning(
                        "invalid_regex_pattern",
                        domain=self.config.get("name", "unknown"),
                        pattern=regex,
                        error=str(e),
                    )

    def _preprocess_keywords(self) -> None:
        """Precompile each keyword as a word match (:func:`keyword_pattern`), grouped with its weight."""
        detection = self.config.get("detection", {})
        keywords_config = detection.get("keywords", {})

        for group_spec in keywords_config.values():
            terms = group_spec.get("terms", [])
            weight = group_spec.get("weight", 1.0)
            patterns = [keyword_pattern(t) for t in terms if t.strip()]
            self._keyword_groups.append((patterns, weight))

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
    def role(self) -> Literal["floor", "pack"] | None:
        """``"floor"`` for the generic floor, ``"pack"`` for a pack; None for an unvalidated config."""
        role = self.config.get("role")
        return cast("Literal['floor', 'pack']", role) if role in ("floor", "pack") else None

    @property
    def extends(self) -> str | None:
        """What a pack extends (``"core"``); None for the floor."""
        extends = self.config.get("extends")
        return str(extends) if extends else None

    def subtypes(self) -> list[SubtypeSpec]:
        """Every subtype the pack declares, in config order (empty for the floor)."""
        out: list[SubtypeSpec] = []
        for raw in self.config.get("subtypes") or []:
            if not isinstance(raw, dict) or not raw.get("name") or not raw.get("of"):
                continue
            out.append(
                SubtypeSpec(
                    name=str(raw["name"]),
                    of=str(raw["of"]),
                    description=str(raw.get("description", "")),
                    properties=tuple(dict(p) for p in raw.get("properties") or []),
                    names_a_person=raw.get("names_a_person") is True,
                    icon=raw.get("icon"),
                    color=raw.get("color"),
                    rescue_keywords=tuple(str(k) for k in raw.get("rescue_keywords") or ()),
                )
            )
        return out

    def subtypes_of(self, core_type: str) -> list[SubtypeSpec]:
        """The pack's subtypes of ``core_type`` (``Person`` -> Character, Author, ...), in config order."""
        return [s for s in self.subtypes() if s.of == core_type]

    def core_properties(self, core_type: str) -> list[dict[str, Any]]:
        """The properties a pack adds to ``core_type`` itself (``core_properties``), in config order."""
        raw = (self.config.get("core_properties") or {}).get(core_type) or []
        return [dict(p) for p in raw if isinstance(p, dict)]

    def core_rescue_keywords(self) -> dict[str, list[str]]:
        """The words a pack rescues a written type to a core type itself by (``core_rescue_keywords``)."""
        raw = self.config.get("core_rescue_keywords") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(core): [str(k) for k in kws]
            for core, kws in raw.items()
            if isinstance(kws, list) and kws
        }

    def extra_edge_templates(self) -> list[dict[str, Any]]:
        """The edge templates a pack adds to the floor's (cleaned as :meth:`get_templates`); none for the floor."""
        if self.role == "floor":
            return []
        return self.get_templates()["edge_templates"]

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
        base_score = detection_base_score(self.config)
        per_keyword_boost = confidence_config.get("per_keyword_boost", 0.05)
        extension_boost = confidence_config.get("extension_boost", 0.1)
        doc_type_boost = confidence_config.get("doc_type_boost", 0.2)
        pattern_boost = confidence_config.get("pattern_boost", 0.15)
        min_threshold = confidence_config.get("min_threshold", 0.4)
        # Raw, uncapped: the registry ranks by it and caps what it reports
        # (``detection_max_confidence``).

        confidence = base_score

        # Count keyword matches with weights (each distinct term once, as a word)
        for patterns, weight in self._keyword_groups:
            matches = sum(1 for pat in patterns if pat.search(text))
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
        """Return domain-specific extraction guidance (the entity guidance).

        The legacy single ``guidance`` key was removed (spec 2026-10-07 § 3):
        a config writes ``entity_guidance`` and ``relationship_guidance``.

        Returns:
            The entity guidance, or an empty string.
        """
        return self.get_entity_guidance() or ""

    def get_entity_guidance(self) -> str | None:
        """Get entity-specific extraction guidance.

        Domains can provide separate guidance for entity extraction
        vs relationship extraction.

        Returns:
            Entity-specific guidance if configured, otherwise None.
        """
        entity_guidance = self.config.get("entity_guidance")
        return cast("str", entity_guidance) if entity_guidance else None

    def get_relationship_guidance(self) -> str | None:
        """Get relationship-specific extraction guidance.

        Domains can provide separate guidance for relationship extraction.
        This guidance can assume entities have already been extracted and
        are provided as context.

        Returns:
            Relationship-specific guidance if configured, otherwise None.
        """
        relationship_guidance = self.config.get("relationship_guidance")
        return cast("str", relationship_guidance) if relationship_guidance else None

    def get_templates(self) -> dict[str, list[dict[str, Any]]]:
        """Return domain-specific entity and edge templates.

        The floor returns the core types and the core labels; a pack has no
        node templates (its subtypes are :meth:`subtypes_of`) and returns
        ``{"node_templates": [], "edge_templates": <the labels it adds>}``.

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

        A pack may declare a ``type_aliases`` block at the top level of its
        ``.jsonld`` config mapping a written type name to one of its
        subtypes or to a core type (a pack has no node templates of its
        own); the written name is preserved under the entity's
        ``entity_subtype`` property. See
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
              (the floor's core types; a pack has none)
            - ``subtype_scores``: ``{entity_subtype: score}`` for a pack's
              subtypes (keyed by the stored value, ``subtype_key``)
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

        subtype_scores: dict[str, int] = {}
        for sub in self.config.get("subtypes") or []:
            if isinstance(sub, dict) and sub.get("name") and sub.get("quality_score") is not None:
                subtype_scores[subtype_key(str(sub["name"]))] = sub["quality_score"]

        relationship_scores: dict[str, int] = {}
        for tmpl in templates.get("edge_templates", []):
            name = tmpl.get("name")
            score = tmpl.get("quality_score")
            if name and score is not None:
                relationship_scores[name] = score

        result: dict[str, Any] = {
            "entity_scores": entity_scores,
            "subtype_scores": subtype_scores,
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

    def get_feminine_titles(self) -> list[str]:
        """Return the titles that say a woman (``feminine_titles``), for the name rule.

        Returns:
            Title words from config; empty when the domain declares none (the
            name rule then uses its universal ones).
        """
        return [str(w) for w in self.config.get("feminine_titles") or []]

    def get_masculine_titles(self) -> list[str]:
        """Return the titles that say a man (``masculine_titles``), for the name rule.

        Returns:
            Title words from config; empty when the domain declares none.
        """
        return [str(w) for w in self.config.get("masculine_titles") or []]

    def get_generation_words(self) -> dict[str, str]:
        """Return the words that say which generation a name means (``generation_words``).

        Example JSON-LD::

            {"generation_words": {"old": "elder", "young": "younger"}}

        Returns:
            Mapping of word to generation label; empty when none are declared.
        """
        raw = self.config.get("generation_words") or {}
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    def get_name_forms(self) -> dict[str, list[dict[str, Any]]]:
        """Return the name-form endings by language (``name_forms``), for the name rule.

        Each form is ``{"ending", "stem", "gender"}``: ``ending`` is replaced
        by ``stem`` to give the family stem both forms share, and ``gender``
        (``"f"``, ``"m"`` or null) is what the ending says of the bearer.

        Example JSON-LD::

            {"name_forms": {"ru": [{"ending": "ova", "stem": "ov", "gender": "f"}]}}

        Returns:
            Mapping of language code to its forms; empty when none are declared.
        """
        raw = self.config.get("name_forms") or {}
        if not isinstance(raw, dict):
            return {}
        return {str(lang): [dict(f) for f in forms or []] for lang, forms in raw.items()}

    def get_role_words(self) -> list[str]:
        """Return the words naming a role one person has to another (``role_words``), for the name rule.

        With the ``relation_vocabulary`` words they make a possessive name a
        descriptor (``Count Rostóv's valet``), never a name of the person it
        mentions (``name_identity.descriptor_possessor``).

        Returns:
            Role words from config; empty when the domain declares none.
        """
        return [str(w) for w in self.config.get("role_words") or []]

    def get_title_equivalents(self) -> list[list[str]]:
        """Return groups of spellings of one title (``title_equivalents``), for the name rule.

        Example JSON-LD::

            {"title_equivalents": [["mme", "madame"], ["mlle", "mademoiselle"]]}

        Returns:
            The groups; empty when the domain declares none.
        """
        raw = self.config.get("title_equivalents") or []
        return [[str(t) for t in group] for group in raw if isinstance(group, list)]

    def get_relation_vocabulary(self) -> dict[str, dict[str, list[str]]]:
        """Return the words that state each relation (``relation_vocabulary``).

        Each entry names a relation and lists the edge labels that state it
        and the words prose uses for it ("sister" for ``sibling_of``); the
        evidence validator's one-endpoint rule and the graph scan read it
        (``relation_vocabulary.RelationVocabulary``).

        Example JSON-LD::

            {"relation_vocabulary": {
                "sibling": {"labels": ["sibling_of"], "words": ["brother", "sister"]}}}

        Returns:
            Mapping of relation name to ``{"labels": [...], "words": [...]}``;
            empty when none is declared.
        """
        raw = self.config.get("relation_vocabulary") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(name): {
                "labels": [str(x) for x in (entry or {}).get("labels") or []],
                "words": [str(x) for x in (entry or {}).get("words") or []],
            }
            for name, entry in raw.items()
        }

    def get_person_types(self) -> list[str]:
        """Return the node templates and subtypes marked ``"names_a_person": true``, in config order.

        Only a person's name can name its bearer through someone else's
        (``Natásha's mother``): the name rule reads a possessive as a
        descriptor for these types (and the universal core type ``Person``,
        which every entity of a Person subtype is: ``NameConventions.is_person``
        reads the core type), and as part of the name for every other
        (``Caesar's Commentaries``, a work).

        Returns:
            Node template and subtype names; empty when none is marked.
        """
        templates = (self.config.get("templates") or {}).get("node_templates") or []
        marked = [*templates, *(self.config.get("subtypes") or [])]
        return [
            str(t["name"])
            for t in marked
            if isinstance(t, dict) and t.get("name") and t.get("names_a_person") is True
        ]

    def get_two_endpoint_relations(self) -> list[str]:
        """Return the edge templates marked ``"two_endpoints_only": true``, in template order.

        The evidence validator never supports one of them on one named
        endpoint, whatever the universal vocabulary says (a financial
        ``parent_of`` is a parent company, not a parent). Every edge template
        of a shipped domain is either stated by the domain's relation
        vocabulary or marked so (a guard test holds the two in step).

        Returns:
            Edge template names; empty when none is marked.
        """
        templates = (self.config.get("templates") or {}).get("edge_templates") or []
        return [
            str(t["name"])
            for t in templates
            if isinstance(t, dict) and t.get("name") and t.get("two_endpoints_only") is True
        ]

    def get_two_endpoint_reasons(self) -> dict[str, str]:
        """Return each two-endpoints-only edge template's ``two_endpoints_only_reason``.

        Why the template needs both endpoints named although its label
        carries a word prose may use for the relation
        (``relation_vocabulary.label_evidence_words``): a shared reason by
        name (``relation_vocabulary.TWO_ENDPOINTS_REASONS``) or one of its
        own. A guard test requires it on every such template of every
        registered domain (audit wave 6: ``rivals`` and ``member_of`` were
        marked for want of a vocabulary entry, and true edges dropped).

        Returns:
            Template name -> reason, for the templates that give one.
        """
        templates = (self.config.get("templates") or {}).get("edge_templates") or []
        return {
            str(t["name"]): str(t["two_endpoints_only_reason"])
            for t in templates
            if isinstance(t, dict) and t.get("name") and t.get("two_endpoints_only_reason")
        }

    def get_vocabulary_exclusions(self) -> list[str]:
        """Return the edge labels the relation vocabulary does not state here.

        The two-endpoints-only edge templates (:meth:`get_two_endpoint_relations`);
        the older top-level ``vocabulary_exclusions`` key was removed (spec
        2026-10-07 § 3).

        Returns:
            Edge labels; empty when the domain declares none.
        """
        return self.get_two_endpoint_relations()

    def get_identity_tables(self) -> dict[str, Any]:
        """Return the declared naming and relation tables, for the content fingerprint.

        Only the tables the domain declares (a domain with none returns an
        empty dict, so its fingerprint is what it was before these tables).

        Returns:
            ``title_words``, ``feminine_titles``, ``masculine_titles``,
            ``generation_words``, ``name_forms``, ``relation_vocabulary``,
            ``role_words`` and ``title_equivalents``, each when non-empty.
        """
        readers: dict[str, Any] = {
            "title_words": lambda: sorted(self.get_title_words()),
            "feminine_titles": lambda: sorted(self.get_feminine_titles()),
            "masculine_titles": lambda: sorted(self.get_masculine_titles()),
            "generation_words": self.get_generation_words,
            "name_forms": self.get_name_forms,
            "title_equivalents": lambda: sorted(sorted(g) for g in self.get_title_equivalents()),
            "relation_vocabulary": self.get_relation_vocabulary,
            "role_words": lambda: sorted(self.get_role_words()),
        }
        tables = {name: readers[name]() for name in IDENTITY_TABLES}
        return {k: v for k, v in tables.items() if v}

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

        The floor's limits are :data:`core_types.FLOOR_LIMITS` (one source the
        schema compares a pack's against); a pack's are its own, which the
        schema keeps at or above the floor's.

        Returns:
            Dictionary of ``FilteringConfig`` field overrides. The preset
            selector lives on ``get_filtering_mode()`` — never inlined here.
            Empty dict uses global defaults.
        """
        if self.role == "floor":
            return dict(FLOOR_LIMITS)
        return cast("dict[str, float | int]", dict(self.config.get("extraction_limits", {})))

    def get_filtering_mode(self) -> str | None:
        """Return the preset selector (``extraction_filtering_mode``) from the jsonld."""
        mode = self.config.get("extraction_filtering_mode")
        return cast("str | None", mode) if mode else None

    def get_evidence_validation_mode(self) -> str | None:
        """Return None: a domain no longer overrides the evidence validation mode.

        The top-level ``evidence_validation_mode`` key was removed (spec
        2026-10-07 § 3); the global ``ExtractionSettings`` value applies (and a
        preset may still set it through ``extraction_limits``). Kept for the
        ``DomainAnalyzer`` protocol.
        """
        return None

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


__all__ = [
    "DEFAULT_DETECTION_BASE_SCORE",
    "DEFAULT_DETECTION_MAX_CONFIDENCE",
    "ConfigurableDomain",
    "SubtypeSpec",
    "detection_base_score",
    "detection_max_confidence",
    "load_domain_config",
]
