# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Filtering configuration for extraction pipeline.

Provides preset-based filtering modes that control which quality filters
are active and how strict they are. Presets act as a base layer; domain
configs and per-source overrides layer on top.

Presets (from most to least restrictive, scale 0-5):
- **maximum** (5): All filters active, drop on mismatches, tight thresholds.
- **strict** (4): Strict evidence + type constraints, standard plausibility.
- **balanced** (3, default): All filters active with fall-throughs.
- **lenient** (2): Lenient evidence for pronoun-heavy prose, lower plausibility.
- **minimal** (1): Most filters disabled, elevated limits.
- **unfiltered** (0): Data integrity only — dedup + index validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.ports.types import FilteringMode


logger = structlog.get_logger(__name__)


# FilteringMode is canonically defined in ``chaoscypher_core.ports.types``
# (cross-package boundary type). It is re-imported here so the Literal
# sits textually adjacent to ``_PRESET_OVERRIDES`` below — the keys of
# that dict are the source of truth for the Literal members, and the
# regression test ``test_filtering_mode_matches_preset_overrides``
# enforces set-equality. External callers (CLI, Cortex) must import from
# ``chaoscypher_core.ports.types``, not from this module.
_ = FilteringMode  # keep import live for grep + re-export via __all__


@dataclass
class FilteringConfig:
    """Resolved filtering configuration from a mode preset plus overrides.

    All filter settings are resolved at extraction start. Each filter
    function checks its corresponding flag/threshold from this config.

    Attributes:
        evidence_validation_mode: Evidence strictness (strict/standard/narrative/relaxed/off).
        min_significant_word_length: Minimum word length for evidence matching.
        strict_edge_type_constraints: Drop on type mismatches vs fall-through.
        enable_type_constraints: Run type constraint validation at all.
        enable_plausibility_filter: Run plausibility filter.
        plausibility_threshold: Threshold for named-referent types.
        plausibility_threshold_non_named: Threshold for non-named types.
        visual_content_plausibility_factor: Multiplier for visual content thresholds.
        enable_relationship_limits: Run relationship limit enforcement.
        max_entity_degree: Max relationships per entity.
        max_same_source_type: Max relationships per (source, type) pair.
        max_relationship_ratio: Max relationships as multiple of entity count.
        enable_structural_filter: Filter structural entities (chapters, sections).
        enable_entity_exclusions: Apply domain exclusion patterns.
        protect_orphans: When True, keep orphan entities (no relationships). When
            False (default), drop them before commit.
        semantic_dedup_threshold: Cosine similarity threshold for semantic dedup.
        loop_max_entity_count: Max entities per chunk before aborting.
        minimum_alias_length: Minimum alias character length.
        enable_direction_correction: When True (default), swap source/target of
            relationships that violate type constraints. When False, drop them.
            Counter increments either way (measures wrong-direction LLM emission).

    """

    evidence_validation_mode: str = "standard"
    min_significant_word_length: int = 4
    strict_edge_type_constraints: bool = False
    enable_type_constraints: bool = True
    enable_plausibility_filter: bool = True
    plausibility_threshold: float = 0.30
    plausibility_threshold_non_named: float = 0.15
    visual_content_plausibility_factor: float = 0.5
    enable_relationship_limits: bool = True
    max_entity_degree: int = 25
    max_same_source_type: int = 12
    max_relationship_ratio: float = 8.0
    enable_structural_filter: bool = True
    enable_entity_exclusions: bool = True
    # Phase 4 (2026-05-08): orphan-protection toggle.
    # When False (default), entities with no relationships are dropped before
    # commit. When True, they are kept (useful for minimal/unfiltered modes or
    # domains where isolated entities carry meaning). The polarity is inverted
    # vs the old filter_orphan_entities field: protect_orphans=False means
    # "do not protect them" = "drop them" (same as filter_orphan_entities=True).
    protect_orphans: bool = False
    semantic_dedup_threshold: float = 0.95
    loop_max_entity_count: int = 50
    minimum_alias_length: int = 2
    # Phase 4 (2026-05-08): direction-correction toggle.
    # When True (default), relationships whose source/target violate domain
    # type constraints are silently swapped (existing behavior). When False,
    # the relationship is dropped instead. The counter always increments so
    # operators can see the wrong-direction LLM emission rate independent of
    # how the system handles it.
    enable_direction_correction: bool = True
    # Phase 6 (2026-05-08): type-rescue gating.
    # When True (default), entities with invalid types are passed through the
    # three-tier rescue system (junk filter → property absorption → type
    # remapping). When False, all invalid-typed entities are dropped without
    # rescue, which is appropriate for unfiltered/minimal modes where the
    # operator expects raw LLM output with no structural fixup.
    enable_type_rescue: bool = True
    # Phase 6 (2026-05-08): self-loop toggle.
    # When False (default), relationships whose source and target resolve to
    # the same entity index are silently dropped. When True, they are kept.
    # Most domains produce self-loops only as LLM emission errors; this flag
    # exists for the rare domain where self-referential relationships are
    # semantically meaningful (e.g., a "subsystem of itself" in an ontology).
    allow_self_loops: bool = False


# Preset overrides — each dict contains only values that differ from balanced defaults.
# The slider is intentionally tuned so each adjacent step changes at least 3
# effective fields; ``loop_max_entity_count``, ``semantic_dedup_threshold``,
# and ``minimum_alias_length`` step monotonically as the slider tightens.
_PRESET_OVERRIDES: dict[str, dict[str, Any]] = {
    "unfiltered": {
        "evidence_validation_mode": "off",
        "enable_type_constraints": False,
        "enable_plausibility_filter": False,
        "enable_relationship_limits": False,
        "enable_structural_filter": False,
        "enable_entity_exclusions": False,
        "protect_orphans": True,
        # Phase 6 (2026-05-08): disable type rescue in unfiltered mode so the
        # operator receives raw LLM output with no structural fixup.
        "enable_type_rescue": False,
        # Wired dead fields:
        "loop_max_entity_count": 200,
        "semantic_dedup_threshold": 0.99,
        "minimum_alias_length": 1,
    },
    "minimal": {
        "evidence_validation_mode": "relaxed",
        "enable_type_constraints": False,
        "enable_plausibility_filter": False,
        "enable_relationship_limits": True,
        "max_entity_degree": 50,
        "max_same_source_type": 20,
        "max_relationship_ratio": 15.0,
        "enable_structural_filter": False,
        "enable_entity_exclusions": False,
        "protect_orphans": True,
        # Phase 6 (2026-05-08): disable type rescue in minimal mode — operator
        # expects minimally-filtered LLM output, not structural fixup passes.
        "enable_type_rescue": False,
        "loop_max_entity_count": 100,
        "semantic_dedup_threshold": 0.97,
        "minimum_alias_length": 1,
    },
    "lenient": {
        "evidence_validation_mode": "narrative",
        "enable_type_constraints": True,
        "enable_plausibility_filter": True,
        "plausibility_threshold": 0.20,
        "plausibility_threshold_non_named": 0.10,
        "enable_relationship_limits": True,
        "max_entity_degree": 30,
        "max_same_source_type": 15,
        "max_relationship_ratio": 10.0,
        "enable_structural_filter": True,
        "enable_entity_exclusions": True,
        "protect_orphans": False,
        "loop_max_entity_count": 75,
        "semantic_dedup_threshold": 0.93,
        "minimum_alias_length": 2,
    },
    "balanced": {
        # FilteringConfig defaults apply; only dead fields are pinned to
        # canonical balanced values so the slider above/below shows a delta.
        "loop_max_entity_count": 50,
        "semantic_dedup_threshold": 0.90,
        "minimum_alias_length": 2,
    },
    "strict": {
        "evidence_validation_mode": "strict",
        "strict_edge_type_constraints": True,
        "visual_content_plausibility_factor": 1.0,
        "max_entity_degree": 20,
        "max_same_source_type": 10,
        "max_relationship_ratio": 6.0,
        "loop_max_entity_count": 35,
        "semantic_dedup_threshold": 0.87,
        "minimum_alias_length": 3,
    },
    "maximum": {
        "evidence_validation_mode": "strict",
        "strict_edge_type_constraints": True,
        "visual_content_plausibility_factor": 1.0,
        "plausibility_threshold": 0.40,
        "plausibility_threshold_non_named": 0.25,
        "max_entity_degree": 15,
        "max_same_source_type": 7,
        "max_relationship_ratio": 4.0,
        "loop_max_entity_count": 25,
        "semantic_dedup_threshold": 0.85,
        "minimum_alias_length": 3,
    },
}

# Valid preset names for validation
VALID_PRESETS: frozenset[str] = frozenset(_PRESET_OVERRIDES.keys())

# Fields on FilteringConfig that can be overridden
_VALID_FIELDS: frozenset[str] = frozenset(f for f in FilteringConfig.__dataclass_fields__)


def resolve_filtering_config(
    mode: str = "balanced",
    domain_overrides: dict[str, Any] | None = None,
    source_overrides: dict[str, Any] | None = None,
    *,
    strict_validation: bool = False,
) -> FilteringConfig:
    """Resolve a FilteringConfig from a preset mode plus optional overrides.

    Priority chain: source_overrides > domain_overrides > preset defaults.

    Args:
        mode: Preset name — one of: maximum, strict, balanced (default),
            lenient, minimal, unfiltered. Legacy aliases (standard, narrative,
            precise, permissive, raw) are no longer accepted and raise
            ValueError.
        domain_overrides: Domain-specific overrides from .jsonld config.
        source_overrides: Per-source overrides from API.
        strict_validation: When True, unknown keys in ``domain_overrides``
            raise ``ValidationError`` instead of being logged-and-dropped.
            Set True during domain-config development to catch typos early.
            Defaults to False so existing domain configs keep working.

    Returns:
        Fully resolved FilteringConfig.

    Raises:
        ValueError: If mode is not one of the six canonical preset names.
        ValidationError: If ``domain_overrides`` contains the preset
            selector ``extraction_filtering_mode`` (always rejected — use
            ``mode=`` instead), or if ``strict_validation=True`` and
            ``domain_overrides`` contains keys that are not valid
            ``FilteringConfig`` fields.

    """
    if mode not in VALID_PRESETS:
        raise ValueError(  # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer-error guard for invalid public API input
            f"Unknown filtering mode {mode!r}. Valid modes: {sorted(VALID_PRESETS)}."
        )

    # Start with preset overrides on top of dataclass defaults
    config_dict: dict[str, Any] = {}
    config_dict.update(_PRESET_OVERRIDES.get(mode, {}))

    # Layer 2: Domain overrides
    if domain_overrides:
        # Hard reject for the preset selector — it has its own ``mode=``
        # argument and is not a ``FilteringConfig`` field. Previously this
        # was a soft drop with a warning, but callers were silently
        # conflating selector + field overrides into the same dict (see
        # the historical ``domain_config_unknown_keys_dropped`` warning).
        # Raising here keeps the abstraction leak from re-appearing.
        if "extraction_filtering_mode" in domain_overrides:
            raise ValidationError(
                "extraction_filtering_mode is the preset selector and must be "
                "passed via the mode= argument, not as a domain_overrides key."
            )
        unknown_keys = set(domain_overrides) - _VALID_FIELDS
        if unknown_keys:
            logger.warning(
                "domain_config_unknown_keys_dropped",
                unknown_keys=sorted(unknown_keys),
            )
            if strict_validation:
                raise ValidationError(
                    f"Unknown filtering-config keys in domain override: "
                    f"{sorted(unknown_keys)}. Set strict_validation=False "
                    f"to log + drop instead of raise."
                )
        domain_overrides = {k: v for k, v in domain_overrides.items() if k in _VALID_FIELDS}
        config_dict.update(domain_overrides)

    # Layer 3: Source overrides (highest priority)
    if source_overrides:
        config_dict.update({k: v for k, v in source_overrides.items() if k in _VALID_FIELDS})

    config = FilteringConfig(**config_dict)

    logger.info(
        "filtering_config_resolved",
        mode=mode,
        evidence_mode=config.evidence_validation_mode,
        type_constraints=config.enable_type_constraints,
        plausibility=config.enable_plausibility_filter,
        limits=config.enable_relationship_limits,
        protect_orphans=config.protect_orphans,
    )

    return config


__all__: list[str] = [
    "VALID_PRESETS",
    "FilteringConfig",
    "FilteringMode",
    "resolve_filtering_config",
]
