# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic schema for domain ``.jsonld`` config files.

This model validates the *shape* of a domain configuration at load
time. It is intentionally permissive about fields that are deep
domain-specific (we keep them as ``dict[str, Any]``) and strict about
the fields the registry itself depends on (``name``, ``description``,
``entity_templates``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TemplateRef(BaseModel):
    """Reference to an entity template inside a domain config.

    Only ``name`` is required because every loaded domain references
    templates by name; all other fields vary per domain and are held as
    a pass-through dict.
    """

    name: str = Field(..., description="Template identifier")

    model_config = ConfigDict(extra="allow")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        """Reject whitespace-only template names."""
        if not v.strip():
            msg = "Template 'name' must not be blank"
            raise ValueError(msg)
        return v


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


class DomainExtractionOverrides(BaseModel):
    """Per-domain overrides for individual ``ExtractionSettings`` fields.

    A ``None`` value means "no domain-level override; use the global default".
    Only fields explicitly set in the domain's JSONLD config will take effect —
    all others transparently fall through to the operator's global setting.

    Example JSONLD usage::

        {
          "extraction_overrides": {
            "system_prompt": "You are a biomedical entity extractor. Output ONLY the requested format."
          }
        }

    Use case: a ``medical`` domain can provide a more focused system prompt
    while inheriting all other extraction settings from the operator's global
    configuration.
    """

    system_prompt: str | None = Field(
        default=None,
        description=(
            "Phase 6 (2026-05-08): per-domain system prompt override. "
            "When set, replaces ExtractionSettings.system_prompt for all "
            "extraction calls on this domain. None = use global default."
        ),
    )

    model_config = ConfigDict(extra="forbid")


class DomainConfigModel(BaseModel):
    """Schema for a domain ``.jsonld`` configuration file.

    Strict about what the registry relies on, permissive about the rest
    (extra fields are preserved so ``ConfigurableDomain`` still sees
    them via the original parsed dict).
    """

    name: str = Field(..., description="Domain identifier")
    description: str = Field(default="", description="Human-readable description")
    entity_templates: list[TemplateRef] = Field(
        default_factory=list,
        description="Entity template references used by this domain",
    )
    content_exclusions: ContentExclusions | None = Field(
        default=None,
        description="Optional content exclusion configuration",
    )
    entity_exclusions: list[ExclusionRule] = Field(
        default_factory=list,
        description="Structured entity-exclusion rules (description + examples)",
    )
    strict_validation: bool = Field(
        default=False,
        description=(
            "When True, an unknown key inside the filtering-config override block "
            "of this domain raises ``ValidationError`` at resolution time, instead "
            "of being silently logged and dropped. "
            "Set to True during domain-config development to catch typos early "
            "(e.g. 'enable_typeconstraints' instead of 'enable_type_constraints'). "
            "Omit or set False in production domain configs."
        ),
    )
    normalizer_overrides: DomainNormalizerOverrides | None = Field(
        default=None,
        description=(
            "Optional per-domain overrides for individual NormalizerSettings flags. "
            "Set a flag to true/false to override the global default for this domain; "
            "omit (or set to null) to fall through to the global setting."
        ),
    )
    allow_template_fallback: bool = Field(
        default=False,
        description=(
            "Phase 6 (2026-05-08): when False (default), a domain with no "
            "node/edge templates raises ValidationError at extraction time so "
            "the operator sees a clear misconfiguration signal rather than "
            "silently receiving generic Person/Organization output. Set True "
            "to restore the legacy fallback behaviour for domains that "
            "intentionally omit templates."
        ),
    )
    extraction_overrides: DomainExtractionOverrides | None = Field(
        default=None,
        description=(
            "Phase 6 (2026-05-08): optional per-domain extraction setting "
            "overrides. Currently supports system_prompt. None = use all "
            "global ExtractionSettings defaults."
        ),
    )

    model_config = ConfigDict(extra="allow")

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        """Reject whitespace-only domain names."""
        if not v.strip():
            msg = "Domain 'name' must not be blank"
            raise ValueError(msg)
        return v


__all__ = [
    "ContentExclusions",
    "DomainConfigModel",
    "DomainExtractionOverrides",
    "DomainNormalizerOverrides",
    "ExclusionRule",
    "TemplateRef",
]
