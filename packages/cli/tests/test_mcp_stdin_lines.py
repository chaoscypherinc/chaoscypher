# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The stdio server's stdin lines: read off the raw descriptor, on a daemon thread.

The MCP library reads stdin in a worker thread that a cancellation cannot stop, so a server
told to stop waited for the client's next line. ``StdinLines`` reads on a daemon thread
that never touches the ``BufferedReader`` (a daemon thread holding its lock at shutdown
aborts the interpreter), and decodes as the library does: UTF-8 with replacement and
universal newlines.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

import pytest

from chaoscypher_cli.mcp.stdin_lines import QUEUE_LINES, StdinLines


async def _lines(data: bytes) -> list[str]:
    read_fd, write_fd = os.pipe()
    writer = threading.Thread(target=lambda: (os.write(write_fd, data), os.close(write_fd)))
    writer.start()
    with os.fdopen(read_fd, "rb") as stream:
        lines = [line async for line in StdinLines(stream)]
    writer.join()
    return lines


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"one\ntwo \xc3\xa9\n", ["one\n", "two é\n"]),
        (b"crlf\r\nnext\r\n", ["crlf\n", "next\n"]),
        (b"lone\rcr\n", ["lone\n", "cr\n"]),
        (b"bad \xff\xfe bytes\n", ["bad �� bytes\n"]),
        (b"last line, no newline", ["last line, no newline"]),
        (b"", []),
    ],
    ids=["utf8", "crlf", "lone-cr", "invalid-utf8", "partial-last-line", "empty"],
)
async def test_lines_decode_as_the_library_reads_them(data: bytes, expected: list[str]) -> None:
    assert await _lines(data) == expected


async def test_a_five_megabyte_line_comes_whole_and_quickly() -> None:
    big = b"x" * (5 * 1024 * 1024)
    started = time.monotonic()
    assert await _lines(big + b"\nafter\n") == [big.decode() + "\n", "after\n"]
    assert time.monotonic() - started < 10


async def test_waiting_for_a_line_is_cancelled_at_once() -> None:
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb") as stream:
        lines = StdinLines(stream)
        waiting = asyncio.ensure_future(anext(aiter(lines)))
        await asyncio.sleep(0.05)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiting, timeout=2)
        os.close(write_fd)  # the reader thread sees the end of input and stops


async def test_the_queue_holds_a_bounded_number_of_lines() -> None:
    read_fd, write_fd = os.pipe()
    writer = threading.Thread(
        target=lambda: (os.write(write_fd, b"line\n" * 500), os.close(write_fd))
    )
    writer.start()
    with os.fdopen(read_fd, "rb") as stream:
        lines = StdinLines(stream)
        assert await anext(aiter(lines)) == "line\n"
        await asyncio.sleep(0.2)  # the thread fills the queue, then waits
        assert lines.queued() <= QUEUE_LINES
        rest = [line async for line in lines]
    writer.join()
    assert len(rest) == 499


def test_a_reader_whose_loop_has_closed_ends_quietly() -> None:
    errors: list[BaseException | None] = []
    hook = threading.excepthook
    threading.excepthook = lambda args: errors.append(args.exc_value)
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"one\ntwo\n")
    os.close(write_fd)
    loop = asyncio.new_event_loop()
    loop.close()
    try:
        with os.fdopen(read_fd, "rb") as stream:
            reader = threading.Thread(
                target=StdinLines(stream).read_into, args=(loop, asyncio.Queue())
            )
            reader.start()
            reader.join(timeout=5)
        assert not reader.is_alive()
    finally:
        threading.excepthook = hook
    assert errors == []
