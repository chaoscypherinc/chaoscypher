# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: forced_domain must persist on the SourceRow at upload."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.management.service import SourceProcessingService


# F14: hash verification rejects mismatched content_hash. Compute the real
# hash for these stub uploads instead of passing a placeholder.
_STAGED_BODY = b"x" * 32
_STAGED_HASH = hashlib.sha256(_STAGED_BODY).hexdigest()


@pytest.mark.asyncio
async def test_forced_domain_passed_to_storage_adapter(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_upload_source(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"id": "src_test", **kwargs}

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

    async def fake_queue(**kwargs: object) -> dict[str, str]:
        return {"task_id": "tsk_1", "status": "queued"}

    operations_manager.queue_import_indexing.side_effect = fake_queue
    validators = MagicMock()

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    await service.upload_file(
        filename="doc.pdf",
        forced_domain="technical",
        staged_file_path=staged,
        content_hash=_STAGED_HASH,
        file_size=len(_STAGED_BODY),
    )

    assert captured["forced_domain"] == "technical"


@pytest.mark.asyncio
async def test_forced_domain_none_remains_none(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_upload_source(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"id": "src_test", **kwargs}

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

    async def fake_queue(**kwargs: object) -> dict[str, str]:
        return {"task_id": "tsk_1", "status": "queued"}

    operations_manager.queue_import_indexing.side_effect = fake_queue
    validators = MagicMock()

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    await service.upload_file(
        filename="doc.pdf",
        staged_file_path=staged,
        content_hash=_STAGED_HASH,
        file_size=len(_STAGED_BODY),
    )

    assert captured["forced_domain"] is None
