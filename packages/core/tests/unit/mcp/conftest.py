# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures and helpers for MCP extraction unit tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core.mcp.extraction import ExtractionOrchestrator


def install_chunk_indices_shortcut(orchestrator: ExtractionOrchestrator) -> None:
    """Patch ``_get_expected_indices`` to honor an in-source-dict cache.

    Tests inject ``extraction_chunk_indices: [0, 1, ...]`` into mock
    source dicts as a fixture shortcut: it spares the test from mocking
    the entire ``_build_source_groups`` chain (storage adapter, domain
    content filters, token-budget grouping).

    Production no longer reads this field — migration 0030 dropped the
    column, so a real ``source`` returned by storage never has it. The
    production code path always re-derives via ``_get_group_indices``.
    This helper installs the shortcut *only on the test instance* so
    tests stay readable without polluting production.

    Call once per orchestrator instance, after construction.
    """
    original = orchestrator._get_expected_indices

    def _shortcut(source: dict[str, Any]) -> set[int]:
        cached = source.get("extraction_chunk_indices")
        if cached is not None:
            return set(cached)
        return original(source)

    # Bound-method replacement — assigning to the instance attribute
    # shadows the class method without mutating the class.
    orchestrator._get_expected_indices = _shortcut  # type: ignore[method-assign]
