# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphCache: per-(corpus, extractor, pipeline) extracted-graph snapshots.

Stores the raw SQLite database file produced by an extraction run so that
follow-up embedding + chat benches can re-use it instead of re-extracting.

Key: SHA-256 of (corpus_id, corpus_version, extractor.provider,
extractor.model, EXT_PIPELINE_VERSION). Bumping EXT_PIPELINE_VERSION
invalidates all cached graphs.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.models import ModelConfig


logger = structlog.get_logger(__name__)


# Bump when the extraction pipeline output shape changes in a way that
# invalidates cached graphs (e.g. schema migration, new entity fields).
EXT_PIPELINE_VERSION = "1"


def cache_key(
    *,
    corpus_id: str,
    corpus_version: str,
    extractor: ModelConfig,
) -> str:
    """Return a 16-char hex digest uniquely identifying the cache slot.

    Args:
        corpus_id: Stable dataset identifier (e.g. ``"war_and_peace_tiny"``).
        corpus_version: Dataset version string (e.g. ``"1.0"``).
        extractor: The LLM used for extraction; provider and model are
            incorporated into the key so swapping models yields a new slot.

    Returns:
        First 16 hex characters of SHA-256 over the joined input fields.
    """
    payload = f"{corpus_id}|{corpus_version}|{extractor.provider}|{extractor.model}|{EXT_PIPELINE_VERSION}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class GraphCache:
    """File-system cache of extracted graph snapshots.

    Storage layout: ``<root>/<key>/app.db``

    Attributes:
        root: Root directory for the cache. Need not exist yet; it is
            created on first write.
    """

    root: Path

    def __post_init__(self) -> None:
        """Resolve `root` to a Path on construction."""
        self._root = Path(self.root)

    def _slot(self, key: str) -> Path:
        """Return the on-disk path for a cache key."""
        return self._root / key

    async def get_or_build(
        self,
        *,
        corpus_id: str,
        corpus_version: str,
        extractor: ModelConfig,
        builder: Callable[[Path], Awaitable[None]],
    ) -> Path:
        """Return the cached snapshot path; call builder on cache miss.

        On a cache hit the builder is not called. On a miss, the builder
        is called with the *target* path where it must write a valid SQLite
        file. A ``RuntimeError`` is raised if the builder returns without
        producing the file.

        Args:
            corpus_id: Stable dataset identifier.
            corpus_version: Dataset version string.
            extractor: The LLM used for extraction.
            builder: Async callable ``(target: Path) -> None`` that writes
                the SQLite snapshot to *target*.

        Returns:
            Path to the cached ``app.db`` snapshot.

        Raises:
            RuntimeError: If the builder did not produce a file at the
                expected target path.
        """
        key = cache_key(
            corpus_id=corpus_id,
            corpus_version=corpus_version,
            extractor=extractor,
        )
        slot = self._slot(key)
        snapshot = slot / "app.db"
        if snapshot.exists():
            logger.info(
                "graph_cache_hit",
                key=key,
                corpus_id=corpus_id,
                extractor=f"{extractor.provider}/{extractor.model}",
            )
            return snapshot

        logger.info(
            "graph_cache_miss",
            key=key,
            corpus_id=corpus_id,
            extractor=f"{extractor.provider}/{extractor.model}",
        )
        slot.mkdir(parents=True, exist_ok=True)
        await builder(snapshot)
        if not snapshot.exists():
            msg = f"builder did not produce a snapshot at {snapshot}"
            raise RuntimeError(msg)
        return snapshot

    def clear(self) -> None:
        """Remove the entire cache directory tree.

        Idempotent — no error is raised if the root does not exist.
        """
        if self._root.exists():
            shutil.rmtree(self._root)


__all__ = ["EXT_PIPELINE_VERSION", "GraphCache", "cache_key"]
