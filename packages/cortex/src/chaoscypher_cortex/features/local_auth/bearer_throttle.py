# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Per-client failure throttle for the ``Authorization: Bearer`` auth arm.

nginx fires ``/auth/verify`` as an ``auth_request`` subrequest on every
``/api/`` request, so the bearer arm is reachable unauthenticated at the full
permitted request rate. A `limit_req` on that location would throttle every
*legitimate* API call too (the subrequest rides along with all of them), so
the cap has to live here, where it can be scoped to bearer *failures* only —
the cookie arm and successful key verifications are never affected.

After ``BEARER_FAILURE_LIMIT`` failed bearer verifies from one client inside
``BEARER_FAILURE_WINDOW_SECONDS``, further bearer attempts from that client
are refused for ``BEARER_BLOCK_SECONDS`` without touching bcrypt at all.

In-memory and **per-process**, matching ``RateLimitMiddleware``'s posture.
With ``W`` uvicorn workers the effective allowance is ``W *
BEARER_FAILURE_LIMIT`` failures per address before every worker has latched,
and a block set in one worker does not apply in the others — a request routed
elsewhere is still verified. That is deliberate: the throttle is defence in
depth behind the keyed selector index, which already makes an unknown key
free once every record has been migrated, and a shared block list would need
the queue as a dependency of the auth path.

The bound within one worker is not exactly ``BEARER_FAILURE_LIMIT`` either.
Verification runs in a thread (``asyncio.to_thread``, so the event loop's
default ``ThreadPoolExecutor`` — ``min(32, cpu_count + 4)`` workers — not
anyio's limiter), and every thread already past its block check when the Nth
failure latches still completes its own pass. The worst case is therefore
``BEARER_FAILURE_LIMIT + (executor workers - 1)`` verifications per block
window, i.e. tens of attempts per minute rather than the permitted hundred
per second. Counting attempts on *entry* would give a hard cap of exactly
``BEARER_FAILURE_LIMIT``, but it would also refuse a client legitimately
verifying several valid keys in parallel, so the count is deliberately taken
when a failure is known rather than when an attempt starts.

Client identity comes from ``client_ip``, which honours the ``X-Real-IP`` the
``/auth/verify`` location sets only when the edge token accompanies it. Both
headers ship in the same change as this module. If a deployment ever pairs a
new Cortex with a stale nginx config that forwards neither, ``client_ip``
falls back to the nginx loopback peer and every caller shares one bucket —
so five failures would refuse *all* bearer auth for 60s. That is a degraded
mode, not a breach: it is self-healing, the cookie path stays unaffected, and
a render test pins the header on all four copies of the location block.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Callable


BEARER_FAILURE_LIMIT = 5
BEARER_FAILURE_WINDOW_SECONDS = 60
BEARER_BLOCK_SECONDS = 60

# Full sweep cadence for idle entries, so the maps stay bounded under a
# source-address-rotating attacker. Mirrors RateLimitMiddleware's approach.
PRUNE_EVERY_N_FAILURES = 1000


class BearerFailureThrottle:
    """Sliding-window failure counter + block list, keyed per client address."""

    def __init__(
        self,
        *,
        limit: int = BEARER_FAILURE_LIMIT,
        window_seconds: int = BEARER_FAILURE_WINDOW_SECONDS,
        block_seconds: int = BEARER_BLOCK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Bind the throttle to its policy and clock.

        Args:
            limit: Failures inside ``window_seconds`` that trigger a block.
            window_seconds: Sliding window over which failures accumulate.
            block_seconds: How long a blocked client stays refused.
            clock: Monotonic time source; injectable for tests.

        """
        self._limit = limit
        self._window = window_seconds
        self._block = block_seconds
        self._clock = clock
        self._failures: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()
        self._failure_count = 0

    def is_blocked(self, client: str) -> bool:
        """Return True if ``client`` is currently refused bearer verification."""
        now = self._clock()
        with self._lock:
            until = self._blocked_until.get(client)
            if until is None:
                return False
            if now >= until:
                del self._blocked_until[client]
                return False
            return True

    def record_failure(self, client: str) -> bool:
        """Count a failed bearer verify, blocking ``client`` once over the limit.

        Returns:
            True only when *this* failure latched a new block. Callers log on
            that edge rather than on every refused attempt, so an attacker
            cannot drive log volume by continuing to hammer a blocked
            address.

        """
        now = self._clock()
        with self._lock:
            cutoff = now - self._window
            recent = [t for t in self._failures.get(client, []) if t > cutoff]
            recent.append(now)
            newly_blocked = len(recent) >= self._limit
            if newly_blocked:
                self._blocked_until[client] = now + self._block
                self._failures.pop(client, None)
            else:
                self._failures[client] = recent

            self._failure_count += 1
            if self._failure_count % PRUNE_EVERY_N_FAILURES == 0:
                self._prune(now)
            return newly_blocked

    def record_success(self, client: str) -> None:
        """Clear ``client``'s accumulated failures after a valid key."""
        with self._lock:
            self._failures.pop(client, None)
            self._blocked_until.pop(client, None)

    def tracked_clients(self) -> int:
        """Return how many clients currently hold state.

        Diagnostics only — nothing in the request path reads this. It exists
        so the pruning invariant (memory stays bounded under an
        address-rotating attacker) can be asserted from a test rather than
        reasoned about.
        """
        with self._lock:
            return len(set(self._failures) | set(self._blocked_until))

    def _prune(self, now: float) -> None:
        """Drop entries that can no longer influence a decision. Caller holds the lock."""
        cutoff = now - self._window
        self._failures = {
            client: stamps
            for client, stamps in self._failures.items()
            if stamps and stamps[-1] > cutoff
        }
        self._blocked_until = {
            client: until for client, until in self._blocked_until.items() if until > now
        }
