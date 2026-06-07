# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SearchHealthProbe."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.search.probe import SearchHealthProbe


@pytest.mark.asyncio
async def test_search_probe_reports_healthy_when_stats_return():
    search_service = MagicMock()
    stats_mock = MagicMock(fulltext_doc_count=10, vector_index_size=10, vector_dimension=384)
    search_service.get_stats.return_value = stats_mock
    probe = SearchHealthProbe(search_service=search_service)

    status = await probe.check()

    assert status.ok is True
    assert "10" in status.detail or "healthy" in status.detail.lower()


@pytest.mark.asyncio
async def test_search_probe_reports_failure_when_stats_raise():
    search_service = MagicMock()
    search_service.get_stats.side_effect = RuntimeError("index corrupted")
    probe = SearchHealthProbe(search_service=search_service)

    status = await probe.check()

    assert status.ok is False
    assert "corrupted" in status.detail or "error" in status.detail.lower()
