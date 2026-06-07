# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for handle_rebuild_search_indexes.

Target: chaoscypher_core.operations.rebuild_handler

Drives the handler with mocked collaborators across its three branches:
- regenerate=True -> SearchService.rebuild_with_regeneration (async)
- regenerate=False -> SearchService.rebuild_indexes (sync)
- exception -> returns {"success": False, ...} and emits task_failed
SearchService is patched at its source path; event_bus in the handler module.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.operations.rebuild_handler import handle_rebuild_search_indexes


def _common_mocks() -> dict[str, Any]:
    return {
        "search_repository": MagicMock(),
        "graph_repository": MagicMock(),
        "indexing_service": MagicMock(),
        "storage_adapter": MagicMock(),
        "engine_settings": MagicMock(),
    }


def _patch(search_service: MagicMock) -> tuple[list[Any], MagicMock]:
    mock_service_cls = MagicMock(return_value=search_service)
    mock_event_bus = MagicMock()
    cms = [
        patch(
            "chaoscypher_core.services.search.engine.search.SearchService",
            mock_service_cls,
        ),
        patch(
            "chaoscypher_core.operations.rebuild_handler.event_bus",
            mock_event_bus,
        ),
    ]
    return cms, mock_event_bus


class TestRebuildWithoutRegeneration:
    @pytest.mark.asyncio
    async def test_calls_rebuild_indexes_and_emits_completed(self) -> None:
        mocks = _common_mocks()
        search_service = MagicMock()
        search_service.rebuild_indexes.return_value = {
            "success": True,
            "indexed": 10,
            "message": "done",
        }
        cms, mock_event_bus = _patch(search_service)
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await handle_rebuild_search_indexes(
                data={"regenerate": False},
                metadata=None,
                task_id="t1",
                **mocks,
            )

        assert result["success"] is True
        assert result["indexed"] == 10
        search_service.rebuild_indexes.assert_called_once_with()
        search_service.rebuild_with_regeneration.assert_not_called()

        ev_args, ev_kwargs = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_completed"
        assert ev_kwargs["details"]["regenerate"] is False

    @pytest.mark.asyncio
    async def test_regenerate_defaults_false_when_absent(self) -> None:
        mocks = _common_mocks()
        search_service = MagicMock()
        search_service.rebuild_indexes.return_value = {"success": True}
        cms, _bus = _patch(search_service)
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            await handle_rebuild_search_indexes(data={}, **mocks)
        search_service.rebuild_indexes.assert_called_once()


class TestRebuildWithRegeneration:
    @pytest.mark.asyncio
    async def test_calls_rebuild_with_regeneration(self) -> None:
        mocks = _common_mocks()
        search_service = MagicMock()
        search_service.rebuild_with_regeneration = AsyncMock(
            return_value={"success": True, "regenerated": 5}
        )
        cms, mock_event_bus = _patch(search_service)
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await handle_rebuild_search_indexes(
                data={"regenerate": True},
                task_id="t2",
                **mocks,
            )

        assert result["success"] is True
        assert result["regenerated"] == 5
        search_service.rebuild_with_regeneration.assert_awaited_once_with(
            indexing_service=mocks["indexing_service"],
        )
        search_service.rebuild_indexes.assert_not_called()

        ev_args, ev_kwargs = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_completed"
        assert ev_kwargs["details"]["regenerate"] is True


class TestRebuildFailure:
    @pytest.mark.asyncio
    async def test_exception_returns_failure_and_emits_task_failed(self) -> None:
        mocks = _common_mocks()
        search_service = MagicMock()
        search_service.rebuild_indexes.side_effect = RuntimeError("index broke")
        cms, mock_event_bus = _patch(search_service)
        with ExitStack() as stack:
            for cm in cms:
                stack.enter_context(cm)
            result = await handle_rebuild_search_indexes(
                data={"regenerate": False},
                task_id="t3",
                **mocks,
            )

        assert result == {"success": False, "error": "Rebuild failed"}
        ev_args, ev_kwargs = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_failed"
        assert "index broke" in ev_kwargs["reason"]

    @pytest.mark.asyncio
    async def test_constructor_failure_is_caught(self) -> None:
        """An error constructing SearchService is also handled gracefully."""
        mocks = _common_mocks()
        mock_service_cls = MagicMock(side_effect=ValueError("ctor failed"))
        mock_event_bus = MagicMock()
        with (
            patch(
                "chaoscypher_core.services.search.engine.search.SearchService",
                mock_service_cls,
            ),
            patch(
                "chaoscypher_core.operations.rebuild_handler.event_bus",
                mock_event_bus,
            ),
        ):
            result = await handle_rebuild_search_indexes(
                data={"regenerate": True},
                **mocks,
            )
        assert result["success"] is False
        assert result["error"] == "Rebuild failed"
        ev_args, _ = mock_event_bus.emit.call_args
        assert ev_args[0] == "task_failed"
