# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Smoke tests for VisionPagesRepository — delegation correctness."""

from __future__ import annotations

from unittest.mock import MagicMock

from chaoscypher_core.vision.states import VisionPageStatus
from chaoscypher_cortex.features.sources.vision_pages_repository import (
    VisionPagesRepository,
)


def test_get_job_by_source_delegates() -> None:
    storage = MagicMock()
    storage.get_vision_job_by_source.return_value = {"id": "j1"}

    repo = VisionPagesRepository(storage=storage, database_name="test")
    result = repo.get_job_by_source("s1")

    assert result == {"id": "j1"}
    storage.get_vision_job_by_source.assert_called_once_with("s1")


def test_list_pages_with_status_filter_delegates() -> None:
    storage = MagicMock()
    storage.list_vision_page_descriptions.return_value = []

    repo = VisionPagesRepository(storage=storage, database_name="test")
    repo.list_pages("s1", statuses=[VisionPageStatus.FAILED])

    storage.list_vision_page_descriptions.assert_called_once_with(
        "s1", statuses=[VisionPageStatus.FAILED]
    )


def test_reset_for_retry_delegates() -> None:
    storage = MagicMock()
    storage.reset_vision_page_for_retry.return_value = True

    repo = VisionPagesRepository(storage=storage, database_name="test")
    result = repo.reset_for_retry(page_id="p1")

    assert result is True
    storage.reset_vision_page_for_retry.assert_called_once_with(page_id="p1")
