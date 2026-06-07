# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""DomainRegistry.rank_domains — the ordered candidate list get_best_domain discards.

rank_domains exposes the `candidates` list that get_best_domain previously built
inline (generic excluded, highest score first, can_handle=False and raising plugins
skipped). It applies NO floor and NO generic fallback — that policy stays in
get_best_domain / detect_extraction_domain.
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
    """A DomainRegistry with plugin discovery skipped (caller sets _plugins)."""
    reg = DomainRegistry(database_name="default")
    reg._plugins = {}
    reg._configs = {}
    return reg


def _set_plugins(reg: DomainRegistry, plugins: dict[str, Any]) -> None:
    reg._plugins = plugins


def test_ranks_candidates_highest_first(registry: DomainRegistry) -> None:
    """Candidates are returned sorted by score, descending."""
    _set_plugins(
        registry,
        {
            "news": _make_plugin("news", True, 0.85),
            "biographical": _make_plugin("biographical", True, 1.20),
            "historical": _make_plugin("historical", True, 1.05),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    ranked = registry.rank_domains("any text", "x.txt", {})
    names = [d.name for d, _ in ranked]
    scores = [s for _, s in ranked]
    assert names == ["biographical", "historical", "news"]
    assert scores == pytest.approx([1.20, 1.05, 0.85])


def test_generic_excluded_from_ranking(registry: DomainRegistry) -> None:
    """`generic` is the fallback only — never a ranked candidate, even if high."""
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin("biographical", True, 1.20),
            "generic": _make_plugin("generic", True, 5.0),
        },
    )
    ranked = registry.rank_domains("any text", "x.txt", {})
    assert [d.name for d, _ in ranked] == ["biographical"]


def test_can_handle_false_excluded(registry: DomainRegistry) -> None:
    """Plugins returning can_handle=False are not ranked."""
    _set_plugins(
        registry,
        {
            "news": _make_plugin("news", False, 0.90),
            "biographical": _make_plugin("biographical", True, 1.20),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    ranked = registry.rank_domains("any text", "x.txt", {})
    assert [d.name for d, _ in ranked] == ["biographical"]


def test_no_candidates_returns_empty_list(registry: DomainRegistry) -> None:
    """When nothing can_handle, the ranking is empty (no floor/fallback applied here)."""
    _set_plugins(
        registry,
        {
            "news": _make_plugin("news", False, 0.30),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    assert registry.rank_domains("any text", "x.txt", {}) == []


def test_no_plugins_returns_empty_list(registry: DomainRegistry) -> None:
    """An empty registry yields an empty ranking, not a crash."""
    _set_plugins(registry, {})
    assert registry.rank_domains("any text", "x.txt", {}) == []


def test_raising_plugin_is_skipped(registry: DomainRegistry) -> None:
    """A plugin whose can_analyze raises is skipped, not propagated."""
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
    ranked = registry.rank_domains("any text", "x.txt", {})
    assert [d.name for d, _ in ranked] == ["biographical"]


def test_below_floor_candidate_still_ranked(registry: DomainRegistry) -> None:
    """rank_domains applies NO floor — sub-1.0 winners still appear (raw scores)."""
    _set_plugins(
        registry,
        {
            "news": _make_plugin("news", True, 0.74),
            "biographical": _make_plugin("biographical", True, 0.60),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    ranked = registry.rank_domains("any text", "x.txt", {})
    assert [d.name for d, _ in ranked] == ["news", "biographical"]
    assert ranked[0][1] == pytest.approx(0.74)


def test_metadata_none_defaults_to_empty_dict(registry: DomainRegistry) -> None:
    """Passing metadata=None must not crash (mirrors get_best_domain's guard)."""
    _set_plugins(
        registry,
        {"biographical": _make_plugin("biographical", True, 1.20)},
    )
    ranked = registry.rank_domains("any text", "x.txt", None)
    assert [d.name for d, _ in ranked] == ["biographical"]
