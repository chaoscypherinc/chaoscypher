# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sliding-window counter for recent auth failures (X-Auth-User missing/invalid)."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from threading import Lock


WINDOW = timedelta(minutes=5)


class AuthFailureTracker:
    """Thread-safe sliding-window counter tracking auth failures over a 5-minute window.

    Records timestamps whenever an incoming request fails authentication due to
    a missing or invalid ``X-Auth-User`` header. Operators can poll
    ``GET /api/v1/health/auth`` to detect silent 401 storms caused by nginx
    misconfiguration.
    """

    def __init__(self) -> None:
        """Initialise an empty failure deque with a lock for thread-safe writes."""
        self._lock = Lock()
        self._failures: deque[datetime] = deque()

    def record_failure(self) -> None:
        """Record a single auth failure at the current UTC time."""
        with self._lock:
            now = datetime.now(UTC)
            self._failures.append(now)
            self._evict_expired(now)

    def window_count(self) -> int:
        """Return the number of failures within the last 5 minutes."""
        with self._lock:
            self._evict_expired(datetime.now(UTC))
            return len(self._failures)

    def last_at(self) -> str | None:
        """Return ISO-8601 UTC timestamp of the most recent failure, or None."""
        with self._lock:
            self._evict_expired(datetime.now(UTC))
            if not self._failures:
                return None
            return self._failures[-1].isoformat()

    def _evict_expired(self, now: datetime) -> None:
        """Remove failure records older than WINDOW from the front of the deque."""
        cutoff = now - WINDOW
        while self._failures and self._failures[0] < cutoff:
            self._failures.popleft()


#: Module-level singleton — one counter per Cortex process.
tracker = AuthFailureTracker()
