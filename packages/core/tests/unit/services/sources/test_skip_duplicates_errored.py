# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: skip_duplicates=True matches errored siblings (no orphans)."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.management.service import SourceProcessingService


# F14: hash verification rejects mismatched content_hash. Compute the real
# hash for these stub uploads instead of passing a placeholder.
_STAGED_BODY = b"x" * 32
_STAGED_HASH = hashlib.sha256(_STAGED_BODY).hexdigest()


@pytest.mark.asyncio
async def test_skip_duplicates_returns_existing_errored_row(tmp_path: Path) -> None:
    source_manager = MagicMock()
    source_manager.find_by_content_hash.return_value = {
        "id": "src_existing",
        "filename": "doc.pdf",
        "status": "error",
    }

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()
    validators = MagicMock()

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    result = await service.upload_file(
        filename="doc.pdf",
        skip_duplicates=True,
        staged_file_path=staged,
        content_hash=_STAGED_HASH,
        file_size=len(_STAGED_BODY),
    )

    assert result["id"] == "src_existing"
    assert result["skipped_duplicate"] is True
    assert result["existing_status"] == "error"
    source_manager.upload_source.assert_not_called()
    operations_manager.queue_import_indexing.assert_not_called()


@pytest.mark.asyncio
async def test_skip_duplicates_returns_existing_committed_row(tmp_path: Path) -> None:
    """Healthy duplicates also flow through with existing_status='committed'."""
    source_manager = MagicMock()
    source_manager.find_by_content_hash.return_value = {
        "id": "src_existing",
        "filename": "doc.pdf",
        "status": "committed",
    }

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    operations_manager = MagicMock()
    validators = MagicMock()

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=operations_manager,
        config_manager=config_manager,
        validators=validators,
    )

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    result = await service.upload_file(
        filename="doc.pdf",
        skip_duplicates=True,
        staged_file_path=staged,
        content_hash=_STAGED_HASH,
        file_size=len(_STAGED_BODY),
    )

    assert result["id"] == "src_existing"
    assert result["skipped_duplicate"] is True
    assert result["existing_status"] == "committed"


@pytest.mark.asyncio
async def test_skip_duplicates_dict_satisfies_source_response_model(tmp_path: Path) -> None:
    """The duplicate-skip dict must successfully build a SourceResponse Pydantic model.

    Regression for C-1: find_by_content_hash previously used load_only() with only
    4 columns (id, database_name, filename, status), causing the duplicate-skip path
    to produce a dict that was missing required SourceResponse fields (created_at,
    updated_at). This test locks the invariant by constructing SourceResponse from
    a realistic full-row dict, proving the model validates without error.
    """
    from chaoscypher_core.models import SourceStatus
    from chaoscypher_cortex.features.sources.models import SourceResponse

    now = datetime.now(UTC)
    # Simulate the full dict that _entity_to_dict(model_dump(mode="json")) produces
    # when find_by_content_hash loads all columns. Datetimes become ISO strings via
    # model_dump(mode="json"); Pydantic v2 coerces them back to datetime on parse.
    full_existing_dict: dict = {
        "id": "src_existing",
        "database_name": "default",
        "filename": "doc.pdf",
        "filepath": f"{tmp_path}/src_existing/doc.pdf",
        "file_type": "pdf",
        "file_size": 100,
        "title": "doc.pdf",
        "source_type": "pdf",
        "status": "error",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    source_manager = MagicMock()
    source_manager.find_by_content_hash.return_value = full_existing_dict

    settings = MagicMock()
    settings.current_database = "default"
    settings.database_dir = tmp_path
    settings.batching.max_upload_bytes = 10 * 1024 * 1024 * 1024  # 10 GB
    config_manager = MagicMock()
    config_manager.get_settings.return_value = settings

    service = SourceProcessingService(
        source_manager=source_manager,
        operations_manager=MagicMock(),
        config_manager=config_manager,
        validators=MagicMock(),
    )

    staged = tmp_path / "doc.pdf"
    staged.write_bytes(_STAGED_BODY)

    result = await service.upload_file(
        filename="doc.pdf",
        skip_duplicates=True,
        staged_file_path=staged,
        content_hash=_STAGED_HASH,
        file_size=len(_STAGED_BODY),
    )

    # The KEY assertion: the service result must build a valid SourceResponse.
    # If find_by_content_hash only loaded 4 columns, this raises ValidationError.
    response = SourceResponse(**result)
    assert response.skipped_duplicate is True
    assert response.existing_status == SourceStatus.ERROR
    assert response.id == "src_existing"
    assert response.created_at is not None
    assert response.updated_at is not None
