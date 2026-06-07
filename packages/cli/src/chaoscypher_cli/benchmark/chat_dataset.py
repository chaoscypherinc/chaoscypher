# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAGChatDataset — end-to-end chat-over-graph evaluator."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_cli.benchmark.dataset import DatasetSource, RawOutput
from chaoscypher_cli.benchmark.judge_prompts import (
    CORRECTNESS_PROMPT,
    FAITHFULNESS_PROMPT,
    REFUSAL_PROMPT,
)
from chaoscypher_cli.benchmark.scorers.chat import GraphRAGChatScorer


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.dataset import DatasetScorer
    from chaoscypher_cli.benchmark.graph_provider import GraphProvider
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_cli.benchmark.queries import LabeledQuerySet


logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class JudgeVerdict:
    """Structured output of one judge evaluation.

    Attributes:
        faithfulness: 1-5 faithfulness score, or None if not evaluated.
        correctness: 1-5 correctness score, or None for out-of-scope queries.
        refusal_correct: True/False refusal verdict, or None for in-scope queries.
    """

    faithfulness: int | None
    correctness: int | None
    refusal_correct: bool | None


@dataclass
class GraphRAGChatDataset:
    """Evaluates a chat candidate end-to-end against a labeled-query set.

    Attributes:
        id: Unique dataset identifier.
        version: Dataset version string.
        corpus_id: ID of the graph snapshot this dataset runs against.
        queries: The labeled query fixture.
        graph_provider: Supplies an indexed graph context manager per run.
        graphrag_search: Async callable ``(question, ctx) -> retrieved dict``.
        chat: Async callable ``(model, question, retrieved, ctx) -> answer``.
        judge: ModelConfig for the judge LLM.
        judge_call: Async callable ``(judge_model, prompt) -> response``.
        source: Discovery source (``"builtin"`` or ``"user"``).
    """

    id: str
    version: str
    corpus_id: str
    queries: LabeledQuerySet
    graph_provider: GraphProvider
    graphrag_search: Callable[[str, Any], Awaitable[dict[str, Any]]]
    chat: Callable[[ModelConfig, str, dict[str, Any], Any], Awaitable[str]]
    judge: ModelConfig
    judge_call: Callable[[ModelConfig, str], Awaitable[Any]]
    source: DatasetSource = "builtin"
    kind: str = field(default="chat", init=False)
    scorer: DatasetScorer = field(default_factory=GraphRAGChatScorer, init=False)
    fixture: Any = field(init=False)

    def __post_init__(self) -> None:
        """Set fixture from queries (satisfies BenchmarkDataset.fixture protocol)."""
        self.fixture = self.queries

    async def run(self, model: ModelConfig) -> RawOutput:
        """Evaluate one chat candidate. ``model`` is the chat candidate.

        For each query the method:

        1. Runs GraphRAG search to retrieve context.
        2. Calls the chat candidate with the retrieved context.
        3. Invokes the judge LLM to score faithfulness + correctness or refusal.

        Per-query failures are recorded in ``extras["per_query"]`` without
        aborting the run.

        Args:
            model: The chat model configuration to evaluate.

        Returns:
            A :class:`~chaoscypher_cli.benchmark.dataset.RawOutput` whose
            ``extras["per_query"]`` carries per-query answer + judge score dicts.
            On unrecoverable failure ``error`` is set and ``per_query`` may be
            partial.
        """
        t0 = time.perf_counter()
        per_query: list[dict[str, Any]] = []
        try:
            async with self.graph_provider.indexed_graph() as graph:
                ctx = graph.ctx
                for q in self.queries.queries:
                    entry: dict[str, Any] = {"query_id": q.id, "band": q.band}
                    try:
                        retrieved = await self.graphrag_search(q.question, ctx)
                        entry["retrieved_entity_ids"] = [
                            str(e.get("id")) for e in retrieved.get("entities", [])
                        ]
                        answer = await self.chat(model, q.question, retrieved, ctx)
                        entry["answer"] = answer
                        verdict = await self._judge_query(q, answer, retrieved)
                        entry["judge_scores"] = {
                            "faithfulness": verdict.faithfulness,
                            "correctness": verdict.correctness,
                            "refusal_correct": verdict.refusal_correct,
                        }
                    except Exception as exc:
                        entry["error"] = f"{type(exc).__name__}: {exc}"
                    per_query.append(entry)

            elapsed = int((time.perf_counter() - t0) * 1000)
            return RawOutput(
                entities=[],
                relationships=[],
                latency_ms=elapsed,
                input_tokens=0,
                output_tokens=0,
                error=None,
                extras={
                    "per_query": per_query,
                    "judge_provider": self.judge.provider,
                    "judge_model": self.judge.model,
                },
            )
        except Exception as exc:
            elapsed = int((time.perf_counter() - t0) * 1000)
            logger.exception(
                "chat_dataset_failed",
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
                extras={
                    "per_query": per_query,
                    "judge_provider": self.judge.provider,
                    "judge_model": self.judge.model,
                },
            )

    async def _judge_query(
        self, query: Any, answer: str, retrieved: dict[str, Any]
    ) -> JudgeVerdict:
        """Run the judge LLM against one query/answer pair.

        Args:
            query: The :class:`~chaoscypher_cli.benchmark.queries.LabeledQuery`.
            answer: The chat candidate's answer string.
            retrieved: The GraphRAG search result dict.

        Returns:
            A :class:`JudgeVerdict` with the parsed judge scores.
        """
        retrieved_text = _format_retrieved(retrieved)
        f_resp = await self.judge_call(
            self.judge, FAITHFULNESS_PROMPT.format(retrieved=retrieved_text, answer=answer)
        )
        f_score = _parse_int_1_5(f_resp)

        if query.band == "out_of_scope":
            r_resp = await self.judge_call(
                self.judge,
                REFUSAL_PROMPT.format(
                    retrieved=retrieved_text, question=query.question, answer=answer
                ),
            )
            return JudgeVerdict(
                faithfulness=f_score,
                correctness=None,
                refusal_correct=_parse_yes_no(r_resp),
            )

        c_resp = await self.judge_call(
            self.judge,
            CORRECTNESS_PROMPT.format(
                question=query.question,
                gold_answer=query.gold_answer or "",
                answer=answer,
            ),
        )
        return JudgeVerdict(
            faithfulness=f_score,
            correctness=_parse_int_1_5(c_resp),
            refusal_correct=None,
        )


def _format_retrieved(retrieved: dict[str, Any]) -> str:
    """Render retrieved entities as a bulleted list for judge prompts."""
    parts: list[str] = []
    for e in retrieved.get("entities", []):
        name = e.get("name", "")
        desc = e.get("description", "")
        parts.append(f"- {name}: {desc}" if desc else f"- {name}")
    return "\n".join(parts) or "(empty)"


def _parse_int_1_5(resp: Any) -> int | None:
    """Pick the first integer 1-5 in the judge response, or None."""
    text = str(resp).strip()
    for tok in text.split():
        if tok.isdigit():
            n = int(tok)
            if 1 <= n <= 5:
                return n
    return None


def _parse_yes_no(resp: Any) -> bool | None:
    """Map a yes/no judge response to bool, or None if neither prefix matches."""
    text = str(resp).strip().lower()
    if text.startswith("yes"):
        return True
    if text.startswith("no"):
        return False
    return None


__all__ = ["GraphRAGChatDataset", "JudgeVerdict"]
