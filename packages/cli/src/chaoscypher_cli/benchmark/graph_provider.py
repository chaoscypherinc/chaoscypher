# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphProvider — bind a cached extracted graph to a runtime CLIContext.

Used by the embedding and chat datasets. Given a snapshot path and an
embedder ModelConfig, returns a context manager that yields an
IndexedGraph: a connected CLIContext over a fresh copy of the graph indexed
with the requested embedder.

The index is built once per snapshot, embedder and reindex recipe and kept
under ``<workspace>/indexed/``; every run copies it, as a reference pack
keeps its one index (2026-10-04). Until then each model's run re-embedded
the whole graph on the GPU, and the vectors came out slightly different
every time, so near-tied chunks swapped between models.

Reindex is injected so unit tests can mock it; the real reindex path
calls the embedding adapter factory and writes vectors back into the
SQLite snapshot copy.
"""

from __future__ import annotations

import shutil
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.benchmark.retrieval import embedder_slug
from chaoscypher_core.benchmark.snapshot import snapshot_database_name
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
        reindex: Async callable that receives a connected ctx and an
            embedder and writes new vectors into the DB; called once per
            snapshot, embedder and reindex recipe, on a staging copy.
        workspace: Where snapshot copies and the kept indexes live; defaults
            to ``.bench_workspace`` in the working directory.
        select_embedder: Called on every run's ctx with the embedder, so the
            queries of a run on a kept index are embedded by the embedder
            that built it (the reindex sets this on the staging ctx only).
    """

    snapshot_path: Path
    ctx_factory: Callable[[Path], Any]
    reindex: Callable[[Any, ModelConfig], Awaitable[None]]
    workspace: Path | None = None
    select_embedder: Callable[[Any, ModelConfig], None] | None = None

    async def _indexed_copy(self, ws: Path, embedder: ModelConfig) -> Path:
        """The snapshot indexed with ``embedder``, built on first use and kept.

        Keyed by the snapshot's sha256, the embedder, its vector size and the reindex recipe,
        so a changed graph, embedder or embedding text builds a new one. The
        build happens in a staging directory renamed into place, after a WAL
        checkpoint so the kept file holds every vector.

        Raises:
            ReferencePackError: When the embedder's native vector size is not in
                the registry (the index could not be built at its size).
        """
        from chaoscypher_core.benchmark.reference import embedder_dimensions
        from chaoscypher_core.benchmark.reindex import reindex_recipe
        from chaoscypher_core.cache.export import file_sha256

        dbname = snapshot_database_name(self.snapshot_path) or "app"
        # The vector size is part of the key (2026-10-06): an index kept at the
        # 1024 default must not be reopened at the embedder's native size. One
        # rule for an embedder the registry does not know, here and in the
        # orchestrator's select_embedder: refused (ReferencePackError), as the
        # reference pack refuses it. Until the wave-2 audit this path keyed it
        # "cfgd" and indexed at the configured size while select_embedder raised.
        size = f"{embedder_dimensions(embedder.model_id)}d"
        key = (
            f"{file_sha256(self.snapshot_path)[:16]}-{embedder_slug(embedder.model_id)}-"
            f"{size}-{reindex_recipe()}"
        )
        kept = ws / "indexed" / key
        if (kept / dbname / "app.db").exists():
            return kept / dbname / "app.db"
        staging = ws / "indexed" / f".{key}.{generate_id()[:8]}"
        (staging / dbname).mkdir(parents=True)
        db = staging / dbname / "app.db"
        shutil.copyfile(self.snapshot_path, db)
        ctx = self.ctx_factory(db)
        ctx.connect()
        try:
            await self.reindex(ctx, embedder)
        except BaseException:
            _disconnect(ctx)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        _disconnect(ctx)
        _checkpoint(db)
        try:
            staging.rename(kept)
        except OSError:  # another run kept the same index first
            shutil.rmtree(staging, ignore_errors=True)
        return kept / dbname / "app.db"

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
        # The engine names the database after its directory, and every row in
        # the snapshot is scoped by the name of the run that built it - so the
        # copy must live in a directory of that exact name (2026-09-25: a
        # random folder name meant every repository query matched nothing).
        source = await self._indexed_copy(ws, embedder) if embedder is not None else None
        run_dir = ws / f"graph_{generate_id()[:8]}"
        copy_dir = run_dir / (snapshot_database_name(self.snapshot_path) or "app")
        copy_dir.mkdir(parents=True)
        copy_path = copy_dir / "app.db"
        shutil.copyfile(source or self.snapshot_path, copy_path)

        ctx = self.ctx_factory(copy_path)
        ctx.connect()
        try:
            if embedder is not None and self.select_embedder is not None:
                self.select_embedder(ctx, embedder)
            yield IndexedGraph(ctx=ctx, snapshot_copy=copy_path, embedder=embedder)
        finally:
            _disconnect(ctx)
            try:
                shutil.rmtree(run_dir)
            except Exception:
                logger.warning("graph_provider_workspace_cleanup_failed", exc_info=True)


def _disconnect(ctx: Any) -> None:
    """Disconnect a ctx, logging rather than raising on failure."""
    try:
        ctx.disconnect()
    except Exception:
        logger.warning("graph_provider_disconnect_failed", exc_info=True)


def _checkpoint(db: Path) -> None:
    """Fold a WAL file into the database so a copy of ``db`` alone holds every row."""
    if not db.with_name(db.name + "-wal").exists():
        return
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()


__all__ = ["GraphProvider", "IndexedGraph", "snapshot_database_name"]
