# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Render-size bound for PDF pages sent to vision models."""

from __future__ import annotations

import math


def clamp_render_scale(
    width_pt: float,
    height_pt: float,
    *,
    dpi: int,
    max_pixels: int,
) -> float:
    """Return the pypdfium2 render scale for a page, bounded by ``max_pixels``.

    The bitmap pdfium allocates is ``(width_pt * scale) x (height_pt * scale)``
    and the page size comes from the file's MediaBox, so an uploaded PDF
    controls it. Pages whose render at ``dpi`` fits within ``max_pixels``
    keep ``dpi / 72``; larger pages get the largest scale that fits.

    Args:
        width_pt: Page width in PDF points (``page.get_size()[0]``).
        height_pt: Page height in PDF points.
        dpi: Requested render resolution.
        max_pixels: Pixel-count ceiling for the rendered bitmap.

    Returns:
        The scale to pass to ``page.render(scale=...)``.
    """
    scale = dpi / 72
    area_pt = width_pt * height_pt
    if area_pt <= 0 or area_pt * scale * scale <= max_pixels:
        return scale
    return math.sqrt(max_pixels / area_pt)
