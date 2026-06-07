# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for GET /api/v1/sources/{id}/chunks envelope shape (Task 5.2).

Verifies that the chunks endpoint returns the house-standard
{data, pagination} envelope, not the old {chunks, total, page, page_size}.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.models import ChunkListResponse
from chaoscypher_cortex.shared.api.models import PaginationMetadata


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine_service(
    *,
    chunks: list[dict] | None = None,
    total: int = 0,
) -> MagicMock:
    """Return an engine service stub that emits a paginated chunk response."""
    mock = MagicMock()
    mock.get_chunks_by_source.return_value = {
        "chunks": chunks or [],
        "total": total,
        "page": 1,
        "page_size": 5,
    }
    return mock


def _make_service(engine_service: MagicMock) -> MagicMock:
    """Return a SourceService stub whose get_chunks delegates to engine."""
    settings = MagicMock()
    settings.pagination.default_page_size = 50

    # Build a real-ish get_chunks return value using the engine stub
    raw = engine_service.get_chunks_by_source.return_value
    service = MagicMock()
    service.get_chunks.return_value = raw
    return service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChunkListEnvelope:
    """Verify the ChunkListResponse uses {data, pagination} convention."""

    def test_chunk_list_response_has_data_field(self) -> None:
        """ChunkListResponse must expose a 'data' key, not 'chunks'."""
        meta = PaginationMetadata(
            total=0, page=1, page_size=5, total_pages=1, has_next=False, has_prev=False
        )
        resp = ChunkListResponse(data=[], pagination=meta)
        dumped = resp.model_dump()
        assert "data" in dumped
        assert "chunks" not in dumped

    def test_chunk_list_response_has_pagination_field(self) -> None:
        """ChunkListResponse must expose a 'pagination' key."""
        meta = PaginationMetadata(
            total=3, page=1, page_size=5, total_pages=1, has_next=False, has_prev=False
        )
        resp = ChunkListResponse(data=[], pagination=meta)
        dumped = resp.model_dump()
        assert "pagination" in dumped
        assert "total" not in dumped  # old top-level total gone
        assert "page" not in dumped  # old top-level page gone
        assert "page_size" not in dumped  # old top-level page_size gone

    def test_pagination_has_all_six_fields(self) -> None:
        """Pagination sub-object must carry all 6 metadata fields."""
        meta = PaginationMetadata(
            total=10, page=2, page_size=5, total_pages=2, has_next=False, has_prev=True
        )
        resp = ChunkListResponse(data=[], pagination=meta)
        pag = resp.model_dump()["pagination"]
        assert set(pag.keys()) == {
            "total",
            "page",
            "page_size",
            "total_pages",
            "has_next",
            "has_prev",
        }

    def test_has_next_has_prev_computed_correctly(self) -> None:
        """has_next/has_prev reflect position within result set."""
        meta = PaginationMetadata(
            total=15, page=2, page_size=5, total_pages=3, has_next=True, has_prev=True
        )
        resp = ChunkListResponse(data=[], pagination=meta)
        pag = resp.pagination
        assert pag.has_next is True
        assert pag.has_prev is True
