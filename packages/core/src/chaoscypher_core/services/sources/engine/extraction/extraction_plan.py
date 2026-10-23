# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""One extraction plan for every extraction path.

Before this module the queue planner (``import_service._build_extraction_config``),
the CLI (``chaoscypher_cli.sources.service.extract_entities``) and the
standalone extractor (``AIEntityExtractor.extract_from_chunks``) each built
the per-chunk extractor inputs and the FilteringConfig themselves, and they
drifted (audit 2026-10-06, E5): the CLI ran the domain's ``lenient`` preset
while the source row said ``balanced``, never set
``strict_edge_type_constraints``, and none of the three passed the domain's
``named_referent_types``, ``normalization_rules`` or
``property_type_mapping``. :func:`build_extraction_plan` is now the only
place those inputs are decided, from the source's
:class:`~chaoscypher_core.services.sources.engine.extraction.domains.stack.DomainStack`
(the floor plus its packs, spec 2026-10-07 § 4); the queue path round-trips
them through the job's ``extraction_config`` JSON with
:func:`chunk_kwargs_to_config` and :func:`chunk_kwargs_from_config`.

The preset selector cascade is :func:`resolve_filtering_mode`: an explicit
per-run override, then the source row, then the stack's
``extraction_filtering_mode``, then ``settings.extraction``; on every
branch but the stack's the mode is the user's, and the plan moves the
stack's loop cap and alias length with it (``follow_slider_limits``), so the
limits it hands every path are the effective ones. The semantic
deduplication threshold is the resolved FilteringConfig's
``semantic_dedup_threshold`` (the preset's value, unless the stack's
``extraction_limits`` overrides it) on every path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from chaoscypher_core.exceptions import OperationError
from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    ExclusionRule,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        FilteringConfig,
    )


# extract_single_chunk keyword -> extraction_config JSON key. The JSON names
# predate this module (the chunk handler, the finalizer and recovery read
# them), so they stay; the kwargs side is what the extractor takes.
_CONFIG_KEY: dict[str, str] = {
    "node_templates_formatted": "node_templates_formatted",
    "edge_templates_formatted": "edge_templates_formatted",
    "entity_guidance": "entity_guidance",
    "relationship_guidance": "relationship_guidance",
    "entity_examples": "entity_examples_formatted",
    "relationship_examples": "relationship_examples_formatted",
    "domain_extraction_limits": "extraction_limits",
    "filtering_mode": "filtering_mode",
    "entity_exclusions": "entity_exclusions",
    "strict_entity_types": "strict_entity_types",
    "valid_entity_type_names": "valid_entity_type_names",
    "evidence_validation_mode": "evidence_validation_mode",
    "named_referent_types": "named_referent_types",
    "normalization_rules": "normalization_rules",
    "property_type_mapping": "property_type_mapping",
    "type_vocabulary": "type_vocabulary",
    "relation_vocabulary": "relation_vocabulary",
    "name_conventions": "name_conventions",
}
"""Every per-chunk extractor input the plan decides, by its config JSON key."""

from chaoscypher_core.constants import SOURCE_ROW_FILTERING_MODE


# SOURCE_ROW_FILTERING_MODE: the row wins over the domain's
# ``extraction_filtering_mode`` (:func:`resolve_filtering_mode`), so every
# stored source runs that preset unless its row says otherwise; a plan built
# without a row (the standalone extractor) reads the domain's. The upload
# path and the recipe read the one value (until 2026-10-06 the recipe built
# its plan without a row and hashed the literary domain's ``lenient``).


def stored_source_row() -> dict[str, Any]:
    """The plan-relevant fields of a source row stored with the defaults (what the queue plans with)."""
    return {"filtering_mode": SOURCE_ROW_FILTERING_MODE}


CHUNK_KWARG_NAMES: frozenset[str] = frozenset(_CONFIG_KEY)
"""The ``extract_single_chunk`` keywords an :class:`ExtractionPlan` supplies."""

PROMPT_KWARG_NAMES: frozenset[str] = frozenset({
    "node_templates_formatted", "edge_templates_formatted", "entity_guidance",
    "relationship_guidance", "entity_examples", "relationship_examples", "entity_exclusions",
    "strict_entity_types",
})  # fmt: skip
"""The chunk kwargs that shape the two prompts: :func:`prompt_inputs` builds exactly these and
``ai_entities.render_plan_prompts`` renders exactly these (it fails on one it does not consume)."""


@dataclass(frozen=True)
class ExtractionPlan:
    """What one source's extraction runs with, decided once.

    Attributes:
        chunk_kwargs: Keyword arguments for
            ``AIEntityExtractor.extract_single_chunk`` (the keys of
            :data:`CHUNK_KWARG_NAMES`); every path passes them unchanged.
        filtering_config: The FilteringConfig the finalize stages
            (deduplication threshold, cross-chunk filters, structural
            filter) use; the per-chunk extractor resolves the same one from
            ``filtering_mode``, ``domain_extraction_limits`` and
            ``evidence_validation_mode``.
        edge_type_constraints: The domain's edge-type constraints for the
            cross-chunk type filter, or None.
    """

    chunk_kwargs: dict[str, Any]
    filtering_config: FilteringConfig
    edge_type_constraints: dict[str, dict[str, list[str]]] | None

    @property
    def filtering_mode(self) -> str:
        """The preset selector this plan resolved."""
        return str(self.chunk_kwargs["filtering_mode"])


def _call(domain: Any, method: str, default: Any) -> Any:
    """``domain.method()`` when the domain has it, else ``default``."""
    if domain is None or not hasattr(domain, method):
        return default
    return getattr(domain, method)()


def domain_guidance(domain: Any) -> tuple[str, str]:
    """``(entity_guidance, relationship_guidance)`` for a domain (empty strings without one)."""
    if domain is None:
        return "", ""
    if hasattr(domain, "get_entity_guidance"):
        entity = domain.get_entity_guidance() or ""
    else:
        entity = domain.get_guidance() or ""
    relationship = _call(domain, "get_relationship_guidance", "") or ""
    return str(entity), str(relationship)


def filtering_mode_choice(
    stack: DomainStack,
    engine_settings: Any,
    *,
    override: str | None = None,
    source_row: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """The preset selector and whether it is the user's choice rather than the stack's own.

    The cascade: per-run override > source row > the stack's > settings. Only
    a non-empty string counts at each step. The source row's
    ``filtering_mode`` column is non-nullable (default ``balanced``), so for a
    stored source the stack's default (:meth:`DomainStack.filtering_mode`) is
    reached only when the row is absent.

    The second value is False only on the stack's branch: a preset a pack (or
    the floor) names is not the user moving the slider, so the domain's
    tuning applies unchanged there (:func:`build_extraction_plan`). The
    settings' mode counts as a choice: it is reached only when neither the
    packs nor the floor name a preset, and at its package default
    (``balanced``) the slider rule changes nothing anyway, so only a mode the
    user configured can move a value.
    """
    if isinstance(override, str) and override:
        return override, True
    if source_row is not None:
        row_value = source_row.get("filtering_mode")
        if isinstance(row_value, str) and row_value:
            return row_value, True
    stack_mode = stack.filtering_mode()
    if isinstance(stack_mode, str) and stack_mode:
        return stack_mode, False
    return str(engine_settings.extraction.extraction_filtering_mode), True


def resolve_filtering_mode(
    stack: DomainStack,
    engine_settings: Any,
    *,
    override: str | None = None,
    source_row: dict[str, Any] | None = None,
) -> str:
    """The preset selector: per-run override > source row > the stack's > settings.

    :func:`filtering_mode_choice` without the provenance.
    """
    return filtering_mode_choice(stack, engine_settings, override=override, source_row=source_row)[
        0
    ]


def build_extraction_limits(
    stack: DomainStack,
    engine_settings: Any,
    *,
    source_row: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The FilteringConfig field overrides for one source (a fresh dict).

    The stack's ``extraction_limits`` (:meth:`DomainStack.limits`) plus, each
    with its own cascade: ``enable_direction_correction`` and
    ``protect_orphans`` (source row > stack > settings),
    ``max_entity_degree`` (a positive per-source override), and
    ``strict_edge_type_constraints``, always on: every entity leaves the
    per-chunk filters typed as a core type (``type_rescue``) and every edge
    template's endpoint types are core types, so an edge whose endpoints do
    not fit is a wrong edge, never an unknown type to let through (spec
    2026-10-07 § 6).
    """
    limits: dict[str, Any] = stack.limits()
    row = source_row or {}
    extraction = engine_settings.extraction

    for key in ("enable_direction_correction", "protect_orphans"):
        row_value = row.get(key)
        if isinstance(row_value, bool):
            limits[key] = row_value
        elif key not in limits:
            limits[key] = getattr(extraction, key)

    degree_override = row.get("max_entity_degree_override")
    if (
        isinstance(degree_override, int)
        and not isinstance(degree_override, bool)
        and degree_override > 0
    ):
        limits["max_entity_degree"] = degree_override

    limits["strict_edge_type_constraints"] = True
    return limits


def plan_filtering_config(
    filtering_mode: str,
    extraction_limits: dict[str, Any] | None,
    evidence_validation_mode: str | None = None,
) -> FilteringConfig:
    """The one FilteringConfig resolution: preset, then limits, then the domain's evidence mode."""
    from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
        resolve_filtering_config,
    )

    config = resolve_filtering_config(
        mode=str(filtering_mode),
        domain_overrides=dict(extraction_limits) if extraction_limits else None,
    )
    if evidence_validation_mode:
        config.evidence_validation_mode = evidence_validation_mode
    return config


def prompt_inputs(stack: DomainStack, engine_settings: Any) -> dict[str, Any]:
    """The chunk kwargs that shape the two prompts, from the stack (part of every plan).

    The formatted type lists, the guidance, the examples (when enabled,
    each member's capped at ``extraction_examples_max_chars``), the
    exclusions and the strict-type switch: what
    :meth:`DomainStack.rendered_sizes` measures and
    :func:`build_extraction_plan` stores.
    """
    from chaoscypher_core.services.sources.engine.extraction.orchestration import (
        format_extraction_templates,
    )

    templates = format_extraction_templates(
        stack,
        examples_enabled=engine_settings.llm.extraction_examples_enabled,
        examples_max_chars=engine_settings.llm.extraction_examples_max_chars,
    )
    entity_guidance, relationship_guidance = stack.guidance()
    inputs = {
        "node_templates_formatted": templates["node_templates"],
        "edge_templates_formatted": templates["edge_templates"],
        "entity_guidance": entity_guidance or None,
        "relationship_guidance": relationship_guidance or None,
        "entity_examples": templates["entity_examples"] or None,
        "relationship_examples": templates["relationship_examples"] or None,
        "entity_exclusions": stack.exclusions() or None,
        "strict_entity_types": stack.strict_entity_types(),
    }
    if set(inputs) != PROMPT_KWARG_NAMES:  # pragma: no cover - a guard on this function's own keys
        msg = f"prompt_inputs builds {sorted(inputs)}, PROMPT_KWARG_NAMES says {sorted(PROMPT_KWARG_NAMES)}"
        raise OperationError(msg, operation="prompt_inputs")
    return inputs


def build_extraction_plan(
    stack: DomainStack,
    engine_settings: Any,
    *,
    filtering_mode_override: str | None = None,
    source_row: dict[str, Any] | None = None,
) -> ExtractionPlan:
    """Decide every extractor input for one source from its domain stack, settings and row.

    Args:
        stack: The floor and the source's packs
            (:meth:`DomainStack.resolve`).
        engine_settings: ``EngineSettings`` (``.llm`` and ``.extraction`` are read).
        filtering_mode_override: A per-run preset override (an API or CLI
            argument); not persisted here.
        source_row: The source row, when the source is stored.

    Returns:
        The plan every extraction path runs with.

    Raises:
        OperationError: When ``stack`` is not a :class:`DomainStack` (a bare
            domain object or None, as callers passed before the stack).
    """
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    if not isinstance(cast("object", stack), DomainStack):
        msg = f"build_extraction_plan takes a DomainStack, not {type(stack).__name__}"
        raise OperationError(msg, operation="build_extraction_plan")

    filtering_mode, mode_chosen = filtering_mode_choice(
        stack, engine_settings, override=filtering_mode_override, source_row=source_row
    )
    from chaoscypher_core.services.sources.engine.extraction.domains.core_types import CORE_TYPES
    from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
        TypeVocabulary,
    )

    limits = build_extraction_limits(stack, engine_settings, source_row=source_row)
    if mode_chosen:
        # The domain's loop cap and alias length move with a slider the user
        # moved (decided 2026-10-22), here once, so the limits every path
        # carries and the FilteringConfig below hold the effective values.
        from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
            follow_slider_limits,
        )

        limits = follow_slider_limits(limits, filtering_mode)

    chunk_kwargs: dict[str, Any] = {
        **prompt_inputs(stack, engine_settings),
        "domain_extraction_limits": limits,
        "filtering_mode": filtering_mode,
        # The types every entity leaves the per-chunk filters with: the core types.
        "valid_entity_type_names": set(CORE_TYPES),
        # A domain no longer overrides the evidence mode (spec 2026-10-07 § 3);
        # a preset may still set it through the limits.
        "evidence_validation_mode": None,
        "named_referent_types": stack.named_referent_types() or None,
        "normalization_rules": stack.normalization_rules() or None,
        "property_type_mapping": stack.property_type_mapping() or None,
        # What the type rescue reads besides the type list and the keywords above:
        # the packs (the mapping table's pack-qualified rows), their subtypes and aliases.
        "type_vocabulary": TypeVocabulary.from_stack(stack).as_config(),
        "relation_vocabulary": stack.relation_vocabulary().as_config() or None,
        # The tables the evidence filters name endpoints by (titles, surname forms):
        # the ones the deduplication reads for this stack (audit wave 6).
        "name_conventions": stack.conventions().as_dict(),
    }
    return ExtractionPlan(
        chunk_kwargs=chunk_kwargs,
        filtering_config=plan_filtering_config(filtering_mode, limits),
        edge_type_constraints=stack.edge_type_constraints() or None,
    )


def chunk_kwargs_to_config(chunk_kwargs: dict[str, Any]) -> dict[str, Any]:
    """The JSON-safe ``extraction_config`` entries for a plan's chunk kwargs."""
    out: dict[str, Any] = {}
    for name, key in _CONFIG_KEY.items():
        value = chunk_kwargs.get(name)
        if name == "entity_exclusions":
            value = [rule.model_dump() for rule in value or []]
        elif isinstance(value, set):
            value = sorted(value)
        out[key] = value
    return out


def chunk_kwargs_from_config(
    config: dict[str, Any], fallback: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The chunk kwargs a stored ``extraction_config`` encodes (inverse of :func:`chunk_kwargs_to_config`).

    Args:
        config: The job's decoded ``extraction_config``.
        fallback: Legacy per-task payload consulted for the template and
            example strings when the config lacks them.

    Returns:
        Keyword arguments for ``extract_single_chunk``, normalised exactly as
        :func:`build_extraction_plan` normalises them.
    """
    legacy = fallback or {}

    def _get(name: str) -> Any:
        """The config value for kwarg ``name``, else the legacy payload's."""
        key = _CONFIG_KEY[name]
        return config[key] if key in config else legacy.get(key)

    exclusions = _get("entity_exclusions")
    valid_names = _get("valid_entity_type_names")
    named_referent = _get("named_referent_types")
    return {
        "node_templates_formatted": _get("node_templates_formatted") or "",
        "edge_templates_formatted": _get("edge_templates_formatted") or "",
        "entity_guidance": _get("entity_guidance") or None,
        "relationship_guidance": _get("relationship_guidance") or None,
        "entity_examples": _get("entity_examples") or None,
        "relationship_examples": _get("relationship_examples") or None,
        "domain_extraction_limits": _get("domain_extraction_limits"),
        "filtering_mode": _get("filtering_mode"),
        "entity_exclusions": (
            [ExclusionRule.model_validate(r) for r in exclusions] if exclusions else None
        ),
        "strict_entity_types": bool(_get("strict_entity_types") or False),
        "valid_entity_type_names": set(valid_names) if valid_names else None,
        "evidence_validation_mode": _get("evidence_validation_mode") or None,
        "named_referent_types": set(named_referent) if named_referent else None,
        "normalization_rules": _get("normalization_rules") or None,
        "property_type_mapping": _get("property_type_mapping") or None,
        "type_vocabulary": _get("type_vocabulary") or None,
        "relation_vocabulary": _get("relation_vocabulary") or None,
        "name_conventions": _get("name_conventions") or None,
    }


def plan_filtering_config_from_config(
    config: dict[str, Any], engine_settings: Any
) -> FilteringConfig:
    """The FilteringConfig a stored ``extraction_config`` encodes (the finalizer's view)."""
    return plan_filtering_config(
        str(config.get("filtering_mode") or engine_settings.extraction.extraction_filtering_mode),
        config.get("extraction_limits") or None,
        config.get("evidence_validation_mode") or None,
    )


__all__ = [
    "CHUNK_KWARG_NAMES",
    "PROMPT_KWARG_NAMES",
    "SOURCE_ROW_FILTERING_MODE",
    "ExtractionPlan",
    "build_extraction_limits",
    "build_extraction_plan",
    "chunk_kwargs_from_config",
    "chunk_kwargs_to_config",
    "domain_guidance",
    "filtering_mode_choice",
    "plan_filtering_config",
    "plan_filtering_config_from_config",
    "prompt_inputs",
    "resolve_filtering_mode",
    "stored_source_row",
]
