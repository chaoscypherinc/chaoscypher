# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: staged-path uploads must carry or compute content_hash."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from chaoscypher_core.services.sources.management.service import (
    SourceProcessingService,
)


@pytest.mark.asyncio
async def test_hash_computed_from_disk_when_omitted(tmp_path: Path) -> None:
    body = b"hello world\n" * 10
    staged = tmp_path / "doc.txt"
    staged.write_bytes(body)
    expected_hash = hashlib.sha256(body).hexdigest()

    captured: dict[str, object] = {}

    def fake_upload_source(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"id": "src_1", **kwargs}

    source_manager = MagicMock()
    source_manager.upload_source.side_effect = fake_upload_source
    source_manager.find_by_content_hash.return_value = None

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    settings.priorities.background = 50
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()
    operations_manager.queue_import_indexing = AsyncMock(return_value={"task_id": "tsk_1"})
    validators = MagicMock()

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    await service.upload_file(
        filename="doc.txt",
        staged_file_path=staged,
        file_size=len(body),
        # content_hash deliberately omitted
    )

    assert captured["content_hash"] == expected_hash
