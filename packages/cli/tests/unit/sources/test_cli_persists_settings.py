# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Workstream 1: CLI uploads persist every upload setting on the source row.

Regression that the ``cc source add`` flags (``--no-vision``,
``--filtering-mode``, ``--no-content-filtering``, ``--normalize``)
flow through ``SourcePipeline`` and
``CLISourceProcessingService.upload_file`` to ``upload_source``, ending
up on the row alongside the file content.

Mirrors the Cortex / URL-import tests so the three frontends have one
shared contract: the user's choice on entry equals the row's column
value at exit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def cli_service(mock_cli_context: Any) -> Any:
    """Build a ``CLISourceProcessingService`` against the existing mock context."""
    from chaoscypher_cli.sources.service import CLISourceProcessingService

    return CLISourceProcessingService(mock_cli_context)


def test_cli_upload_persists_filtering_mode_and_vision(
    cli_service: Any,
    sample_text_file: Path,
    mock_cli_context: Any,
) -> None:
    """``service.upload_file(..., enable_vision=False, filtering_mode="strict")``
    lands those values on the row recorded by the mock storage adapter.
    """
    file_id = cli_service.upload_file(
        sample_text_file,
        enable_vision=False,
        content_filtering=False,
        filtering_mode="strict",
        enable_normalization=False,
        auto_analyze=False,
    )
    assert isinstance(file_id, str)

    row = mock_cli_context.storage_adapter._files[file_id]
    assert row["enable_vision"] is False
    assert row["filtering_mode"] == "strict"
    assert row["content_filtering"] is False
    assert row["enable_normalization"] is False
    assert row["auto_analyze"] is False


def test_cli_upload_defaults_when_flags_omitted(
    cli_service: Any,
    sample_text_file: Path,
    mock_cli_context: Any,
) -> None:
    """Omitting the flags lands the documented defaults on the row."""
    file_id = cli_service.upload_file(sample_text_file)
    assert isinstance(file_id, str)

    row = mock_cli_context.storage_adapter._files[file_id]
    assert row["enable_vision"] is True
    assert row["filtering_mode"] == "balanced"
    assert row["content_filtering"] is True
    # enable_normalization=None means "use file-type default" (column is nullable).
    assert row["enable_normalization"] is None
    assert row["auto_analyze"] is True
