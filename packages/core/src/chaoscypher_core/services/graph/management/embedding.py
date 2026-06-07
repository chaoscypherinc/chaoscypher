# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Template Embedding Service.

Generates embeddings for templates using the embedding provider.
Enables semantic search over template names and descriptions.
"""

from typing import TYPE_CHECKING

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol

logger = structlog.get_logger(__name__)


class TemplateEmbeddingService:
    """Generates embeddings for templates.

    Uses the configured embedding provider to create vector representations
    of template names and descriptions for semantic search.
    """

    def __init__(self, embedding_service: EmbeddingProviderProtocol) -> None:
        """Initialize with embedding provider.

        Args:
            embedding_service: Embedding provider for generating vectors.

        """
        self._embedding_service = embedding_service

    async def generate_embedding(self, name: str, description: str | None) -> list[float]:
        """Generate embedding for a template's name and description.

        Args:
            name: Template name.
            description: Optional template description.

        Returns:
            List of floats representing the embedding vector.

        """
        text = f"{name}. {description or ''}"
        result = await self._embedding_service.embed(text)

        logger.debug(
            "template_embedding_generated",
            name=name,
            dimensions=len(result.embedding),
        )

        return result.embedding

    def get_embedding_model(self) -> str:
        """Get the name of the configured embedding model.

        Returns:
            Model name string.

        """
        return self._embedding_service.model_name


__all__ = ["TemplateEmbeddingService"]
