# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Grounded chat as a benchmark suite: the local chat board, answered by an MCP client.

Every question of a reference pack's fixture is one task with one stage,
``answer``. The bridge retrieves against the pack's indexed graph exactly as
the CLI's chat stage does, renders the context and the prompt with the same
functions, and hands the client that single user message. The answer is
scored by :class:`ChatProbeScorer`, the local chat board's scorer, so the row
sits on the same board as the local models.

The suite answers under one of the retrieval rungs of
:mod:`chaoscypher_core.benchmark.retrieval`: ``graphrag`` (the default, the
product's search), ``hybrid`` (the chunk search without the graph) or
``naive`` (the LlamaIndex tutorial pipeline, built by the factory the CLI
injects). The two baselines render chunks only; their rows carry the mode
in the dataset id, so they never pool with graphrag rows.

The levers a run answers under are the client model's, resolved exactly as
the CLI's chat stage resolves them
(:func:`~chaoscypher_core.benchmark.chat_run.resolve_run_graphrag`: the
model's profile, by the run's ``model``), and the row records them
(``extras["run_settings"]``). When the pack holds pins for the rung
(:mod:`chaoscypher_core.benchmark.pins`), every question is served its pinned
retrieval, so a harness row sees the same context as every local row built
from the same pins; the row records which file served it. A pinned pack with
no file for the resolved settings, or a file missing a question, is refused
like the CLI refuses it (until 2026-10-06 the suite retrieved live, uncached,
instead: audit R10). A pack with no pins of the rung retrieves live and says
so. :meth:`ChatSuite.build_pins` writes them: one index, each question
embedded once.
"""

from __future__ import annotations

import functools
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from chaoscypher_core.benchmark.chat_prompt import format_retrieved_context, grounded_chat_prompt
from chaoscypher_core.benchmark.chat_run import (
    SHAPE_ROW_KEYS,
    graph_identity,
    queries_file_sha256,
    resolve_run_graphrag,
    retrieval_shape,
    retrieved_entity_ids,
    run_provenance,
)
from chaoscypher_core.benchmark.pins import (
    PINS_DIR,
    PinError,
    QueryVectorCache,
    RetrievalPins,
    dir_digest,
    file_sha256,
    find_pins,
    index_digest,
    new_pins,
    retrieval_fingerprint,
)
from chaoscypher_core.benchmark.queries import LabeledQuerySet, load_queries
from chaoscypher_core.benchmark.reference import (
    ReferencePackError,
    list_packs,
    load_pack,
)
from chaoscypher_core.benchmark.retrieval import (
    DEFAULT_RETRIEVAL_MODE,
    NAIVE_INDEX_PREFIX,
    NaiveIndexSpec,
    embedder_slug,
    require_complete_retrieval,
    retrieval_dataset_suffix,
    retrieval_handlers,
    validate_retrieval_mode,
)
from chaoscypher_core.benchmark.scorers.chat_probes import (
    BAND_TIER,
    CHAT_PROBE_SCORER_VERSION,
    ChatProbeScorer,
)
from chaoscypher_core.benchmark.types import RawOutput
from chaoscypher_core.graphrag_profiles import profile_for
from chaoscypher_core.mcp.benchmark.errors import (
    ERR_INVALID_ARGUMENT,
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
from chaoscypher_core.utils.query_text import normalize_query


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from chaoscypher_core.benchmark.queries import LabeledQuery
    from chaoscypher_core.benchmark.reference import ReferencePack
    from chaoscypher_core.benchmark.retrieval import NaiveRetriever, NaiveRetrieverFactory
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )
    from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
        ChatCallback,
    )
    from chaoscypher_core.settings import EngineSettings, GraphRAGSettings


CHAT_STAGE = "answer"

CHAT_ANSWER_FORMAT = (
    "A plain-prose answer to the question using ONLY the retrieved context in the "
    "prompt; if the context does not contain the answer, say that it is not in the "
    "sources. No tool use, no reading files, no outside knowledge."
)


class ChatSuite:
    """The grounded-chat questions of one reference pack.

    Attributes:
        retrieve: ``async (question) -> retrieved`` - retrieval over the
            pack's indexed graph under the suite's rung. Injectable (tests
            swap in a fake); the default opens the pack's engine on first use
            and keeps it open.
        retrieval: The rung: ``graphrag``, ``hybrid`` or ``naive``.
    """

    name: str = "chat"
    kind: str = "chat"
    stages: tuple[str, ...] = (CHAT_STAGE,)
    answer_format: str = CHAT_ANSWER_FORMAT

    def __init__(
        self,
        settings: EngineSettings,
        pack: ReferencePack,
        *,
        retrieval: str = DEFAULT_RETRIEVAL_MODE,
        naive_retriever: NaiveRetrieverFactory | None = None,
        queries_file: Path | None = None,
        chat_callback: ChatCallback | None = None,
        router_model: dict[str, Any] | None = None,
    ) -> None:
        """Bind the settings, the pack and the rung; the fixture and engine load on first use.

        ``chat_callback`` is the shape router's model, handed to the retrieval
        handlers (``GraphRAGToolHandlers(chat_callback=...)``): the pin builder
        (``pin_retrieval.py``) passes the CLI's, so a ``rules_then_llm`` pin
        asks the model where the rules abstain. Without one the router decides
        by the rules alone and an abstention is lookup (``shape_source``
        ``default``); the MCP bridge passes none today. ``router_model`` is
        what a graphrag pin file's header records about that model
        (:func:`~chaoscypher_core.benchmark.pins.router_model_record`, from the
        same place as the callback: ``orchestrator.pin_router``), so a build
        under ``--if-missing`` knows a file another model routed is stale.

        ``queries_file`` replaces the pack's own fixture (``queries.yaml``): a
        holdout set asked over the same graph (validation plan 2026-10-05 § 4)
        is pinned and served without touching the pack. Every row records the
        file it was asked (``extras["queries_file"]``) and the sha256 of its
        bytes when it was read (``extras["queries_sha256"]``), as the CLI's
        chat rows do.

        Raises:
            BenchmarkError: ``INVALID_ARGUMENT`` on an unknown rung, or on
                ``naive`` without a factory (the CLI's ``benchmark`` extra
                provides one; Core never imports llama-index).
        """
        self.settings = settings
        self.queries_file: Path = queries_file or pack.queries_path
        self._queries_sha256: str | None = None
        self.pack = pack
        self.reference: str | None = pack.name
        try:
            self.retrieval: str | None = validate_retrieval_mode(retrieval)
        except ValueError as exc:
            raise BenchmarkError(ERR_INVALID_ARGUMENT, str(exc)) from exc
        if self.retrieval == "naive" and naive_retriever is None:
            raise BenchmarkError(
                ERR_INVALID_ARGUMENT,
                "naive retrieval needs the CLI's benchmark extra (llama-index): run the "
                "server and the driver from a checkout synced with --extra dev, or install "
                "chaoscypher-cli[benchmark]",
            )
        self._naive_factory = naive_retriever
        self._chat_callback = chat_callback
        self._router_model = router_model
        self._naive: NaiveRetriever | None = None
        self._fixture: LabeledQuerySet | None = None
        self._engine: Any = None
        self._stack: AsyncExitStack | None = None
        # the pin file per settings fingerprint (None: the pack has none for it)
        self._pins: dict[str, RetrievalPins | None] = {}
        # set by build_pins: the query vector cache the engine's embedder goes through
        self._vector_cache: QueryVectorCache | None = None
        # set while a run's question is retrieved live: that run's resolved levers
        self._live_graphrag: GraphRAGSettings | None = None
        self.retrieve: Callable[[str], Awaitable[dict[str, Any]]] = self._retrieve_from_pack

    # ------------------------------------------------------------------ #
    #  Fixture and retrieval
    # ------------------------------------------------------------------ #

    def _queries(self) -> LabeledQuerySet:
        """The suite's chat fixture (:attr:`queries_file`), read once per suite with its sha256."""
        if self._fixture is None:
            self._queries_sha256 = queries_file_sha256(self.queries_file)
            self._fixture = load_queries(self.queries_file)
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

    async def _handlers(self) -> GraphRAGToolHandlers:
        """The product's retrieval handlers over the pack's engine, wired as the CLI's chat stage wires them.

        The one benchmark wiring (:func:`~chaoscypher_core.benchmark.retrieval.retrieval_handlers`):
        a live run's resolved levers, the query vector cache while pins are
        built, and the suite's router model.
        """
        engine = await self._open_engine()
        service = engine.embedding_service
        settings = engine.settings
        if self._live_graphrag is not None:
            settings = settings.model_copy(update={"graphrag": self._live_graphrag})
        return retrieval_handlers(
            engine,
            settings=settings,
            embedding_callback=(
                self._vector_cache.wrap(service.embed, provider=service)
                if self._vector_cache
                else None
            ),
            chat_callback=self._chat_callback,
        )

    def naive_index_spec(self) -> NaiveIndexSpec:
        """Where the naive rung's index for this pack lives: ``index/naive-<embedder slug>``."""
        return NaiveIndexSpec(
            corpus_id=self.pack.corpus_id,
            embedder=self.pack.embedder,
            cache_dir=self.pack.index_dir
            / f"{NAIVE_INDEX_PREFIX}{embedder_slug(self.pack.embedder)}",
        )

    async def _retrieve_from_pack(self, question: str) -> dict[str, Any]:
        """Retrieve under the suite's rung; the baselines return chunks only.

        The question is composed first (:func:`normalize_query`), for every
        rung: the naive retriever tokenizes and embeds what it is given, and
        a decomposed accent splits a word (audit wave 5).
        """
        question = normalize_query(question)
        if self.retrieval == "naive":
            if self._naive is None:
                assert self._naive_factory is not None  # checked in __init__
                self._naive = await self._naive_factory(self.naive_index_spec(), self.settings)
            return {"chunks": await self._naive(question)}
        handlers = await self._handlers()
        limit = (self._live_graphrag or self.settings.graphrag).chunk_limit
        if self.retrieval == "hybrid":
            # the chunks with their fault record, so a pin build refuses a degraded one
            hybrid: dict[str, Any] = await handlers.hybrid_retrieval(question, limit)
            return hybrid
        result: dict[str, Any] = await handlers.graphrag_search(query=question, limit=limit)
        return result

    # ------------------------------------------------------------------ #
    #  Pinned retrieval
    # ------------------------------------------------------------------ #

    def run_settings(self, state: dict[str, Any] | None = None) -> EngineSettings:
        """The settings a run answers under: the client model's profile, as the CLI resolves it.

        The model is the run's ``model`` (what the client said it is); the
        domain is the pack's (:func:`~chaoscypher_core.benchmark.reference.pack_domain`:
        the manifest's ``extraction_domain`` or ``domain``, else the
        snapshot's source rows). Without a run (``state`` None) the plain
        settings.
        """
        model = (state or {}).get("model")
        if not model:
            return self.settings
        graphrag = resolve_run_graphrag(
            self.settings.graphrag, model=str(model), domain=self.pack.domain
        )
        if graphrag is self.settings.graphrag:
            return self.settings
        return self.settings.model_copy(update={"graphrag": graphrag})

    def _rung_pinned(self) -> bool:
        """Whether the pack ships any pin file for this rung (then every run must be served from one)."""
        pins_dir = self.pack.root / PINS_DIR
        if not pins_dir.is_dir():
            return False
        for path in pins_dir.glob(f"{self.retrieval}-*.json"):
            try:
                RetrievalPins.load(path)
            except PinError, ValueError:
                continue
            return True
        return False

    def pins(self, settings: EngineSettings | None = None) -> RetrievalPins | None:
        """The pack's pins for this rung and these settings (the suite's own by default), or None.

        Raises:
            BenchmarkError: ``UNKNOWN_REFERENCE`` when the pin file was built
                for another graph, embedder, rung or retrieval recipe.
        """
        assert self.retrieval is not None  # set in __init__
        settings = settings or self.settings
        try:
            fingerprint = retrieval_fingerprint(self.retrieval, self.pack.embedder, settings)
        except PinError as exc:
            raise BenchmarkError(ERR_UNKNOWN_REFERENCE, str(exc)) from exc
        if fingerprint not in self._pins:
            try:
                found = find_pins(
                    self.pack.root / PINS_DIR,
                    retrieval=self.retrieval,
                    embedder=self.pack.embedder,
                    settings=settings,
                )
                if found is not None:
                    found.check(
                        graph_sha256=file_sha256(self.pack.snapshot_path),
                        embedder=self.pack.embedder,
                        retrieval=self.retrieval,
                    )
            except PinError as exc:
                raise BenchmarkError(ERR_UNKNOWN_REFERENCE, str(exc)) from exc
            self._pins[fingerprint] = found
        return self._pins[fingerprint]

    def _served_pins(self, settings: EngineSettings) -> RetrievalPins | None:
        """The pins a run must be served from, or None when the pack has none for the rung.

        Raises:
            BenchmarkError: ``UNKNOWN_REFERENCE`` when the pack is pinned for the
                rung but holds no file for these settings (the CLI refuses the
                same: retrieving live would hand this run another context).
        """
        pins = self.pins(settings)
        if pins is None and self._rung_pinned():
            assert self.retrieval is not None  # set in __init__
            fingerprint = retrieval_fingerprint(self.retrieval, self.pack.embedder, settings)
            raise BenchmarkError(
                ERR_UNKNOWN_REFERENCE,
                f"reference {self.pack.name!r} has {self.retrieval} pins, but none for this "
                f"run's settings (fingerprint {fingerprint}): build them with pin_retrieval.py "
                "for the model's profile first",
            )
        return pins

    def pin_router_model(self) -> dict[str, Any] | None:
        """The router model a pin file of this suite records: the graphrag rung's, with a callback; else None."""
        if self.retrieval != "graphrag" or self._chat_callback is None:
            return None
        return self._router_model

    async def build_pins(self, pins_dir: Path, *, force: bool = False) -> RetrievalPins:
        """Retrieve every question once and write this rung's pin file.

        The graphrag and hybrid rungs retrieve over the pack's one indexed
        copy with each question embedded once (the query vector cache in
        ``pins_dir`` is reused and extended), so pins built for two settings
        variants differ only by what the variant changes. The naive rung
        retrieves through its own index, built once per pack.

        Args:
            pins_dir: Where the pin file and the query vector cache go.
            force: Write even when another pin file in ``pins_dir`` came
                from a different index of the same kind (mixing indexes
                brings back the drift pins remove).

        Raises:
            PinError: When ``pins_dir`` holds pins from another index and
                ``force`` is not set.
        """
        assert self.retrieval is not None  # set in __init__
        naive = self.retrieval == "naive"
        cache = (
            None
            if naive
            else QueryVectorCache.for_embedder(
                pins_dir, self.pack.embedder, dimensions=self.pack.vector_dimensions
            )
        )
        self._vector_cache = cache
        pins = new_pins(
            pins_dir,
            retrieval=self.retrieval,
            embedder=self.pack.embedder,
            settings=self.settings,
            graph_sha256=file_sha256(self.pack.snapshot_path),
            index_digest=None,
            pack=self.pack.name,
        )
        # which model routed the shapes: in the header, compared by an --if-missing build
        pins.header["router_model"] = self.pin_router_model()
        try:
            for q in self._queries().queries:
                retrieved = await self.retrieve(q.question)
                if not naive:  # the naive retriever refuses a short retrieval itself
                    # a degraded (empty or short) retrieval is never pinned (audit R8)
                    require_complete_retrieval(
                        retrieved,
                        mode=self.retrieval,
                        chunk_limit=self.settings.graphrag.chunk_limit,
                        indexed_chunks=self.pack.indexed_chunk_count(),
                    )
                pins.put(q.id, q.question, retrieved)
        finally:
            self._vector_cache = None
        if naive:
            built = self.naive_index_spec().cache_dir
            pins.header["index_digest"] = dir_digest(built) if built.exists() else None
        else:
            db = self.pack.index_db_path
            pins.header["index_digest"] = index_digest(db) if db.exists() else None
        if not force:
            _refuse_mixed_indexes(pins_dir, pins)
        pins.save()
        if cache is not None:
            cache.save()
        return pins

    # ------------------------------------------------------------------ #
    #  Suite contract
    # ------------------------------------------------------------------ #

    def run_info(self) -> dict[str, Any]:
        """The chat dataset id (fixture, extractor, embedder, a baseline's rung) and fixture version."""
        pack = self.pack
        assert self.retrieval is not None  # set in __init__
        return {
            "dataset_id": (
                f"{pack.fixture_id}__chat__{embedder_slug(pack.extractor)}__"
                f"{embedder_slug(pack.embedder)}{retrieval_dataset_suffix(self.retrieval)}"
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
        settings = self.run_settings(state)
        cached = state["stages"].setdefault(task_id, {})
        if "context_text" not in cached:
            pins = self._served_pins(settings)
            if pins is not None:
                retrieved = pins.lookup(q.id, q.question)
                if retrieved is None:
                    raise BenchmarkError(
                        ERR_UNKNOWN_REFERENCE,
                        f"{pins.path.name} has no pin for {q.id} (new or reworded): rebuild "
                        "the pins with pin_retrieval.py",
                    )
            else:
                self._live_graphrag = settings.graphrag
                try:
                    retrieved = await self.retrieve(q.question)
                finally:
                    self._live_graphrag = None
            cached["pinned"] = pins is not None
            cached["context_text"] = format_retrieved_context(retrieved)
            cached["retrieved_entity_ids"] = retrieved_entity_ids(retrieved)
            # the shape the router chose (None on the hybrid and naive rungs), as the CLI rows
            cached.update(retrieval_shape(retrieved))
        return {
            "system_prompt": "",
            "user_prompt": grounded_chat_prompt(
                q.question,
                cached["context_text"],
                ask_for_citations=settings.graphrag.ask_for_citations,
                answer_policy=settings.graphrag.answer_policy,
            ),
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
            # a run cached before the shape was recorded reads None: unrecorded, never lookup
            **{key: cached.get(key) for key in SHAPE_ROW_KEYS},
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
        verdicts = score.metrics["verdicts"]
        if not verdicts:
            # ``answerable: false`` in the fixture: asked and kept raw, never scored.
            return {
                "passed": None,
                "details": ["excluded: not answerable from the retrieved context (fixture audit)"],
            }
        verdict = verdicts[0]
        return {
            "passed": bool(verdict["passed"]),
            "details": list(verdict["details"]),
            "citation": verdict.get("citation"),
        }

    def score(self, state: dict[str, Any], records: list[dict[str, Any]]) -> ScoreBundle:
        """Score the run with :class:`ChatProbeScorer`; unanswered questions count as fails."""
        fixture = self._fixture_for(list(state["task_ids"]))
        truncated = sum(1 for r in records if r.get("finish_reason") == "length")
        output_tokens = sum(int(r.get("output_tokens") or 0) for r in records)
        settings = self.run_settings(state)
        pins = self.pins(settings)
        stages = state.get("stages") or {}
        assert self.retrieval is not None  # set in __init__
        model = state.get("model")
        extras = {
            "per_query": records,
            "reference": dict(self.pack.manifest),
            "retrieval": self.retrieval,
            # the question file and its content, read with the fixture (the CLI rows' keys)
            "queries_file": str(self.queries_file),
            "queries_sha256": self._queries_sha256,
            "run_settings": run_provenance(
                settings.graphrag,
                profile=profile_for(
                    self.settings.graphrag,
                    model=str(model) if model else None,
                    domain=self.pack.domain,
                ),
                retrieval_fingerprint=retrieval_fingerprint(
                    self.retrieval, self.pack.embedder, settings
                ),
                pins=pins.provenance() if pins is not None else None,
                pack=self.pack.name,
                # the graph the pack serves: its snapshot's sha256 and the identity fields
                # its manifest records (audit wave 5; the CLI reads the same from slot.json)
                graph=graph_identity(
                    self.pack.manifest,
                    snapshot_sha256=file_sha256(self.pack.snapshot_path),
                    cache_key=self.pack.manifest.get("graph_cache_key"),
                ),
            ),
            # which pin file served the run, and how many questions it served
            "retrieval_pins": (
                {
                    **pins.provenance(),
                    "served": sum(
                        1 for t in state["task_ids"] if (stages.get(t) or {}).get("pinned")
                    ),
                    "of": len(state["task_ids"]),
                }
                if pins is not None
                else None
            ),
        }
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


def _refuse_mixed_indexes(pins_dir: Path, pins: RetrievalPins) -> None:
    """Refuse a pin file whose index differs from its siblings' of the same kind.

    The graphrag and hybrid rungs read the pack's indexed copy; the naive
    rung reads its own. Pins of one kind from two different builds of an
    index would compare contexts that drifted apart again.

    Raises:
        PinError: Naming the file built from another index.
    """
    naive = pins.header["retrieval"] == "naive"
    for other in sorted(pins_dir.glob("*.json")):
        if other.name.startswith("query-vectors-") or other == pins.path:
            continue
        try:
            header = RetrievalPins.load(other).header
        except PinError, ValueError:
            continue
        if (header.get("retrieval") == "naive") != naive:
            continue
        if header.get("index_digest") != pins.header.get("index_digest"):
            msg = (
                f"{other.name} was built from index {header.get('index_digest')}, this run's is "
                f"{pins.header.get('index_digest')}: rebuild every pin file of the kind, or pass force"
            )
            raise PinError(msg)


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
    build = functools.partial(
        ChatSuite, ctx.settings, retrieval=ctx.retrieval, naive_retriever=ctx.naive_retriever
    )
    if ctx.reference is None:
        if len(packs) == 1:
            return build(packs[0])
        if not packs:
            raise BenchmarkError(ERR_UNKNOWN_REFERENCE, f"no reference pack found; {hint}")
        raise BenchmarkError(
            ERR_UNKNOWN_REFERENCE, f"several reference packs; pass reference, one of: {names}"
        )
    try:
        pack = load_pack(data_dir, ctx.reference)
    except ReferencePackError as exc:
        raise BenchmarkError(ERR_UNKNOWN_REFERENCE, f"{exc} (available: {names}; {hint})") from exc
    return build(pack)


__all__ = ["CHAT_ANSWER_FORMAT", "CHAT_STAGE", "ChatSuite", "chat_suite_factory"]
