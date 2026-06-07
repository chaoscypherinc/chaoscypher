# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Empty-content errors include a specific actionable hint.

Workstream 6 (2026-05-07): the previous guard raised a generic "no
extractable content" message regardless of cause. The new guard
inspects loader metadata (``extraction_method``, ``image_page_count``,
``file_type``) plus the row's ``enable_vision`` setting and produces
specific advice — "this PDF has 250 image-only pages, enable vision",
"this image needs vision to extract text", etc.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.operations.importing.indexing_handler import _run_indexing


def _patch_pipeline_internals(
    monkeypatch: pytest.MonkeyPatch,
    *,
    documents: list[dict[str, object]],
) -> MagicMock:
    """Wire common monkey-patches for indexing_handler tests.

    Returns the loader registry mock so callers can assert on it.
    """
    import chaoscypher_core.operations.importing.indexing_handler as mod

    monkeypatch.setattr(
        mod,
        "_extract_text",
        lambda **kwargs: (
            "",
            {"lines_removed": 0, "paragraphs_deduplicated": 0, "chars_removed": 0},
        ),
    )

    async def _no_op_vision(
        **kw: object,
    ) -> tuple[object, str | None]:
        return kw["documents"], None

    monkeypatch.setattr(mod, "_apply_vision_processing", _no_op_vision)

    fake_registry = MagicMock()
    fake_registry.load_document.return_value = documents
    monkeypatch.setattr(
        "chaoscypher_core.services.sources.loaders.get_loader_registry",
        lambda *a, **kw: fake_registry,
    )
    return fake_registry


@pytest.mark.asyncio
async def test_scanned_pdf_with_vision_off_says_enable_vision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = MagicMock()
    chunking_service = MagicMock()
    engine_settings = MagicMock()
    settings = MagicMock()

    _patch_pipeline_internals(
        monkeypatch,
        documents=[
            {
                "content": "",
                "metadata": {
                    "extraction_method": "pypdf_extract",
                    "image_page_count": 250,
                    "page_count": 250,
                },
            }
        ],
    )

    with pytest.raises(ValidationError) as exc:
        await _run_indexing(
            file_id="src_test",
            file_info={"filename": "scan.pdf"},
            filepath=str(tmp_path / "scan.pdf"),
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
    assert "image-only" in msg or "scanned" in msg
    assert "vision" in msg
    assert "250" in msg


@pytest.mark.asyncio
async def test_scanned_pdf_with_vision_on_blames_vision_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When vision is on but content still empty, blame the vision model."""
    adapter = MagicMock()
    chunking_service = MagicMock()
    engine_settings = MagicMock()
    settings = MagicMock()

    _patch_pipeline_internals(
        monkeypatch,
        documents=[
            {
                "content": "",
                "metadata": {
                    "extraction_method": "pypdf_extract",
                    "image_page_count": 5,
                },
            }
        ],
    )

    with pytest.raises(ValidationError) as exc:
        await _run_indexing(
            file_id="src_test",
            file_info={"filename": "scan.pdf"},
            filepath=str(tmp_path / "scan.pdf"),
            analysis_depth="full",
            enable_normalization=True,
            enable_vision=True,
            adapter=adapter,
            chunking_service=chunking_service,
            engine_settings=engine_settings,
            settings=settings,
            database_name="default",
        )

    msg = str(exc.value).lower()
    assert "vision" in msg
    # Should not point at the user's vision toggle (it's already on).
    assert "enable vision" not in msg


@pytest.mark.asyncio
async def test_image_upload_with_vision_off_says_enable_vision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    adapter = MagicMock()
    chunking_service = MagicMock()
    engine_settings = MagicMock()
    settings = MagicMock()

    _patch_pipeline_internals(
        monkeypatch,
        documents=[{"content": "", "metadata": {"extraction_method": "image_loader"}}],
    )

    with pytest.raises(ValidationError) as exc:
        await _run_indexing(
            file_id="src_test",
            file_info={"filename": "photo.png"},
            filepath=str(tmp_path / "photo.png"),
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
    assert "vision" in msg
    assert "image" in msg


@pytest.mark.asyncio
async def test_generic_empty_content_mentions_normalization(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without specific metadata clues, fall back to actionable normalize hint."""
    adapter = MagicMock()
    chunking_service = MagicMock()
    engine_settings = MagicMock()
    settings = MagicMock()

    _patch_pipeline_internals(
        monkeypatch,
        documents=[{"content": "", "metadata": {"extraction_method": "read_text"}}],
    )

    with pytest.raises(ValidationError) as exc:
        await _run_indexing(
            file_id="src_test",
            file_info={"filename": "weird.txt"},
            filepath=str(tmp_path / "weird.txt"),
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
    # Generic hint mentions one of: normalization, empty, structurally, filtered
    assert any(keyword in msg for keyword in ("normalization", "empty", "structurally", "filtered"))
