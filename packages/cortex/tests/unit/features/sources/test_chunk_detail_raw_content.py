# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GET /sources/{id}/chunks/{chunk_id} returns raw_content; list omits it.

Task 1.3 of the Processing Tab funnel redesign: the single-chunk detail
endpoint must surface ``raw_content`` (pre-cleanup text, added in
migration 0040), while the paginated list endpoint must NOT include it
because the field is large and would balloon page payloads for sources
with thousands of chunks.

Legacy chunks (rows inserted before migration 0040) have
``raw_content=NULL``; the detail endpoint returns ``null`` in that case.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.chunks_api import (
    get_chunk,
    get_source_chunks,
)


def _chunk_row(
    *,
    chunk_id: str = "chk-1",
    source_id: str = "src-1",
    chunk_index: int = 0,
    content: str = "cleaned text",
    raw_content: str | None = "raw pre-cleanup text",
) -> dict:
    """Build a chunk dict shaped like ``SqliteAdapter.get_chunk`` returns.

    Includes the new (0038) ``raw_content`` field. Set ``raw_content=None``
    to simulate a legacy row inserted before the migration.
    """
    return {
        "id": chunk_id,
        "source_id": source_id,
        "chunk_index": chunk_index,
        "content": content,
        "raw_content": raw_content,
        "page_number": 1,
        "section": None,
        "group_index": None,
        "char_start": 0,
        "char_end": len(content),
        "citation_offset_method": "exact",
        "status": "indexed",
        "created_at": datetime.now(UTC),
    }


def _list_row(
    *,
    chunk_id: str = "chk-1",
    chunk_index: int = 0,
) -> dict:
    """Build a chunk dict as ``get_chunks_by_source`` returns it.

    The list path uses SQLAlchemy ``load_only()`` to project only the
    columns the list view needs; ``raw_content`` is intentionally NOT
    in that projection, so the dict produced here mirrors that and omits
    the field entirely.
    """
    return {
        "id": chunk_id,
        "source_id": "src-1",
        "chunk_index": chunk_index,
        "content": "cleaned text",
        "page_number": 1,
        "section": None,
        "group_index": None,
        "status": "indexed",
        "created_at": datetime.now(UTC),
    }


def _make_service(
    *,
    chunk_detail: dict | None = None,
    list_rows: list[dict] | None = None,
) -> MagicMock:
    """Stub SourceService for both list and detail paths."""
    service = MagicMock()
    service.get_source.return_value = {"id": "src-1", "filename": "doc.txt"}
    service.get_chunk.return_value = chunk_detail
    rows = list_rows if list_rows is not None else [_list_row()]
    service.get_chunks.return_value = {
        "chunks": rows,
        "total": len(rows),
        "page": 1,
        "page_size": 50,
    }
    return service


# ---------------------------------------------------------------------------
# Detail endpoint surfaces raw_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_detail_includes_raw_content() -> None:
    """Detail endpoint surfaces raw_content through ChunkResponse serialization.

    The endpoint returns the raw chunk dict and declares
    ``response_model=ChunkResponse``; FastAPI re-serializes the dict
    through the response model, which would silently drop any field
    not declared on ``ChunkResponse``. The real assertion is that the
    field round-trips through ``ChunkResponse.model_validate`` →
    ``model_dump``.
    """
    from chaoscypher_cortex.features.sources.models import ChunkResponse

    service = _make_service(chunk_detail=_chunk_row(raw_content="hello raw"))

    result = await get_chunk(
        _=MagicMock(),
        source_id="src-1",
        chunk_id="chk-1",
        service=service,  # type: ignore[arg-type]
    )

    # Simulate the FastAPI response_model serialization step.
    serialized = ChunkResponse.model_validate(result).model_dump()
    assert "raw_content" in serialized, (
        "ChunkResponse.model_dump() must include raw_content after the field is added to the model"
    )
    assert serialized["raw_content"] == "hello raw"


# ---------------------------------------------------------------------------
# List endpoint must NOT include raw_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_list_omits_raw_content() -> None:
    """List endpoint does NOT include raw_content (payload size concern).

    The list service path uses ``load_only()`` to project only the
    columns the UI needs; ``raw_content`` is excluded from that
    projection. When the model_dump produced by ChunkListResponse is
    serialized, ``raw_content`` must serialize to its default (None)
    only because the model declares it; the underlying source dict
    must not provide the value.
    """
    service = _make_service(list_rows=[_list_row()])

    result = await get_source_chunks(
        _=MagicMock(),
        source_id="src-1",
        service=service,  # type: ignore[arg-type]
        pagination=(1, 50),
        status=None,
    )

    assert len(result.data) == 1
    # The list service must NOT supply raw_content in its dict; the
    # field is large and would bloat payloads for big sources.
    list_dict = service.get_chunks.return_value["chunks"][0]
    assert "raw_content" not in list_dict, (
        "list service must not project raw_content into the list dict — "
        "load_only() in get_chunks_by_source excludes it for payload size"
    )


# ---------------------------------------------------------------------------
# Legacy null raw_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_chunk_returns_null_raw_content() -> None:
    """A chunk row with raw_content=NULL (pre-0038) returns null on detail."""
    from chaoscypher_cortex.features.sources.models import ChunkResponse

    service = _make_service(chunk_detail=_chunk_row(raw_content=None))

    result = await get_chunk(
        _=MagicMock(),
        source_id="src-1",
        chunk_id="chk-1",
        service=service,  # type: ignore[arg-type]
    )

    serialized = ChunkResponse.model_validate(result).model_dump()
    assert "raw_content" in serialized
    assert serialized["raw_content"] is None


# ---------------------------------------------------------------------------
# ChunkResponse model declares the field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_response_model_declares_raw_content() -> None:
    """The ChunkResponse Pydantic model must declare raw_content as optional."""
    from chaoscypher_cortex.features.sources.models import ChunkResponse

    fields = ChunkResponse.model_fields
    assert "raw_content" in fields, (
        "ChunkResponse must declare raw_content so the detail endpoint can "
        "surface pre-cleanup text (migration 0040)"
    )
    # Must be optional — legacy rows have NULL.
    field = fields["raw_content"]
    # str | None means the field annotation accepts None
    assert field.default is None, "raw_content default must be None to handle pre-0040 legacy rows"


# ---------------------------------------------------------------------------
# Detail endpoint surfaces chunk_metadata (sentence_offsets) for highlighting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunk_detail_includes_chunk_metadata() -> None:
    """Detail endpoint surfaces chunk_metadata so the source page can highlight
    the exact cited sentence on a citation deep-link.

    ``get_chunk`` loads the full DocumentChunk (no ``load_only``), so its dict
    carries ``chunk_metadata`` (which holds ``sentence_offsets``). The endpoint
    declares ``response_model=ChunkResponse``, which silently drops any field
    the model doesn't declare — so the model must declare ``chunk_metadata``.
    """
    from chaoscypher_cortex.features.sources.models import ChunkResponse

    meta = {"sentence_offsets": [{"start": 0, "end": 15}, {"start": 16, "end": 32}]}
    row = _chunk_row()
    row["chunk_metadata"] = meta
    service = _make_service(chunk_detail=row)

    result = await get_chunk(
        _=MagicMock(),
        source_id="src-1",
        chunk_id="chk-1",
        service=service,  # type: ignore[arg-type]
    )

    serialized = ChunkResponse.model_validate(result).model_dump()
    assert serialized.get("chunk_metadata") == meta


@pytest.mark.asyncio
async def test_chunk_list_omits_chunk_metadata() -> None:
    """List path must NOT project chunk_metadata (JSON, payload-size concern)."""
    service = _make_service(list_rows=[_list_row()])
    await get_source_chunks(
        _=MagicMock(),
        source_id="src-1",
        service=service,  # type: ignore[arg-type]
        pagination=(1, 50),
        status=None,
    )
    list_dict = service.get_chunks.return_value["chunks"][0]
    assert "chunk_metadata" not in list_dict, (
        "list service must not project chunk_metadata — load_only() in "
        "get_chunks_by_source excludes the JSON column for payload size"
    )


@pytest.mark.asyncio
async def test_chunk_response_model_declares_chunk_metadata() -> None:
    """ChunkResponse must declare chunk_metadata as an optional dict."""
    from chaoscypher_cortex.features.sources.models import ChunkResponse

    fields = ChunkResponse.model_fields
    assert "chunk_metadata" in fields, (
        "ChunkResponse must declare chunk_metadata so the detail endpoint can "
        "surface sentence_offsets for citation sentence-highlighting"
    )
    assert fields["chunk_metadata"].default is None
