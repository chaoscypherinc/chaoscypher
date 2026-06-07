# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GET /sources?status=awaiting_confirmation threads the status filter to the
service without an allowlist rejecting the new value.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.api import list_sources


@pytest.mark.asyncio
async def test_list_sources_passes_awaiting_confirmation_status() -> None:
    service = MagicMock()
    service.list_sources_enriched.return_value = {"sources": [], "total": 0}

    settings = MagicMock()

    result = await list_sources(
        _="user",
        service=service,
        settings=settings,
        pagination=(1, 50),
        source_type=None,
        status="awaiting_confirmation",
        enabled=None,
        search=None,
        tag_id=None,
    )

    service.list_sources_enriched.assert_called_once()
    assert service.list_sources_enriched.call_args.kwargs["status"] == "awaiting_confirmation"
    assert result.pagination.total == 0
