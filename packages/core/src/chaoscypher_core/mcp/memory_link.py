# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The one step that links a memory into the graph: ``link_memory`` (spec 2026-10-24 § 4.3).

A memory is stored first, so its text is kept and searchable at once, and linked after:
``remember`` links it while the agent waits, and a link that cannot finish now (the model
is busy, failing or missing, or the process ends) leaves the memory marked with why, for
a later link to finish. Every link goes through :func:`link_memory`:

1. claim the row (a compare-and-set on its claim token; a live claim elsewhere wins);
2. chunk and index it from its kept text when its store never finished;
3. with no extraction model configured, mark it ``no_model`` (never an attempt);
4. extract and commit it, seeded from the graph as it is now, or, when its commit stood
   but the steps after it did not, run those steps again;
5. mark it ``linked`` (or ``linked_empty``), or ``failed`` with a fixed reason.

A heartbeat refreshes the claim while the link runs; a claim whose heartbeat is older
than :data:`CLAIM_STALE_SECONDS` is taken over. Cancelling a link (shutdown) gives its
claim back. Every link-state read and write runs in a worker thread on its own short
session, so a write waiting on another writer never blocks the event loop.

This module never imports ``mcp/memory.py`` (which owns the status copy and imports this).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.memory_link import LinkRow, MemoryLinkRepository
from chaoscypher_core.exceptions import DataIntegrityError
from chaoscypher_core.mcp.memory_seed import MemorySeed, build_seed
from chaoscypher_core.models import MemoryLinkState, SourceErrorStage
from chaoscypher_core.policy import SECONDS_PER_DAY
from chaoscypher_core.utils.id import generate_hex_id
from chaoscypher_core.utils.logging.privacy import (
    log_failure,
    outside_private_text,
    private_text,
)


if TYPE_CHECKING:
    import sqlalchemy

    from chaoscypher_core.bootstrap import Engine
    from chaoscypher_core.mcp.memory_relink import RelinkReport
    from chaoscypher_core.settings import EngineSettings

logger = structlog.get_logger(__name__)

CLAIM_HEARTBEAT_SECONDS = 30
"""How often a held claim's heartbeat (``last_activity_at``) is refreshed."""
CLAIM_STALE_SECONDS = 300
"""A claim whose heartbeat is older than this is taken over (its process is gone)."""
BACKOFF_CAP_SECONDS = SECONDS_PER_DAY
"""The longest a failed memory waits before the start-up sweep tries it again."""


class LinkOutcome(StrEnum):
    """What one :func:`link_memory` call ended with."""

    LINKED = "linked"
    LINKED_EMPTY = "linked_empty"
    NO_MODEL = "no_model"
    FAILED = "failed"
    TIMED_OUT = "timed_out"  # only ``remember`` gives it: its wait ran out first
    ELSEWHERE = "elsewhere"  # another holder has a live claim
    ALREADY_LINKED = "already_linked"  # linked before this call, which did nothing
    GONE = "gone"  # the memory was forgotten


FAILURE_REASONS: Mapping[SourceErrorStage, str] = {
    SourceErrorStage.INDEXING: "The text could not be chunked.",
    SourceErrorStage.EXTRACTION: "The extraction model failed.",
    SourceErrorStage.COMMIT: "The graph commit failed.",
    SourceErrorStage.AFTER_COMMIT: "The steps after the commit failed.",
}
"""The fixed reason a failed link writes in ``error_message``, by stage: never the
exception's text, which may quote the memory (D16)."""


class LinkView(StrEnum):
    """How a memory's link reads now: its state, with a stale ``linking`` read as interrupted."""

    PENDING = "pending"
    LINKING = "linking"
    INTERRUPTED = "interrupted"
    NO_MODEL = "no_model"
    FAILED = "failed"
    LINKED = "linked"
    LINKED_EMPTY = "linked_empty"


def link_view(row: LinkRow, now: datetime) -> LinkView:
    """The view of ``row`` at ``now``: a ``linking`` claim whose heartbeat went stale is interrupted.

    Raises:
        DataIntegrityError: The memory row has no link state (every writer sets one).
    """
    if row.state is None:
        msg = f"memory source {row.id} has no link state"
        raise DataIntegrityError(msg, details={"source_id": row.id})
    if row.state is MemoryLinkState.LINKING and (
        row.last_activity_at is None
        or row.last_activity_at < now - timedelta(seconds=CLAIM_STALE_SECONDS)
    ):
        return LinkView.INTERRUPTED
    return LinkView(row.state.value)


def extraction_model_configured(settings: EngineSettings) -> bool:
    """Whether the settings name an extraction provider at all (no provider: index only)."""
    return bool(settings.llm.chat_provider)


def new_claim_token() -> str:
    """A fresh claim token: one per link attempt."""
    return generate_hex_id()


def link_db(engine: Engine) -> sqlalchemy.Engine:
    """The SQLAlchemy engine of the memory database file (cached per path)."""
    return get_engine(engine.storage_adapter.db_path)


def link_repository(engine: Engine) -> MemoryLinkRepository:
    """The link-state repository on the engine's database file."""
    return MemoryLinkRepository(link_db(engine))


async def _heartbeat(repo: MemoryLinkRepository, source_id: str, token: str) -> None:
    """Refresh the claim every :data:`CLAIM_HEARTBEAT_SECONDS` until cancelled."""
    while True:
        # nosemgrep: cc-046-hardcoded-sleep-duration - the claim heartbeat is a protocol constant of the link step, paired with CLAIM_STALE_SECONDS (spec 2026-10-24 § 5: constants, not settings)
        await asyncio.sleep(CLAIM_HEARTBEAT_SECONDS)
        try:
            held = await asyncio.to_thread(repo.touch, source_id, token, now=datetime.now(UTC))
        except Exception as error:
            # a missed beat is not a failed link: the next one tries again
            log_failure(
                logger, "memory_link_heartbeat_failed", error, level="warning", source_id=source_id
            )
            continue
        if not held:
            return


def _stored_text(engine: Engine, source_id: str, full_text: str | None) -> str:
    """The memory's text: the kept one, else its stored chunks' joined."""
    if full_text:
        return full_text
    chunks, _ = engine.storage_adapter.get_chunks_by_source(source_id, page_size=1000)
    return " ".join(str(c.get("content") or "") for c in chunks)


def _by(engine: Engine, row: LinkRow) -> str:
    """Who the memory is by: its row's, else the configured default."""
    return row.memory_by or engine.settings.mcp.memory.default_by


def _seed(engine: Engine, row: LinkRow, about: Sequence[str]) -> MemorySeed:
    """What the memory's extraction is offered, read once per link: names, scope, templates.

    Read on the event loop: the reads use the engine's own storage session, which is not
    one a worker thread may share.
    """
    return build_seed(
        engine,
        _stored_text(engine, row.id, row.full_text),
        about,
        _by(engine, row),
        scope=row.memory_scope,
        exclude_source=row.id,
    )


@dataclass
class _Stage:
    """Where a link is, so a failure records the stage it failed at."""

    value: SourceErrorStage = SourceErrorStage.INDEXING
    claimed: bool = False


async def link_memory(
    engine: Engine,
    source_id: str,
    *,
    token: str | None = None,
    about: Sequence[str] = (),
    include_empty: bool = False,
) -> LinkOutcome:
    """Link one memory into the graph and record the outcome on its row.

    Args:
        engine: The engine of the memory's database.
        source_id: The memory's source id.
        token: The claim token to link under (``remember`` writes its row already
            claimed with one); None makes a fresh one.
        about: Names the memory is about, offered first in its seed.
        include_empty: Also relink a memory that linked with no facts.

    Returns:
        The outcome; the row holds the matching state.
    """
    with private_text():  # every log of the link, down to the commit, may quote the memory
        return await _link(engine, source_id, token=token, about=about, include_empty=include_empty)


async def _link(
    engine: Engine,
    source_id: str,
    *,
    token: str | None,
    about: Sequence[str],
    include_empty: bool,
) -> LinkOutcome:
    """:func:`link_memory`'s body, run inside ``private_text()``."""
    repo = link_repository(engine)
    token = token or new_claim_token()
    stage = _Stage()
    beat: asyncio.Task[None] | None = None
    try:
        if not await _claim(repo, source_id, token, include_empty=include_empty):
            return await _not_claimed(repo, source_id, include_empty=include_empty)
        stage.claimed = True
        beat = asyncio.create_task(_heartbeat(repo, source_id, token))
        outcome = await _link_claimed(engine, repo, source_id, token, stage, about=about)
    except asyncio.CancelledError:
        await asyncio.to_thread(repo.release, source_id, token)
        raise
    except Exception as error:
        log_failure(
            logger, "memory_link_failed", error, source_id=source_id, stage=stage.value.value
        )
        outcome = LinkOutcome.FAILED
    finally:
        if beat is not None:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await beat
    if outcome is not LinkOutcome.FAILED:
        return outcome
    if not stage.claimed:
        # the claim itself failed: no stage to blame, and the row is not ours to mark
        # (a row remember wrote already claimed goes stale and is linked again later)
        return LinkOutcome.FAILED
    return await _record_failure(repo, source_id, token, stage.value)


async def _claim(
    repo: MemoryLinkRepository, source_id: str, token: str, *, include_empty: bool
) -> bool:
    """Claim the row; a cancel arriving while the claim's thread runs waits for it to end.

    The thread cannot be stopped, so the claim may land after the cancel: the caller's
    release (on ``CancelledError``) must run after it, or the claim would be left behind.
    """
    now = datetime.now(UTC)
    claiming = asyncio.ensure_future(
        asyncio.to_thread(
            repo.claim,
            source_id,
            token,
            now=now,
            stale_before=now - timedelta(seconds=CLAIM_STALE_SECONDS),
            include_empty=include_empty,
        )
    )
    try:
        return await asyncio.shield(claiming)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await claiming
        raise


async def _record_failure(
    repo: MemoryLinkRepository, source_id: str, token: str, stage: SourceErrorStage
) -> LinkOutcome:
    """Mark the link failed at ``stage``; a mark that cannot be written still answers FAILED."""
    try:
        marked = await asyncio.to_thread(
            repo.mark_failed,
            source_id,
            token,
            stage=stage,
            reason=FAILURE_REASONS[stage],
            now=datetime.now(UTC),
        )
    except Exception as error:
        log_failure(logger, "memory_link_mark_failed_failed", error, source_id=source_id)
        return LinkOutcome.FAILED
    return LinkOutcome.FAILED if marked else await _lost(repo, source_id)


async def _lost(repo: MemoryLinkRepository, source_id: str) -> LinkOutcome:
    """A mark found the claim gone: the memory was forgotten, or another holder took it over."""
    row = await asyncio.to_thread(repo.read_link_row, source_id)
    return LinkOutcome.GONE if row is None else LinkOutcome.ELSEWHERE


async def _not_claimed(
    repo: MemoryLinkRepository, source_id: str, *, include_empty: bool
) -> LinkOutcome:
    """Why a claim failed: the memory is gone, already linked, or linked elsewhere now.

    A memory already linked is told apart from one this call linked, so a sweep never
    counts another process's link as its own.
    """
    row = await asyncio.to_thread(repo.read_link_row, source_id)
    if row is None:
        return LinkOutcome.GONE
    if row.state is MemoryLinkState.LINKED or (
        row.state is MemoryLinkState.LINKED_EMPTY and not include_empty
    ):
        return LinkOutcome.ALREADY_LINKED
    return LinkOutcome.ELSEWHERE


async def _link_claimed(
    engine: Engine,
    repo: MemoryLinkRepository,
    source_id: str,
    token: str,
    stage: _Stage,
    *,
    about: Sequence[str],
) -> LinkOutcome:
    """Steps 2 to 5 under a held claim. ``FAILED`` means: record a failure at ``stage``."""
    engine.expire_cached_rows()  # the commit reads what the claim wrote on its own session
    row = await asyncio.to_thread(repo.read_link_row, source_id)
    if row is None:
        return LinkOutcome.GONE
    if not row.indexing_complete:  # the store never finished (failed, or killed midway)
        await engine.chunk_and_index(source_id, row.full_text or "")
    if not extraction_model_configured(engine.settings):
        marked = await asyncio.to_thread(repo.mark_no_model, source_id, token)
        return LinkOutcome.NO_MODEL if marked else await _lost(repo, source_id)
    if not row.commit_complete:
        stage.value = SourceErrorStage.EXTRACTION
        seed = _seed(engine, row, about)

        def on_progress(step: str, _result: Any) -> None:
            """Move the failure stage on once extraction is done."""
            if step == "extraction":  # extracted and finalized: the commit is next
                stage.value = SourceErrorStage.COMMIT

        result = await engine.extract_and_commit(
            source_id,
            seed=seed.records(_by(engine, row)),
            offered_nodes=seed.attach_ids,
            offered_types=seed.templates,
            forced_domain="generic",
            strict=True,
            on_progress=on_progress,
        )
        followups_complete = result.followups_complete
    else:
        stage.value = SourceErrorStage.AFTER_COMMIT
        followups_complete = await engine.commit_service.finish_memory(
            source_id, join=_seed(engine, row, about).attach_ids
        )
    if not followups_complete:
        # memory_linked_at stays NULL: the next link runs finish_memory
        stage.value = SourceErrorStage.AFTER_COMMIT
        return LinkOutcome.FAILED
    empty = not engine.graph_repository.member_node_ids(source_id)
    marked = await asyncio.to_thread(
        repo.mark_linked, source_id, token, empty=empty, now=datetime.now(UTC)
    )
    if not marked:
        return await _lost(repo, source_id)
    return LinkOutcome.LINKED_EMPTY if empty else LinkOutcome.LINKED


def _log_sweep_end(task: asyncio.Task[RelinkReport]) -> None:
    """Retrieve the sweep's end, so a sweep that raised is logged, never lost."""
    if task.cancelled():
        return
    if (error := task.exception()) is not None:
        with private_text():
            log_failure(logger, "memory_relink_sweep_failed", error)


class MemoryLinker:
    """The links one server runs in the background, released on shutdown.

    ``remember`` waits for its link only so long; the link goes on as a task held here,
    and :meth:`close` cancels every live one, so each gives its claim back.
    """

    def __init__(self, engine: Engine) -> None:
        """Link memories of ``engine``'s database."""
        self._engine = engine
        self._tasks: set[asyncio.Task[LinkOutcome]] = set()
        self._sweep: asyncio.Task[RelinkReport] | None = None

    @property
    def sweep_task(self) -> asyncio.Task[RelinkReport] | None:
        """The start-up sweep's task, once :meth:`start_sweep` has started it."""
        return self._sweep

    def start_sweep(self) -> asyncio.Task[RelinkReport] | None:
        """Start the start-up sweep in the background; once per linker (None after the first).

        The sweep honours each failed memory's backoff and links nothing while no
        extraction model is configured; :meth:`close` stops it, giving its claim back.
        """
        if self._sweep is not None:
            return None
        from chaoscypher_core.mcp.memory_relink import relink_pending  # it imports this module

        self._sweep = asyncio.create_task(
            relink_pending(self._engine, honour_backoff=True), context=outside_private_text()
        )
        self._sweep.add_done_callback(_log_sweep_end)
        return self._sweep

    def spawn(
        self, source_id: str, *, token: str, about: Sequence[str] = ()
    ) -> asyncio.Task[LinkOutcome]:
        """Start linking ``source_id`` under ``token``; the task is kept until it is done."""
        # the link marks its own logs private; the task's context does not carry the caller's
        task = asyncio.create_task(
            link_memory(self._engine, source_id, token=token, about=about),
            context=outside_private_text(),
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def close(self) -> None:
        """Cancel the sweep and every live link, and wait for each to give its claim back."""
        tasks: list[asyncio.Task[Any]] = list(self._tasks)
        if self._sweep is not None and not self._sweep.done():
            tasks.append(self._sweep)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = [
    "BACKOFF_CAP_SECONDS",
    "CLAIM_HEARTBEAT_SECONDS",
    "CLAIM_STALE_SECONDS",
    "FAILURE_REASONS",
    "LinkOutcome",
    "LinkView",
    "MemoryLinker",
    "extraction_model_configured",
    "link_db",
    "link_memory",
    "link_repository",
    "link_view",
    "new_claim_token",
]
