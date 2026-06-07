# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the check_paused handler guard helper."""

from unittest.mock import MagicMock

from chaoscypher_core.operations.pause_guard import (
    PauseCheckResult,
    check_paused,
)


def _adapter_with(source: dict | None, system: dict) -> MagicMock:
    adapter = MagicMock()
    adapter.get_source = MagicMock(return_value=source)
    adapter.get_system_state = MagicMock(return_value=system)
    return adapter


def test_not_paused_returns_false() -> None:
    adapter = _adapter_with(
        source={"id": "s-1", "is_paused": False},
        system={"processing_paused": False},
    )

    result = check_paused(source_id="s-1", database_name="default", adapter=adapter)
    assert isinstance(result, PauseCheckResult)
    assert result.paused is False
    assert result.scope is None


def test_source_paused_returns_scope_source() -> None:
    adapter = _adapter_with(
        source={"id": "s-1", "is_paused": True, "paused_reason": "manual"},
        system={"processing_paused": False},
    )

    result = check_paused(source_id="s-1", database_name="default", adapter=adapter)
    assert result.paused is True
    assert result.scope == "source"
    assert result.reason == "manual"


def test_system_paused_returns_scope_system() -> None:
    adapter = _adapter_with(
        source={"id": "s-1", "is_paused": False},
        system={"processing_paused": True, "processing_paused_reason": "deploy"},
    )

    result = check_paused(source_id="s-1", database_name="default", adapter=adapter)
    assert result.paused is True
    assert result.scope == "system"
    assert result.reason == "deploy"


def test_both_paused_source_takes_precedence() -> None:
    adapter = _adapter_with(
        source={"id": "s-1", "is_paused": True, "paused_reason": "src"},
        system={"processing_paused": True, "processing_paused_reason": "sys"},
    )

    result = check_paused(source_id="s-1", database_name="default", adapter=adapter)
    assert result.paused is True
    assert result.scope == "source"
    assert result.reason == "src"


def test_missing_source_falls_through_to_system() -> None:
    """A non-existent source (deleted race) should not crash.

    The guard should check system state and return that result, so
    handlers with a bad source_id still exit cleanly rather than
    raising AttributeError on a None source.
    """
    adapter = _adapter_with(
        source=None,
        system={"processing_paused": False},
    )

    result = check_paused(source_id="missing", database_name="default", adapter=adapter)
    assert result.paused is False
