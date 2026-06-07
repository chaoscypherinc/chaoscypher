# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chunking protocol interface for chaoscypher-engine.

Defines Protocol for hierarchical chunk operations.
Used by ExtractionService and ChunkingService for smart document chunking.
Main app implements this via an adapter that wraps its chunking repository.
"""

from typing import Any, Protocol


class ChunkingProtocol(Protocol):
    """Interface for chunking operations.

    Provides access to hierarchical chunk groups for
    smart entity extraction. Hierarchical groups combine
    small RAG chunks into larger semantic units.

    Used by:
    - ExtractionService: Prefer hierarchical chunking over legacy text splitting
    - ChunkingService: Store and retrieve chunks for import files
    """

    def store_chunks_and_groups(
        self,
        small_chunks: list[dict[str, Any]],
        hierarchical_groups: list[dict[str, Any]],
        batch_size: int = 500,
    ) -> None:
        """Store small chunks and hierarchical group metadata.

        Args:
            small_chunks: List of chunk dictionaries with keys:
                - id, source_id, database_name, chunk_index
                - content, embedding, char_start, char_end
                - chunk_metadata, status, created_at
            hierarchical_groups: List of group dictionaries with keys:
                - id, group_index, small_chunk_ids
                - combined_content, char_start, char_end, token_count
            batch_size: Number of chunks to insert per batch

        Notes:
            - Implementation may store groups in chunk metadata
            - Chunks are stored in 'staged' status (not searchable yet)
            - No embeddings are generated (done at index time)

        """
        ...

    def get_small_chunks(self, source_id: str) -> list[dict[str, Any]]:
        """Get all small chunks for a source (for RAG indexing).

        Args:
            source_id: Source identifier

        Returns:
            List of chunk dictionaries with keys:
                - id, chunk_index, content, embedding
                - embedding_model, embedding_dimensions, status

        Notes:
            - Only returns chunks with chunk_type='small' in metadata
            - Ordered by chunk_index
            - Used by indexing service to generate embeddings

        """
        ...

    def get_hierarchical_groups(self, source_id: str) -> list[dict[str, Any]]:
        """Get hierarchical chunk groups for a source.

        Hierarchical groups combine small chunks into larger
        semantic units for better entity extraction. Each group
        represents a semantic section (paragraph, heading + content, etc.)

        Args:
            source_id: Source identifier

        Returns:
            List of group dicts with keys:
                - id, group_index, small_chunk_ids
                - combined_content, char_start, char_end, token_count

        Notes:
            - Groups are ordered by group_index
            - Each group references 3+ small chunks
            - Used by ExtractionService for entity extraction

        Example:
            groups = chunking_repo.get_hierarchical_groups("source_123")

            if groups:
                print(f"Using {len(groups)} hierarchical groups")
                for group in groups:
                    text = group['combined_content']
                    chunk_ids = group['small_chunk_ids']
                    print(f"Group from {len(chunk_ids)} chunks: {len(text)} chars")
            else:
                print("No hierarchical groups - using legacy chunking")

        """
        ...

    def update_chunk_status(self, source_id: str, status: str) -> int:
        """Update status for all chunks of a source.

        Args:
            source_id: Source identifier
            status: New status ('staged', 'indexed', 'committed')

        Returns:
            Number of chunks updated

        Notes:
            - Changes status for all chunks (small + groups)
            - Used during import lifecycle transitions
            - 'committed' status makes chunks searchable

        """
        ...
