# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""DomainRegistry.get_best_domain absolute-floor fallback to ``generic``.

Pinned by the 2026-05-23 38-fixture audit: every correctly-detected fixture
scored >= 1.02 on its right domain, so an absolute floor of 1.0 catches
~7/17 mismatches whose wrong winners barely cleared each domain's per-plugin
``min_threshold`` (default 0.4) without losing any of the known-good
detections.

The fallback path returns the ``generic`` plugin at confidence 0.1, matching
the legacy "nothing matched" path so downstream callers don't need a special
case.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
    DomainRegistry,
)


def _make_plugin(name: str, can_handle: bool, confidence: float) -> Any:
    """Return a minimal plugin stub for the registry's lookup loop."""
    plug = MagicMock()
    plug.name = name
    plug.can_analyze = MagicMock(return_value=(can_handle, confidence))
    return plug


@pytest.fixture
def registry() -> DomainRegistry:
    """A DomainRegistry pre-populated with a `generic` fallback + a couple of stub plugins."""
    reg = DomainRegistry(database_name="default")
    # Override the internal plugin map with our stubs (skipping plugin discovery).
    reg._plugins = {}
    reg._configs = {}
    return reg


def _set_plugins(reg: DomainRegistry, plugins: dict[str, Any]) -> None:
    reg._plugins = plugins


def test_winner_above_absolute_floor_wins(registry: DomainRegistry) -> None:
    """A clear winner above the absolute floor is returned as-is."""
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin("biographical", True, 1.20),
            "news": _make_plugin("news", True, 0.85),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "biographical"
    assert conf == pytest.approx(1.20)


def test_winner_below_floor_falls_to_generic(registry: DomainRegistry) -> None:
    """A top candidate scoring below the absolute floor is rejected; returns generic at 0.1."""
    _set_plugins(
        registry,
        {
            # news barely passes per-plugin min_threshold (0.4) but is below
            # the registry-level absolute floor (1.0). Without the floor it
            # would win; with it, generic should be returned instead.
            "news": _make_plugin("news", True, 0.74),
            "biographical": _make_plugin("biographical", True, 0.60),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "generic"
    assert conf == pytest.approx(0.10)


def test_no_can_handle_candidates_falls_to_generic(registry: DomainRegistry) -> None:
    """If every plugin says can_handle=False, fall back to generic."""
    _set_plugins(
        registry,
        {
            "news": _make_plugin("news", False, 0.30),
            "biographical": _make_plugin("biographical", False, 0.20),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "generic"
    assert conf == pytest.approx(0.10)


def test_generic_is_never_a_primary_winner(registry: DomainRegistry) -> None:
    """`generic` shouldn't be selected as the top candidate even if it scores high.

    The registry excludes generic from the competition; generic is only the fallback.
    """
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin("biographical", True, 1.20),
            # If generic were ever erroneously listed with a high score, it
            # must still not be picked over a real domain.
            "generic": _make_plugin("generic", True, 5.0),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "biographical"
    assert conf == pytest.approx(1.20)


def test_no_plugins_returns_minimal_fallback(registry: DomainRegistry) -> None:
    """When no plugins are registered at all, return the minimal fallback (not crash)."""
    _set_plugins(registry, {})
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    # _MinimalFallbackDomain returns name="fallback"
    assert domain.name == "fallback"
    assert conf == 0.0


def test_plugin_raising_exception_is_skipped(registry: DomainRegistry) -> None:
    """A plugin whose can_analyze raises should not crash the whole detection pass."""
    broken = MagicMock()
    broken.name = "broken"
    broken.can_analyze = MagicMock(side_effect=RuntimeError("boom"))

    _set_plugins(
        registry,
        {
            "broken": broken,
            "biographical": _make_plugin("biographical", True, 1.20),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "biographical"
    assert conf == pytest.approx(1.20)


def test_top_candidate_exactly_at_floor_wins(registry: DomainRegistry) -> None:
    """The floor is inclusive: a score exactly at the floor wins."""
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin(
                "biographical", True, DomainRegistry._DETECTION_ABSOLUTE_MIN
            ),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "biographical"


def test_top_candidate_just_below_floor_falls_to_generic(registry: DomainRegistry) -> None:
    """A score just below the floor must fall to generic."""
    just_below = DomainRegistry._DETECTION_ABSOLUTE_MIN - 0.001
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin("biographical", True, just_below),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "generic"
