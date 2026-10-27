# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The stdio MCP server's input lines, read so that waiting for one can be cancelled.

The MCP library reads stdin with ``anyio.wrap_file``: each line is read in a worker thread
that a cancellation waits for, so a server told to stop (SIGTERM) sat until the client sent
its next line, and its cleanup (the memory linker giving its claims back) never ran.

:class:`StdinLines` reads on a daemon thread of its own and hands each line over through a
bounded ``asyncio.Queue``; waiting on the queue is cancelled at once. The thread reads the
raw file descriptor with ``os.read`` and never touches the ``BufferedReader`` over it: a
daemon thread still inside the reader's ``read`` at interpreter shutdown holds its lock,
and Python aborts (``could not acquire lock ... at interpreter shutdown``) instead of
exiting. Lines decode as the library reads them: UTF-8 with replacement, universal
newlines (CR LF and a lone CR become LF), a last line without a newline
kept as it is.
"""

from __future__ import annotations

import asyncio
import codecs
import concurrent.futures
import io
import os
import sys
import threading
from typing import IO


QUEUE_LINES = 64
"""The most lines read ahead of the server; past it the reader thread waits."""
_CHUNK_BYTES = 65536
_PUT_POLL_SECONDS = 0.5
"""How often a reader waiting on a full queue checks that the loop still runs."""


class StdinLines:
    """An async iterator of the text lines of ``stream`` (default: this process's stdin)."""

    def __init__(self, stream: IO[bytes] | None = None) -> None:
        """Read ``stream``'s file descriptor, once iteration starts."""
        self._stream = stream if stream is not None else sys.stdin.buffer
        self._queue: asyncio.Queue[str | None] | None = None

    def __aiter__(self) -> StdinLines:
        """This object is its own iterator."""
        return self

    async def __anext__(self) -> str:
        """The next line; the end of input ends the iteration."""
        if self._queue is None:
            self._queue = asyncio.Queue(maxsize=QUEUE_LINES)
            reader = threading.Thread(
                target=self.read_into,
                args=(asyncio.get_running_loop(), self._queue),
                name="mcp-stdin",
                daemon=True,
            )
            reader.start()
        line = await self._queue.get()
        if line is None:
            raise StopAsyncIteration
        return line

    def queued(self) -> int:
        """How many lines are read and not yet taken."""
        return 0 if self._queue is None else self._queue.qsize()

    def read_into(self, loop: asyncio.AbstractEventLoop, lines: asyncio.Queue[str | None]) -> None:
        """Read every line into ``lines`` on ``loop``, then None; stop quietly once the loop ends.

        Runs on the reader thread. Each line is joined from its parts once, so a very long
        line costs time in proportion to its length.
        """
        fd = self._stream.fileno()
        decoder = io.IncrementalNewlineDecoder(
            codecs.getincrementaldecoder("utf-8")("replace"), translate=True
        )
        parts: list[str] = []
        while True:
            try:
                chunk = os.read(fd, _CHUNK_BYTES)
            except OSError:  # the descriptor was closed under the thread
                chunk = b""
            text = decoder.decode(chunk, final=not chunk)
            start = 0
            while (end := text.find("\n", start)) != -1:
                parts.append(text[start : end + 1])
                if not _put(loop, lines, "".join(parts)):
                    return
                parts = []
                start = end + 1
            if start < len(text):
                parts.append(text[start:])
            if not chunk:
                break
        if parts and not _put(loop, lines, "".join(parts)):
            return
        _put(loop, lines, None)


def _put(
    loop: asyncio.AbstractEventLoop, lines: asyncio.Queue[str | None], line: str | None
) -> bool:
    """Put ``line`` on the loop's queue, waiting while it is full; False once the loop has ended."""
    putting = lines.put(line)
    try:
        put = asyncio.run_coroutine_threadsafe(putting, loop)
    except RuntimeError:  # the loop has closed
        putting.close()
        return False
    while True:
        try:
            put.result(timeout=_PUT_POLL_SECONDS)
        except concurrent.futures.TimeoutError:
            if loop.is_closed() or not loop.is_running():
                put.cancel()
                return False
            continue
        except concurrent.futures.CancelledError, RuntimeError:
            return False
        return True
