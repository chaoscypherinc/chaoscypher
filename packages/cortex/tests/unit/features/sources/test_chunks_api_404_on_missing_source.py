# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: GET /sources/{id}/chunks returns 404 when source is missing.

Mirrors the sibling /sources/{id}/stats pattern which calls
raise_if_not_found(stats, "Source not found").  The chunks endpoint
previously returned 200 with an empty list for any unknown source ID,
silently masking caller bugs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.chunks_api import get_source_chunks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(*, source_exists: bool = True) -> MagicMock:
    """Return a SourceService stub.

    If *source_exists* is False, ``get_source`` returns None (unknown ID).
    """
    service = MagicMock()
    service.get_source.return_value = (
        {"id": "src-001", "filename": "doc.txt"} if source_exists else None
    )
    service.get_chunks.return_value = {
        "chunks": [],
        "total": 0,
        "page": 1,
        "page_size": 50,
    }
    return service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSourceChunks404OnMissingSource:
    """get_source_chunks raises HTTP 404 when the source does not exist."""

    @pytest.mark.asyncio
    async def test_raises_404_when_source_missing(self) -> None:
        """Handler must raise NotFoundError (→ HTTP 404) for an unknown source."""
        from fastapi import HTTPException

        service = _make_service(source_exists=False)
        pagination: tuple[int, int] = (1, 50)

        with pytest.raises(HTTPException) as exc_info:
            await get_source_chunks(
                _=MagicMock(),  # CurrentUsername
                source_id="does-not-exist",
                service=service,  # type: ignore[arg-type]
                pagination=pagination,
                status=None,
            )

        assert exc_info.value.status_code == 404
        service.get_source.assert_called_once_with("does-not-exist")
        service.get_chunks.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_200_with_empty_data_when_source_exists_but_no_chunks(self) -> None:
        """Existing source with zero chunks → 200 + empty data list (no regression)."""
        service = _make_service(source_exists=True)
        pagination: tuple[int, int] = (1, 50)

        result = await get_source_chunks(
            _=MagicMock(),
            source_id="src-001",
            service=service,  # type: ignore[arg-type]
            pagination=pagination,
            status=None,
        )

        assert result.data == []
        assert result.pagination.total == 0
        service.get_source.assert_called_once_with("src-001")
        service.get_chunks.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_source_called_before_get_chunks(self) -> None:
        """Source existence is probed before attempting to list chunks."""
        call_order: list[str] = []

        service = MagicMock()
        service.get_source.side_effect = lambda *_a, **_kw: (
            call_order.append("get_source") or {"id": "src-002"}
        )
        service.get_chunks.side_effect = lambda *_a, **_kw: (
            call_order.append("get_chunks")
            or {"chunks": [], "total": 0, "page": 1, "page_size": 50}
        )

        pagination: tuple[int, int] = (1, 50)
        await get_source_chunks(
            _=MagicMock(),
            source_id="src-002",
            service=service,  # type: ignore[arg-type]
            pagination=pagination,
            status=None,
        )

        assert call_order == ["get_source", "get_chunks"], (
            "get_source must be called before get_chunks"
        )
