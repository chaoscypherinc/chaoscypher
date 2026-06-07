# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphProvider — bind a cached extracted graph to a runtime CLIContext.

Used by the embedding and chat datasets. Given a snapshot path and an
embedder ModelConfig, returns a context manager that yields an
IndexedGraph: a connected CLIContext whose vector store has been
re-indexed with the requested embedder.

Reindex is injected so unit tests can mock it; the real reindex path
calls the embedding adapter factory and writes vectors back into the
SQLite snapshot copy.
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.utils.id import generate_id


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from chaoscypher_cli.benchmark.models import ModelConfig


logger = structlog.get_logger(__name__)


@dataclass
class IndexedGraph:
    """A CLIContext bound to a snapshot copy with vectors re-indexed.

    Attributes:
        ctx: Connected CLIContext (caller can read graph + run searches).
        snapshot_copy: Path to the temp copy backing this context.
        embedder: The embedder used for re-indexing.
    """

    ctx: Any
    snapshot_copy: Path
    embedder: ModelConfig | None


@dataclass
class GraphProvider:
    """Hands out indexed graphs for a single cached snapshot.

    Attributes:
        snapshot_path: The cached snapshot to clone for each indexed_graph().
        ctx_factory: Callable taking a DB path, returns a CLIContext-like
            object with connect()/disconnect()/settings/llm/embedding.
        reindex: Async callable invoked per indexed_graph(), receives the
            connected ctx and embedder; writes new vectors into the DB.
        workspace: Where snapshot copies live; defaults to a tmp dir.
    """

    snapshot_path: Path
    ctx_factory: Callable[[Path], Any]
    reindex: Callable[[Any, ModelConfig], Awaitable[None]]
    workspace: Path | None = None

    @asynccontextmanager
    async def indexed_graph(
        self, *, embedder: ModelConfig | None = None
    ) -> AsyncIterator[IndexedGraph]:
        """Yield a fresh IndexedGraph bound to a snapshot copy + embedder.

        When ``embedder`` is ``None`` the snapshot is used as-is without
        re-indexing — callers (e.g. ``GraphRAGChatDataset``) that do not
        themselves know which embedder produced the snapshot rely on the
        graph being pre-indexed upstream.
        """
        ws = self.workspace if self.workspace is not None else Path.cwd() / ".bench_workspace"
        ws.mkdir(parents=True, exist_ok=True)
        copy_dir = ws / f"graph_{generate_id()[:8]}"
        copy_dir.mkdir()
        copy_path = copy_dir / "app.db"
        shutil.copyfile(self.snapshot_path, copy_path)

        ctx = self.ctx_factory(copy_path)
        ctx.connect()
        try:
            if embedder is not None:
                await self.reindex(ctx, embedder)
            yield IndexedGraph(ctx=ctx, snapshot_copy=copy_path, embedder=embedder)
        finally:
            try:
                ctx.disconnect()
            except Exception:
                logger.warning("graph_provider_disconnect_failed", exc_info=True)
            try:
                shutil.rmtree(copy_dir)
            except Exception:
                logger.warning("graph_provider_workspace_cleanup_failed", exc_info=True)


__all__ = ["GraphProvider", "IndexedGraph"]
