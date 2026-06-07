# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Build EngineSettings from the Dynaconf-loaded application Settings.

Replaces the old reflection-based settings bridge. With the Tier-2 schema
unification every group is the SAME class on both sides, so this is group
copies plus the handful of legacy cross-group mappings — no field reflection.

Copies (``model_copy(deep=True)``) preserve the pre-Tier-2 isolation
semantics: engine consumers can never write through to the app Settings
singleton (which ConfigManager persists), and vice versa.

Cross-group mappings exist because some engine fields were historically fed
from timeouts/backoff/retries/batching groups before they were directly
settable. Each mapping is guarded: an EXPLICIT value on the target group
(in ``model_fields_set``) wins over the legacy source.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from chaoscypher_core.settings import EngineSettings


if TYPE_CHECKING:
    from pydantic import BaseModel

    from chaoscypher_core.app_config import Settings


def _map(target: BaseModel, field: str, value: Any) -> None:
    """Apply a legacy cross-group mapping unless the field was set explicitly."""
    if field not in target.model_fields_set:
        setattr(target, field, value)


def build_engine_settings(settings: Settings) -> EngineSettings:
    """EngineSettings view of the application Settings (isolated copies)."""
    e = EngineSettings(
        current_database=settings.current_database,
        paths=settings.paths.model_copy(deep=True),
        llm=settings.llm.model_copy(deep=True),
        batching=settings.batching.model_copy(deep=True),
        source_processing=settings.source_processing.model_copy(deep=True),
        extraction=settings.extraction.model_copy(deep=True),
        chunking=settings.chunking.model_copy(deep=True),
        analysis=settings.analysis.model_copy(deep=True),
        pagination=settings.pagination.model_copy(deep=True),
        database=settings.database.model_copy(deep=True),
        search=settings.search.model_copy(deep=True),
        embedding=settings.embedding.model_copy(deep=True),
        chat=settings.chat.model_copy(deep=True),
        mcp=settings.mcp.model_copy(deep=True),
        export=settings.export.model_copy(deep=True),
        web=settings.web.model_copy(deep=True),
        backoff=settings.backoff.model_copy(deep=True),
        retries=settings.retries.model_copy(deep=True),
        quality=settings.quality.model_copy(deep=True),
        # normalizer/migrations/graphrag/graph/archive/loader/compose:
        # engine-only or engine-default groups — cli is shared post-union:
        cli=settings.cli.model_copy(deep=True),
    )

    # Legacy cross-group mappings (pre-union the targets weren't directly
    # settable; explicit target values now win — see _map).
    t = settings.timeouts
    _map(e.llm, "ollama_health_check_timeout", t.ollama_health_check)
    _map(e.llm, "stream_chunk_timeout", t.llm_stream_chunk_timeout)
    _map(e.llm, "llm_request_timeout", float(t.llm_operation_max))
    _map(e.database, "connection_timeout_secs", t.sqlite_connection)
    _map(e.database, "busy_timeout_ms", t.sqlite_busy_timeout_ms)
    _map(e.database, "cache_size_kb", settings.batching.sqlite_cache_size_kb)
    _map(e.database, "commit_max_retries", settings.retries.sqlite_max_attempts)
    _map(e.database, "commit_base_delay_secs", settings.backoff.sqlite_base_delay)
    _map(e.extraction, "llm_backoff_max_seconds", settings.backoff.max_seconds)
    _map(e.extraction, "llm_backoff_multiplier", settings.backoff.llm_backoff_multiplier)
    _map(e.extraction, "llm_healthy_pause_seconds", t.llm_health_pause)

    if "rerank_cache_dir" not in settings.search.model_fields_set:
        e.search.rerank_cache_dir = str(Path(settings.paths.data_dir) / "cache" / "rerankers")

    return e


__all__ = ["build_engine_settings"]
