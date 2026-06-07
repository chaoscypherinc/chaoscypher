# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Base Loader Protocol.

Defines the interface that all document loaders must implement.
Users can create custom loaders by implementing this protocol and
adding their loader file to the loaders/ directory.

Example Custom Loader:
    ```python
    # loaders/my_custom_loader.py
    from chaoscypher_core.services.sources.loaders.base import BaseLoader
    from chaoscypher_core.plugins import PluginMetadata

    class MyCustomLoader(BaseLoader):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                plugin_id="my_custom",
                name="My Custom Loader",
                description="Loads .xyz and .custom files",
                category="loader",
            )

        @property
        def supported_extensions(self) -> List[str]:
            return ['.xyz', '.custom']

        def load_document(self, filepath: str) -> List[Dict[str, Any]]:
            # Custom loading logic here
            pass
    ```

The loader will be automatically discovered and registered by the LoaderRegistry.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from chaoscypher_core.exceptions import LoaderFileTooLargeError


if TYPE_CHECKING:
    from chaoscypher_core.plugins import PluginMetadata
    from chaoscypher_core.settings import EngineSettings


class BaseLoader(Protocol):
    """Protocol that all document loaders must implement.

    Loaders are auto-discovered from the loaders/ directory and registered
    based on their supported_extensions property.

    Attributes:
        metadata: Plugin metadata (optional, for standardized plugin info).
        supported_extensions: List of file extensions this loader handles (e.g., ['.pdf', '.txt'])

    Methods:
        load_document: Load a document and return raw content
        supports_ocr: Whether this loader supports OCR (optional, defaults to False)

    """

    @property
    def metadata(self) -> PluginMetadata:
        """Get plugin metadata (optional).

        Returns:
            PluginMetadata instance with loader information.

        Note:
            This property is optional for backwards compatibility.
            New loaders should implement it for consistent metadata.
        """
        ...

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports.

        Returns:
            List of extensions (e.g., ['.pdf', '.PDF'])

        Example:
            >>> loader.supported_extensions
            ['.pdf', '.PDF']

        """
        ...

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load a document and return raw content.

        Args:
            filepath: Path to the file to load

        Returns:
            List of document chunks, each with:
                - content: str - The document text
                - metadata: Dict[str, Any] - Document metadata

        Raises:
            FileNotFoundError: If filepath doesn't exist
            ValueError: If file format is invalid

        Example:
            >>> chunks = loader.load_document('/path/to/file.pdf')
            >>> chunks[0].keys()
            dict_keys(['content', 'metadata'])

        """
        ...

    def supports_ocr(self) -> bool:
        """Check if this loader supports OCR for scanned documents.

        Returns:
            True if OCR is supported, False otherwise

        Note:
            Default implementation returns False.
            Override this method if your loader supports OCR.

        """
        return False


def documents_to_dict(documents: list[Any]) -> list[dict[str, Any]]:
    """Convert LangChain Documents to dictionaries.

    Args:
        documents: List of LangChain Document objects with page_content and metadata.

    Returns:
        List of dicts with 'content' and 'metadata' keys.

    """
    return [{"content": doc.page_content, "metadata": doc.metadata} for doc in documents]


def check_loader_file_size(
    filepath: str | Path,
    settings: EngineSettings | None,
) -> None:
    """Reject files larger than ``settings.loader.max_disk_bytes`` before parsing.

    Called from each loader's ``load_document`` BEFORE the heavyweight
    parser (pypdf, python-docx, full-text read) is invoked. A malicious
    or accidental multi-GB upload would otherwise materialise into RAM
    at 5-10x disk size and OOM-kill the worker — the source would then
    be stuck in ``extracting`` with no operator-visible reason.

    No-op when:
    - ``settings`` is None (used in standalone tests or CLI flows that
      construct loaders without a settings instance);
    - ``settings.loader.max_disk_bytes`` is None (cap explicitly
      disabled for trusted single-user deployments).

    Args:
        filepath: Path to the file on disk.
        settings: EngineSettings instance, or None to skip the check.

    Raises:
        LoaderFileTooLargeError: If the file exceeds the configured cap.
    """
    if settings is None or settings.loader.max_disk_bytes is None:
        return
    path = Path(filepath)
    size = path.stat().st_size
    if size > settings.loader.max_disk_bytes:
        raise LoaderFileTooLargeError(
            filename=path.name,
            actual_bytes=size,
            max_bytes=settings.loader.max_disk_bytes,
        )


__all__ = ["BaseLoader", "check_loader_file_size", "documents_to_dict"]
