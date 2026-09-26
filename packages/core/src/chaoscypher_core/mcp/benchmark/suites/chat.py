# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Grounded chat as a benchmark suite: the local chat board, answered by an MCP client.

Every question of a reference pack's fixture is one task with one stage,
``answer``. The bridge runs GraphRAG retrieval against the pack's indexed
graph exactly as the CLI's chat stage does, renders the context and the
prompt with the same functions, and hands the client that single user
message. The answer is scored by :class:`ChatProbeScorer`, the local chat
board's scorer, so the row sits on the same board as the local models.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from chaoscypher_core.benchmark.chat_prompt import format_retrieved_context, grounded_chat_prompt
from chaoscypher_core.benchmark.queries import LabeledQuerySet
from chaoscypher_core.benchmark.reference import (
    ReferencePackError,
    list_packs,
    load_pack,
)
from chaoscypher_core.benchmark.scorers.chat_probes import (
    BAND_TIER,
    CHAT_PROBE_SCORER_VERSION,
    ChatProbeScorer,
)
from chaoscypher_core.benchmark.types import RawOutput
from chaoscypher_core.mcp.benchmark.errors import (
    ERR_OUT_OF_ORDER,
    ERR_UNKNOWN_PROBE,
    ERR_UNKNOWN_REFERENCE,
    BenchmarkError,
)
from chaoscypher_core.mcp.benchmark.suites.base import (
    ScoreBundle,
    SubmitOutcome,
    SuiteContext,
    TaskRef,
)


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from chaoscypher_core.benchmark.queries import LabeledQuery
    from chaoscypher_core.benchmark.reference import ReferencePack
    from chaoscypher_core.settings import EngineSettings


CHAT_STAGE = "answer"

CHAT_ANSWER_FORMAT = (
    "A plain-prose answer to the question using ONLY the retrieved context in the "
    "prompt; if the context does not contain the answer, say that it is not in the "
    "sources. No tool use, no reading files, no outside knowledge."
)


def _slug(model_id: str) -> str:
    """A model id as the local chat rows spell it inside a dataset id."""
    return model_id.replace("/", "_")


class ChatSuite:
    """The grounded-chat questions of one reference pack.

    Attributes:
        retrieve: ``async (question) -> retrieved`` - GraphRAG search over the
            pack's indexed graph. Injectable (tests swap in a fake); the
            default opens the pack's engine on first use and keeps it open.
    """

    name: str = "chat"
    kind: str = "chat"
    stages: tuple[str, ...] = (CHAT_STAGE,)
    answer_format: str = CHAT_ANSWER_FORMAT

    def __init__(self, settings: EngineSettings, pack: ReferencePack) -> None:
        """Bind the settings and the pack; the fixture and engine load on first use."""
        self.settings = settings
        self.pack = pack
        self.reference: str | None = pack.name
        self._fixture: LabeledQuerySet | None = None
        self._engine: Any = None
        self._stack: AsyncExitStack | None = None
        self.retrieve: Callable[[str], Awaitable[dict[str, Any]]] = self._retrieve_from_pack

    # ------------------------------------------------------------------ #
    #  Fixture and retrieval
    # ------------------------------------------------------------------ #

    def _queries(self) -> LabeledQuerySet:
        """The pack's chat fixture (cached per suite)."""
        if self._fixture is None:
            self._fixture = self.pack.load_queries()
        return self._fixture

    def _query(self, task_id: str) -> LabeledQuery:
        """One question by id."""
        for q in self._queries().queries:
            if q.id == task_id:
                return q
        raise BenchmarkError(ERR_UNKNOWN_PROBE, f"question {task_id!r} is not in this run")

    async def _open_engine(self) -> Any:
        """The pack's indexed engine, opened (and indexed if needed) on first use."""
        if self._engine is None:
            stack = AsyncExitStack()
            self._engine = await stack.enter_async_context(self.pack.indexed_engine(self.settings))
            self._stack = stack
        return self._engine

    async def aclose(self) -> None:
        """Close the pack's engine if it was opened."""
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._engine = None

    async def _retrieve_from_pack(self, question: str) -> dict[str, Any]:
        """Run the product's GraphRAG search, wired as the CLI's chat stage wires it."""
        from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
            GraphRAGToolHandlers,
        )

        engine = await self._open_engine()
        handlers = GraphRAGToolHandlers(
            graph_repository=engine.graph_repository,
            search_repository=engine.search_repository,
            # The storage adapter serves both ports, as in the product's
            # bootstrap; with None the handler returns no chunks at all.
            indexing_repository=engine.storage_adapter,
            source_storage=engine.storage_adapter,
            embedding_callback=engine.embedding_service.embed,
            settings=engine.settings,
            database_name=engine.database_name,
        )
        result: dict[str, Any] = await handlers.graphrag_search(query=question)
        return result

    # ------------------------------------------------------------------ #
    #  Suite contract
    # ------------------------------------------------------------------ #

    def run_info(self) -> dict[str, Any]:
        """The chat dataset id (fixture, extractor, embedder) and fixture version."""
        pack = self.pack
        return {
            "dataset_id": (
                f"{pack.fixture_id}__chat__{_slug(pack.extractor)}__{_slug(pack.embedder)}"
            ),
            "dataset_version": pack.fixture_version,
        }

    def tasks(self, only: list[str] | None) -> list[TaskRef]:
        """The fixture's questions in fixture order, or the ``only`` subset."""
        queries = self._queries().queries
        if only is not None:
            unknown = sorted(set(only) - {q.id for q in queries})
            if unknown:
                raise BenchmarkError(
                    ERR_UNKNOWN_PROBE,
                    f"not in reference {self.pack.name!r}: {', '.join(unknown)}",
                )
            queries = [q for q in queries if q.id in set(only)]
        return [
            TaskRef(q.id, {"band": q.band, "tier": BAND_TIER.get(q.band, "easy")}) for q in queries
        ]

    def pending_stage(self, state: dict[str, Any], task_id: str) -> str | None:
        """``answer`` until the question has a record."""
        return None if task_id in state["records"] else CHAT_STAGE

    async def prompt(self, state: dict[str, Any], task_id: str, stage: str) -> dict[str, Any]:
        """Retrieve once per question (cached in the run state) and build the prompt."""
        q = self._query(task_id)
        cached = state["stages"].setdefault(task_id, {})
        if "context_text" not in cached:
            retrieved = await self.retrieve(q.question)
            cached["context_text"] = format_retrieved_context(retrieved)
            cached["retrieved_entity_ids"] = [
                str(e.get("id")) for e in retrieved.get("entities", []) or []
            ]
        return {
            "system_prompt": "",
            "user_prompt": grounded_chat_prompt(q.question, cached["context_text"]),
        }

    async def submit(
        self,
        state: dict[str, Any],
        task_id: str,
        stage: str,
        output_text: str,
        *,
        output_tokens: int | None,
        empty_answer: bool,
        truncated: bool,
    ) -> SubmitOutcome:
        """Store the answer and build the per-question record the local chat stage builds."""
        from chaoscypher_core.utils.tokens import estimate_tokens

        q = self._query(task_id)
        cached = state["stages"].get(task_id) or {}
        if "context_text" not in cached:
            raise BenchmarkError(
                ERR_OUT_OF_ORDER,
                f"get the task for {task_id!r} first: its retrieved context is built then",
            )
        cached["answer"] = output_text
        tokens = int(output_tokens) if output_tokens is not None else estimate_tokens(output_text)
        record = {
            "query_id": q.id,
            "band": q.band,
            "retrieved_entity_ids": list(cached.get("retrieved_entity_ids") or []),
            "finish_reason": "length" if truncated else "stop",
            "output_tokens": tokens,
            "context_text": cached["context_text"],
            "answer": output_text,
        }
        return SubmitOutcome(record=record)

    def _fixture_for(self, ids: list[str]) -> LabeledQuerySet:
        """The fixture restricted to ``ids``, in fixture order."""
        fixture = self._queries()
        keep = set(ids)
        return LabeledQuerySet(
            version=fixture.version, queries=[q for q in fixture.queries if q.id in keep]
        )

    def verdict(self, task_id: str, record: dict[str, Any]) -> dict[str, Any]:
        """Score one answer with the chat board's scorer (a one-question fixture)."""
        raw = RawOutput(
            entities=[],
            relationships=[],
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            error=None,
            extras={"per_query": [record]},
        )
        score = ChatProbeScorer().score(raw, self._fixture_for([task_id]))
        verdict = score.metrics["verdicts"][0]
        return {"passed": bool(verdict["passed"]), "details": list(verdict["details"])}

    def score(self, state: dict[str, Any], records: list[dict[str, Any]]) -> ScoreBundle:
        """Score the run with :class:`ChatProbeScorer`; unanswered questions count as fails."""
        fixture = self._fixture_for(list(state["task_ids"]))
        truncated = sum(1 for r in records if r.get("finish_reason") == "length")
        output_tokens = sum(int(r.get("output_tokens") or 0) for r in records)
        extras = {"per_query": records, "reference": dict(self.pack.manifest)}
        raw = RawOutput(
            entities=[],
            relationships=[],
            latency_ms=0,
            input_tokens=0,
            output_tokens=output_tokens,
            error=None,
            chunks_truncated=truncated,
            extras=extras,
        )
        score = ChatProbeScorer().score(raw, fixture)
        return ScoreBundle(
            headline_score=score.headline_score,
            metrics=score.metrics,
            dataset_id=state["dataset_id"],
            dataset_kind=self.kind,
            dataset_version=state["dataset_version"],
            dataset_source="builtin",
            config_name="chat",
            scorer_version=CHAT_PROBE_SCORER_VERSION,
            seed=None,
            temperature=None,
            thinking=None,
            thinking_honoured=None,
            input_tokens=0,
            output_tokens=output_tokens,
            chunks_truncated=truncated,
            chunks_aborted_by_loop=0,
            extras=extras,
        )


def chat_suite_factory(ctx: SuiteContext) -> ChatSuite:
    """Build the chat suite over the named pack, or the only one there is.

    Raises:
        BenchmarkError: ``UNKNOWN_REFERENCE`` when the pack does not exist or
            does not validate, when there is none, or when there are several
            and none was named; the message lists the available packs.
    """
    data_dir = ctx.settings.paths.data_dir
    packs = list_packs(data_dir)
    names = ", ".join(p.name for p in packs) or "none"
    hint = "export one with `chaoscypher benchmark reference export`"
    if ctx.reference is None:
        if len(packs) == 1:
            return ChatSuite(ctx.settings, packs[0])
        if not packs:
            raise BenchmarkError(ERR_UNKNOWN_REFERENCE, f"no reference pack found; {hint}")
        raise BenchmarkError(
            ERR_UNKNOWN_REFERENCE, f"several reference packs; pass reference, one of: {names}"
        )
    try:
        pack = load_pack(data_dir, ctx.reference)
    except ReferencePackError as exc:
        raise BenchmarkError(ERR_UNKNOWN_REFERENCE, f"{exc} (available: {names}; {hint})") from exc
    return ChatSuite(ctx.settings, pack)


__all__ = ["CHAT_ANSWER_FORMAT", "CHAT_STAGE", "ChatSuite", "chat_suite_factory"]
