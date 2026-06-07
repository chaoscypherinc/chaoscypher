# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Facade for document loading with auto-detection.

Provides a simple interface for loading documents without needing to
know which loader to use or how to unwrap the result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.loaders.factory import get_loader_registry


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


class Loaders:
    """Facade for document loading with auto-detection.

    Wraps ``LoaderRegistry`` to provide a simple one-call interface
    that returns plain text from any supported file type.

    Example:
        >>> text = Loaders.load_text("paper.pdf")
        >>> print(text[:100])

    """

    @staticmethod
    def load_text(file_path: str, settings: EngineSettings | None = None) -> str:
        """Load a document and return its text content.

        Auto-detects file type from extension using ``LoaderRegistry``.
        Supports PDF, text, CSV, JSON, audio, video, image, and archive
        formats.

        Args:
            file_path: Path to the document file.
            settings: Optional engine settings. When ``None``, uses
                default ``EngineSettings()``.

        Returns:
            Document text content as a string.

        Raises:
            FileNotFoundError: If file_path doesn't exist.
            ValidationError: If no loader available or document is empty.

        """
        if settings is None:
            from chaoscypher_core.settings import EngineSettings

            settings = EngineSettings()

        registry = get_loader_registry(settings)
        documents: list[dict[str, Any]] = registry.load_document(file_path)

        if not documents:
            msg = f"No content loaded from: {file_path}"
            raise ValidationError(msg, field="content")

        return str(documents[0]["content"])
