# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Memory servers in real processes: one ``spawn`` process per server, one database file.

Several servers on one database are tested in processes of their own, never threads: threads
share the process's locks and sessions and would prove nothing (spec 2026-10-24 D10). Each
process builds the memory world of ``memory_world.py`` (a real engine, the embedder faked, a
scripted extraction model of its own) on the shared directory, waits on an optional barrier
so several start together, and drives the server through its ``CallToolRequest`` handler the
way a host does. What it saw comes back on a queue, as tuples:

- ``("held",)``: the ``"hold"`` model has been called and is waiting (it never answers);
- ``("sweep", report)``: the start-up sweep's ``RelinkReport`` as a dict (``sweep=True``);
- ``("call", text, is_error)``: one per tool call, in order;
- ``("done", {"model_calls": n})``: the process is about to exit; ``n`` model calls were made.

A ``"hold"`` process never sends ``done``: it waits for the parent to kill it.

Usage, with the :func:`servers` fixture (it kills and joins every process it started when
the test ends, passed or failed)::

    process, out = servers.spawn(home, calls, model="ok")
    messages = collect(out, until="done", process=process)
    process.join(JOIN_SECONDS)

Several writers on one database (spec 2026-10-24 § 3) run plans of steps instead: each
process walks its list of :data:`Step` (open a database, call a tool, signal or wait on a
named event, wait on the barrier, pause, hold the writer lock), and
:meth:`ServerProcesses.run_writers` returns what every process saw as a :class:`Run`::

    plan = [Open(str(home)), Barrier(), Call("remember", LYON)]
    run = servers.run_writers([plan, plan])
    run.assert_clean()

Each process starts with this package's root on its import path, so the child imports
``tests.*`` whatever import mode the parent's pytest runs in. ``log_level`` sets up the real
logging (``configure_logging``, to stderr, as ``chaoscypher mcp`` does) in the child.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import multiprocessing
import multiprocessing.process
import multiprocessing.queues
import multiprocessing.synchronize
import queue
import re
import sys
import time
import traceback
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import patch

import mcp.types
import pytest

from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.mcp.memory_link import MemoryLinker
from chaoscypher_core.mcp.server import create_mcp_server
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
)
from tests.fakes.scripted_llm import Script, ScriptedExtractionLLM
from tests.unit.mcp.memory_world import (
    WORLD_SCRIPT,
    build_world,
    call_raw,
    engine_at,
    settings,
)


if TYPE_CHECKING:
    from chaoscypher_core import Engine

JOIN_SECONDS = 120
"""The longest a test waits on a process or its next message before it fails."""

Model = Literal["ok", "hold", "raise", "none"]
"""``ok``: the scripted model answers; ``hold``: it is called and never answers; ``raise``: it
raises an error that quotes the text it was given; ``none``: no extraction model is
configured (``llm.chat_provider`` empty)."""

_INITIALIZED = mcp.types.InitializedNotification(method="notifications/initialized")
_SPAWN = multiprocessing.get_context("spawn")
_PACKAGE_ROOT = str(Path(__file__).resolve().parents[3])
"""The core package's root, where ``tests`` is importable from."""


def barrier(parties: int) -> multiprocessing.synchronize.Barrier:
    """A barrier ``parties`` server processes wait on, so they start together."""
    return _SPAWN.Barrier(parties)


@contextlib.contextmanager
def _package_root_importable() -> Iterator[None]:
    """Put the package root on ``sys.path`` while a process starts (spawn copies the path then).

    Under ``--import-mode=importlib`` pytest imports ``tests.*`` without it on the path, and a
    spawned child that unpickles :func:`run_tool_calls` by name would not find ``tests``.
    """
    added = _PACKAGE_ROOT not in sys.path
    if added:
        sys.path.insert(0, _PACKAGE_ROOT)
    try:
        yield
    finally:
        if added:
            sys.path.remove(_PACKAGE_ROOT)


@dataclasses.dataclass(frozen=True)
class Open:
    """Close the database open now (if any) and open the memory world on ``data_dir``."""

    data_dir: str


@dataclasses.dataclass(frozen=True)
class Call:
    """Call one tool through the server's ``CallToolRequest`` handler; one :class:`Result`."""

    tool: str
    args: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class ForgetMatching:
    """Forget the first memory ``query`` matches: the preview, then ``confirm``; two results."""

    query: str


@dataclasses.dataclass(frozen=True)
class Signal:
    """Set the named event."""

    event: str


@dataclasses.dataclass(frozen=True)
class Wait:
    """Wait for the named event (failing after :data:`JOIN_SECONDS`)."""

    event: str


@dataclasses.dataclass(frozen=True)
class Barrier:
    """Wait until every process of the run reaches its own ``Barrier`` step."""


@dataclasses.dataclass(frozen=True)
class Pause:
    """Sleep ``ms`` milliseconds (the jitter between two writers)."""

    ms: int


@dataclasses.dataclass(frozen=True)
class HoldWriterLock:
    """Take the writer lock on the world's storage session, set ``signal``, wait to be killed."""

    signal: str


Step = Open | Call | ForgetMatching | Signal | Wait | Barrier | Pause | HoldWriterLock
"""One step of a writer's plan."""


@dataclasses.dataclass(frozen=True)
class Result:
    """One tool call's answer and how long it took."""

    tool: str
    text: str
    is_error: bool
    seconds: float


@dataclasses.dataclass(frozen=True)
class Options:
    """How every writer of a run opens its world.

    ``script`` names a :data:`SCRIPTS` entry; ``vectors`` is ``search.enable_vector_search``;
    ``busy_timeout_ms`` (None: the fixed 60 s) is the database engine's busy wait.
    """

    script: str = "writers"
    vectors: bool = True
    busy_timeout_ms: int | None = None


@dataclasses.dataclass(frozen=True)
class Sync:
    """The run's barrier and named events (multiprocessing primitives of the spawn context)."""

    barrier: Any
    events: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class Run:
    """What the writers of one run saw: each one's results, errors and exit codes."""

    results: dict[int, list[Result]]
    errors: dict[int, str]
    exitcodes: list[int | None]

    def assert_clean(self, killed: Collection[int] = ()) -> None:
        """No writer raised, and every writer not in ``killed`` exited 0."""
        assert not self.errors, "\n\n".join(f"writer {i}:\n{tb}" for i, tb in self.errors.items())
        for index, code in enumerate(self.exitcodes):
            if index not in killed:
                assert code == 0, f"writer {index} exited {code}"


_SCRIPT_EVENTS = ("model_entered", "model_release")
"""The events the ``"blocking"`` script uses; every run has them."""


def _drain(
    out: multiprocessing.queues.Queue[tuple[int, list[Result] | None, str | None]],
    *,
    wait: float,
) -> list[tuple[int, list[Result] | None, str | None]]:
    """Every entry on ``out`` now; the first waited for up to ``wait`` seconds."""
    entries = []
    try:
        entries.append(out.get(timeout=wait) if wait else out.get_nowait())
        while True:
            entries.append(out.get_nowait())
    except queue.Empty:
        pass
    return entries


class ServerProcesses:
    """The server processes one test started; :meth:`stop` kills and joins every live one."""

    def __init__(self) -> None:
        """No process yet."""
        self._started: list[multiprocessing.process.BaseProcess] = []

    def spawn(
        self, home: str | Path, calls: Sequence[tuple[str, dict[str, Any]]], **kwargs: Any
    ) -> tuple[multiprocessing.process.BaseProcess, multiprocessing.queues.Queue[tuple[Any, ...]]]:
        """Start :func:`run_tool_calls` in a spawn process; the process and its message queue.

        ``kwargs`` are :func:`run_tool_calls`'s keyword arguments except ``out``.
        """
        out: multiprocessing.queues.Queue[tuple[Any, ...]] = _SPAWN.Queue()
        process = self._start(run_tool_calls, (str(home), list(calls)), {"out": out, **kwargs})
        return process, out

    def run_writers(
        self,
        plans: Sequence[Sequence[Step]],
        *,
        options: Options = Options(),
        events: Sequence[str] = (),
        parent: Callable[[Sync, Sequence[multiprocessing.process.BaseProcess]], None] | None = None,
        timeout_s: float = 300.0,
    ) -> Run:
        """Run one writer process per plan, all at once; what each saw.

        Every process shares one barrier (of ``len(plans)``) and the named ``events``
        (plus ``model_entered`` and ``model_release``, the ``"blocking"`` script's).
        ``parent`` runs here once every process has started (it may kill one). A process
        the parent killed sends nothing; its exit code is negative.

        Raises:
            AssertionError: A process had not finished after ``timeout_s`` (every live
                process is killed first).
        """
        sync = Sync(
            barrier=barrier(len(plans)),
            events={name: _SPAWN.Event() for name in (*events, *_SCRIPT_EVENTS)},
        )
        out: multiprocessing.queues.Queue[tuple[int, list[Result] | None, str | None]] = (
            _SPAWN.Queue()
        )
        procs = [
            self._start(_worker, (index, list(plan), options, sync, out))
            for index, plan in enumerate(plans)
        ]
        if parent is not None:
            parent(sync, procs)
        results: dict[int, list[Result]] = {}
        errors: dict[int, str] = {}

        def record(entries: list[tuple[int, list[Result] | None, str | None]]) -> None:
            for index, got, error in entries:
                pending.discard(index)
                if error is not None:
                    errors[index] = error
                else:
                    results[index] = got or []

        pending = set(range(len(procs)))
        deadline = time.monotonic() + timeout_s
        while pending:
            record(_drain(out, wait=1.0))
            gone = {i for i in pending if not procs[i].is_alive()}
            if gone:
                record(_drain(out, wait=0.0))  # a process flushes its entry before it exits
                pending -= gone  # ended without one: killed
            if pending and time.monotonic() > deadline:
                for i in pending:
                    procs[i].kill()
                msg = f"writers {sorted(pending)} did not finish in {timeout_s} s"
                raise AssertionError(msg)
        for process in procs:
            process.join(JOIN_SECONDS)
        return Run(results=results, errors=errors, exitcodes=[p.exitcode for p in procs])

    def _start(
        self,
        target: Callable[..., None],
        args: tuple[Any, ...],
        kwargs: Mapping[str, Any] | None = None,
    ) -> multiprocessing.process.BaseProcess:
        """Start ``target`` in a spawn process with the package root importable."""
        process = _SPAWN.Process(target=target, args=args, kwargs=dict(kwargs or {}), daemon=True)
        with _package_root_importable():
            process.start()
        self._started.append(process)
        return process

    def stop(self) -> None:
        """Kill every process still alive and join each, so none outlives its test."""
        for process in self._started:
            if process.is_alive():
                process.kill()
            process.join(JOIN_SECONDS)


@pytest.fixture
def servers() -> Iterator[ServerProcesses]:
    """A test's server processes, every one killed and joined when the test ends."""
    started = ServerProcesses()
    try:
        yield started
    finally:
        started.stop()


def run_tool_calls(
    home: str,
    calls: Sequence[tuple[str, dict[str, Any]]],
    *,
    model: Model,
    out: multiprocessing.queues.Queue[tuple[Any, ...]],
    start: multiprocessing.synchronize.Barrier | None = None,
    sweep: bool = False,
    wait_seconds: int | None = None,
    script: Mapping[str, Script] | None = None,
    log_level: str | None = None,
) -> None:
    """A write-mode memory server on ``home`` in this process: the sweep, then ``calls``.

    The target of a ``spawn`` process (module level, every argument picklable).

    Args:
        home: The directory the database lives in (the same for every process).
        calls: The tool calls to make, ``(name, arguments)``, after the sweep.
        model: How the extraction model behaves (:data:`Model`).
        out: Where the messages go (see the module docstring).
        start: A barrier every process of a test waits on before it begins.
        sweep: Fire the handshake's end and await the start-up sweep first.
        wait_seconds: ``mcp.memory.wait_seconds`` (None keeps the default).
        script: The model's script (None: the memory world's).
        log_level: Set up the real logging at this level, to stderr (None: structlog's
            default, which prints to stdout).
    """
    if log_level is not None:
        import logging

        from chaoscypher_core.utils.logging import configure_logging

        # chaoscypher_core.testing pre-empts the once-per-process guard for test workers;
        # this child is a server of its own, so it lifts it and sets logging up for real
        logging._chaoscypher_logging_configured = False  # type: ignore[attr-defined]
        configure_logging(log_level=log_level, stream=sys.stderr)
    # nosemgrep: cc-041-asyncio-run-in-tests - a spawned process's entry point, not a test: no pytest loop runs here
    asyncio.run(
        _serve(
            Path(home),
            calls,
            model=model,
            out=out,
            start=start,
            sweep=sweep,
            wait_seconds=wait_seconds,
            script=WORLD_SCRIPT if script is None else script,
        )
    )


async def _serve(
    home: Path,
    calls: Sequence[tuple[str, dict[str, Any]]],
    *,
    model: Model,
    out: multiprocessing.queues.Queue[tuple[Any, ...]],
    start: multiprocessing.synchronize.Barrier | None,
    sweep: bool,
    wait_seconds: int | None,
    script: Mapping[str, Script],
) -> None:
    s = settings()
    if model == "none":
        s.llm.chat_provider = ""
    if wait_seconds is not None:
        s.mcp.memory.wait_seconds = wait_seconds
    never = asyncio.Event()
    # "" is in every prompt: a held model waits on every call
    raising = {key: LLMError(f"cannot read the prompt: {key}") for key in script}
    llm = ScriptedExtractionLLM(
        dict(script),
        hold_on={"": never} if model == "hold" else None,
        raise_on=raising if model == "raise" else None,
    )
    with patch.object(AIEntityExtractor, "_get_llm_provider", lambda _self: llm):
        engine = engine_at(home, s)
        linker = MemoryLinker(engine)
        server = create_mcp_server(engine, linker=linker)
        watch = asyncio.create_task(_say_held(llm, out)) if model == "hold" else None
        try:
            if start is not None:
                await asyncio.to_thread(start.wait, JOIN_SECONDS)
            if sweep:
                await server.notification_handlers[mcp.types.InitializedNotification](_INITIALIZED)
                task = linker.sweep_task
                assert task is not None, "the server started no sweep"
                out.put(("sweep", dataclasses.asdict(await task)))
            for name, args in calls:
                out.put(("call", *await call_raw(server, name, args)))
            if model == "hold":
                await never.wait()  # the parent kills this process
            out.put(("done", {"model_calls": llm.call_count}))
        finally:
            if watch is not None:
                watch.cancel()
            await linker.close()
            engine.close()


async def _say_held(llm: ScriptedExtractionLLM, out: multiprocessing.queues.Queue[Any]) -> None:
    """Send ``("held",)`` once the held model has been called (polled: the fake has no hook)."""
    while llm.call_count == 0:  # noqa: ASYNC110 - polls a counter, there is no event to wait on
        await asyncio.sleep(0.01)
    out.put(("held",))


def collect(
    out: multiprocessing.queues.Queue[tuple[Any, ...]],
    *,
    until: str,
    also: str | None = None,
    process: multiprocessing.process.BaseProcess | None = None,
) -> list[tuple[Any, ...]]:
    """The messages a process sends, up to its first ``until`` (and an ``also``, when named).

    Raises:
        AssertionError: No such message came within :data:`JOIN_SECONDS`, or ``process``
            ended without sending it.
    """
    messages: list[tuple[Any, ...]] = []
    wanted = {until} | ({also} if also else set())
    deadline = time.monotonic() + JOIN_SECONDS
    while not wanted <= {m[0] for m in messages}:
        try:
            messages.append(out.get(timeout=1))
            continue
        except queue.Empty:
            pass
        if process is not None and not process.is_alive():
            msg = f"the process ended (exit {process.exitcode}) before {sorted(wanted)}: {messages}"
            raise AssertionError(msg)
        if time.monotonic() > deadline:
            msg = f"no {sorted(wanted)} message in {JOIN_SECONDS} s; got {messages}"
            raise AssertionError(msg)
    return messages


# ---------------------------------------------------------------------------
# Several writers: the fixtures, the scripts and the step worker
# ---------------------------------------------------------------------------

GUESTS: tuple[str, ...] = tuple(
    a + b
    for a in ("Ka", "Lo", "Mi", "Ru", "Ta", "Ve", "Zo", "Ne")
    for b in ("dris", "menk", "valt", "sorn", "pelt")
)
"""Forty invented one-word guests of the Otradnoe ball."""


def guest_memory(name: str) -> str:
    """A guest's memory text: one guest, the estate, and the guest located there."""
    return f"{name} danced at Otradnoe."


def _memory(text: str, by: str, valid_from: str) -> dict[str, Any]:
    return {"text": text, "by": by, "valid_from": valid_from}


LYON = _memory("Pierre Bezukhov moved to Lyon.", "alice", "2026-10-20")
ROME = _memory("Pierre Bezukhov moved to Rome.", "alice", "2026-10-20")
"""Saved after Lyon, on the same day: Lyon, then Rome, then Kyiv."""
KYIV = _memory("Pierre Bezukhov moved to Kyiv.", "bob", "2026-10-21")
MARYA_PLAYS = _memory("Marya Bolkonskaya plays the clavichord.", "alice", "2026-10-20")
MARYA_LIVES = _memory("Marya Bolkonskaya lives at Bald Hills.", "bob", "2026-10-20")
LISE_PARIS = _memory("Lise Bolkonskaya lives in Paris.", "bob", "2026-10-20")
LISE_VIENNA = _memory("Lise Bolkonskaya moved to Vienna.", "bob", "2026-10-21")
FRIEND = _memory("Pierre Bezukhov is a friend of Anna Pavlovna.", "bob", "2026-10-22")
POND = _memory("Lise Bolkonskaya walked to the pond.", "alice", "2026-10-20")

_PIERRE = "E|Pierre Bezukhov|Person|Pierre|0.9|S1|A count\n"
_MARYA = "E|Marya Bolkonskaya|Person|Marya|0.9|S1|A princess\n"
_LISE = "E|Lise Bolkonskaya|Person|Lise|0.9|S1|A princess\n"

WRITERS_SCRIPT: dict[str, Script] = {
    **WORLD_SCRIPT,
    KYIV["text"]: (
        _PIERRE + "E|Kyiv|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to Kyiv\n",
    ),
    ROME["text"]: (
        _PIERRE + "E|Rome|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Pierre Bezukhov moved to Rome\n",
    ),
    MARYA_PLAYS["text"]: (_MARYA, ""),
    MARYA_LIVES["text"]: (
        _MARYA + "E|Bald Hills|Location||0.9|S1|An estate\n",
        "R|0|1|located_in|0.9|S1|Marya Bolkonskaya lives at Bald Hills\n",
    ),
    LISE_PARIS["text"]: (
        _LISE + "E|Paris|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Lise Bolkonskaya lives in Paris\n",
    ),
    LISE_VIENNA["text"]: (
        _LISE + "E|Vienna|Location||0.9|S1|A city\n",
        "R|0|1|located_in|0.9|S1|Lise Bolkonskaya moved to Vienna\n",
    ),
    POND["text"]: (_LISE, ""),
    **{
        guest_memory(name): (
            f"E|{name}|Person||0.9|S1|A guest\nE|Otradnoe|Location||0.9|S1|An estate\n",
            f"R|0|1|located_in|0.9|S1|{name} danced at Otradnoe\n",
        )
        for name in GUESTS
    },
}
"""The memory world's script plus every memory of the several-writers tests."""


def _blocking_script(sync: Sync) -> dict[str, Script]:
    """:data:`WRITERS_SCRIPT` with a model that, on its first ``POND`` call, blocks.

    It sets ``model_entered`` and waits (up to :data:`JOIN_SECONDS`) for ``model_release``:
    a model still thinking while another process tries the writer lock.
    """
    entered = False

    def pond(_prompt: str) -> tuple[str, str]:
        nonlocal entered
        if not entered:
            entered = True
            sync.events["model_entered"].set()
            assert sync.events["model_release"].wait(JOIN_SECONDS), "model_release never came"
        return (_LISE, "")

    return {**WRITERS_SCRIPT, POND["text"]: pond}


SCRIPTS: dict[str, Callable[[Sync], dict[str, Script]]] = {
    "writers": lambda _sync: WRITERS_SCRIPT,
    "blocking": _blocking_script,
}
"""The model scripts a run's writers can use (:attr:`Options.script`)."""


def db_path(data_dir: Path) -> Path:
    """The memory world's database file under ``data_dir``."""
    return data_dir / "databases" / "test" / "app.db"


def open_world(data_dir: Path) -> Engine:
    """The test's own engine on a writers' database, to read what they left; close it."""
    return engine_at(data_dir, settings())


_HANDLE = re.compile(r'forget\(handle="([^"]+)"')


def _worker(
    index: int,
    plan: Sequence[Step],
    options: Options,
    sync: Sync,
    out: multiprocessing.queues.Queue[tuple[int, list[Result] | None, str | None]],
) -> None:
    """One writer process: walk ``plan``, then put ``(index, results, None)`` on ``out``.

    The target of a ``spawn`` process. On any error it puts ``(index, None, traceback)``
    and exits 1.
    """
    try:
        # nosemgrep: cc-041-asyncio-run-in-tests - a spawned process's entry point, not a test: no pytest loop runs here
        results = asyncio.run(_walk(plan, options, sync))
    except BaseException:
        out.put((index, None, traceback.format_exc()))
        out.close()
        out.join_thread()
        sys.exit(1)
    out.put((index, results, None))


async def _walk(plan: Sequence[Step], options: Options, sync: Sync) -> list[Result]:
    """Run ``plan``'s steps in order; every tool call's :class:`Result`."""
    script = SCRIPTS[options.script](sync)
    results: list[Result] = []
    world: SimpleNamespace | None = None
    with pytest.MonkeyPatch.context() as mp:
        try:
            for step in plan:
                match step:
                    case Open(data_dir=d):
                        await _close(world)
                        world = _open(Path(d), mp, script, options)
                    case Call(tool=tool, args=args):
                        results.append(await _timed(_need(world), tool, args))
                    case ForgetMatching(query=query):
                        preview = await _timed(_need(world), "forget", {"query": query})
                        results.append(preview)
                        found = _HANDLE.search(preview.text)
                        assert found, f"no handle in the preview: {preview.text!r}"
                        args = {"handle": found.group(1), "confirm": True}
                        results.append(await _timed(_need(world), "forget", args))
                    case Signal(event=name):
                        sync.events[name].set()
                    case Wait(event=name):
                        waited = await asyncio.to_thread(sync.events[name].wait, JOIN_SECONDS)
                        assert waited, f"event {name!r} never came"
                    case Barrier():
                        await asyncio.to_thread(sync.barrier.wait, JOIN_SECONDS)
                    case Pause(ms=ms):
                        await asyncio.sleep(ms / 1000)
                    case HoldWriterLock(signal=name):
                        connection = _need(world).storage.session.connection()
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                        sync.events[name].set()
                        await asyncio.Event().wait()  # the parent kills this process
        finally:
            await _close(world)
    return results


def _open(
    data_dir: Path, mp: pytest.MonkeyPatch, script: dict[str, Script], options: Options
) -> SimpleNamespace:
    """The memory world on ``data_dir`` as ``options`` say (the sweep off, a long link wait)."""
    if options.busy_timeout_ms is not None:
        # the engine cache keeps the first caller's values: this short wait, before the world's
        get_engine(
            db_path(data_dir),
            busy_timeout_ms=options.busy_timeout_ms,
            connection_timeout=max(1, options.busy_timeout_ms // 1000),
        )
    s = settings(vectors=options.vectors)
    s.mcp.memory.wait_seconds = 120
    s.mcp.memory.relink_on_start = False
    return build_world(data_dir, mp, script=script, s=s)


async def _close(world: SimpleNamespace | None) -> None:
    if world is not None:
        await world.linker.close()
        world.engine.close()


def _need(world: SimpleNamespace | None) -> SimpleNamespace:
    assert world is not None, "a step needs an Open first"
    return world


async def _timed(world: SimpleNamespace, tool: str, args: dict[str, Any]) -> Result:
    began = time.monotonic()
    text, is_error = await call_raw(world.server, tool, args)
    return Result(tool=tool, text=text, is_error=is_error, seconds=time.monotonic() - began)
