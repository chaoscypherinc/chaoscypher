# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""PDF page renders for the vision pipeline are bounded by a pixel cap.

The bitmap size is MediaBox x (dpi/72)^2 and the MediaBox comes from the
uploaded file: a 655-byte PDF with a 10000x10000 pt page allocated ~3 GB
before ``vision_max_render_pixels`` existed.
"""

from __future__ import annotations

import io
from pathlib import Path

import pypdfium2 as pdfium  # type: ignore[import-untyped]
import pytest
from PIL import Image

from chaoscypher_core.operations.importing.vision_page_handler import _render_image_bytes
from chaoscypher_core.vision import VisionPageKind, clamp_render_scale


def _blank_pdf(path: Path, width_pt: float, height_pt: float) -> None:
    pdf = pdfium.PdfDocument.new()
    try:
        pdf.new_page(width_pt, height_pt)
        pdf.save(str(path))
    finally:
        pdf.close()


def test_clamp_keeps_requested_dpi_for_normal_pages() -> None:
    # US Letter at 300 DPI is ~8.4 MP.
    assert clamp_render_scale(612, 792, dpi=300, max_pixels=25_000_000) == 300 / 72


def test_clamp_shrinks_scale_to_fit_the_pixel_cap() -> None:
    scale = clamp_render_scale(10_000, 10_000, dpi=150, max_pixels=25_000_000)
    assert scale < 150 / 72
    assert (10_000 * scale) * (10_000 * scale) == pytest.approx(25_000_000)


def test_clamp_tolerates_degenerate_page_size() -> None:
    assert clamp_render_scale(0, 0, dpi=150, max_pixels=1_000_000) == 150 / 72


def test_render_of_oversized_page_stays_within_pixel_cap(tmp_path: Path) -> None:
    pdf_path = tmp_path / "huge.pdf"
    _blank_pdf(pdf_path, 5_000, 5_000)  # 10417 x 10417 px at 150 DPI uncapped
    row = {"kind": VisionPageKind.PDF_PAGE.value, "image_path": str(pdf_path), "page_number": 1}

    png = _render_image_bytes(row, dpi=150, max_pixels=1_000_000)

    width, height = Image.open(io.BytesIO(png)).size
    assert 900_000 < width * height <= 1_000_000


def test_render_of_normal_page_keeps_dpi(tmp_path: Path) -> None:
    pdf_path = tmp_path / "letter.pdf"
    _blank_pdf(pdf_path, 612, 792)
    row = {"kind": VisionPageKind.PDF_PAGE.value, "image_path": str(pdf_path), "page_number": 1}

    png = _render_image_bytes(row, dpi=144, max_pixels=25_000_000)

    assert Image.open(io.BytesIO(png)).size == (1224, 1584)
