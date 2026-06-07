# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Handler Protocol.

Defines the interface that all documentation format handlers must implement.
Handlers process specific documentation formats within extracted archives.

Example:
    from chaoscypher_core.services.sources.loaders.archive.handlers import (
        ArchiveHandler,
    )

    class MyCustomHandler:
        metadata = PluginMetadata(
            name="my_custom",
            version="1.0.0",
            description="Custom archive format.",
            priority=5,
        )

        @property
        def name(self) -> str:
            return "my_custom"

        def can_handle(self, extracted_dir: Path) -> int:
            # Return 0 for no match, higher values for more specific matches
            return 80

        def process(self, extracted_dir: Path, settings: Any) -> list[dict]:
            # Extract documents
            return [{"content": "...", "metadata": {...}}]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.plugins.base import PluginMetadata
    from chaoscypher_core.settings import EngineSettings


class ArchiveHandler(Protocol):
    """Protocol for documentation format handlers.

    Each handler processes a specific documentation format (Sphinx, Markdown, OpenAPI)
    and produces normalized document chunks. Handlers are registered with an
    ``ArchiveHandlerRegistry`` which uses ``can_handle()`` specificity scoring
    to pick exactly one handler per archive.

    Attributes:
        metadata: Plugin descriptor for registry discovery (name, version,
            priority, description). ``metadata.priority`` is only a tiebreaker
            when two handlers return the same specificity score; selection is
            driven primarily by ``can_handle()``.
        name: Handler identifier for logging and detection results.
    """

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin descriptor for registry discovery.

        Returns:
            Metadata describing this handler (name, version, priority, etc.).
        """
        ...

    @property
    def name(self) -> str:
        """Handler identifier (e.g., 'sphinx_html', 'markdown', 'openapi').

        Used for logging and in detection results.
        """
        ...

    def can_handle(self, extracted_dir: Path) -> int:
        """Compute specificity score for this handler against the directory.

        Higher values indicate a more specific/confident match. ``0`` means
        the handler does not apply. The registry picks the handler with the
        highest score (falling back to ``metadata.priority`` as a tiebreaker).

        Handlers whose documentation can live in a subdirectory of the
        archive (Sphinx's ``docs/_build/html/``, MkDocs' ``docs/``) should
        walk the tree here so a nested archive still scores highly.

        Scoring convention:
            * ``0`` — does not apply.
            * ``1-10`` — generic fallback (e.g., always-matches handler).
            * ``50-100`` — format-specific match; higher = stronger evidence.

        Args:
            extracted_dir: Path to the extracted archive contents.

        Returns:
            Non-negative integer specificity score. ``0`` if not applicable.

        Example:
            >>> handler.can_handle(Path("/tmp/extracted"))
            85
        """
        ...

    def find_root(self, extracted_dir: Path) -> Path:
        """Locate the directory that actually contains this handler's docs.

        Many archives nest documentation under a subdirectory — Sphinx
        projects ship source plus ``docs/_build/html/``, MkDocs and
        Docusaurus keep content under ``docs/``. The archive loader calls
        ``find_root`` after :meth:`can_handle` so :meth:`process` sees the
        narrower, format-specific root; file hierarchies, relative paths,
        and static-asset resolution all key off that narrower directory.

        The default contract is identity: if a handler has no notion of a
        narrower root, it returns ``extracted_dir`` unchanged. Overrides
        must stay inside ``extracted_dir`` (never return a path outside it)
        and must cap their subtree walk to a sensible depth to avoid
        scanning pathological archives.

        Args:
            extracted_dir: Path to the extracted archive contents.

        Returns:
            The directory :meth:`process` should treat as the docs root.
            Must be ``extracted_dir`` or one of its descendants.
        """
        ...

    def process(
        self,
        extracted_dir: Path,
        settings: EngineSettings,
    ) -> list[dict[str, Any]]:
        """Process the extracted directory and return document chunks.

        Args:
            extracted_dir: Path to extracted archive contents.
            settings: Engine settings for configuration.

        Returns:
            List of document chunks with 'content' and 'metadata' keys.
            Metadata should include:
              - source: Original file path within archive
              - hierarchy: Path-based hierarchy (e.g., 'docs/api/endpoints')
              - doc_type: Handler name (sphinx_html, markdown, openapi)
              - title: Document/section title if extractable

        Example:
            >>> chunks = handler.process(Path("/tmp/extracted"), settings)
            >>> chunks[0].keys()
            dict_keys(['content', 'metadata'])
        """
        ...


__all__ = ["ArchiveHandler"]
