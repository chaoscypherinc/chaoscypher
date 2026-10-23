# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Polite request pacing for one site: a minimum gap between starts, bounded concurrency.

``CrawlThrottle`` is shared by a site's discovery and its import so every
request to the site, whichever step makes it, goes through one gate:
``web.crawl_concurrency`` requests in flight at most, and request starts at
least ``web.crawl_delay_seconds`` apart (raised to the site's robots.txt
``Crawl-delay`` when that is larger). The clock and the sleep are injectable so
tests can drive it without waiting.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager


class CrawlThrottle:
    """Gate every request to one site through a start-spacing lock and a semaphore.

    Args:
        delay_seconds: Minimum gap between two request starts.
        concurrency: Maximum requests in flight.
        clock: Monotonic clock (seconds).
        sleep: Coroutine function that waits a number of seconds.
    """

    def __init__(
        self,
        *,
        delay_seconds: float,
        concurrency: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Set the pacing; see the class docstring for the arguments."""
        self._delay = max(0.0, delay_seconds)
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._lock = asyncio.Lock()
        self._clock = clock
        self._sleep = sleep
        self._last_start: float | None = None

    @property
    def delay_seconds(self) -> float:
        """The gap currently enforced between request starts."""
        return self._delay

    def raise_delay(self, seconds: float) -> None:
        """Lengthen the gap to *seconds* if that is longer (a robots.txt Crawl-delay)."""
        self._delay = max(self._delay, seconds)

    def now(self) -> float:
        """The throttle's clock (monotonic seconds), for deadlines measured against it."""
        return self._clock()

    @asynccontextmanager
    async def slot(self, *, deadline: float | None = None) -> AsyncIterator[bool]:
        """Wait for a free slot and the start gap, then hold the slot for one request.

        Args:
            deadline: A time on :meth:`now`'s clock. When the request could
                start only after it, the slot does not wait for the gap and
                yields ``False``: the caller skips the request (and it does
                not count as a start).

        Yields:
            ``True`` when the request may go ahead.
        """
        async with self._semaphore:
            async with self._lock:
                allowed = await self._wait_for_start(deadline)
            yield allowed

    async def _wait_for_start(self, deadline: float | None) -> bool:
        """Sleep out the start gap and record the start; ``False`` when it would pass *deadline*."""
        now = self._clock()
        start = now if self._last_start is None else max(now, self._last_start + self._delay)
        if deadline is not None and start > deadline:
            return False
        if start > now:
            await self._sleep(start - now)
        self._last_start = self._clock()
        return True


__all__ = ["CrawlThrottle"]
