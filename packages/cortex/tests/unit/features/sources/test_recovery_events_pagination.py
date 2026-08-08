# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GET /sources/{id}/recovery_events uses the house pagination envelope.

The endpoint took a bare ``?limit=`` param and returned ``{events: []}``
— the only list endpoint in the slice off the canonical
``?page=&page_size=`` + ``{data, pagination}`` shape (API_DESIGN.md).
The feed is not intrinsically bounded (events accumulate per recovery
dispatch and the attempt counter resets on stage transitions), so it
gets the full envelope.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.api import list_source_recovery_events


_NOW = datetime.now(UTC)


def _event(i: int) -> dict:
    return {
        "id": f"ev-{i}",
        "source_id": "src-1",
        "database_name": "default",
        "attempt_at": _NOW,
        "from_status": "extracting",
        "action_taken": "extract_chunk",
        "reason": "stalled",
        "enqueued_count": 1,
    }


@pytest.mark.unit
class TestRecoveryEventsPagination:
    """House envelope: {data, pagination} driven by PageParams."""

    @pytest.mark.asyncio
    async def test_returns_house_envelope(self) -> None:
        service = MagicMock()
        service.get_source.return_value = {"id": "src-1"}
        service.list_recovery_events.return_value = {
            "events": [_event(1), _event(2)],
            "total": 5,
            "page": 1,
            "page_size": 2,
        }

        result = await list_source_recovery_events(
            _="test-user",
            source_id="src-1",
            service=service,
            pagination=(1, 2),
        )

        service.list_recovery_events.assert_called_once_with("src-1", page=1, page_size=2)
        assert [e.id for e in result.data] == ["ev-1", "ev-2"]
        assert result.pagination.total == 5
        assert result.pagination.page == 1
        assert result.pagination.page_size == 2
        assert result.pagination.total_pages == 3
        assert result.pagination.has_next is True
        assert result.pagination.has_prev is False

    @pytest.mark.asyncio
    async def test_last_page_flags(self) -> None:
        service = MagicMock()
        service.get_source.return_value = {"id": "src-1"}
        service.list_recovery_events.return_value = {
            "events": [_event(5)],
            "total": 5,
            "page": 3,
            "page_size": 2,
        }

        result = await list_source_recovery_events(
            _="test-user",
            source_id="src-1",
            service=service,
            pagination=(3, 2),
        )

        assert result.pagination.has_next is False
        assert result.pagination.has_prev is True

    @pytest.mark.asyncio
    async def test_raises_404_when_source_missing(self) -> None:
        from fastapi import HTTPException

        service = MagicMock()
        service.get_source.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await list_source_recovery_events(
                _="test-user",
                source_id="missing",
                service=service,
                pagination=(1, 50),
            )

        assert exc_info.value.status_code == 404
        service.list_recovery_events.assert_not_called()
