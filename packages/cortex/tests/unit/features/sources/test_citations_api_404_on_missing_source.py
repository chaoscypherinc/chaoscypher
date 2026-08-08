# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GET /sources/{id}/citations returns 404 when the source is missing.

Every sibling endpoint in chunks_api.py validates source existence
(``raise_if_not_found``) and declares NOT_FOUND_RESPONSE; citations
returned 200 with an empty list for any unknown source ID, silently
masking caller bugs. Mirrors test_chunks_api_404_on_missing_source.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.chunks_api import get_source_citations


def _make_service(*, source_exists: bool = True) -> MagicMock:
    """SourceService stub; ``get_source`` returns None when the ID is unknown."""
    service = MagicMock()
    service.get_source.return_value = (
        {"id": "src-001", "filename": "doc.txt"} if source_exists else None
    )
    service.get_citations.return_value = {
        "citations": [{"id": "cit-1", "entity_name": "Napoleon"}],
        "total": 1,
        "page": 1,
        "page_size": 50,
    }
    return service


@pytest.mark.unit
class TestGetSourceCitations404OnMissingSource:
    """get_source_citations raises HTTP 404 for an unknown source ID."""

    @pytest.mark.asyncio
    async def test_raises_404_when_source_missing(self) -> None:
        from fastapi import HTTPException

        service = _make_service(source_exists=False)

        with pytest.raises(HTTPException) as exc_info:
            await get_source_citations(
                _=MagicMock(),
                source_id="does-not-exist",
                service=service,  # type: ignore[arg-type]
                pagination=(1, 50),
            )

        assert exc_info.value.status_code == 404
        service.get_citations.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_data_when_source_exists(self) -> None:
        service = _make_service(source_exists=True)

        result = await get_source_citations(
            _=MagicMock(),
            source_id="src-001",
            service=service,  # type: ignore[arg-type]
            pagination=(1, 50),
        )

        assert result["total"] == 1
        assert result["citations"][0]["entity_name"] == "Napoleon"
        service.get_source.assert_called_once_with("src-001")
        service.get_citations.assert_called_once()

    def test_endpoint_declares_not_found_response(self) -> None:
        """The route metadata advertises 404 like every sibling."""
        from chaoscypher_cortex.features.sources.chunks_api import router

        route = next(r for r in router.routes if getattr(r, "path", "") == "/{source_id}/citations")
        assert 404 in route.responses  # type: ignore[attr-defined]
