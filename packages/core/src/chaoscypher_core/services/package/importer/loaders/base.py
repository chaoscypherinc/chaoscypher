# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Base Loader - Protocol definition for content loaders.

Defines the interface that all content loaders must implement.

Example:
    from chaoscypher_core.services.package.importer.loaders.base import PackageLoaderBase

    class MyLoader(PackageLoaderBase):
        def load(self, data: dict, mapper: IdMapper, stats: ImportStats) -> None:
            # Implementation
            pass
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core.services.package.importer.models import IdMapper, ImportStats


class PackageLoaderBase(ABC):
    """Abstract base class for content loaders.

    All content loaders must implement the load() method which takes
    the parsed content data, an ID mapper for tracking ID transformations,
    and a stats object for recording import statistics.

    Subclasses:
        - TemplateLoader: Loads templates
        - KnowledgeLoader: Loads knowledge nodes and edges
        - WorkflowLoader: Loads workflows and triggers
        - SourceLoader: Loads sources, chunks, citations
    """

    @abstractmethod
    def load(
        self,
        data: dict[str, Any] | list[dict[str, Any]],
        mapper: IdMapper,
        stats: ImportStats,
        database_name: str,
    ) -> None:
        """Load content from parsed data.

        Args:
            data: Parsed content data (structure depends on content type).
            mapper: IdMapper for tracking ID transformations.
            stats: ImportStats for recording statistics.
            database_name: Target database name for import.

        Raises:
            ImportError: If content loading fails.
        """
        ...


__all__ = ["PackageLoaderBase"]
