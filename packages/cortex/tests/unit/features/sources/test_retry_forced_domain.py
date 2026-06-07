# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: manual retry preserves user-chosen forced_domain.

A source that errored at indexing stage hasn't run extraction yet, so
extraction_domain is NULL. Retry must read forced_domain from the
source row (set at upload time per fix #1).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_cortex.features.sources.service import SourceService


@pytest.mark.asyncio
async def test_retry_uses_forced_domain_when_extraction_domain_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_file_info: dict[str, Any] = {}

    async def fake_queue_import_indexing(**kwargs: Any) -> dict[str, str]:
        captured_file_info.update(kwargs["file_info"])
        return {"task_id": "tsk_1", "status": "queued"}

    monkeypatch.setattr(
        "chaoscypher_cortex.features.sources.service.queue_utils.queue_import_indexing",
        fake_queue_import_indexing,
    )

    settings = MagicMock()
    settings.priorities.background = 50

    service = SourceService.__new__(SourceService)
    service.engine_service = MagicMock()
    service.database_name = "default"
    service.settings = settings
    service.storage_adapter = MagicMock()
    service.graph_repository = None
    service.search_repository = None

    source_dict: dict[str, Any] = {
        "id": "src_1",
        "filepath": "/tmp/doc.pdf",
        "file_type": "pdf",
        "filename": "doc.pdf",
        "extraction_depth": "full",
        "forced_domain": "technical",
        "extraction_domain": None,
        "extraction_domain_auto": True,
    }

    await service._dispatch_retry_task(
        source_id="src_1",
        source=source_dict,
        new_status="pending",
    )

    assert captured_file_info["forced_domain"] == "technical"


@pytest.mark.asyncio
async def test_retry_falls_back_to_extraction_domain_for_legacy_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy rows persisted before fix #1 have forced_domain=None but extraction_domain set."""
    captured_file_info: dict[str, Any] = {}

    async def fake_queue_import_indexing(**kwargs: Any) -> dict[str, str]:
        captured_file_info.update(kwargs["file_info"])
        return {"task_id": "tsk_1", "status": "queued"}

    monkeypatch.setattr(
        "chaoscypher_cortex.features.sources.service.queue_utils.queue_import_indexing",
        fake_queue_import_indexing,
    )

    settings = MagicMock()
    settings.priorities.background = 50

    service = SourceService.__new__(SourceService)
    service.engine_service = MagicMock()
    service.database_name = "default"
    service.settings = settings
    service.storage_adapter = MagicMock()
    service.graph_repository = None
    service.search_repository = None

    source_dict: dict[str, Any] = {
        "id": "src_1",
        "filepath": "/tmp/doc.pdf",
        "file_type": "pdf",
        "filename": "doc.pdf",
        "extraction_depth": "full",
        "forced_domain": None,
        "extraction_domain": "technical",
        "extraction_domain_auto": False,
    }

    await service._dispatch_retry_task(
        source_id="src_1",
        source=source_dict,
        new_status="pending",
    )

    assert captured_file_info["forced_domain"] == "technical"


@pytest.mark.asyncio
async def test_retry_auto_detect_remains_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When user never picked a domain (auto-detect), retry should also not force one."""
    captured_file_info: dict[str, Any] = {}

    async def fake_queue_import_indexing(**kwargs: Any) -> dict[str, str]:
        captured_file_info.update(kwargs["file_info"])
        return {"task_id": "tsk_1", "status": "queued"}

    monkeypatch.setattr(
        "chaoscypher_cortex.features.sources.service.queue_utils.queue_import_indexing",
        fake_queue_import_indexing,
    )

    settings = MagicMock()
    settings.priorities.background = 50

    service = SourceService.__new__(SourceService)
    service.engine_service = MagicMock()
    service.database_name = "default"
    service.settings = settings
    service.storage_adapter = MagicMock()
    service.graph_repository = None
    service.search_repository = None

    source_dict: dict[str, Any] = {
        "id": "src_1",
        "filepath": "/tmp/doc.pdf",
        "file_type": "pdf",
        "filename": "doc.pdf",
        "extraction_depth": "full",
        "forced_domain": None,
        "extraction_domain": None,
        "extraction_domain_auto": True,
    }

    await service._dispatch_retry_task(
        source_id="src_1",
        source=source_dict,
        new_status="pending",
    )

    assert captured_file_info["forced_domain"] is None
