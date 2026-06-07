# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""EmbeddingRetrievalDataset — direct test of the embedding stage."""

from __future__ import annotations

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_cli.benchmark.dataset import DatasetSource, RawOutput
from chaoscypher_cli.benchmark.scorers.embedding import EmbeddingRetrievalScorer


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.dataset import DatasetScorer
    from chaoscypher_cli.benchmark.graph_provider import GraphProvider
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_cli.benchmark.queries import LabeledQuerySet


logger = structlog.get_logger(__name__)
_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    """Lowercase and strip non-alphanumeric chars for fuzzy entity matching."""
    return _NORMALIZE_RE.sub("", s.lower())


def resolve_gold_entity(gold_name: str, entities: list[dict[str, Any]]) -> str | None:
    """Resolve a gold entity name to a live entity id.

    Tries three strategies in order:

    1. Exact name match (case-insensitive).
    2. Alias match (case-insensitive).
    3. Normalized fallback — strips non-alphanumeric chars from both sides.

    Args:
        gold_name: Canonical name from the query fixture.
        entities: Live entity dicts with at least ``id``, ``name``, and
            ``aliases`` keys.

    Returns:
        The entity ``id`` string on a match, or ``None`` when no entity
        satisfies any strategy.
    """
    needle_lower = gold_name.lower().strip()
    for e in entities:
        if str(e.get("name", "")).lower().strip() == needle_lower:
            return str(e["id"])
    for e in entities:
        for alias in e.get("aliases", []) or []:
            if str(alias).lower().strip() == needle_lower:
                return str(e["id"])
    needle_norm = _normalize(gold_name)
    for e in entities:
        if _normalize(str(e.get("name", ""))) == needle_norm:
            return str(e["id"])
        for alias in e.get("aliases", []) or []:
            if _normalize(str(alias)) == needle_norm:
                return str(e["id"])
    return None


@dataclass
class EmbeddingRetrievalDataset:
    """Evaluates an embedder against a labeled-query set on a fixed graph.

    Attributes:
        id: Unique dataset identifier (e.g. ``"arpanet_v1"``).
        version: Dataset version string; bumps when the query set or corpus
            changes.
        corpus_id: ID of the graph snapshot this dataset runs against.
        queries: The labeled query fixture.
        graph_provider: Supplies an indexed graph copy per :meth:`run` call.
        embed_query: Async callable ``(question, ctx) -> embedding vector``.
        vector_search: Async callable
            ``(vector, ctx, top_k) -> [(entity_id, score), ...]``.
        top_k: Number of results to retrieve per query.
        source: Discovery source (``"builtin"`` or ``"user"``).
    """

    id: str
    version: str
    corpus_id: str
    queries: LabeledQuerySet
    graph_provider: GraphProvider
    embed_query: Callable[[str, Any], Awaitable[list[float]]]
    vector_search: Callable[[list[float], Any, int], Awaitable[list[tuple[str, float]]]]
    top_k: int = 10
    source: DatasetSource = "builtin"
    kind: str = field(default="embedding", init=False)
    scorer: DatasetScorer = field(default_factory=EmbeddingRetrievalScorer, init=False)
    fixture: Any = field(init=False)

    def __post_init__(self) -> None:
        """Set fixture from queries (satisfies BenchmarkDataset.fixture protocol)."""
        self.fixture = self.queries

    async def run(self, model: ModelConfig) -> RawOutput:
        """Evaluate one embedder candidate against the query fixture.

        For each in-scope query the method:

        1. Resolves every gold entity name to a live entity ID.
        2. Embeds the query question via ``embed_query``.
        3. Calls ``vector_search`` and records the 1-based rank of each gold ID.

        Queries whose gold entities cannot be resolved are recorded as skipped
        (``"skip_reason": "gold_unresolved"``). ``out_of_scope`` queries are
        silently excluded from the run (handled by the scorer via
        ``queries_total``).

        Args:
            model: The embedder configuration to evaluate.

        Returns:
            A :class:`~chaoscypher_cli.benchmark.dataset.RawOutput` whose
            ``extras["per_query"]`` carries per-query rank dicts. On
            unrecoverable failure ``error`` is set and ``per_query`` may be
            partial.
        """
        t0 = time.perf_counter()
        per_query: list[dict[str, Any]] = []
        try:
            async with self.graph_provider.indexed_graph(embedder=model) as graph:
                ctx = graph.ctx
                entities = ctx.storage_adapter.list_entities()
                for q in self.queries.queries:
                    if q.band == "out_of_scope":
                        continue
                    resolved = {n: resolve_gold_entity(n, entities) for n in q.gold_entities}
                    if any(rid is None for rid in resolved.values()):
                        per_query.append(
                            {
                                "query_id": q.id,
                                "band": q.band,
                                "ranks": {},
                                "skipped": True,
                                "skip_reason": "gold_unresolved",
                                "unresolved": [n for n, rid in resolved.items() if rid is None],
                            }
                        )
                        continue
                    vec = await self.embed_query(q.question, ctx)
                    ranked = await self.vector_search(vec, ctx, self.top_k)
                    rank_by_id = {eid: i + 1 for i, (eid, _s) in enumerate(ranked)}
                    ranks = {
                        # rid is str here: the `any(rid is None)` guard above
                        # ensures we only enter this branch when all rids resolved.
                        n: rank_by_id.get(rid, self.top_k + 1)  # type: ignore[arg-type]
                        for n, rid in resolved.items()
                    }
                    per_query.append(
                        {
                            "query_id": q.id,
                            "band": q.band,
                            "ranks": ranks,
                            "skipped": False,
                        }
                    )
            elapsed = int((time.perf_counter() - t0) * 1000)
            return RawOutput(
                entities=[],
                relationships=[],
                latency_ms=elapsed,
                input_tokens=0,
                output_tokens=0,
                error=None,
                extras={"per_query": per_query, "top_k": self.top_k},
            )
        except Exception as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.exception(
                "embedding_dataset_failed",
                dataset_id=self.id,
                model_id=model.model_id,
            )
            return RawOutput(
                entities=[],
                relationships=[],
                latency_ms=elapsed,
                input_tokens=0,
                output_tokens=0,
                error=f"{type(exc).__name__}: {exc}",
                extras={"per_query": per_query, "top_k": self.top_k},
            )


__all__ = ["EmbeddingRetrievalDataset", "resolve_gold_entity"]
