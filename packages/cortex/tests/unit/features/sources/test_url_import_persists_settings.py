# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workstream 1: URL imports forward every upload setting to the queue.

The /api/v1/sources/url route accepts ``UrlImportRequest``; the route
hands the values to ``queue_fetch_url`` as an ``options`` dict. The
url-fetch handler then forwards those options into
``SourceProcessingService.upload_file`` which persists them on the row.

This test covers the route → queue boundary; ``test_url_fetch_handler_*``
covers the handler-side fan-out into upload_file.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_cortex.features.sources.api import import_url
from chaoscypher_cortex.features.sources.models import (
    UrlImportRequest,
    UrlImportResponse,
)


@pytest.mark.asyncio
async def test_url_import_threads_all_settings_into_queue_options() -> None:
    """``UrlImportRequest`` flags arrive in the queue ``options`` dict."""
    request = UrlImportRequest(
        url="https://example.com/doc",
        extract_entities=False,
        analysis_depth="quick",
        enable_normalization=False,
        enable_vision=False,
        domain="technical",
        content_filtering=False,
        filtering_mode="strict",
    )
    settings = MagicMock()
    settings.current_database = "default"
    settings.priorities.background = 50

    captured: dict[str, Any] = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return "tsk_url_1"

    with (
        patch(
            "chaoscypher_cortex.features.sources.api.queue_utils.queue_fetch_url",
            new=AsyncMock(side_effect=_capture),
        ),
        patch(
            "chaoscypher_cortex.features.sources.api.validate_url_safety",
            return_value=True,
        ),
        # The action gate is exercised by dedicated tests; this case
        # asserts the settings-threading contract independently. Patch
        # get_llm_health (called by require_extraction_ready) to return a
        # verified health snapshot so the gate passes without side effects.
        patch(
            "chaoscypher_core.services.llm.health.get_llm_health",
            new=AsyncMock(
                return_value=type(
                    "H",
                    (),
                    {"verified": True, "missing_models": (), "provider": "ollama"},
                )()
            ),
        ),
    ):
        response = await import_url(_=None, request=request, settings=settings)

    assert isinstance(response, UrlImportResponse)
    options = captured["options"]
    assert options["auto_analyze"] is False
    assert options["extraction_depth"] == "quick"
    assert options["enable_normalization"] is False
    assert options["enable_vision"] is False
    assert options["forced_domain"] == "technical"
    assert options["content_filtering"] is False
    assert options["filtering_mode"] == "strict"
