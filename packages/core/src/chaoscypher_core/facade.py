# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chaos Cypher namespace facade — zero-boilerplate entry point.

Provides a single static class that consolidates all convenience functions.
Import once, discover everything via IDE autocomplete.

Usage:
    from chaoscypher_core import ChaosCypher

    # Configure once (optional — defaults to Ollama, env vars auto-detected)
    ChaosCypher.configure(provider="openai", api_key="sk-...")

    result = await ChaosCypher.extract("paper.pdf")
    response = await ChaosCypher.chat("What is a knowledge graph?")
    embedding = await ChaosCypher.embed("quantum entanglement")
    results = await ChaosCypher.search("quantum", database="demo")

    # Sync variants (no async/await needed)
    result = ChaosCypher.extract_sync("paper.pdf")
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_core.models import (
        BatchEmbedResult,
        ChunksResult,
        EmbedResult,
        EngineSearchResult,
        ExtractionResult,
        LLMChatResponse,
        ProcessingResult,
    )
    from chaoscypher_core.settings import EngineSettings


# API key prefix → provider mapping for auto-detection.
# Order matters: longer prefixes MUST come first (sk-ant- before sk-).
_KEY_PREFIXES: list[tuple[str, str]] = [
    ("sk-ant-", "anthropic"),
    ("sk-", "openai"),
]

# Providers that run locally and don't accept API keys
_LOCAL_PROVIDERS = frozenset({"ollama"})

# Lazily resolved from LLMSettings.model_fields to stay in sync automatically.
_known_llm_fields: frozenset[str] | None = None

# Top-level aliases for configure() — maps user-friendly names to nested settings paths.
# Format: "alias_name" -> ("settings_group", "field_name")
# All targets must be valid EngineSettings fields (core, not cortex).
_CONFIGURE_ALIASES: dict[str, tuple[str, str]] = {
    "embedding_model": ("embedding", "model"),
    "embedding_provider": ("embedding", "provider"),
    "chunk_size": ("chunking", "small_chunk_size"),
    "chunk_overlap": ("chunking", "small_chunk_overlap"),
    "extraction_depth": ("source_processing", "source_processing_analysis_depth"),
    "vector_dimensions": ("search", "vector_dimensions"),
    "auto_extract": ("source_processing", "auto_extract_entities"),
}


def _get_known_llm_fields() -> frozenset[str]:
    """Return the set of valid LLMSettings field names (cached)."""
    global _known_llm_fields
    if _known_llm_fields is None:
        from chaoscypher_core.settings import LLMSettings as _LLMSettings

        _known_llm_fields = frozenset(_LLMSettings.model_fields.keys())
    return _known_llm_fields


# Module-level cached settings (set via ChaosCypher.configure())
_default_settings: EngineSettings | None = None


def _get_default_settings() -> EngineSettings:
    """Return cached settings or a fresh EngineSettings instance.

    Used by convenience functions to respect ChaosCypher.configure().
    When no settings are cached, creates a new EngineSettings() which
    auto-detects env vars (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.).
    """
    if _default_settings is not None:
        return _default_settings
    from chaoscypher_core.settings import EngineSettings as _EngineSettings

    return _EngineSettings()


def _build_settings_from_kwargs(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    **kwargs: Any,
) -> EngineSettings:
    """Build EngineSettings from user-friendly keyword arguments.

    Shared by :meth:`ChaosCypher.configure` and :class:`Engine.__init__`.
    Resolves top-level aliases (``embedding_model``, ``chunk_size``, etc.),
    auto-detects provider from API key prefix, and constructs a properly
    nested :class:`EngineSettings`.

    Args:
        provider: LLM provider name ("openai", "anthropic", "gemini", "ollama").
        api_key: API key for the selected provider.
        **kwargs: LLMSettings fields or top-level aliases from
            ``_CONFIGURE_ALIASES``.

    Returns:
        Configured EngineSettings instance.

    Raises:
        ValueError: If ``api_key`` prefix is unrecognised or unknown kwargs
            are passed.

    """
    from chaoscypher_core.settings import EngineSettings as _EngineSettings

    # Separate alias kwargs from LLM kwargs
    nested_overrides: dict[str, dict[str, Any]] = {}
    llm_extra: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _CONFIGURE_ALIASES:
            group, field = _CONFIGURE_ALIASES[key]
            nested_overrides.setdefault(group, {})[field] = value
        else:
            llm_extra[key] = value

    # Validate remaining kwargs against known LLMSettings fields
    known = _get_known_llm_fields()
    unknown = set(llm_extra) - known
    if unknown:
        msg = (
            f"Unknown configure() arguments: {sorted(unknown)}. "
            f"See LLMSettings for valid fields, or use top-level aliases: "
            f"{sorted(_CONFIGURE_ALIASES.keys())}."
        )
        raise ValueError(msg)

    llm_kwargs: dict[str, Any] = {}

    # Auto-detect provider from API key prefix if not explicitly given
    if api_key and not provider:
        for prefix, detected_provider in _KEY_PREFIXES:
            if api_key.startswith(prefix):
                provider = detected_provider
                break
        else:
            raise ValueError(
                "Cannot detect provider from api_key prefix. "
                "Pass provider='openai' (or 'anthropic', 'gemini') explicitly."
            )

    if provider:
        llm_kwargs["chat_provider"] = provider

    if api_key and provider:
        if provider in _LOCAL_PROVIDERS:
            warnings.warn(
                f"Provider '{provider}' runs locally and does not use API keys. "
                "The api_key argument will be ignored.",
                UserWarning,
                stacklevel=3,
            )
        else:
            key_field = {
                "openai": "openai_api_key",
                "anthropic": "anthropic_api_key",
                "gemini": "gemini_api_key",
            }.get(provider)
            if key_field:
                llm_kwargs[key_field] = api_key

    llm_kwargs.update(llm_extra)

    # Build settings with all overrides
    settings_kwargs: dict[str, Any] = {}
    if llm_kwargs:
        settings_kwargs["llm"] = llm_kwargs
    settings_kwargs.update(nested_overrides)

    return _EngineSettings(**settings_kwargs) if settings_kwargs else _EngineSettings()


class ChaosCypher:
    """Zero-boilerplate namespace for ChaosCypher convenience functions.

    All methods are static — no instantiation needed. Each delegates
    to the corresponding module-level function in ``chaoscypher_core``.

    Configuration:
        configure: Set global LLM provider/API key for all convenience functions.
        reset: Clear cached settings.

    Async methods:
        extract, chat, embed, embed_batch, search, add_document, add_documents, chunk

    Sync methods:
        extract_sync, chat_sync, embed_sync, embed_batch_sync, search_sync,
        add_document_sync, add_documents_sync, chunk_sync

    Utilities:
        load: Load a file and return text (synchronous, no LLM needed)

    Example:
        from chaoscypher_core import ChaosCypher

        # Optional — configure provider once
        ChaosCypher.configure(provider="openai", api_key="sk-...")

        # Async
        result = await ChaosCypher.extract("paper.pdf")
        response = await ChaosCypher.chat("Explain knowledge graphs")

        # Sync (no asyncio needed)
        result = ChaosCypher.extract_sync("paper.pdf")
    """

    __slots__ = ()  # Prevent instantiation with state

    # -- Configuration -------------------------------------------------------

    @staticmethod
    def configure(
        *,
        provider: str | None = None,
        api_key: str | None = None,
        settings: EngineSettings | None = None,
        **kwargs: Any,
    ) -> None:
        """Set default settings for all convenience functions.

        Call once at program start to configure the LLM provider globally.
        All subsequent calls to extract, chat, search, etc. will use these
        settings instead of creating fresh defaults.

        If ``api_key`` is passed without ``provider``, the provider is
        auto-detected from the key prefix (``sk-ant-`` → Anthropic,
        ``sk-`` → OpenAI).  Raises ``ValueError`` if the prefix is
        unrecognised.

        In addition to LLM settings, top-level aliases are supported for
        commonly tweaked parameters:

        - ``embedding_model``: Embedding model name (e.g., "BAAI/bge-large-en-v1.5")
        - ``embedding_provider``: Embedding provider ("local", "ollama", "openai", "gemini")
        - ``chunk_size``: Target chunk size in characters
        - ``chunk_overlap``: Chunk overlap in characters
        - ``extraction_depth``: Analysis depth ("full" or "quick")
        - ``vector_dimensions``: Embedding vector dimensions
        - ``auto_extract``: Auto-extract entities after indexing (bool)

        Args:
            provider: LLM provider name ("openai", "anthropic", "gemini", "ollama").
            api_key: API key for the selected provider.
            settings: Pre-built EngineSettings (overrides all other args).
            **kwargs: LLMSettings fields or top-level aliases listed above.

        Raises:
            ValueError: If ``api_key`` is passed without ``provider`` and the
                provider cannot be auto-detected from the key prefix, or if
                an unknown kwarg is passed.

        Example:
            from chaoscypher_core import ChaosCypher

            # Explicit provider
            ChaosCypher.configure(provider="openai", api_key="sk-...")

            # With embedding and chunking overrides
            ChaosCypher.configure(
                provider="openai",
                api_key="sk-...",
                embedding_model="BAAI/bge-large-en-v1.5",
                chunk_size=512,
            )

        """
        global _default_settings

        if settings is not None:
            _default_settings = settings
            return

        _default_settings = _build_settings_from_kwargs(
            provider=provider, api_key=api_key, **kwargs
        )

    @staticmethod
    def reset() -> None:
        """Clear cached settings from configure().

        After calling reset(), convenience functions revert to fresh
        EngineSettings() defaults (which still respect env vars).
        """
        global _default_settings
        _default_settings = None

    # -- Async methods -------------------------------------------------------

    @staticmethod
    async def extract(
        source: str | Path | None = None,
        **kwargs: Any,
    ) -> ExtractionResult:
        """Extract entities and relationships from a file or text.

        See :func:`chaoscypher_core.extract` for full documentation.
        """
        from chaoscypher_core import extract

        return await extract(source, **kwargs)

    @staticmethod
    async def chat(
        messages: str | list[dict[str, Any]],
        **kwargs: Any,
    ) -> LLMChatResponse:
        """Quick LLM chat with default settings.

        See :func:`chaoscypher_core.chat` for full documentation.
        """
        from chaoscypher_core import chat

        return await chat(messages, **kwargs)

    @staticmethod
    async def embed(
        text: str | list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> EmbedResult | BatchEmbedResult:
        """Quick embedding with default settings.

        Args:
            text: Text string or list of text strings to embed.
            model: Override embedding model (e.g., "BAAI/bge-large-en-v1.5").
            **kwargs: Forwarded to embedding provider methods.

        See :func:`chaoscypher_core.embed` for full documentation.
        """
        from chaoscypher_core import embed

        return await embed(text, model=model, **kwargs)

    @staticmethod
    async def search(
        query: str,
        **kwargs: Any,
    ) -> list[EngineSearchResult]:
        """Search an existing knowledge graph database.

        See :func:`chaoscypher_core.search` for full documentation.
        """
        from chaoscypher_core import search

        return await search(query, **kwargs)

    @staticmethod
    async def embed_batch(
        texts: list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> BatchEmbedResult:
        """Batch embedding with unambiguous return type.

        See :func:`chaoscypher_core.embed_batch` for full documentation.
        """
        from chaoscypher_core import embed_batch

        return await embed_batch(texts, model=model, **kwargs)

    @staticmethod
    async def add_document(
        filepath: str | Path,
        **kwargs: Any,
    ) -> ProcessingResult:
        """Load a file and process it into a knowledge graph.

        See :func:`chaoscypher_core.add_document` for full documentation.
        """
        from chaoscypher_core import add_document

        return await add_document(filepath, **kwargs)

    @staticmethod
    async def add_documents(
        paths: str | list[str | Path],
        **kwargs: Any,
    ) -> list[ProcessingResult]:
        """Load and process multiple documents into a knowledge graph.

        See :func:`chaoscypher_core.add_documents` for full documentation.
        """
        from chaoscypher_core import add_documents

        return await add_documents(paths, **kwargs)

    @staticmethod
    async def chunk(
        source: str | Path | None = None,
        **kwargs: Any,
    ) -> ChunksResult:
        """Chunk a file or text into hierarchical segments for RAG.

        See :func:`chaoscypher_core.chunk` for full documentation.
        """
        from chaoscypher_core import chunk

        return await chunk(source, **kwargs)

    @staticmethod
    def load(filepath: str | Path) -> str:
        """Load a file and return its text content.

        Auto-detects file type (PDF, Word, CSV, JSON, text, etc.).

        Args:
            filepath: Path to the document file.

        Returns:
            Extracted text content.

        Example:
            text = ChaosCypher.load("paper.pdf")

        """
        from chaoscypher_core import Loaders

        return Loaders.load_text(str(filepath))

    # -- Sync methods --------------------------------------------------------

    @staticmethod
    def extract_sync(
        source: str | Path | None = None,
        **kwargs: Any,
    ) -> ExtractionResult:
        """Synchronous wrapper for extract().

        See :func:`chaoscypher_core.extract_sync` for full documentation.
        """
        from chaoscypher_core import extract_sync

        return extract_sync(source, **kwargs)

    @staticmethod
    def chat_sync(
        messages: str | list[dict[str, Any]],
        **kwargs: Any,
    ) -> LLMChatResponse:
        """Synchronous wrapper for chat().

        See :func:`chaoscypher_core.chat_sync` for full documentation.
        """
        from chaoscypher_core import chat_sync

        return chat_sync(messages, **kwargs)

    @staticmethod
    def embed_sync(
        text: str | list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> EmbedResult | BatchEmbedResult:
        """Synchronous wrapper for embed().

        See :func:`chaoscypher_core.embed_sync` for full documentation.
        """
        from chaoscypher_core import embed_sync

        return embed_sync(text, model=model, **kwargs)

    @staticmethod
    def search_sync(
        query: str,
        **kwargs: Any,
    ) -> list[EngineSearchResult]:
        """Synchronous wrapper for search().

        See :func:`chaoscypher_core.search_sync` for full documentation.
        """
        from chaoscypher_core import search_sync

        return search_sync(query, **kwargs)

    @staticmethod
    def embed_batch_sync(
        texts: list[str],
        *,
        model: str | None = None,
        **kwargs: Any,
    ) -> BatchEmbedResult:
        """Synchronous wrapper for embed_batch().

        See :func:`chaoscypher_core.embed_batch_sync` for full documentation.
        """
        from chaoscypher_core import embed_batch_sync

        return embed_batch_sync(texts, model=model, **kwargs)

    @staticmethod
    def add_document_sync(
        filepath: str | Path,
        **kwargs: Any,
    ) -> ProcessingResult:
        """Synchronous wrapper for add_document().

        See :func:`chaoscypher_core.add_document_sync` for full documentation.
        """
        from chaoscypher_core import add_document_sync

        return add_document_sync(filepath, **kwargs)

    @staticmethod
    def add_documents_sync(
        paths: str | list[str | Path],
        **kwargs: Any,
    ) -> list[ProcessingResult]:
        """Synchronous wrapper for add_documents().

        See :func:`chaoscypher_core.add_documents_sync` for full documentation.
        """
        from chaoscypher_core import add_documents_sync

        return add_documents_sync(paths, **kwargs)

    @staticmethod
    def chunk_sync(
        source: str | Path | None = None,
        **kwargs: Any,
    ) -> ChunksResult:
        """Synchronous wrapper for chunk().

        See :func:`chaoscypher_core.chunk_sync` for full documentation.
        """
        from chaoscypher_core import chunk_sync

        return chunk_sync(source, **kwargs)


# Short alias
CC = ChaosCypher

__all__ = [
    "CC",
    "ChaosCypher",
    "_build_settings_from_kwargs",
    "_get_default_settings",
]
