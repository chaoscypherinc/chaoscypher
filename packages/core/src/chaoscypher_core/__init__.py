# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""chaoscypher-core: AI-powered knowledge graph engine.

A reusable package for document processing, entity extraction, and
knowledge graph construction. Supports standalone extraction (no database)
and full graph engine with persistent storage.
"""

__version__ = "0.1.0"

from pathlib import Path
from typing import Any

# Embedding providers
from chaoscypher_core.adapters.embedding.factory import create_embedding_provider

# LLM providers
from chaoscypher_core.adapters.llm.factory import ProviderFactory
from chaoscypher_core.adapters.llm.provider import LLMProvider
from chaoscypher_core.adapters.llm.providers.base import BaseLLMProvider

# Storage adapters
from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter

# Bootstrap
from chaoscypher_core.bootstrap import Engine

# Core exceptions
from chaoscypher_core.exceptions import (
    ChaosCypherException,
    ConflictError,
    DataIntegrityError,
    EncryptedPDFError,
    InsufficientStorageError,
    InvalidStateError,
    MaxBytesExceeded,
    NotFoundError,
    OperationError,
    ValidationError,
)

# Namespace facade
from chaoscypher_core.facade import CC, ChaosCypher

# Graph CRUD models and type aliases
from chaoscypher_core.models import (
    AnalysisDepth,
    BatchEmbedResult,
    ChunkingResult,
    ChunksResult,
    DatabaseInfo,
    DatabaseStats,
    Edge,
    EdgeCreate,
    EdgeUpdate,
    EdgeWithNodes,
    EmbedResult,
    EngineSearchResult,
    ExtractionResult,
    HealthReport,
    HealthResult,
    IndexingResult,
    LLMChatResponse,
    Node,
    NodeCreate,
    NodePosition,
    NodeUpdate,
    PaginatedResult,
    ProcessingResult,
    ProgressCallback,
    ProgressStage,
    PropertyDefinition,
    PropertyType,
    RebuildResult,
    SearchMode,
    SourceErrorStage,
    SourceStatus,
    Template,
    TemplateCreate,
    TemplateUpdate,
    TokenUsage,
    ToolResult,
)

# Port protocols (for custom adapter authors)
from chaoscypher_core.ports import (
    ChunkingProtocol,
    ChunkStorageProtocol,
    CitationStorageProtocol,
    EmbeddingProviderProtocol,
    EntityEmbeddingStorageProtocol,
    ExtractionQueueStorageProtocol,
    GraphRepositoryProtocol,
    IndexingProtocol,
    SearchRepositoryProtocol,
    SourceStorageProtocol,
    SourceTagStorageProtocol,
)

# Services
from chaoscypher_core.services.graph.management.edge import EdgeService
from chaoscypher_core.services.graph.management.node import NodeService
from chaoscypher_core.services.graph.management.template import TemplateService
from chaoscypher_core.services.search.engine.index import IndexingService
from chaoscypher_core.services.search.engine.search import SearchService
from chaoscypher_core.services.sources.engine.commit.service import (
    SourceCommitService,
)
from chaoscypher_core.services.sources.engine.extraction.service import (
    ExtractionService,
)

# Document loading
from chaoscypher_core.services.sources.loaders.facade import Loaders

# Tool plugin context
from chaoscypher_core.services.workflows.tools.engine.context import (
    ToolExecutionContext,
)

# Settings
from chaoscypher_core.settings import EngineSettings, LLMSettings

# Utilities
from chaoscypher_core.utils import generate_id

# RAG pipeline
from chaoscypher_core.utils.chunk import ChunkingService


# ============================================================================
# Module-level convenience functions
# ============================================================================


async def extract(
    source: str | Path | None = None,
    *,
    text: str | None = None,
    analysis_depth: AnalysisDepth = "full",
) -> ExtractionResult:
    """Extract entities and relationships from a file or raw text.

    Pass a file path as ``source`` (auto-detected) or explicit text via
    the ``text`` keyword argument to skip file detection entirely.

    Args:
        source: File path (PDF, text, CSV, etc.) or raw text string.
            Auto-detects whether the string is a path or text content.
        text: Explicit text input — skips file detection. Mutually
            exclusive with ``source``.
        analysis_depth: ``"full"`` (all chunks) or ``"quick"`` (sampled).

    Returns:
        ExtractionResult with entities, relationships, domain, and confidence.

    Raises:
        ValueError: If both ``source`` and ``text`` are provided, or neither.

    Example:
        >>> result = await extract("paper.pdf")           # file path
        >>> result = await extract(text="Raw content...")  # explicit text

    """
    if source is not None and text is not None:
        raise ValueError("Pass either 'source' or 'text', not both.")
    if source is None and text is None:
        raise ValueError("Pass either 'source' (file path or text) or text='...'.")

    if text is None:
        path = Path(source) if not isinstance(source, Path) else source  # type: ignore[arg-type]
        text = Loaders.load_text(str(path)) if path.exists() else str(source)

    from chaoscypher_core.facade import _get_default_settings

    return await ChunkingService(_get_default_settings()).process(
        text, analysis_depth=analysis_depth
    )


async def chat(
    messages: str | list[dict[str, Any]],
    *,
    stream: bool = False,
    **kwargs: Any,
) -> LLMChatResponse:
    """Quick LLM chat with default settings.

    Accepts a plain string (auto-wrapped as a user message) or a full
    message list for multi-turn conversations.

    Args:
        messages: A string prompt or list of message dicts.
        stream: Whether to stream the response.
        **kwargs: Forwarded to LLMProvider.chat() (temperature,
            max_tokens, enable_thinking, etc.).

    Returns:
        LLMChatResponse with content, tool_calls, usage, and provider info.

    Example:
        >>> from chaoscypher_core import chat
        >>> response = await chat("What is a knowledge graph?")
        >>> print(response.content)

    """
    from chaoscypher_core.facade import _get_default_settings

    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    return await LLMProvider(settings=_get_default_settings()).chat(
        messages, stream=stream, **kwargs
    )


async def embed(
    text: str | list[str],
    *,
    model: str | None = None,
    **kwargs: Any,
) -> EmbedResult | BatchEmbedResult:
    """Quick embedding with default settings.

    Accepts a single string or a list of strings for batch embedding.

    Args:
        text: Text string or list of text strings to embed.
        model: Override embedding model (e.g., "BAAI/bge-large-en-v1.5").
            When omitted, uses the default from settings or configure().
        **kwargs: Forwarded to embedding provider methods.

    Returns:
        EmbedResult for a single string, BatchEmbedResult for a list.

    Example:
        >>> from chaoscypher_core import embed
        >>> result = await embed("quantum entanglement")
        >>> print(f"Dimensions: {len(result.embedding)}")

    """
    from chaoscypher_core.facade import _get_default_settings

    settings = _get_default_settings()
    if model is not None:
        settings = settings.model_copy(
            update={"embedding": {**settings.embedding.model_dump(), "model": model}},
        )
    provider = create_embedding_provider(settings)
    if isinstance(text, list):
        return await provider.batch_embed(text, **kwargs)
    return await provider.embed(text, **kwargs)


async def embed_batch(
    texts: list[str],
    *,
    model: str | None = None,
    **kwargs: Any,
) -> BatchEmbedResult:
    """Batch embedding with unambiguous return type.

    Unlike :func:`embed` which returns a union type, this always returns
    :class:`BatchEmbedResult` — no runtime type checking needed.

    Args:
        texts: List of text strings to embed.
        model: Override embedding model (e.g., "BAAI/bge-large-en-v1.5").
            When omitted, uses the default from settings or configure().
        **kwargs: Forwarded to embedding provider's batch_embed().

    Returns:
        BatchEmbedResult with embeddings list, counts, and provider.

    Example:
        >>> from chaoscypher_core import embed_batch
        >>> result = await embed_batch(["text one", "text two"])
        >>> print(f"{len(result.embeddings)} embeddings")

    """
    from chaoscypher_core.facade import _get_default_settings

    settings = _get_default_settings()
    if model is not None:
        settings = settings.model_copy(
            update={"embedding": {**settings.embedding.model_dump(), "model": model}},
        )
    provider = create_embedding_provider(settings)
    return await provider.batch_embed(texts, **kwargs)


async def search(
    query: str,
    *,
    database: str = "default",
    data_dir: str | Path | None = None,
    limit: int = 10,
    mode: SearchMode = "hybrid",
) -> list[EngineSearchResult]:
    """Search an existing knowledge graph database.

    Creates a temporary Engine to query the database, then cleans up.
    For repeated searches, use ``Engine`` directly for better performance.

    Args:
        query: Search query string.
        database: Database name (default: "default").
        data_dir: Explicit path to the database directory. If omitted,
            resolves using XDG data dir + database name.
        limit: Maximum number of results.
        mode: Search mode — 'hybrid' (default), 'semantic', or 'keyword'.

    Returns:
        List of EngineSearchResult models sorted by relevance score.

    Example:
        >>> from chaoscypher_core import search
        >>> results = await search("quantum entanglement", database="demo")
        >>> for r in results:
        ...     print(f"{r.label} ({r.score:.2f})")

    """
    from chaoscypher_core.facade import _get_default_settings

    if data_dir is None:
        _settings = _get_default_settings()
        data_dir = Path(_settings.paths.data_dir) / "databases" / database

    with Engine(data_dir, database=database, initialize_db=False) as engine:
        return await engine.search(query, limit=limit, mode=mode)


async def chunk(
    source: str | Path | None = None,
    *,
    text: str | None = None,
    analysis_depth: AnalysisDepth = "full",
) -> ChunksResult:
    """Chunk a file or raw text into hierarchical segments for RAG.

    Pass a file path as ``source`` (auto-detected) or explicit text via
    the ``text`` keyword argument to skip file detection entirely.

    Args:
        source: File path (PDF, text, CSV, etc.) or raw text string.
            Auto-detects whether the string is a path or text content.
        text: Explicit text input — skips file detection. Mutually
            exclusive with ``source``.
        analysis_depth: ``"full"`` (all chunks) or ``"quick"`` (sampled).

    Returns:
        ChunksResult with chunk data, totals, and group structure.

    Raises:
        ValueError: If both ``source`` and ``text`` are provided, or neither.

    Example:
        >>> result = await chunk("paper.pdf")               # file path
        >>> result = await chunk(text="Raw content...")      # explicit text

    """
    if source is not None and text is not None:
        raise ValueError("Pass either 'source' or 'text', not both.")
    if source is None and text is None:
        raise ValueError("Pass either 'source' (file path or text) or text='...'.")

    if text is None:
        path = Path(source) if not isinstance(source, Path) else source  # type: ignore[arg-type]
        text = Loaders.load_text(str(path)) if path.exists() else str(source)

    from chaoscypher_core.facade import _get_default_settings

    return await ChunkingService(_get_default_settings()).create_chunks(
        text, analysis_depth=analysis_depth
    )


async def add_document(
    filepath: str | Path,
    *,
    database: str = "default",
    data_dir: str | Path | None = None,
    analysis_depth: AnalysisDepth = "full",
    on_progress: ProgressCallback | None = None,
) -> ProcessingResult:
    """Load a file and process it into a knowledge graph.

    Creates a temporary Engine, runs the full extraction pipeline
    (chunk, index, extract, commit), then cleans up.

    Args:
        filepath: Path to the document file.
        database: Database name (default: "default").
        data_dir: Explicit path to the database directory. If omitted,
            resolves using XDG data dir + database name.
        analysis_depth: 'full' (default) or 'quick'.
        on_progress: Optional callback invoked after each pipeline stage
            (chunking, indexing, extraction) with the stage name and its
            typed result. Same contract as Engine.add_document().

    Returns:
        ProcessingResult with source_id and lists of created node,
        edge, and template IDs.

    Example:
        >>> from chaoscypher_core import add_document
        >>> result = await add_document("paper.pdf", database="demo")
        >>> print(f"Created {len(result.nodes)} nodes")

    """
    from chaoscypher_core.facade import _get_default_settings

    if data_dir is None:
        _settings = _get_default_settings()
        data_dir = Path(_settings.paths.data_dir) / "databases" / database

    with Engine(data_dir, database=database, initialize_db=True) as engine:
        return await engine.add_document(
            filepath, analysis_depth=analysis_depth, on_progress=on_progress
        )


async def add_documents(
    paths: str | list[str | Path],
    *,
    database: str = "default",
    data_dir: str | Path | None = None,
    on_document_complete: Any | None = None,
) -> list[ProcessingResult]:
    """Load and process multiple documents into a knowledge graph.

    Creates a single Engine for all documents (efficient for batches).
    Accepts a glob pattern or a list of file paths.

    Args:
        paths: Glob pattern string (e.g., ``"docs/*.pdf"``) or list of
            file paths.
        database: Database name (default: "default").
        data_dir: Explicit path to the database directory. If omitted,
            resolves using XDG data dir + database name.
        on_document_complete: Optional callback ``(filename, result) -> None``
            invoked after each document.

    Returns:
        List of ProcessingResult models, one per document.

    Example:
        >>> from chaoscypher_core import add_documents
        >>> results = await add_documents(["doc1.pdf", "doc2.pdf"])
        >>> print(f"Processed {len(results)} documents")

    """
    from chaoscypher_core.facade import _get_default_settings

    if data_dir is None:
        _settings = _get_default_settings()
        data_dir = Path(_settings.paths.data_dir) / "databases" / database

    with Engine(data_dir, database=database, initialize_db=True) as engine:
        return await engine.add_documents(paths, on_document_complete=on_document_complete)


# ============================================================================
# Synchronous convenience wrappers
# ============================================================================


def extract_sync(
    source: str | Path,
    **kwargs: Any,
) -> ExtractionResult:
    """Synchronous wrapper for extract().

    Runs the async extract() function in a new event loop.
    Ideal for scripts, notebooks, and non-async contexts.

    Args:
        source: File path or raw text string.
        **kwargs: Forwarded to extract() (analysis_depth, etc.).

    Returns:
        ExtractionResult with entities, relationships, domain, and confidence.

    Example:
        >>> from chaoscypher_core import extract_sync
        >>> result = extract_sync("paper.pdf")
        >>> print(f"{len(result.entities)} entities found")

    """
    import asyncio

    return asyncio.run(extract(source, **kwargs))


def chat_sync(
    messages: str | list[dict[str, Any]],
    **kwargs: Any,
) -> LLMChatResponse:
    """Synchronous wrapper for chat().

    Runs the async chat() function in a new event loop.

    Args:
        messages: A string prompt or list of message dicts.
        **kwargs: Forwarded to chat() (stream, temperature, etc.).

    Returns:
        LLMChatResponse with content, tool_calls, usage, and provider info.

    Example:
        >>> from chaoscypher_core import chat_sync
        >>> response = chat_sync("What is a knowledge graph?")
        >>> print(response.content)

    """
    import asyncio

    return asyncio.run(chat(messages, **kwargs))


def embed_sync(
    text: str | list[str],
    *,
    model: str | None = None,
    **kwargs: Any,
) -> EmbedResult | BatchEmbedResult:
    """Synchronous wrapper for embed().

    Runs the async embed() function in a new event loop.

    Args:
        text: Text string or list of text strings to embed.
        model: Override embedding model (e.g., "BAAI/bge-large-en-v1.5").
        **kwargs: Forwarded to embed().

    Returns:
        EmbedResult for a single string, BatchEmbedResult for a list.

    Example:
        >>> from chaoscypher_core import embed_sync
        >>> result = embed_sync("quantum entanglement")
        >>> print(f"Dimensions: {len(result.embedding)}")

    """
    import asyncio

    if model is not None:
        kwargs["model"] = model
    return asyncio.run(embed(text, **kwargs))


def embed_batch_sync(
    texts: list[str],
    *,
    model: str | None = None,
    **kwargs: Any,
) -> BatchEmbedResult:
    """Synchronous wrapper for embed_batch().

    Args:
        texts: List of text strings to embed.
        model: Override embedding model.
        **kwargs: Forwarded to embed_batch().

    Returns:
        BatchEmbedResult with embeddings list, counts, and provider.

    """
    import asyncio

    return asyncio.run(embed_batch(texts, model=model, **kwargs))


def search_sync(
    query: str,
    **kwargs: Any,
) -> list[EngineSearchResult]:
    """Synchronous wrapper for search().

    Args:
        query: Search query string.
        **kwargs: Forwarded to search() (database, data_dir, limit, mode).

    Returns:
        List of EngineSearchResult models sorted by relevance score.

    """
    import asyncio

    return asyncio.run(search(query, **kwargs))


def add_document_sync(
    filepath: str | Path,
    **kwargs: Any,
) -> ProcessingResult:
    """Synchronous wrapper for add_document().

    Args:
        filepath: Path to the document file.
        **kwargs: Forwarded to add_document() (database, data_dir,
            analysis_depth, on_progress).

    Returns:
        ProcessingResult with source_id and created entity IDs.

    """
    import asyncio

    return asyncio.run(add_document(filepath, **kwargs))


def add_documents_sync(
    paths: str | list[str | Path],
    **kwargs: Any,
) -> list[ProcessingResult]:
    """Synchronous wrapper for add_documents().

    Args:
        paths: Glob pattern string or list of file paths.
        **kwargs: Forwarded to add_documents() (database, data_dir,
            on_document_complete).

    Returns:
        List of ProcessingResult models, one per document.

    """
    import asyncio

    return asyncio.run(add_documents(paths, **kwargs))


def chunk_sync(
    source: str | Path,
    **kwargs: Any,
) -> ChunksResult:
    """Synchronous wrapper for chunk().

    Args:
        source: File path or raw text string.
        **kwargs: Forwarded to chunk() (analysis_depth, etc.).

    Returns:
        ChunksResult with chunk data, totals, and group structure.

    """
    import asyncio

    return asyncio.run(chunk(source, **kwargs))


__all__ = [  # noqa: RUF022 — intentionally grouped by tier, not sorted
    # --- Primary API (start here) ---
    "CC",
    "ChaosCypher",
    "Engine",
    # Async convenience functions
    "extract",
    "chat",
    "embed",
    "embed_batch",
    "search",
    "add_document",
    "add_documents",
    "chunk",
    # Sync convenience functions (no asyncio needed)
    "extract_sync",
    "chat_sync",
    "embed_sync",
    "embed_batch_sync",
    "search_sync",
    "add_document_sync",
    "add_documents_sync",
    "chunk_sync",
    # --- Configuration ---
    "EngineSettings",
    "LLMSettings",
    # --- Models (input DTOs) ---
    "NodeCreate",
    "NodeUpdate",
    "NodePosition",
    "EdgeCreate",
    "EdgeUpdate",
    "EdgeWithNodes",
    "TemplateCreate",
    "TemplateUpdate",
    "PropertyDefinition",
    "PropertyType",
    "AnalysisDepth",
    "SearchMode",
    "ProgressCallback",
    "ProgressStage",
    # --- Models (output DTOs) ---
    "Node",
    "Edge",
    "EdgeWithNodes",
    "Template",
    "ExtractionResult",
    "ProcessingResult",
    "LLMChatResponse",
    "EmbedResult",
    "BatchEmbedResult",
    "EngineSearchResult",
    "ChunksResult",
    "ChunkingResult",
    "IndexingResult",
    "RebuildResult",
    "PaginatedResult",
    "DatabaseStats",
    "DatabaseInfo",
    "HealthReport",
    "HealthResult",
    "TokenUsage",
    "ToolResult",
    "SourceErrorStage",
    "SourceStatus",
    # --- Services (for Engine direct usage) ---
    "NodeService",
    "EdgeService",
    "TemplateService",
    "IndexingService",
    "SearchService",
    "ExtractionService",
    "SourceCommitService",
    "ChunkingService",
    "Loaders",
    # --- Exceptions ---
    "ChaosCypherException",
    "ConflictError",
    "DataIntegrityError",
    "EncryptedPDFError",
    "InsufficientStorageError",
    "InvalidStateError",
    "MaxBytesExceeded",
    "NotFoundError",
    "OperationError",
    "ValidationError",
    # --- Advanced (adapters, protocols, factories) ---
    "LLMProvider",
    "BaseLLMProvider",
    "ProviderFactory",
    "SqliteAdapter",
    "create_embedding_provider",
    "ChunkingProtocol",
    "ChunkStorageProtocol",
    "CitationStorageProtocol",
    "EmbeddingProviderProtocol",
    "EntityEmbeddingStorageProtocol",
    "ExtractionQueueStorageProtocol",
    "GraphRepositoryProtocol",
    "IndexingProtocol",
    "SearchRepositoryProtocol",
    "SourceStorageProtocol",
    "SourceTagStorageProtocol",
    "ToolExecutionContext",
    "generate_id",
]
