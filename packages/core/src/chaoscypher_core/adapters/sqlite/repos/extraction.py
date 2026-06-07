# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Data access layer for entity embeddings.

Repository handles CRUD operations for ``SourceEntityEmbedding``. Lives
under the SQLite adapter because the method binds to the adapter's
SQLModel entity directly. Callers type-hint on this concrete class (or,
if a narrower port is introduced later, on the port protocol) and get a
concrete instance via dependency injection at the composition root.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog

from chaoscypher_core.adapters.sqlite.models import SourceEntityEmbedding
from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.safe_session import SafeSession


logger = structlog.get_logger(__name__)


class ExtractionRepository:
    """Data access layer for extraction entity embeddings.

    Handles storage and retrieval of entity embeddings generated during
    the extraction process. Embeddings are stored as base64-encoded
    float32 arrays for security and portability.

    Example:
        repo = ExtractionRepository(session, "default")
        repo.store_entity_embeddings(
            source_id="src_123",
            entity_metadata=[{"entity_index": 0, "entity_id": "entity_1"}],
            embeddings=[np.array([0.1, 0.2, ...])],
            embedding_model="text-embedding-3-small",
            embedding_dimensions=1536
        )

    """

    def __init__(self, session: SafeSession, database_name: str):
        """Initialize extraction repository.

        Args:
            session: SafeSession providing maybe_commit() transaction coordination.
            database_name: Current database name

        """
        self.session = session
        self.database_name = database_name

    def store_entity_embeddings(
        self,
        source_id: str,
        entity_metadata: list[dict[str, Any]],
        embeddings: list[np.ndarray],
        embedding_model: str,
        embedding_dimensions: int,
    ) -> list[dict[str, Any]]:
        """Store entity embeddings for a source and return dict projections.

        Embeddings are base64-encoded for safe serialisation and stored
        as BLOB in SQLite.

        Args:
            source_id: Source ID
            entity_metadata: List of entity metadata dicts with entity_index, entity_id
            embeddings: List of numpy embedding arrays
            embedding_model: Embedding model name
            embedding_dimensions: Embedding dimensions

        Returns:
            List of dict projections of the stored rows: ``id``,
            ``source_id``, ``entity_index``, ``entity_id``,
            ``embedding_model``, ``embedding_dimensions``, ``created_at``.
            The raw ``embedding`` bytes are intentionally omitted — callers
            that need the embedding fetch it by ``entity_id`` separately.

        Raises:
            ValueError: If metadata count doesn't match embeddings count

        """
        if len(entity_metadata) != len(embeddings):
            msg = f"Metadata count ({len(entity_metadata)}) must match embeddings count ({len(embeddings)})"
            raise ValueError(msg)

        records: list[dict[str, Any]] = []
        for metadata, embedding in zip(entity_metadata, embeddings, strict=False):
            # Accept either np.ndarray (the typed signature, normal path)
            # or list[float] (the cached-embeddings path, where the
            # extractor surfaces the values it loaded out of
            # ``chunk_extraction_tasks.raw_entity_embeddings`` JSON without
            # re-coercing). ``np.asarray`` is a no-op for an ndarray of the
            # right dtype and a copy-and-convert for lists.
            embedding_array = np.asarray(embedding, dtype=np.float32)
            embedding_bytes = base64.b64encode(embedding_array.tobytes())

            created_at = datetime.now(UTC)
            record_id = generate_id("emb")
            row = SourceEntityEmbedding(
                id=record_id,
                source_id=source_id,
                entity_index=metadata["entity_index"],
                entity_id=metadata.get("entity_id"),
                embedding=embedding_bytes,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
                created_at=created_at,
            )
            self.session.add(row)
            records.append(
                {
                    "id": record_id,
                    "source_id": source_id,
                    "entity_index": metadata["entity_index"],
                    "entity_id": metadata.get("entity_id"),
                    "embedding_model": embedding_model,
                    "embedding_dimensions": embedding_dimensions,
                    "created_at": created_at,
                }
            )

        self.session.maybe_commit()
        logger.info(
            "entity_embeddings_stored",
            embedding_count=len(records),
            source_id=source_id,
        )
        return records
