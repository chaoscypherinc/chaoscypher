# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Which retrieval stages swallowed an exception during one retrieval.

The retrieval path degrades instead of failing: a search arm that raises
returns no hits, a GraphRAG stage that raises returns an empty result, and the
answer is built from what is left. That keeps a chat answering, but a degraded
retrieval looked exactly like a complete one, so the benchmark pinned and
served such retrievals without complaint (audit wave 2).

Every site that swallows an exception calls :func:`record_retrieval_fault`
with its stage name. A caller that wants to know opens
:func:`collect_retrieval_faults` around the retrieval and reads the list: the
GraphRAG pipeline does, and puts it on ``retrieval_stats["faults"]``, which
:func:`~chaoscypher_core.benchmark.retrieval.require_complete_retrieval`
refuses. Outside a collector a record is a no-op. The list lives in a
context variable, so concurrent retrievals (one asyncio task each) never see
each other's faults.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Iterator


_FAULTS: ContextVar[list[str] | None] = ContextVar("retrieval_faults", default=None)

NEEDS_FULL_REINDEX_UNREADABLE = "needs_full_reindex:unreadable"
"""The fault of a retrieval that could not read the index's ``needs_full_reindex`` flag.

Recorded by the search repository's flag read and by the GraphRAG handlers'
index check alike, so both name it the same.
"""

SCOPE_SHORT = "hybrid_search:scope_short"
"""The fault of a scoped hybrid arm that returned fewer chunks than ``min(budget, candidates the search saw)``.

The candidates are the search's own count before its cut
(:class:`~chaoscypher_core.ports.search.SearchHits`), so a scope that holds
fewer chunks that answer than the budget is not a fault (audit wave 6).
"""


def record_retrieval_fault(stage: str) -> None:
    """Note that ``stage`` swallowed an exception (or degraded) in the current retrieval.

    Args:
        stage: A short, stable name of the site (``seed_match``, ``vector_search``).
    """
    faults = _FAULTS.get()
    if faults is not None:
        faults.append(stage)


@contextmanager
def collect_retrieval_faults() -> Iterator[list[str]]:
    """Collect the faults recorded inside the block; the yielded list fills as they happen.

    Nested collectors each see the faults recorded while they are the
    innermost one, and an outer collector also receives them on exit.
    """
    outer = _FAULTS.get()
    faults: list[str] = []
    token = _FAULTS.set(faults)
    try:
        yield faults
    finally:
        _FAULTS.reset(token)
        if outer is not None:
            outer.extend(faults)


__all__ = [
    "NEEDS_FULL_REINDEX_UNREADABLE",
    "SCOPE_SHORT",
    "collect_retrieval_faults",
    "record_retrieval_fault",
]
