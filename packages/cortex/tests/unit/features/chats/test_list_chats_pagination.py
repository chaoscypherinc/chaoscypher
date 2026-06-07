# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: GET /api/v1/chats uses canonical ?page=&page_size= pagination.

Migrated from ?skip=&limit= as part of the API consistency campaign.
The handler must:
  * Accept ``pagination: PageParams`` (a (page, page_size) tuple).
  * Forward ``offset = (page - 1) * page_size`` and ``limit = page_size``
    to the engine ChatService.
  * Build the response envelope with the canonical ``PaginationMetadata``
    fields (page, page_size, total, total_pages, has_next, has_prev).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.chats.api import list_chats


def _make_chat_service(*, count: int) -> MagicMock:
    """Return a ChatService stub with ``count_chats`` and a 3-row ``list_chats``."""
    service = MagicMock()
    service.list_chats.return_value = [
        {
            "id": f"c{i}",
            "title": f"Chat {i}",
            "status": "active",
            "created_at": "2026-05-17T00:00:00+00:00",
            "updated_at": "2026-05-17T00:00:00+00:00",
            "message_count": 0,
            "source_ids": None,
        }
        for i in range(3)
    ]
    service.count_chats.return_value = count
    return service


@pytest.mark.unit
class TestListChatsPagination:
    """list_chats translates (page, page_size) → (offset, limit) correctly."""

    @pytest.mark.asyncio
    async def test_default_pagination_passes_offset_zero(self) -> None:
        """page=1 → offset=0; page_size flows through unchanged."""
        service = _make_chat_service(count=100)
        result = await list_chats(
            chat_service=service,  # type: ignore[arg-type]
            pagination=(1, 50),
            _=MagicMock(),
            scoped=None,
        )

        kwargs = service.list_chats.call_args.kwargs
        assert kwargs["offset"] == 0
        assert kwargs["limit"] == 50
        assert kwargs["scoped"] is None

        assert result.pagination.page == 1
        assert result.pagination.page_size == 50
        assert result.pagination.total == 100
        assert result.pagination.total_pages == 2
        assert result.pagination.has_next is True
        assert result.pagination.has_prev is False

    @pytest.mark.asyncio
    async def test_page_three_page_size_ten_offset_twenty(self) -> None:
        """page=3, page_size=10 → offset=20."""
        service = _make_chat_service(count=100)
        result = await list_chats(
            chat_service=service,  # type: ignore[arg-type]
            pagination=(3, 10),
            _=MagicMock(),
            scoped=None,
        )

        kwargs = service.list_chats.call_args.kwargs
        assert kwargs["offset"] == 20
        assert kwargs["limit"] == 10

        assert result.pagination.page == 3
        assert result.pagination.page_size == 10
        assert result.pagination.total_pages == 10
        assert result.pagination.has_next is True
        assert result.pagination.has_prev is True

    @pytest.mark.asyncio
    async def test_scoped_filter_forwarded(self) -> None:
        """scoped=True is passed through to chat_service.list_chats."""
        service = _make_chat_service(count=5)
        await list_chats(
            chat_service=service,  # type: ignore[arg-type]
            pagination=(1, 50),
            _=MagicMock(),
            scoped=True,
        )

        kwargs = service.list_chats.call_args.kwargs
        assert kwargs["scoped"] is True

    @pytest.mark.asyncio
    async def test_zero_total_pagination(self) -> None:
        """total=0 → total_pages=1, has_next=False, has_prev=False on page 1."""
        service = _make_chat_service(count=0)
        result = await list_chats(
            chat_service=service,  # type: ignore[arg-type]
            pagination=(1, 50),
            _=MagicMock(),
            scoped=None,
        )

        assert result.pagination.total == 0
        assert result.pagination.total_pages == 1
        assert result.pagination.has_next is False
        assert result.pagination.has_prev is False

    @pytest.mark.asyncio
    async def test_last_page_has_no_next(self) -> None:
        """On the final page, has_next is False and has_prev is True."""
        service = _make_chat_service(count=100)
        result = await list_chats(
            chat_service=service,  # type: ignore[arg-type]
            pagination=(2, 50),
            _=MagicMock(),
            scoped=None,
        )

        assert result.pagination.page == 2
        assert result.pagination.total_pages == 2
        assert result.pagination.has_next is False
        assert result.pagination.has_prev is True
