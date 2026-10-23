# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic schema for domain ``.jsonld`` config files.

This model validates the *shape* of a domain configuration at load
time. It is intentionally permissive about fields that are deep
domain-specific (we keep them as ``dict[str, Any]``) and strict about
the fields the registry itself depends on (``name``, ``role``, ``templates``,
``subtypes``).

Since 2026-10-07 (spec "additive domains" § 3) a config is either the one
**floor** (``generic.jsonld``: the eight core types and the core labels) or a
**pack** (``extends: "core"``) that only adds: subtypes of core types, labels
the core lacks, and limits at or above the floor's. A pack that re-declares a
core type or label, or lowers a floor limit, is refused at load.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from chaoscypher_core.services.sources.engine.extraction.domains.core_types import (
    CORE_LABELS,
    CORE_TYPES,
    FLOOR_LIMITS,
    FLOOR_NAME,
)


class ContentExclusions(BaseModel):
    """Schema for the ``content_exclusions`` block."""

    categories: list[str] = Field(default_factory=list)
    custom_patterns: list[dict[str, Any]] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class ExclusionRule(BaseModel):
    """A single domain entity-exclusion rule.

    Each rule has a human-readable ``description`` (shown to the LLM in
    the extraction prompt) and a non-empty list of ``examples`` (used by
    the post-extraction filter to drop entities whose names match).

    The previous shape was a single ``str`` per rule with quoted
    examples extracted via regex. That allowed silent-degrade failures
    (missing quotes, smart quotes, typos) — extraction continued with
    an empty exclusion set and no signal. The structured shape rejects
    malformed rules at config load.
    """

    description: str = Field(..., description="Human-readable rule description")
    examples: list[str] = Field(..., description="Example entity names this rule excludes")

    model_config = ConfigDict(extra="forbid")

    @field_validator("description")
    @classmethod
    def _description_not_blank(cls, v: str) -> str:
        """Reject whitespace-only exclusion descriptions."""
        if not v.strip():
            msg = "ExclusionRule 'description' must not be blank"
            raise ValueError(msg)
        return v

    @field_validator("examples")
    @classmethod
    def _examples_non_empty_and_non_blank(cls, v: list[str]) -> list[str]:
        """Reject empty example lists and blank example entries."""
        if not v:
            msg = "ExclusionRule 'examples' must contain at least one entry"
            raise ValueError(msg)
        for ex in v:
            if not ex.strip():
                msg = "ExclusionRule 'examples' entries must not be blank"
                raise ValueError(msg)
        return v

    def as_prompt_text(self) -> str:
        """Render as a single LLM-facing line: ``description: "ex1", "ex2"``.

        Both the entity-extraction harvest prompt and the MCP-tool prompt
        consume this format. Centralised here so the format stays in
        sync across consumers.
        """
        quoted = ", ".join(f'"{ex}"' for ex in self.examples)
        return f"{self.description}: {quoted}"


class DomainNormalizerOverrides(BaseModel):
    """Per-domain overrides for individual ``NormalizerSettings`` boolean flags.

    A ``None`` value means "no domain-level override; use the global default".
    Only fields explicitly set in the domain's JSONLD config will take effect —
    all others transparently fall through to the operator's global setting.

    Example JSONLD usage::

        {
          "normalizer_overrides": {
            "enable_ocr_cleaning": false
          }
        }

    Use case: a ``medical_records`` domain can disable OCR cleaning even when
    the global ``enable_ocr_cleaning`` setting is ``true``, because
    medical PDFs are typically generated (not scanned) and OCR artifact removal
    can strip legitimate hyphenated clinical terms.
    """

    enable_encoding_fix: bool | None = Field(
        default=None,
        description="Override ftfy encoding-fix step. None = use global default.",
    )
    enable_unicode_normalize: bool | None = Field(
        default=None,
        description="Override NFC unicode normalisation. None = use global default.",
    )
    enable_control_char_removal: bool | None = Field(
        default=None,
        description="Override control-character removal. None = use global default.",
    )
    enable_whitespace_normalize: bool | None = Field(
        default=None,
        description="Override whitespace normalisation. None = use global default.",
    )
    enable_ocr_cleaning: bool | None = Field(
        default=None,
        description="Override OCR artifact cleaning. None = use global default.",
    )
    enable_duplicate_removal: bool | None = Field(
        default=None,
        description="Override duplicate-paragraph removal. None = use global default.",
    )
    enable_markdown_normalize: bool | None = Field(
        default=None,
        description="Override Markdown output normalisation. None = use global default.",
    )

    model_config = ConfigDict(extra="forbid")


class NameFormSpec(BaseModel):
    """One name-form ending of a ``name_forms`` table (the name rule's surname fold).

    ``ending`` is replaced by ``stem`` to give the family stem both forms
    share (``ova`` -> ``ov``: "Rostova" and "Rostov" are one family);
    ``gender`` is what the ending says of the bearer, null for the base form.
    """

    ending: str = Field(..., min_length=1, description="The word ending, folded spelling")
    stem: str = Field(..., description="What the ending becomes in the family stem")
    gender: Literal["f", "m"] | None = Field(default=None, description="Gender the ending says")

    model_config = ConfigDict(extra="forbid")


class RelationVocabularyEntry(BaseModel):
    """One relation of a ``relation_vocabulary``: the edge labels that state it and its words.

    A word may be a phrase ("engaged to"), matched as consecutive whole
    words; a word or phrase made only of function words ("with", "of the")
    states nothing and is refused.
    """

    labels: list[str] = Field(..., min_length=1, description="Edge labels stating this relation")
    words: list[str] = Field(..., min_length=1, description="Words prose uses for the relation")

    model_config = ConfigDict(extra="forbid")

    @field_validator("words")
    @classmethod
    def _no_function_words(cls, v: list[str]) -> list[str]:
        """Refuse a word or phrase made only of function words: it is no evidence."""
        from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
            is_function_phrase,
        )

        bad = [w for w in v if is_function_phrase(w)]
        if bad:
            msg = f"relation_vocabulary words state nothing (function words only): {bad}"
            raise ValueError(msg)
        return v


class PropertyTemplateModel(BaseModel):
    """One property a node template or subtype offers (``{name, display_name, property_type}``)."""

    name: str = Field(..., min_length=1, description="Property key")
    display_name: str | None = Field(default=None, description="Label shown for the property")
    property_type: str | None = Field(default=None, description="Value type (string, date, ...)")

    model_config = ConfigDict(extra="forbid")


class NodeTemplateModel(BaseModel):
    """A node template of the floor: one of the core entity types."""

    at_type: str | None = Field(default=None, alias="@type")
    id: str = Field(default="", description="Template identifier")
    name: str = Field(..., description="Entity type name")
    description: str = Field(default="", description="What the type covers (shown to the model)")
    icon: str | None = None
    color: str | None = None
    quality_score: int | None = None
    requires_named_referent: bool | None = None
    names_a_person: bool | None = None
    is_generic: bool | None = None
    is_structural: bool | None = None
    properties: list[PropertyTemplateModel] = Field(default_factory=list)
    normalization_keywords: list[str] = Field(
        default_factory=list,
        description="Words in a description that rescue an unknown written type to this one",
    )

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        """Reject whitespace-only template names."""
        if not v.strip():
            msg = "Template 'name' must not be blank"
            raise ValueError(msg)
        return v


class EdgeTemplateModel(BaseModel):
    """An edge template: a relationship label with its endpoint types over the core types."""

    at_type: str | None = Field(default=None, alias="@type")
    id: str = Field(default="", description="Template identifier")
    name: str = Field(..., description="Relationship label")
    description: str = Field(default="", description="What the label states (shown to the model)")
    icon: str | None = None
    color: str | None = None
    quality_score: int | None = None
    source_types: list[str] = Field(default_factory=list)
    target_types: list[str] = Field(default_factory=list)
    symmetric: bool | None = None
    inverse: str | None = None
    two_endpoints_only: bool | None = None
    two_endpoints_only_reason: str | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        """Reject whitespace-only labels."""
        if not v.strip():
            msg = "Edge template 'name' must not be blank"
            raise ValueError(msg)
        return v


class TemplatesModel(BaseModel):
    """The ``templates`` block: the floor's node templates and every config's edge templates."""

    node_templates: list[NodeTemplateModel] = Field(default_factory=list)
    edge_templates: list[EdgeTemplateModel] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _no_duplicates(self) -> TemplatesModel:
        """A type or a label is declared once (labels compared in canonical form)."""
        from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
            canonical_edge_label,
        )

        nodes = [t.name.strip().lower() for t in self.node_templates]
        edges = [canonical_edge_label(t.name) for t in self.edge_templates]
        for kind, names in (("node template", nodes), ("edge label", edges)):
            dupes = sorted({n for n in names if names.count(n) > 1})
            if dupes:
                msg = f"duplicate {kind}s: {dupes}"
                raise ValueError(msg)
        return self


_CORE_TYPES_LOWER = frozenset(t.lower() for t in CORE_TYPES)


class SubtypeModel(BaseModel):
    """A pack's subtype of a core type (``Character`` of ``Person``), stored as ``entity_subtype``."""

    name: str = Field(..., description="Subtype name as the pack shows it ('Military Unit')")
    of: str = Field(..., description="The core type it is a kind of")
    description: str = Field(default="", description="What the subtype covers")
    properties: list[PropertyTemplateModel] = Field(default_factory=list)
    names_a_person: bool = Field(
        default=False, description="A possessive of this subtype's name is a descriptor"
    )
    icon: str | None = None
    color: str | None = None
    quality_score: int | None = None
    requires_named_referent: bool | None = None
    rescue_keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Words that rescue a written type to this subtype (the type rescue's pack keyword "
            "tier): the old template's normalization_keywords"
        ),
    )

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _a_kind_of_a_core_type(self) -> SubtypeModel:
        """The name is not a core type; ``of`` is one; only a Person subtype names a person."""
        if not self.name.strip():
            msg = "subtype 'name' must not be blank"
            raise ValueError(msg)
        if self.name.strip().lower() in _CORE_TYPES_LOWER:
            msg = (
                f"subtype {self.name!r} is a core type; a pack adds subtypes of the core types, "
                "never a core type (spec 2026-10-07 § 3)"
            )
            raise ValueError(msg)
        if self.of not in CORE_TYPES:
            msg = f"subtype {self.name!r} is of {self.of!r}, not a core type {list(CORE_TYPES)}"
            raise ValueError(msg)
        if self.names_a_person and self.of != "Person":
            msg = f"subtype {self.name!r} names a person but is of {self.of!r}, not Person"
            raise ValueError(msg)
        return self


REMOVED_KEYS: dict[str, str] = {
    "guidance": "write entity_guidance and relationship_guidance",
    "entity_templates": "templates are declared under 'templates'",
    "strict_validation": "the schema validates every domain strictly",
    "normalizer_overrides": "set the normalizer in the settings",
    "allow_template_fallback": "the floor's templates always apply",
    "extraction_overrides": "set the system prompt in the extraction settings",
    "vocabulary_exclusions": 'mark the edge template "two_endpoints_only": true',
    "evidence_validation_mode": "set it in the extraction settings",
}
"""Keys a domain config may no longer declare (spec 2026-10-07 § 3), with what replaces them."""

FLOOR_ONLY_KEYS: tuple[str, ...] = ("strict_entity_types",)
"""Keys only the floor declares: strict core types are the floor's, for every pack."""


def describe_validation_error(exc: ValidationError) -> str:
    """A domain config's validation failure as one line: each problem, ``; ``-separated.

    A rule's own message is kept verbatim (``subtype 'Person' is a core type ...``);
    a shape error names the field it is in.
    """
    parts: list[str] = []
    for err in exc.errors():
        ctx = err.get("ctx") or {}
        if err.get("type") == "value_error" and "error" in ctx:
            parts.append(str(ctx["error"]))
        else:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            parts.append(f"{loc}: {err.get('msg', '')}" if loc else str(err.get("msg", "")))
    return "; ".join(parts)


class DomainConfigModel(BaseModel):
    """Schema for a domain ``.jsonld`` configuration file: the floor or a pack.

    Strict about what the registry relies on (the role, the templates, the
    subtypes, the limits), permissive about the rest (extra fields are
    preserved so ``ConfigurableDomain`` still sees them via the original
    parsed dict).
    """

    name: str = Field(..., description="Domain identifier")
    description: str = Field(default="", description="Human-readable description")
    role: Literal["floor", "pack"] = Field(
        ..., description="'floor' for the one built-in generic plugin, 'pack' for every other"
    )
    extends: str | None = Field(default=None, description="'core' for every pack")
    templates: TemplatesModel = Field(
        default_factory=TemplatesModel,
        description="The floor's node templates (the core types) and the edge templates",
    )
    subtypes: list[SubtypeModel] = Field(
        default_factory=list, description="A pack's subtypes of the core types"
    )
    core_properties: dict[str, list[PropertyTemplateModel]] = Field(
        default_factory=dict,
        description="A pack's extra properties of a core type ('Event' -> host, date, place)",
    )
    core_rescue_keywords: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "A pack's words that rescue a written type to a core type itself ('Event' -> battle, "
            "duel): the normalization_keywords of its templates folded into a core type"
        ),
    )
    type_aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Written type -> a core type or one of the pack's subtypes",
    )
    extraction_limits: dict[str, int | float] = Field(
        default_factory=dict,
        description="FilteringConfig overrides; a pack's may only raise the floor's (FLOOR_LIMITS)",
    )
    content_exclusions: ContentExclusions | None = Field(
        default=None,
        description="Optional content exclusion configuration",
    )
    entity_exclusions: list[ExclusionRule] = Field(
        default_factory=list,
        description="Structured entity-exclusion rules (description + examples)",
    )
    feminine_titles: list[str] = Field(
        default_factory=list,
        description="Titles that say a woman (the name rule's gender guard); empty = universal",
    )
    masculine_titles: list[str] = Field(
        default_factory=list,
        description="Titles that say a man (the name rule's gender guard); empty = universal",
    )
    generation_words: dict[str, str] = Field(
        default_factory=dict,
        description="Word -> generation ('old' -> 'elder'): which same-named person; empty = universal",
    )
    name_forms: dict[str, list[NameFormSpec]] = Field(
        default_factory=dict,
        description="Language -> name-form endings the name rule folds (surname forms); empty = none",
    )
    role_words: list[str] = Field(
        default_factory=list,
        description=(
            "Words naming a role one person has to another ('valet', 'steward'): with the "
            "relation_vocabulary words they make 'X's valet' a descriptor, never X's name"
        ),
    )
    title_equivalents: list[list[str]] = Field(
        default_factory=list,
        description="Groups of spellings of one title (['mme', 'madame']); empty = universal",
    )
    relation_vocabulary: dict[str, RelationVocabularyEntry] = Field(
        default_factory=dict,
        description=(
            "Relation -> the edge labels that state it and the words prose uses for it "
            "(the evidence validator's one-endpoint rule), added to the universal kin "
            "vocabulary relation by relation; empty = the universal vocabulary alone"
        ),
    )

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _no_removed_keys(cls, data: Any) -> Any:
        """Refuse a key the restructure removed, and an older config with no role, by name."""
        if not isinstance(data, dict):
            return data
        removed = [k for k in REMOVED_KEYS if k in data]
        if removed:
            hints = "; ".join(f"{k}: {REMOVED_KEYS[k]}" for k in removed)
            msg = f"keys removed from domain configs (spec 2026-10-07 § 3): {hints}"
            raise ValueError(msg)
        if "role" not in data:
            msg = (
                "the config declares no role: 'pack' (with extends: 'core') or 'floor'; convert "
                "an older plugin with python -m "
                "chaoscypher_core.services.sources.engine.extraction.domains.convert_packs"
            )
            raise ValueError(msg)
        return data

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        """Reject whitespace-only domain names."""
        if not v.strip():
            msg = "Domain 'name' must not be blank"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _floor_or_pack(self) -> DomainConfigModel:
        """The floor declares exactly the core vocabulary; a pack only adds to it."""
        problems = self._floor_problems() if self.role == "floor" else self._pack_problems()
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def _floor_problems(self) -> list[str]:
        """What keeps this config from being the floor."""
        from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
            canonical_edge_label,
        )

        problems: list[str] = []
        if self.name != FLOOR_NAME:
            problems.append(f"the floor is the built-in {FLOOR_NAME!r} plugin, not {self.name!r}")
        if self.extends is not None:
            problems.append("the floor extends nothing")
        names = [t.name for t in self.templates.node_templates]
        if names != list(CORE_TYPES):
            problems.append(f"the floor's node templates must be the core types {list(CORE_TYPES)}")
        labels = {canonical_edge_label(t.name) for t in self.templates.edge_templates}
        if labels != CORE_LABELS:
            missing, extra = sorted(CORE_LABELS - labels), sorted(labels - CORE_LABELS)
            problems.append(
                f"the floor's edge labels must be the core labels: missing {missing}, extra {extra}"
            )
        if self.subtypes or self.core_properties or self.core_rescue_keywords:
            problems.append(
                "the floor declares no subtypes, core_properties or core_rescue_keywords "
                "(its rescue words are its templates' normalization_keywords)"
            )
        if self.extraction_limits:
            problems.append("the floor's extraction_limits live in core_types.FLOOR_LIMITS")
        if "detection" in (self.model_extra or {}):
            problems.append("the floor has no detection block: it is never ranked, packs are")
        if getattr(self, "strict_entity_types", None) is not True:
            problems.append("the floor's core types are strict (strict_entity_types: true)")
        return problems

    def _pack_problems(self) -> list[str]:
        """What a pack declares that is not an addition to the floor."""
        from chaoscypher_core.services.sources.engine.extraction.utils.relation_vocabulary import (
            canonical_edge_label,
        )

        problems: list[str] = []
        if self.name.strip().lower() == FLOOR_NAME:
            problems.append(f"{FLOOR_NAME!r} is the floor's name; a pack takes another")
        if self.extends != "core":
            problems.append(f"a pack extends 'core' (extends: {self.extends!r})")
        if self.templates.node_templates:
            names = [t.name for t in self.templates.node_templates]
            problems.append(f"a pack declares subtypes, not node templates: {names}")
        subtype_names = [s.name for s in self.subtypes]
        dupes = sorted({n for n in subtype_names if subtype_names.count(n) > 1})
        if dupes:
            problems.append(f"duplicate subtypes: {dupes}")
        core = set(CORE_TYPES)
        problems.extend(
            f"{key} of {off}: not core types"
            for key, by_core in (
                ("core_properties", self.core_properties),
                ("core_rescue_keywords", self.core_rescue_keywords),
            )
            if (off := sorted(set(by_core) - core))
        )
        for edge in self.templates.edge_templates:
            if canonical_edge_label(edge.name) in CORE_LABELS:
                problems.append(
                    f"edge label {edge.name!r} is a core label; a pack adds labels the core lacks"
                )
            for field in ("source_types", "target_types"):
                off = [t for t in getattr(edge, field) if t not in core]
                if off:
                    problems.append(f"edge {edge.name!r} {field} {off} are not core types")
        for key, value in self.extraction_limits.items():
            floor = FLOOR_LIMITS.get(key)
            if floor is not None and value < floor:
                shown = int(value) if float(value).is_integer() else value
                problems.append(
                    f"extraction_limits.{key} {shown} is below the floor ({floor}); "
                    "a pack may raise a limit, never lower one"
                )
        subtypes = set(subtype_names)
        for alias, target in self.type_aliases.items():
            if alias.strip().lower() in _CORE_TYPES_LOWER or alias in subtypes:
                problems.append(f"type alias {alias!r} shadows a core type or a subtype")
            if target not in core and target not in subtypes:
                problems.append(f"type alias {alias!r} -> {target!r}: not a core type or a subtype")
        problems.extend(
            f"{key} is the floor's; a pack does not declare it"
            for key in FLOOR_ONLY_KEYS
            if key in (self.model_extra or {})
        )
        return problems


__all__ = [
    "FLOOR_ONLY_KEYS",
    "REMOVED_KEYS",
    "ContentExclusions",
    "DomainConfigModel",
    "DomainNormalizerOverrides",
    "EdgeTemplateModel",
    "ExclusionRule",
    "NameFormSpec",
    "NodeTemplateModel",
    "PropertyTemplateModel",
    "RelationVocabularyEntry",
    "SubtypeModel",
    "TemplatesModel",
    "describe_validation_error",
]
