# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""A failed link logs no word of the memory (spec 2026-10-24 D16).

The model raises an error that quotes the memory. Neither ``remember``, the link, nor the
relink may write that text, or a traceback carrying it, to a log at INFO or above.
"""

from __future__ import annotations

import re
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog.testing

from chaoscypher_core.exceptions import LLMError
from chaoscypher_core.mcp import memory_link, memory_relink
from chaoscypher_core.mcp.memory_relink import relink_pending
from chaoscypher_core.services.sources.engine.extraction.utils.template_extractor import (
    TemplateExtractor,
)
from chaoscypher_core.utils.logging.privacy import text_is_private, withhold_private_text
from tests.unit.mcp.memory_processes import (
    KYIV,
    ROME,
    WRITERS_SCRIPT,
    ServerProcesses,
    collect,
)
from tests.unit.mcp.memory_processes import LYON as LYON_MEMORY
from tests.unit.mcp.memory_world import build_world, call, store_pending


LYON = "Pierre Bezukhov moved to Lyon."
WORDS = ("Pierre", "Bezukhov", "Lyon")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


def _leaks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The events at INFO or above that carry a traceback or a word of the memory."""
    return [
        e
        for e in events
        if e.get("log_level") != "debug"
        and ("exc_info" in e or "exception" in e or any(w in repr(e) for w in WORDS))
    ]


async def test_a_failed_link_logs_no_memory_text(world: SimpleNamespace) -> None:
    world.llm.raise_on[LYON] = LLMError(f"cannot parse the prompt: {LYON}")
    with structlog.testing.capture_logs() as events:
        await call(world.server, "remember", {"text": LYON})
        report = await relink_pending(world.engine, honour_backoff=False)
    assert report.failed == 1
    assert {e["event"] for e in events} >= {"memory_link_failed"}
    assert _leaks(events) == []


def test_the_server_process_logs_no_memory_text(
    tmp_path: Path, capfd: pytest.CaptureFixture[str], servers: ServerProcesses
) -> None:
    """A stdio server's logs, rendered by the real logging setup (stderr, as ``mcp`` runs)."""
    process, out = servers.spawn(
        tmp_path, [("remember", {"text": LYON})], model="raise", log_level="INFO"
    )
    [(_, answer, _)] = [m for m in collect(out, until="done", process=process) if m[0] == "call"]
    process.join(120)
    assert "the model failed" in answer
    printed = capfd.readouterr()
    logs = printed.out + printed.err
    assert "memory_link_failed" in logs
    assert [line for line in logs.splitlines() if any(w in line for w in WORDS)] == []
    assert "Traceback" not in logs


async def test_private_text_reaches_the_threads_a_link_runs_in(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The template suggestions run off the loop; their logs must still be a memory's."""
    seen: list[bool] = []
    real = TemplateExtractor.generate_suggestions_from_entities

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(text_is_private())
        return real(*args, **kwargs)

    monkeypatch.setattr(TemplateExtractor, "generate_suggestions_from_entities", spy)
    await call(world.server, "remember", {"text": LYON})
    assert seen == [True]
    assert not text_is_private()  # and it is not left set behind the call


async def test_private_text_ends_with_each_link_in_the_sweep_and_the_linker(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever a task does after a link (a document, a later step) logs as usual."""
    after: list[bool] = []
    real = memory_link.link_memory

    async def spy(*args: Any, **kwargs: Any) -> Any:
        outcome = await real(*args, **kwargs)
        after.append(text_is_private())
        return outcome

    monkeypatch.setattr(memory_link, "link_memory", spy)  # the linker's tasks
    monkeypatch.setattr(memory_relink, "link_memory", spy)  # the sweep's
    await call(world.server, "remember", {"text": LYON})
    await store_pending(world.engine, "Anna Pavlovna moved to Paris.")
    sweep = world.linker.start_sweep()
    assert sweep is not None
    assert (await sweep).linked == 1
    assert after == [False, False]


async def test_a_forget_logs_no_memory_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forgetting Rome reopens Lyon and Kyiv closes it again: no name reaches the log.

    The records pass through the real privacy processor, as the server's logging runs it.
    """
    w = build_world(tmp_path, monkeypatch, script=WRITERS_SCRIPT)
    try:
        for memory in (LYON_MEMORY, ROME, KYIV):
            await call(w.server, "remember", memory)
        preview = await call(w.server, "forget", {"query": "Rome"})
        found = re.search(r'forget\(handle="([^"]+)"', preview)
        assert found, preview
        with structlog.testing.capture_logs(processors=[withhold_private_text]) as events:
            answer = await call(w.server, "forget", {"handle": found.group(1), "confirm": True})
        assert answer.startswith("Forgotten")
        assert "memory_fact_superseded" in {e["event"] for e in events}
        names = (*WORDS, "Rome", "Kyiv")
        leaks = [
            e
            for e in events
            if e.get("log_level") != "debug"
            and ("exc_info" in e or "exception" in e or any(n in repr(e) for n in names))
        ]
        assert leaks == []
    finally:
        await w.linker.close()
        w.engine.close()
