# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphRAGChatDataset — end-to-end chat-over-graph evaluator."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_cli.benchmark.dataset import DatasetSource, RawOutput
from chaoscypher_cli.benchmark.determinism import pin_llm_determinism
from chaoscypher_cli.benchmark.judge_prompts import (
    CORRECTNESS_PROMPT,
    FAITHFULNESS_PROMPT,
    REFUSAL_PROMPT,
)
from chaoscypher_cli.benchmark.scorers.chat import GraphRAGChatScorer
from chaoscypher_core.benchmark.chat_prompt import format_retrieved_context
from chaoscypher_core.benchmark.chat_run import (
    chat_context_window,
    loop_guard_thresholds,
    queries_file_sha256,
    resolve_run_graphrag,
    retrieval_shape,
    retrieved_entity_ids,
    run_provenance,
)
from chaoscypher_core.benchmark.pins import (
    PinError,
    RetrievalPins,
    find_pins,
    retrieval_fingerprint,
)
from chaoscypher_core.benchmark.results import (
    LATENCY_EXCLUDES_RETRY_WAIT,
    per_query_retry_wait_ms,
)
from chaoscypher_core.benchmark.retrieval import (
    DEFAULT_RETRIEVAL_MODE,
    NAIVE_INDEX_PREFIX,
    NaiveIndexSpec,
    embedder_slug,
    validate_retrieval_mode,
)
from chaoscypher_core.cache.call_site import call_site
from chaoscypher_core.cache.export import file_sha256
from chaoscypher_core.graphrag_profiles import profile_for
from chaoscypher_core.settings import GraphRAGSettings
from chaoscypher_core.utils.stream_errors import retry_budget


if TYPE_CHECKING:
    from pathlib import Path

    from chaoscypher_cli.benchmark.dataset import DatasetScorer
    from chaoscypher_cli.benchmark.graph_provider import GraphProvider
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_cli.benchmark.queries import LabeledQuerySet
    from chaoscypher_core.benchmark.retrieval import NaiveRetriever, NaiveRetrieverFactory


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
        retrieval: The rung the context comes from (see
            :mod:`chaoscypher_core.benchmark.retrieval`); ``graphrag`` uses
            ``graphrag_search``, ``hybrid`` uses ``hybrid_search``, ``naive``
            builds an index once through ``naive_retriever`` under
            ``naive_index_root``.
        hybrid_search: Async callable ``(question, ctx) -> retrieved dict``
            (chunks only); needed for the ``hybrid`` rung.
        naive_retriever: The naive index factory; needed for ``naive``.
        naive_index_root: Where naive indexes are cached, one directory per
            corpus and embedder.
        pins_dir: Serve every question its pinned retrieval from this
            directory (:mod:`chaoscypher_core.benchmark.pins`) instead of
            retrieving: the graph is not re-indexed and nothing is embedded,
            so every model of a comparison answers from the same context.
            The run fails before any chat call when the directory has no pin
            file for the rung and the settings, or the file was built for
            another graph or embedder, or misses a question.
        sweep_set: The ``SWEEP_SET`` variant applied on top of each model's
            profile (:func:`~chaoscypher_core.benchmark.chat_run.resolve_run_graphrag`).
        queries_file: The question file the run asks (the dataset's own or a
            holdout set); recorded on the row with ``queries_sha256``, the
            sha256 of its bytes when the run starts (audit wave 3).
        graph_provenance: What the orchestrator knows about the graph it
            serves, read from the slot record
            (:data:`~chaoscypher_core.benchmark.chat_run.GRAPH_PROVENANCE_KEYS`:
            the snapshot's sha256 and the slot's identity fields); recorded
            on the row.
    """

    id: str
    version: str
    corpus_id: str
    queries: LabeledQuerySet
    graph_provider: GraphProvider
    graphrag_search: Callable[[str, Any], Awaitable[dict[str, Any]]]
    chat: Callable[[ModelConfig, str, dict[str, Any], Any], Awaitable[str | dict[str, Any]]]
    judge: ModelConfig | None
    judge_call: Callable[[ModelConfig, str], Awaitable[Any]]
    source: DatasetSource = "builtin"
    # The embedder that indexes this run's snapshot copy. Without it the
    # snapshot is used as-is, which is only right when it was indexed upstream
    # (2026-09-25: it never was - the embedding stage indexes a throwaway copy -
    # so retrieval returned nothing and every chat answer was ungrounded).
    embedder: ModelConfig | None = None
    # Chat runs with thinking on: that is how the product uses chat models.
    # Recorded on every row; the runner probes whether the model obeyed.
    thinking: bool = True
    retrieval: str = DEFAULT_RETRIEVAL_MODE
    hybrid_search: Callable[[str, Any], Awaitable[dict[str, Any]]] | None = None
    naive_retriever: NaiveRetrieverFactory | None = None
    naive_index_root: Path | None = None
    domain: str | None = None  # the corpus domain, a profile key (graphrag_profiles)
    seed: int = 42
    temperature: float = 0.0
    """The decoding every row records; the runner sets both and ``run`` pins them on the context."""
    pins_dir: Path | None = None
    # a retryable model failure (refused or dropped connection, timeout) asks the question
    # again instead of scoring it wrong; from BenchmarkSettings when not given
    question_retries: int | None = None
    retry_backoff_seconds: float | None = None
    sweep_set: str | None = None
    queries_file: str | None = None
    graph_provenance: dict[str, Any] | None = None
    queries_sha256: str | None = field(default=None, init=False)
    thinking_honoured: bool | None = field(default=None, init=False)
    kind: str = field(default="chat", init=False)
    scorer: DatasetScorer = field(init=False)
    fixture: Any = field(init=False)

    def __post_init__(self) -> None:
        """Set fixture from queries; pick the scorer from whether a judge is configured.

        No judge means the pass/fail chat probes (``ChatProbeScorer``); a
        judge keeps the 1-5 faithfulness/correctness aggregation.
        """
        self.fixture = self.queries
        # the content that asks and scores the questions: the bytes the loader parsed (audit
        # wave 4), else the file read once now; the file can change mid-sweep
        parsed = getattr(self.queries, "sha256", None)
        self.queries_sha256 = (
            parsed if isinstance(parsed, str) else queries_file_sha256(self.queries_file)
        )
        self.retrieval = validate_retrieval_mode(self.retrieval)
        if self.retrieval == "hybrid" and self.hybrid_search is None:
            msg = "hybrid retrieval needs the wiring's hybrid_search"
            raise ValueError(msg)
        if self.retrieval == "naive" and (
            self.naive_retriever is None or self.naive_index_root is None or self.embedder is None
        ):
            msg = "naive retrieval needs the wiring's naive_retriever, naive_index_root and an embedder"
            raise ValueError(msg)
        if self.judge is None:
            from chaoscypher_cli.benchmark.scorers.chat_probes import ChatProbeScorer

            self.scorer = ChatProbeScorer()
        else:
            self.scorer = GraphRAGChatScorer()

    def naive_index_spec(self) -> NaiveIndexSpec:
        """Where this corpus and embedder's naive index lives under ``naive_index_root``."""
        assert self.naive_index_root is not None  # __post_init__
        assert self.embedder is not None  # __post_init__
        slug = embedder_slug(self.embedder.model_id)
        return NaiveIndexSpec(
            corpus_id=self.corpus_id,
            embedder=self.embedder.model_id,
            cache_dir=self.naive_index_root / f"{self.corpus_id}-{NAIVE_INDEX_PREFIX}{slug}",
        )

    async def _retrieve(
        self, question: str, ctx: Any, naive: NaiveRetriever | None
    ) -> dict[str, Any]:
        """Retrieve under the dataset's rung; the baselines return chunks only."""
        if self.retrieval == "naive":
            assert naive is not None  # built once per run below
            return {"chunks": await naive(question)}
        if self.retrieval == "hybrid":
            assert self.hybrid_search is not None  # __post_init__
            return await self.hybrid_search(question, ctx)
        return await self.graphrag_search(question, ctx)

    def _load_pins(self, ctx: Any) -> RetrievalPins:
        """This run's pins, checked against the graph, the embedder and every question.

        Raises:
            PinError: When no file matches or the file cannot serve the run.
        """
        assert self.pins_dir is not None  # the caller checks
        if self.embedder is None:
            msg = "pinned retrieval needs the run's embedder (the pins are keyed by it)"
            raise PinError(msg)
        embedder = self.embedder.model_id
        pins = find_pins(
            self.pins_dir, retrieval=self.retrieval, embedder=embedder, settings=ctx.settings
        )
        if pins is None:
            fingerprint = retrieval_fingerprint(self.retrieval, embedder, ctx.settings)
            msg = (
                f"no {self.retrieval} pins for these retrieval settings (fingerprint "
                f"{fingerprint}) in {self.pins_dir}: build them from the reference pack first"
            )
            raise PinError(msg)
        pins.check(
            graph_sha256=file_sha256(self.graph_provider.snapshot_path),
            embedder=embedder,
            retrieval=self.retrieval,
        )
        missing = pins.missing((q.id, q.question) for q in self.queries.queries)
        if missing:
            msg = f"{pins.path.name} has no pin for {', '.join(missing)} (new or reworded)"
            raise PinError(msg)
        return pins

    async def _answer(
        self,
        model: ModelConfig,
        question: str,
        retrieved: dict[str, Any],
        ctx: Any,
        entry: dict[str, Any],
    ) -> str | dict[str, Any]:
        """The chat call, asked again on a transient failure; ``entry`` records ``retried``.

        2026-10-04: a host waking from suspend dropped Gemma 4 26B's connection mid-answer
        and refused Qwen3 14B's for the rest of the row; 135 questions scored as wrong
        answers that were never answered. A transient failure (:func:`transient_failure`:
        the extraction stage's own retry rule, plus a raw dropped connection or timeout
        that no provider classified) waits and asks again, up to
        ``benchmark.chat_question_retries`` times; anything else is recorded at once.
        """
        return await self._with_retries(
            lambda: self.chat(model, question, retrieved, ctx), model, entry
        )

    async def _with_retries[T](
        self,
        call: Callable[[], Awaitable[T]],
        model: ModelConfig,
        entry: dict[str, Any],
    ) -> T:
        """``call()``, again after a backoff while it fails transiently; ``retried``/``retries`` on ``entry``.

        Raises:
            Exception: The last failure, once it is not transient or the retries are spent.
        """
        from chaoscypher_core.app_config import get_settings

        cfg = get_settings().benchmark
        retries = (
            self.question_retries
            if self.question_retries is not None
            else cfg.chat_question_retries
        )
        backoff = (
            self.retry_backoff_seconds
            if self.retry_backoff_seconds is not None
            else cfg.chat_retry_backoff_seconds
        )
        for attempt in range(retries + 1):
            try:
                return await call()
            except Exception as exc:
                # a provider error may allow fewer retries than configured (a crashed runner:
                # once; retry_budget, the one rule the chat loop applies too, audit wave 5)
                budget = retry_budget(exc, retries) if hasattr(exc, "is_retryable") else retries
                if not transient_failure(exc) or attempt >= budget:
                    raise
                entry["retried"] = True
                entry["retries"] = int(entry.get("retries") or 0) + 1
                logger.warning(
                    "chat_question_retry",
                    model=model.model_id,
                    attempt=attempt + 1,
                    error=str(exc)[:200],
                )
                waited = time.perf_counter()
                await asyncio.sleep(backoff * (attempt + 1))
                # the backoff is not the model's time: recorded apart, left out of the latency
                entry["retry_wait_ms"] = int(entry.get("retry_wait_ms") or 0) + int(
                    (time.perf_counter() - waited) * 1000
                )
        msg = "unreachable: the loop returns or raises"  # pragma: no cover
        raise AssertionError(msg)  # pragma: no cover

    async def run(self, model: ModelConfig) -> RawOutput:
        """Evaluate one chat candidate. ``model`` is the chat candidate.

        For each query the method:

        1. Retrieves context under the dataset's rung (GraphRAG by default).
        2. Calls the chat candidate with the retrieved context.
        3. Invokes the judge LLM to score faithfulness + correctness or refusal.

        Per-query failures are recorded in ``extras["per_query"]`` without
        aborting the run, after the retries a transient one gets
        (:meth:`_with_retries`; the retrieval and the chat call both).

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
        latencies: list[int] = []
        # Reset per model: the verdict below is Ollama-only, so a stale value
        # would otherwise leak onto the next (non-Ollama) model's row.
        self.thinking_honoured = None
        if model.provider == "ollama":
            from chaoscypher_cli.benchmark.thinking_probe import probe_thinking, thinking_honoured
            from chaoscypher_core.app_config import get_settings

            # The configured Ollama, as the probe and extraction datasets do: the
            # probe's localhost default loaded the chat model on the wrong card
            # when chat ran against a second host (2026-10-03 06:18, qwen3.5:9b
            # on the 5090 beside a 27B and the embedder; the embedder crashed).
            verdict_t = await probe_thinking(
                model.model, base_url=get_settings().llm.primary_ollama_url
            )
            self.thinking_honoured = thinking_honoured(verdict_t, requested=self.thinking)
        pins: RetrievalPins | None = None
        run_settings: dict[str, Any] | None = None
        try:
            # Pinned: no re-index (the vectors are not read), the copy is
            # opened only for its settings, which key the pins.
            embedder = None if self.pins_dir is not None else self.embedder
            async with self.graph_provider.indexed_graph(embedder=embedder) as graph:
                ctx = graph.ctx
                # The decoding the row records is the decoding the chat runs with
                # (a stub context in the tests carries no llm settings to pin).
                if hasattr(getattr(ctx, "settings", None), "llm"):
                    pin_llm_determinism(
                        ctx.settings.llm, seed=self.seed, temperature=self.temperature
                    )
                resolved, profile = self._settle_levers(ctx, model)
                if self.pins_dir is not None:
                    pins = self._load_pins(ctx)
                if resolved:
                    run_settings = self._run_settings(ctx, pins, profile, model)
                naive: NaiveRetriever | None = None
                if self.retrieval == "naive" and pins is None:
                    assert self.naive_retriever is not None  # __post_init__
                    naive = await self.naive_retriever(self.naive_index_spec(), ctx.settings)
                for q in self.queries.queries:
                    entry: dict[str, Any] = {"query_id": q.id, "band": q.band}
                    try:
                        pinned = pins.lookup(q.id, q.question) if pins is not None else None
                        retrieved = (
                            pinned
                            if pinned is not None
                            else await self._with_retries(
                                functools.partial(self._retrieve, q.question, ctx, naive),
                                model,
                                entry,
                            )
                        )
                        entry["retrieved_entity_ids"] = retrieved_entity_ids(retrieved)
                        # the shape the router chose (None on the hybrid and naive rungs)
                        entry.update(retrieval_shape(retrieved))
                        q0 = time.perf_counter()
                        waited_before = int(entry.get("retry_wait_ms") or 0)
                        reply = await self._answer(model, q.question, retrieved, ctx, entry)
                        # the chat call's own time: a retry's backoff is retry_wait_ms
                        waited = int(entry.get("retry_wait_ms") or 0) - waited_before
                        latencies.append(max(0, int((time.perf_counter() - q0) * 1000) - waited))
                        # The wiring may return the bare answer or a record with
                        # what the probes check: completion, tokens, the context
                        # the model actually saw.
                        answer = _record_reply(entry, reply)
                        if self.judge is not None:
                            # the judge's calls are not the candidate's (the stage's site, chat)
                            with call_site("judge"):
                                verdict = await self._judge_query(q, answer, retrieved)
                            entry["judge_scores"] = {
                                "faithfulness": verdict.faithfulness,
                                "correctness": verdict.correctness,
                                "refusal_correct": verdict.refusal_correct,
                            }
                    except Exception as exc:
                        entry["error"] = f"{type(exc).__name__}: {exc}"
                    per_query.append(entry)

            # the row's time without the retries' backoff (audit wave 5): the sleep is the
            # harness's, not the model's, and it is recorded per question as retry_wait_ms
            waited_ms = per_query_retry_wait_ms(per_query)
            elapsed = max(0, int((time.perf_counter() - t0) * 1000) - waited_ms)
            return RawOutput(
                entities=[],
                relationships=[],
                latency_ms=elapsed,
                input_tokens=sum(int(e.get("prompt_tokens") or 0) for e in per_query),
                output_tokens=sum(int(e.get("output_tokens") or 0) for e in per_query),
                error=None,
                chunks_truncated=sum(1 for e in per_query if e.get("finish_reason") == "length"),
                # answers the repetition guard stopped: kept as trimmed ("loop") or
                # asked again without thinking (the retry's finish reason, loop_retried)
                chunks_aborted_by_loop=sum(
                    1
                    for e in per_query
                    if e.get("finish_reason") == "loop" or e.get("loop_retried")
                ),
                per_chunk_latency_ms=latencies,
                extras={
                    "per_query": per_query,
                    "judge_provider": self.judge.provider if self.judge else None,
                    "judge_model": self.judge.model if self.judge else None,
                    "judge_prompts_sha256": judge_prompts_sha256() if self.judge else None,
                    "retrieval": self.retrieval,
                    "retrieval_pins": pins.provenance() if pins is not None else None,
                    "run_settings": run_settings,
                    "queries_file": self.queries_file,
                    "queries_sha256": self.queries_sha256,
                    "per_query_errors": sum(1 for e in per_query if e.get("error")),
                    LATENCY_EXCLUDES_RETRY_WAIT: True,
                    "thinking_retried": sum(1 for e in per_query if e.get("thinking_retried")),
                },
            )
        except Exception as exc:
            waited_ms = per_query_retry_wait_ms(per_query)
            elapsed = max(0, int((time.perf_counter() - t0) * 1000) - waited_ms)
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
                    "judge_provider": self.judge.provider if self.judge else None,
                    "judge_model": self.judge.model if self.judge else None,
                    "judge_prompts_sha256": judge_prompts_sha256() if self.judge else None,
                    "retrieval": self.retrieval,
                    "run_settings": run_settings,
                    "queries_file": self.queries_file,
                    "queries_sha256": self.queries_sha256,
                    LATENCY_EXCLUDES_RETRY_WAIT: True,
                },
            )

    def _settle_levers(self, ctx: Any, model: ModelConfig) -> tuple[bool, str | None]:
        """Set the levers this model runs with on ``ctx``; ``(resolved, profile name)``.

        Its profile, then the sweep's variant
        (:func:`~chaoscypher_core.benchmark.chat_run.resolve_run_graphrag`, the
        one resolution the pin builder and the MCP suite share), before the pins
        are keyed, so a profile gets its own pins. A stub context (the tests'
        fakes) carries no settings to resolve: ``(False, None)``.
        """
        graphrag = getattr(getattr(ctx, "settings", None), "graphrag", None)
        if not isinstance(graphrag, GraphRAGSettings):
            return False, None
        profile = profile_for(graphrag, model=model.model, domain=self.domain)
        ctx.settings.graphrag = resolve_run_graphrag(
            graphrag, model=model.model, domain=self.domain, sweep_set=self.sweep_set
        )
        return True, profile

    def _run_settings(
        self, ctx: Any, pins: RetrievalPins | None, profile: str | None, model: ModelConfig
    ) -> dict[str, Any]:
        """``extras["run_settings"]``: the resolved levers, the prompt's hash, the pins and the graph.

        Every row records what changed its prompt (audit H5): until 2026-10-06
        the answer-policy rows, the ``SWEEP_SET`` variants and the default rows
        shared one pin fingerprint and differed by app version alone.
        """
        graph = self.graph_provenance or {}
        llm = getattr(ctx.settings, "llm", None)
        fingerprint = (
            retrieval_fingerprint(self.retrieval, self.embedder.model_id, ctx.settings)
            if self.embedder is not None
            else None
        )
        return run_provenance(
            ctx.settings.graphrag,
            sweep_set=self.sweep_set,
            profile=profile,
            retrieval_fingerprint=fingerprint,
            pins=pins.provenance() if pins is not None else None,
            graph=graph,
            queries_file=self.queries_file,
            # the candidate's window: its provider on the run's settings, as the chat call
            # copies them (orchestrator._provider_llm_settings), not the shared chat provider
            num_ctx=chat_context_window(llm, provider=model.provider),
            ai_max_tokens=_positive_int(getattr(llm, "ai_max_tokens", None)),
            loop_guard=loop_guard_thresholds(ctx.settings),
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
        if self.judge is None:  # pragma: no cover - callers check first
            msg = "no judge configured"
            raise RuntimeError(msg)
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


def judge_prompts_sha256() -> str:
    """The sha256 of the judge's three prompt templates (correctness, faithfulness, refusal).

    A judge-scored row records it as ``extras["judge_prompts_sha256"]`` next
    to ``judge_model`` (audit wave 4): an edited judge prompt changes every
    judged score while the judge model stays the same, so two rows judged
    under two prompts are two measurements.
    """
    digest = hashlib.sha256()
    for template in (CORRECTNESS_PROMPT, FAITHFULNESS_PROMPT, REFUSAL_PROMPT):
        digest.update(template.encode("utf-8") + b"\0")
    return digest.hexdigest()


def dataset_queries_path(corpus_id: str) -> Path | None:
    """A dataset's own question file (its bundle's ``queries_path``); None when it has none.

    The ``dataset_queries`` every rescoring tool hands
    :func:`~chaoscypher_core.benchmark.results.row_queries_path` and
    :func:`~chaoscypher_core.benchmark.results.common_queries_file`; an
    unknown dataset has none.
    """
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    try:
        return getattr(load_dataset_bundle(corpus_id), "queries_path", None)
    except FileNotFoundError, ValueError:
        return None


@functools.cache
def _questions_at(path: Path, _mtime_ns: int) -> LabeledQuerySet:
    """The parsed question file, once per file version."""
    from chaoscypher_core.benchmark.queries import load_queries

    return load_queries(path)


def row_questions(row: Any) -> tuple[LabeledQuerySet, Path]:
    """The question set a chat row was asked, and its file (:func:`~chaoscypher_core.benchmark.results.row_queries_path`).

    Raises:
        FileNotFoundError: When the row's question file is gone, or its dataset has none.
    """
    from chaoscypher_core.benchmark.results import row_queries_path

    path = row_queries_path(row, dataset_queries_path)
    if path is None:
        msg = f"{row.model_label} ({row.dataset_id}): no question file to rescore it with"
        raise FileNotFoundError(msg)
    return _questions_at(path, path.stat().st_mtime_ns), path


_TRANSPORT_FAILURES: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)
"""Raw failures no provider classified that are still transient: a dropped or refused
connection, a timeout (``asyncio.TimeoutError`` is ``TimeoutError``)."""


def transient_failure(exc: BaseException) -> bool:
    """Whether a chat-stage question that raised ``exc`` is asked again rather than recorded.

    The extraction stage's rule (:func:`~chaoscypher_core.services.sources.engine.extraction.chunk_failures.is_retryable_chunk_failure`:
    a provider error that says it is retryable, never a fatal model error), and
    a raw dropped connection or timeout (:data:`_TRANSPORT_FAILURES`, or an
    ``httpx`` transport error) that reached the dataset unclassified.
    """
    from chaoscypher_core.services.sources.engine.extraction.chunk_failures import (
        is_retryable_chunk_failure,
    )

    if is_retryable_chunk_failure(exc) or isinstance(exc, _TRANSPORT_FAILURES):
        return True
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx ships with the providers
        return False
    return isinstance(exc, httpx.TransportError)


def _positive_int(value: Any) -> int | None:
    """``value`` when it is a positive int (not a bool), else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _format_retrieved(retrieved: dict[str, Any]) -> str:
    """The context the judge checks the answer against: the text the chat model read.

    Until 2026-10-06 this listed ``retrieved["entities"]``, a key no rung
    fills, so every judge prompt said ``(empty)`` (audit R10).
    """
    return format_retrieved_context(retrieved)


_REPLY_ACCOUNTING = (
    "prompt_tokens",
    "prompt_tokens_estimated",
    "num_ctx",
    "num_predict",
    "loop_phase",
    "loop_reason",
    "thinking_chars",
    "thinking_retried",
)
"""What a chat reply reports about its tokens and its loop guard, copied onto the question's record."""


def _record_reply(entry: dict[str, Any], reply: str | dict[str, Any]) -> str:
    """Record one chat reply on its question's ``entry`` and return the answer text.

    The wiring may return the bare answer or a record with what the probes
    check (completion, tokens, the context the model actually saw) and the
    token accounting and loop details (audit H9, H10).
    """
    if not isinstance(reply, dict):
        answer = str(reply)
        entry["answer"] = answer
        return answer
    entry["finish_reason"] = reply.get("finish_reason", "stop")
    entry["output_tokens"] = int(reply.get("output_tokens") or 0)
    entry["context_text"] = str(reply.get("context_text", ""))
    if reply.get("loop_retried"):
        entry["loop_retried"] = True
    for key in _REPLY_ACCOUNTING:
        value = reply.get(key)
        if value is not None and value is not False:
            entry[key] = value
    answer = str(reply.get("answer", ""))
    entry["answer"] = answer
    return answer


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


__all__ = [
    "GraphRAGChatDataset",
    "JudgeVerdict",
    "dataset_queries_path",
    "judge_prompts_sha256",
    "row_questions",
    "transient_failure",
]
