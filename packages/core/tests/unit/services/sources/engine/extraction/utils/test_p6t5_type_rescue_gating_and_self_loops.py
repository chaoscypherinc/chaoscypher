# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 6 Task 5 tests: type-rescue gating and self-loop toggle.

Tests:
1. ``FilteringConfig.enable_type_rescue`` defaults to True.
2. Presets unfiltered and minimal set ``enable_type_rescue=False``.
3. ``FilteringConfig.allow_self_loops`` defaults to False.
4. ``validate_relationships`` drops self-loops by default.
5. ``validate_relationships`` keeps self-loops when ``allow_self_loops=True``.
6. ``filtering_config.enable_type_rescue=False`` turns off the rescue's salvage
   tiers (junk, absorption), never the core-type rescue.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.entity_cleaner import (
    validate_relationships,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    FilteringConfig,
    resolve_filtering_config,
)
from chaoscypher_core.services.sources.engine.extraction.utils.type_rescue import (
    rescue_invalid_entity_types,
)


# ---------------------------------------------------------------------------
# FilteringConfig field defaults
# ---------------------------------------------------------------------------


def test_enable_type_rescue_defaults_to_true() -> None:
    """New field must not break existing balanced-mode behaviour."""
    cfg = FilteringConfig()
    assert cfg.enable_type_rescue is True


def test_allow_self_loops_defaults_to_false() -> None:
    """Self-loops must remain suppressed unless the domain explicitly opts in."""
    cfg = FilteringConfig()
    assert cfg.allow_self_loops is False


# ---------------------------------------------------------------------------
# Preset overrides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["unfiltered", "minimal"])
def test_unfiltered_and_minimal_disable_type_rescue(mode: str) -> None:
    """Unfiltered and minimal must set enable_type_rescue=False so raw LLM
    output is preserved without the rescue pass.
    """
    cfg = resolve_filtering_config(mode)
    assert cfg.enable_type_rescue is False, (
        f"Preset '{mode}' should set enable_type_rescue=False; got {cfg.enable_type_rescue}"
    )


@pytest.mark.parametrize("mode", ["lenient", "balanced", "strict", "maximum"])
def test_higher_presets_keep_type_rescue_enabled(mode: str) -> None:
    """Presets above minimal keep type rescue active."""
    cfg = resolve_filtering_config(mode)
    assert cfg.enable_type_rescue is True, (
        f"Preset '{mode}' should keep enable_type_rescue=True; got {cfg.enable_type_rescue}"
    )


# ---------------------------------------------------------------------------
# validate_relationships self-loop behaviour
# ---------------------------------------------------------------------------


def _make_entity(name: str) -> dict:
    return {"name": name, "type": "Person"}


def _make_rel(src: int, tgt: int, rtype: str = "knows") -> dict:
    return {"source": src, "target": tgt, "type": rtype}


def test_validate_relationships_drops_self_loop_by_default() -> None:
    """Without allow_self_loops, a self-referencing relationship is invalid."""
    entities = [_make_entity("Alice"), _make_entity("Bob")]
    rels = [_make_rel(0, 0), _make_rel(0, 1)]  # first is self-loop

    valid, invalid_count = validate_relationships(rels, entities)
    assert invalid_count == 1
    assert len(valid) == 1
    assert valid[0]["source"] == 0
    assert valid[0]["target"] == 1


def test_validate_relationships_keeps_self_loop_when_allowed() -> None:
    """With allow_self_loops=True, the self-referencing relationship survives."""
    entities = [_make_entity("Alice"), _make_entity("Bob")]
    rels = [_make_rel(0, 0), _make_rel(0, 1)]

    valid, invalid_count = validate_relationships(rels, entities, allow_self_loops=True)
    assert invalid_count == 0
    assert len(valid) == 2


# ---------------------------------------------------------------------------
# Type rescue gating integration
# ---------------------------------------------------------------------------


def _filter_entities_kwargs(cfg: FilteringConfig) -> dict:
    """Common kwargs driving the real ``_filter_entities`` with only the
    type-rescue stage active (evidence/exclusions/plausibility all off).
    """
    return {
        "entities": [
            {"name": "Alice", "type": "InvalidType"},
            {"name": "Bob", "type": "Person"},
            {"name": "Lise", "type": "Character"},
            {"name": "he", "type": "Character"},
        ],
        "sentences": [],
        "chunk_content": "",
        "filtering_config": cfg,
        "entity_exclusions": None,
        "valid_entity_type_names": None,
        "named_referent_types": None,
        "normalization_rules": None,
        "property_type_mapping": None,
        "filtering_log": MagicMock(),
    }


@pytest.mark.asyncio
async def test_type_rescue_disabled_via_filtering_config() -> None:
    """With enable_type_rescue=False the type rescue still runs; only its salvage tiers stop.

    Every entity leaves the per-chunk filters core-typed (spec 2026-10-07
    § 6): a type no core type answers to is kept as a Concept flagged
    ``unplaced_type`` (keep, flag, never delete, 2026-10-07) and a mapped name is
    retyped whatever the preset. What ``enable_type_rescue`` (off for the
    unfiltered and minimal presets) turns off is the junk and absorption
    tiers: ``he`` is kept here (as the Person its written type maps to).

    Drives ``_filter_entities`` itself (a hand-copied gate expression once
    stayed green after the production gate was deleted, hunt queue
    2026-08-01).
    """
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _filter_entities,
    )

    cfg = FilteringConfig(
        enable_type_rescue=False,
        evidence_validation_mode="off",
        enable_entity_exclusions=False,
        enable_plausibility_filter=False,
    )

    with patch(
        "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.rescue_invalid_entity_types",
        wraps=rescue_invalid_entity_types,
    ) as mock_rescue:
        filtered, stats = await _filter_entities(**_filter_entities_kwargs(cfg))

    mock_rescue.assert_called_once()
    assert mock_rescue.call_args.kwargs["salvage"] is False
    assert stats["type_rescue"]["tier1_junk"] == 0
    assert [(e["name"], e["type"]) for e in filtered] == [
        ("Alice", "Concept"),
        ("Bob", "Person"),
        ("Lise", "Person"),
        ("he", "Person"),
    ]
    assert filtered[0]["properties"] == {"unplaced_type": True, "written_type": "InvalidType"}


@pytest.mark.asyncio
async def test_type_rescue_enabled_reaches_rescue_via_filter_entities() -> None:
    """Sibling proof the salvage switch is live: with enable_type_rescue=True junk is dropped."""
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        _filter_entities,
    )

    cfg = FilteringConfig(
        enable_type_rescue=True,
        evidence_validation_mode="off",
        enable_entity_exclusions=False,
        enable_plausibility_filter=False,
    )

    with patch(
        "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.rescue_invalid_entity_types",
        wraps=rescue_invalid_entity_types,
    ) as mock_rescue:
        filtered, stats = await _filter_entities(**_filter_entities_kwargs(cfg))

    mock_rescue.assert_called_once()
    assert mock_rescue.call_args.kwargs["salvage"] is True
    assert stats["type_rescue"]["tier1_junk"] == 1
    assert [(e["name"], e["type"]) for e in filtered] == [
        ("Alice", "Concept"),
        ("Bob", "Person"),
        ("Lise", "Person"),
    ]


__all__: list[str] = []
