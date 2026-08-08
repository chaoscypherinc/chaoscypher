# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 6 Task 5 tests: type-rescue gating and self-loop toggle.

Tests:
1. ``FilteringConfig.enable_type_rescue`` defaults to True.
2. Presets unfiltered and minimal set ``enable_type_rescue=False``.
3. ``FilteringConfig.allow_self_loops`` defaults to False.
4. ``validate_relationships`` drops self-loops by default.
5. ``validate_relationships`` keeps self-loops when ``allow_self_loops=True``.
6. Type rescue is skipped when ``filtering_config.enable_type_rescue=False``.
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
        ],
        "sentences": [],
        "chunk_content": "",
        "filtering_config": cfg,
        "entity_exclusions": None,
        "strict_entity_types": True,
        "valid_entity_type_names": {"Person"},
        "named_referent_types": None,
        "normalization_rules": None,
        "property_type_mapping": None,
        "filtering_log": MagicMock(),
    }


@pytest.mark.asyncio
async def test_type_rescue_disabled_via_filtering_config() -> None:
    """When enable_type_rescue=False, the real gate inside ``_filter_entities``
    must skip ``rescue_invalid_entity_types`` and preserve invalid-typed
    entities.

    The previous version of this test hand-copied the gating expression into
    the test body and asserted a mock that a pre-patch local import made
    unreachable, so deleting the production gate left it green (hunt queue
    2026-08-01). This version drives ``_filter_entities`` itself.
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
        "chaoscypher_core.services.sources.engine.extraction.utils.ai_entities.rescue_invalid_entity_types"
    ) as mock_rescue:
        filtered, stats = await _filter_entities(**_filter_entities_kwargs(cfg))

    mock_rescue.assert_not_called()
    assert "type_rescue" not in stats
    # Gate closed: InvalidType is neither rescued nor dropped.
    assert [e["name"] for e in filtered] == ["Alice", "Bob"]
    assert filtered[0]["type"] == "InvalidType"


@pytest.mark.asyncio
async def test_type_rescue_enabled_reaches_rescue_via_filter_entities() -> None:
    """Sibling proof the gate is live: with enable_type_rescue=True the same
    call path must invoke ``rescue_invalid_entity_types`` exactly once.
    """
    from chaoscypher_core.services.sources.engine.extraction.utils import (
        rescue_invalid_entity_types,
    )
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
    assert "type_rescue" in stats
    # The valid entity always survives the real rescue pass.
    assert "Bob" in [e["name"] for e in filtered]


__all__: list[str] = []
