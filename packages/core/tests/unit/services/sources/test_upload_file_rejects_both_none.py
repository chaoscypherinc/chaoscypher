# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: upload_file raises ValidationError when both file_content and staged_file_path are None."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.management.service import SourceProcessingService


@pytest.mark.asyncio
async def test_upload_file_raises_when_both_content_and_path_none(tmp_path: Path) -> None:
    """ValidationError fires before any storage call."""
    config = MagicMock()
    config.get_settings.return_value = MagicMock(
        current_database="default",
        database_dir=tmp_path,
        batching=MagicMock(max_upload_bytes=10 * 1024 * 1024 * 1024),  # 10 GB
    )
    service = SourceProcessingService(
        source_manager=MagicMock(),
        operations_manager=MagicMock(),
        config_manager=config,
        validators=MagicMock(),
    )

    with pytest.raises(ValidationError) as exc_info:
        await service.upload_file(
            file_content=None,
            staged_file_path=None,
            filename="x.txt",
        )

    assert "file_content" in str(exc_info.value) or "staged_file_path" in str(exc_info.value)
