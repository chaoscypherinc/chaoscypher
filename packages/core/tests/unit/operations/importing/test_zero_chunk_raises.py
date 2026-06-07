# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""When normalize+chunk eats everything, indexing must fail loudly.

Workstream 5.4 (2026-05-07): a document with non-empty extracted text
but zero surviving chunks (every candidate dropped by ``min_chunk_size``,
or every cleaner ate every line) used to land as ``INDEXED`` with
``chunks_count=0``. That looks like success in the UI but every search
returns nothing. Raise a ``ValidationError`` with an actionable hint
instead so the operator knows to retry with looser settings.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.importing.indexing_handler import _run_indexing


@pytest.mark.asyncio
async def test_zero_chunk_after_non_empty_input_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-empty text but zero surviving chunks → ValidationError."""
    adapter = MagicMock()
    engine_settings = MagicMock()
    settings = MagicMock()
    # Pin data_dir to tmp_path so the incidental persist_original_text
    # write lands inside the pytest-managed tmp tree rather than
    # stringifying the MagicMock into a CWD directory.
    settings.data_dir = str(tmp_path)

    import chaoscypher_core.operations.importing.indexing_handler as mod

    # Force enough text to clear the empty-content guard (>50 chars) but
    # then produce zero chunks from chunking_service. This simulates the
    # tiny-document scenario where every chunk was below min_chunk_size.
    extracted = "real content here. " * 10  # ~190 chars; plenty to pass guard
    monkeypatch.setattr(
        mod,
        "_extract_text",
        lambda **kwargs: (
            extracted,
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )

    # No-op vision so we don't need real images.
    async def _no_op_vision(
        **kw: object,
    ) -> tuple[object, str | None]:
        return kw["documents"], None

    monkeypatch.setattr(mod, "_apply_vision_processing", _no_op_vision)

    # Loader stub.
    fake_registry = MagicMock()
    fake_registry.load_document.return_value = [{"content": extracted, "metadata": {}}]
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )

    # Chunking service that returns zero-chunk result with a non-zero
    # ``chunks_filtered`` so the handler can attribute the failure cause.
    chunking_service = MagicMock()

    class _ZeroChunkResult:
        small_chunks: list = []
        hierarchical_groups: list = []
        total_small_chunks = 0
        total_groups = 0
        total_original_chunks = 0
        total_original_groups = 0
        chunks_filtered = 5

    async def _create_zero_chunks(**kw: object) -> _ZeroChunkResult:
        return _ZeroChunkResult()

    chunking_service.create_chunks = _create_zero_chunks

    with pytest.raises(ValidationError) as exc:
        await _run_indexing(
            file_id="src_zero",
            file_info={"filename": "tiny.txt"},
            filepath=str(tmp_path / "tiny.txt"),
            analysis_depth="full",
            enable_normalization=True,
            enable_vision=False,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=engine_settings,
            settings=settings,
            database_name="default",
        )

    msg = str(exc.value).lower()
    assert "no chunks" in msg or "zero chunks" in msg, (
        f"Error must mention chunk filtering; got: {exc.value}"
    )
    # Actionable hint must reference normalization/cleaners so the
    # operator knows what to disable.
    assert "normalize" in msg or "cleaner" in msg or "normalization" in msg, (
        f"Error must hint at normalization/cleaners; got: {exc.value}"
    )
    # Adapter must have been told the indexing failed.
    assert adapter.fail_indexing.called
