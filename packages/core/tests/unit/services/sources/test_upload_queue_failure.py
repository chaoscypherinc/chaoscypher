# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: queue enqueue failure raises ExternalServiceError, never stale success dict."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ExternalServiceError
from chaoscypher_core.services.sources.management.service import SourceProcessingService


# F14: hash verification rejects mismatched content_hash. Compute the real
# hash for these stub uploads instead of passing a placeholder.
_STAGED_BODY = b"x" * 32
_STAGED_HASH = hashlib.sha256(_STAGED_BODY).hexdigest()


@pytest.mark.asyncio
async def test_queue_failure_raises_external_service_error(tmp_path: Path) -> None:
    """queue_import_indexing raising surfaces as ExternalServiceError to the caller."""
    source_manager = MagicMock()
    source_manager.find_by_content_hash.return_value = None
    source_manager.upload_source.return_value = {
        "id": "src_1",
        "status": "pending",
        "filename": "doc.pdf",
    }
    source_manager.get_file.return_value = None

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    settings.priorities.background = 50
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()

    async def boom(**kwargs: object) -> dict[str, str]:
        raise RuntimeError("queue server unreachable")

    operations_manager.queue_import_indexing.side_effect = boom
    validators = MagicMock()

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    with pytest.raises(ExternalServiceError) as exc_info:
        await service.upload_file(
            filename="doc.pdf",
            staged_file_path=staged,
            content_hash=_STAGED_HASH,
            file_size=len(_STAGED_BODY),
        )

    rendered = str(exc_info.value).lower()
    assert "valkey" in rendered
    assert "queue" in rendered
    source_manager.update_file.assert_called_once()
    call_args = source_manager.update_file.call_args
    assert call_args.args[0] == "src_1"
    assert call_args.kwargs["database_name"] == "default"
    update_payload = call_args.kwargs["updates"]
    assert update_payload["status"] == "error"
    assert update_payload["error_stage"] == "indexing"
    assert "Failed to queue indexing" in update_payload["error_message"]
    source_manager.get_file.assert_not_called()


@pytest.mark.asyncio
async def test_queue_failure_raises_even_when_get_file_succeeds(tmp_path: Path) -> None:
    """Even when get_file returns the errored row, we should still raise (not return)."""
    source_manager = MagicMock()
    source_manager.find_by_content_hash.return_value = None
    source_manager.upload_source.return_value = {
        "id": "src_1",
        "status": "pending",
        "filename": "doc.pdf",
    }
    # get_file returns the errored row this time
    source_manager.get_file.return_value = {
        "id": "src_1",
        "status": "error",
        "error_message": "Failed to queue indexing: queue down",
    }

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    settings.priorities.background = 50
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()

    async def boom(**kwargs: object) -> dict[str, str]:
        raise RuntimeError("queue down")

    operations_manager.queue_import_indexing.side_effect = boom
    validators = MagicMock()

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    with pytest.raises(ExternalServiceError):
        await service.upload_file(
            filename="doc.pdf",
            staged_file_path=staged,
            content_hash=_STAGED_HASH,
            file_size=len(_STAGED_BODY),
        )
