# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Index one source row in the MCP server's process: load, normalize, chunk, embed.

Shared by ``add_document``'s index-only pipeline and ``add_site``, which
indexes each page a site import left ``pending``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_core.models import SourceStatus


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine


async def index_source_text(
    engine: Engine,
    *,
    file_id: str,
    file_path: str,
    is_url: bool,
    text: str,
    file_type: str,
    enable_normalization: bool | None,
) -> dict[str, Any]:
    """Load, normalize, chunk and index one source row that is already 'indexing'."""
    from pathlib import Path

    from chaoscypher_core.services.sources.loaders.facade import Loaders

    # Load text (files only — URL text already fetched above)
    if not is_url:
        text = Loaders.load_text(str(file_path), settings=engine.settings)

    # Resolve the tri-state ``enable_normalization`` the same way the CLI
    # (sources/service.py) and Cortex (indexing_handler.py) do — ``None``
    # defers to ``resolve_normalization_default(filename)``, which returns True
    # for prose and False for structured formats (CSV / TSV / JSON / JSONL /
    # NDJSON / XML). Pre-fix the MCP default was hardcoded True, which
    # corrupted structured uploads by stripping their whitespace as OCR noise.
    if enable_normalization is None:
        from chaoscypher_core.utils.normalization_default import (
            resolve_normalization_default,
        )

        enable_normalization = resolve_normalization_default(
            filename=Path(file_path).name if file_path else ""
        )

    # Normalize content if enabled
    if enable_normalization and text:
        from chaoscypher_core.services.sources.normalizer.service import (
            ContentNormalizerService,
        )

        normalizer = ContentNormalizerService(settings=engine.settings)
        normalized = normalizer.normalize(content=text, content_type=file_type)
        text = normalized.content

    # Chunk
    await engine.chunking_service.create_chunks(full_text=text, source_id=file_id)

    # Index (generate embeddings)
    index_result = await engine.indexing_service.create_index(source_id=file_id)

    # Complete indexing with proper metadata
    engine.storage_adapter.complete_indexing(
        file_id,
        chunks_count=index_result.get("chunks_count", 0),
        embedding_model=index_result.get("embedding_model", ""),
        embedding_dimensions=index_result.get("embedding_dimensions", 0),
    )

    return {
        "source_id": file_id,
        "status": SourceStatus.INDEXED,
        "nodes": [],
        "edges": [],
        "templates": [],
    }


__all__ = ["index_source_text"]
