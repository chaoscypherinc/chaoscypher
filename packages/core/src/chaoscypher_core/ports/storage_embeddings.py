# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""EntityEmbeddingStorageProtocol for ChaosCypher storage.

Split from the original SourceStorageProtocol god-protocol.
Covers SourceEntityEmbedding table operations — a self-contained concern
separate from the SourceRow model.
Binds to SourceIndexingMixin in the SQLite adapter.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class EntityEmbeddingStorageProtocol(Protocol):
    """Storage protocol for entity embedding operations.

    Handles persistence and retrieval of per-entity vector embeddings
    generated during the entity extraction phase.
    """

    def store_entity_embeddings(
        self,
        source_id: str,
        embeddings_data: list[dict[str, Any]],
        embedding_model: str,
        embedding_dimensions: int,
    ) -> None:
        """Store entity embeddings for source.

        Args:
            source_id: The source processing file ID.
            embeddings_data: List of embedding dictionaries, each containing
                ``entity_index``, optionally ``entity_id``, and ``embedding``
                (a float list or numpy array).
            embedding_model: Name of the embedding model used.
            embedding_dimensions: Dimensions of the embeddings.

        """
        ...

    def get_entity_embeddings(self, source_id: str) -> list[dict[str, Any]]:
        """Get entity embeddings for source.

        Args:
            source_id: The source processing file ID.

        Returns:
            List of embedding dicts with keys: source_id, entity_index,
            entity_id, embedding (list of floats).

        """
        ...

    # ------------------------------------------------------------------
    # Bulk / reset operations (PR2a Task 5).
    # Consumed by the reset services moving into core in PR2b.
    # ------------------------------------------------------------------

    def count_embeddings(self) -> int:
        """Count all SourceEntityEmbedding rows.

        Returns:
            Non-negative integer count.
        """
        ...

    def clear_all_embeddings(self) -> int:
        """Delete every SourceEntityEmbedding row.

        Returns:
            Number of rows deleted.
        """
        ...
