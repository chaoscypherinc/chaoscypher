# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression: empty extracted content must fail indexing, not silently succeed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.importing.indexing_handler import _run_indexing


@pytest.mark.asyncio
async def test_empty_content_raises_validation_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = MagicMock()
    chunking_service = MagicMock()
    engine_settings = MagicMock()
    settings = MagicMock()

    import chaoscypher_core.operations.importing.indexing_handler as mod

    # Force _extract_text to return empty string — simulates a scanned PDF
    # with vision off, a corrupt file, or an over-aggressive normalizer.
    monkeypatch.setattr(
        mod,
        "_extract_text",
        lambda **kwargs: (
            "",
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )

    # Patch _apply_vision_processing to be a no-op coroutine returning
    # the (documents, vision_job_id) tuple shape the caller now expects
    # (PR 2 Task 12, 2026-05-13).
    async def _no_op_vision(
        **kw: object,
    ) -> tuple[object, str | None]:
        return kw["documents"], None

    monkeypatch.setattr(mod, "_apply_vision_processing", _no_op_vision)

    # Patch loader registry (imported locally inside _run_indexing) so we
    # don't need a real file on disk.
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": "", "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    # Workstream 6 (2026-05-07): the old generic "no extractable content"
    # message has been replaced with metadata-aware variants. For an
    # empty PDF with no image_page_count we land in the generic
    # fallback that mentions normalization and chunking-size filters.
    with pytest.raises(ValidationError, match="extractable content"):
        await _run_indexing(
            file_id="src_test",
            file_info={"filename": "blank.pdf"},
            filepath=str(tmp_path / "blank.pdf"),
            analysis_depth="full",
            enable_normalization=True,
            enable_vision=False,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=engine_settings,
            settings=settings,
            database_name="default",
        )

    # Adapter must have been told the indexing failed (with our error message).
    assert adapter.fail_indexing.called
    args, _ = adapter.fail_indexing.call_args
    assert "extractable content" in args[1].lower()
