# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Upload-settings columns survive insert and read."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_upload_source_persists_auto_analyze_false(sqlite_adapter, tmp_path: Path):
    """A user uploading with auto_analyze=False must see that on the row."""
    src_id = "src_test_aa_false"
    row = sqlite_adapter.upload_source(
        source_id=src_id,
        database_name="default",
        filename="x.txt",
        file_content=b"hello world " * 10,
        staging_dir=str(tmp_path),
        auto_analyze=False,
        enable_normalization=False,
        enable_vision=False,
        content_filtering=False,
        filtering_mode="strict",
    )
    assert row["auto_analyze"] is False
    assert row["enable_normalization"] is False
    assert row["enable_vision"] is False
    assert row["content_filtering"] is False
    assert row["filtering_mode"] == "strict"

    fetched = sqlite_adapter.get_source(src_id, "default")
    assert fetched["auto_analyze"] is False
    assert fetched["filtering_mode"] == "strict"
