# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Image Loader using Pillow for metadata extraction.

Reads image dimensions and format. Text content extraction is handled
by VisionService (called from the indexing handler or CLI pipeline),
not in the loader itself.

Implements BaseLoader protocol for auto-discovery by LoaderRegistry.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)

IMAGE_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "jpg",
        "jpeg",
        "png",
        "gif",
        "webp",
        "tiff",
        "tif",
        "bmp",
    }
)
"""Canonical set of image source type values (lowercase file extensions without dots).

Used by the graph source groups endpoint to filter image-type sources.
"""


class ImageLoader:
    """Image loader that reads metadata via Pillow.

    Returns image metadata (dimensions, format, path). Actual content
    description requires a vision LLM and is performed outside the
    loader by VisionService.
    """

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [
            ".jpg",
            ".JPG",
            ".jpeg",
            ".JPEG",
            ".png",
            ".PNG",
            ".gif",
            ".GIF",
            ".webp",
            ".WEBP",
            ".tiff",
            ".TIFF",
            ".tif",
            ".TIF",
            ".bmp",
            ".BMP",
        ]

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize image loader.

        Args:
            settings: Settings instance (not currently used).
        """
        self.settings = settings

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load image and extract metadata.

        Opens the image with Pillow to read dimensions and format.
        Content description is deferred to VisionService.

        Args:
            filepath: Path to image file.

        Returns:
            List with single document dict containing metadata.
        """
        from PIL import Image

        start_time = time.time()
        filepath_obj = Path(filepath)

        try:
            logger.info("image_loading_started", filepath=filepath)

            image = Image.open(filepath)
            extraction_time = time.time() - start_time

            # Minimal content — vision LLM will provide the real description
            content = f"[Image: {filepath_obj.name} ({image.width}x{image.height})]"

            metadata: dict[str, Any] = {
                "source": str(filepath_obj.absolute()),
                "filename": filepath_obj.name,
                "width": image.width,
                "height": image.height,
                "format": image.format,
                "mode": image.mode,
                "total_characters": len(content),
                "extraction_method": "vision_pending",
                "extraction_time_seconds": round(extraction_time, 3),
                "image_path": str(filepath_obj.absolute()),
            }

            logger.info(
                "image_metadata_extracted",
                image_size=f"{image.width}x{image.height}",
                extraction_time_seconds=round(extraction_time, 2),
            )

            return [{"content": content, "metadata": metadata}]

        except Exception as e:
            logger.exception(
                "image_loading_failed",
                filepath=filepath,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def supports_ocr(self) -> bool:
        """Check if this loader supports OCR.

        Returns:
            False — OCR replaced by vision LLM processing.
        """
        return False


__all__ = ["ImageLoader"]
