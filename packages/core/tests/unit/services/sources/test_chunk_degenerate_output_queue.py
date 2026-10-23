# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Every kind of degenerate output climbs the ladder on the queue path; one attempt, one requeue.

Audit wave 7: ``unparseable_output_error`` had no caller, so a group whose
every line failed to parse made one call and completed with nothing; the
queue's chunk handler and the CLI loop each checked empty output and loop
aborts on their own. Both now run one check
(``chunk_failures.degenerate_output_check``). These tests drive the queue's
real chunk handler and the real extractor against a fake provider, per kind
(empty output, loop abort, unparseable): the degenerate try is requeued one
rung up and the next rung's good output completes with its entities; on
the last attempt a loop abort or an unparseable output is kept.

Duplicate delivery (the same wave): two deliveries of one chunk task at
rung 0 that both failed each wrote the next rung, so the row reached rung 2
and the chunk was queued twice. A delivery now records a failure only while
the row's retry count is the one it started at (the delivery token).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chaoscypher_core.settings import EngineSettings
from tests.unit.services.sources.test_chunk_extraction_decode_fallback import (
    _CHUNK,
    _FakeProvider,
    _kept_output,
    _requeued,
    _run,
    _settings,
)
from tests.unit.services.sources.test_chunk_extraction_empty_output_retry import _make_adapter


def _done(content: str, tokens: int = 20) -> dict[str, Any]:
    """The provider's closing chunk for ``content``."""
    return {
        "type": "done",
        "content": content,
        "usage": {"prompt_tokens": 900, "completion_tokens": tokens},
        "finish_reason": "stop",
    }


_UNPARSEABLE = "E|x\nE|y\n"
"""Two entity lines with too few fields: the parser drops both."""

_LOOP = "E|Pierre|Character||1.0|S1|A guest\n" + "".join(f"P|0|age|{n}\n" for n in range(6))
"""One entity, then the same property key five times running: the stream's loop guard aborts."""


def _lines(text: str) -> list[dict[str, Any]]:
    """``text`` streamed one line per chunk, then done."""
    return [{"type": "content", "delta": line} for line in text.splitlines(keepends=True)] + [
        _done(text)
    ]


_KINDS: dict[str, list[dict[str, Any]]] = {
    "empty_output": [{"type": "done", "usage": {"prompt_tokens": 900, "completion_tokens": 0}}],
    "loop_abort": _lines(_LOOP),
    "unparseable": [{"type": "content", "delta": _UNPARSEABLE}, _done(_UNPARSEABLE)],
}
"""Each degenerate kind's script."""


def _script(kind: str) -> list[dict[str, Any]]:
    """The degenerate script of ``kind``."""
    return _KINDS[kind]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_KINDS))
async def test_each_degenerate_kind_is_requeued_one_rung_up(kind: str) -> None:
    """A try at rung 0 with degenerate output is requeued at rung 1, its output not stored."""
    provider = _FakeProvider(_script(kind))
    adapter = await _run(provider, retry_count=0)
    assert _requeued(adapter)
    update = adapter.update_chunk_task.call_args.args[1]
    assert update["decode_fallback"] == {"step": 1}
    adapter.complete_chunk_task_with_output.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", sorted(_KINDS))
async def test_the_next_rung_completes_with_its_entities(kind: str) -> None:
    """After a degenerate try the next delivery asks at rung 1 and keeps its good output."""
    del kind  # whatever the degenerate kind was, the rung it left is what the next try reads
    provider = _FakeProvider(_kept_output())
    adapter = await _run(provider, retry_count=1, rung=1)
    assert provider.calls and set(provider.calls) == {(43, 0.0)}
    stored = adapter.complete_chunk_task_with_output.call_args.kwargs
    assert [e["name"] for e in stored["raw_entities"]] == ["Pierre"]
    assert stored["decode_fallback"]["step"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["loop_abort", "unparseable"])
async def test_the_last_attempt_keeps_a_loop_abort_or_an_unparseable_output(kind: str) -> None:
    """With no retry left the output is kept (a loop abort's partial, an unparseable group empty)."""
    adapter = await _run(_FakeProvider(_script(kind)), retry_count=3, rung=3)
    assert not _requeued(adapter)
    adapter.fail_chunk_task.assert_not_called()
    stored = adapter.complete_chunk_task_with_output.call_args.kwargs
    names = [e["name"] for e in stored["raw_entities"]]
    assert names == (["Pierre"] if kind == "loop_abort" else [])
    assert stored["aborted_by_loop"] is (kind == "loop_abort")


@pytest.mark.asyncio
async def test_an_empty_output_on_the_last_attempt_fails_the_chunk() -> None:
    """An empty output is never kept: with no retry left the chunk fails."""
    adapter = await _run(_FakeProvider(_script("empty_output")), retry_count=3, rung=3)
    assert not _requeued(adapter)
    assert adapter.fail_chunk_task.call_count == 1
    adapter.complete_chunk_task_with_output.assert_not_called()


# -- duplicate delivery -------------------------------------------------------------------------


class _GatedProvider(_FakeProvider):
    """A provider whose stream waits for a gate before answering."""

    def __init__(self, script: list[dict[str, Any]], gate: asyncio.Event | None) -> None:
        """``gate``: what the stream waits for (None: answer at once)."""
        super().__init__(script)
        self.gate = gate
        self.waiting = asyncio.Event()
        """Set when the stream starts waiting on the gate."""

    async def _stream(self) -> AsyncIterator[dict[str, Any]]:
        """Wait for the gate, then stream the script."""
        if self.gate is not None:
            self.waiting.set()
            await self.gate.wait()
        for chunk in self.script:
            yield chunk


def _live_adapter() -> tuple[MagicMock, dict[str, Any]]:
    """An adapter whose chunk-task row is one dict that ``update_chunk_task`` writes to."""
    adapter = _make_adapter(_CHUNK)
    row: dict[str, Any] = {
        "id": "ct-1",
        "job_id": "job-1",
        "status": "pending",
        "chunk_index": 0,
        "retry_count": 0,
        "decode_fallback": None,
    }
    adapter.get_chunk_task = MagicMock(side_effect=lambda _id: dict(row))
    adapter.update_chunk_task = MagicMock(side_effect=lambda _id, updates: row.update(updates))
    return adapter, row


@pytest.mark.asyncio
async def test_two_deliveries_of_one_attempt_requeue_it_once_at_rung_one() -> None:
    """Both read the row at retry 0, both fail: the row ends at retry 1, rung 1, queued once."""
    from chaoscypher_core.operations.extraction import chunk_extraction_service as ces
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )

    empty = _script("empty_output")
    adapter, row = _live_adapter()
    gate = asyncio.Event()
    first = _GatedProvider(empty, gate)  # waits until the second delivery has answered
    second = _GatedProvider(empty, None)
    providers = iter([first, second])

    def _provider(_self: Any) -> _FakeProvider:
        """Hand each delivery's extractor its own provider, in order."""
        return next(providers)

    data = {
        "chunk_task_id": "ct-1",
        "job_id": "job-1",
        "database_name": "default",
        "chunk_content": "",
        "chunk_index": 0,
        "small_chunk_ids": ["sc-1"],
        "node_templates_formatted": "- Character: a person",
        "edge_templates_formatted": "- knows (Knows): acquainted",
    }
    with (
        patch.object(AIEntityExtractor, "_get_llm_provider", _provider),
        patch("chaoscypher_core.app_config.get_settings", return_value=_settings()),
        patch(
            "chaoscypher_core.app_config.engine_factory.build_engine_settings",
            return_value=EngineSettings(),
        ),
    ):
        service = ces.ChunkExtractionOperationsService(source_repository=adapter)
        service.queue_extract_chunk = AsyncMock()  # type: ignore[method-assign]

        async def _second_then_open_gate() -> dict[str, Any]:
            """The second delivery, started once the first is waiting on its stream."""
            await first.waiting.wait()
            try:
                return await service._extract_chunk_handler(data=data, metadata=None, task_id="t-2")
            finally:
                gate.set()

        results = await asyncio.gather(
            service._extract_chunk_handler(data=data, metadata=None, task_id="t-1"),
            _second_then_open_gate(),
        )

    assert first.calls == [(42, 0.0)] and second.calls == [(42, 0.0)]  # both at rung 0
    assert service.queue_extract_chunk.await_count == 1
    assert row["retry_count"] == 1
    assert row["decode_fallback"] == {"step": 1}
    assert row["status"] == "queued"
    adapter.fail_chunk_task.assert_not_called()
    assert [r.get("reason") for r in results] == ["attempt_handled_by_other_delivery", None]


@pytest.mark.asyncio
async def test_a_cancellation_of_a_superseded_delivery_writes_nothing() -> None:
    """The cancellation handler reads the same token: a delivery another already requeued is a no-op."""
    from chaoscypher_core.operations.extraction import chunk_extraction_service as ces

    adapter, row = _live_adapter()
    row["retry_count"] = 1  # another delivery of attempt 0 already requeued it
    service = ces.ChunkExtractionOperationsService(source_repository=adapter)
    service.queue_extract_chunk = AsyncMock()  # type: ignore[method-assign]
    result = await service._handle_chunk_cancellation(
        adapter, {}, "ct-1", "job-1", "default", 0, _settings(), expected_retries=0
    )
    assert result["reason"] == "attempt_handled_by_other_delivery"
    adapter.update_chunk_task.assert_not_called()
    service.queue_extract_chunk.assert_not_awaited()
    assert row["retry_count"] == 1

    # the delivery that still owns the attempt requeues it
    result = await service._handle_chunk_cancellation(
        adapter, {}, "ct-1", "job-1", "default", 0, _settings(), expected_retries=1
    )
    assert result["retry_count"] == 2
    assert service.queue_extract_chunk.await_count == 1
