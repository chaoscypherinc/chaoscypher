# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for PauseRepository."""

from unittest.mock import MagicMock

from chaoscypher_cortex.features.pause.repository import PauseRepository


def test_pause_source_delegates_to_adapter() -> None:
    adapter = MagicMock()
    repo = PauseRepository(adapter=adapter)

    repo.pause_source(source_id="s-1", database_name="default", reason="x")

    adapter.set_source_paused.assert_called_once_with(
        source_id="s-1",
        database_name="default",
        is_paused=True,
        reason="x",
    )


def test_resume_source_passes_false() -> None:
    adapter = MagicMock()
    repo = PauseRepository(adapter=adapter)

    repo.resume_source(source_id="s-1", database_name="default")

    adapter.set_source_paused.assert_called_once_with(
        source_id="s-1",
        database_name="default",
        is_paused=False,
        reason=None,
    )


def test_bulk_pause_returns_count() -> None:
    adapter = MagicMock()
    adapter.bulk_set_sources_paused = MagicMock(return_value=3)
    repo = PauseRepository(adapter=adapter)

    count = repo.pause_sources(
        source_ids=["a", "b", "c"],
        database_name="default",
        reason="test",
    )
    assert count == 3
    adapter.bulk_set_sources_paused.assert_called_once_with(
        source_ids=["a", "b", "c"],
        database_name="default",
        is_paused=True,
        reason="test",
    )


def test_bulk_resume_returns_count() -> None:
    adapter = MagicMock()
    adapter.bulk_set_sources_paused = MagicMock(return_value=2)
    repo = PauseRepository(adapter=adapter)

    count = repo.resume_sources(source_ids=["a", "b"], database_name="default")
    assert count == 2
    adapter.bulk_set_sources_paused.assert_called_once_with(
        source_ids=["a", "b"],
        database_name="default",
        is_paused=False,
        reason=None,
    )


def test_pause_system() -> None:
    adapter = MagicMock()
    repo = PauseRepository(adapter=adapter)

    repo.pause_system(reason="maintenance")

    adapter.set_system_paused.assert_called_once_with(
        is_paused=True, reason="maintenance", paused_by=None
    )


def test_resume_system() -> None:
    adapter = MagicMock()
    repo = PauseRepository(adapter=adapter)

    repo.resume_system()

    adapter.set_system_paused.assert_called_once_with(
        is_paused=False, reason=None, paused_by="user"
    )


def test_get_system_state() -> None:
    adapter = MagicMock()
    adapter.get_system_state = MagicMock(
        return_value={"processing_paused": True, "processing_paused_reason": "x"}
    )
    repo = PauseRepository(adapter=adapter)

    state = repo.get_system_state()
    assert state["processing_paused"] is True
