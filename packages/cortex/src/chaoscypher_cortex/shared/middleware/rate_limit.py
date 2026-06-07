# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sliding-window rate limiting middleware for authentication endpoints.

Applies per-IP rate limits to configured paths using an in-memory store.
Suitable for single-instance deployments (all-in-one Docker container).
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import TYPE_CHECKING

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from chaoscypher_cortex.shared.utils.client_ip import client_ip


if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

    from chaoscypher_core.app_config import RateLimitSettings

logger = structlog.get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter for authentication endpoints.

    Applies per-IP rate limits to configured POST paths. Expired entries
    are pruned on each check so memory usage stays bounded.
    """

    def __init__(
        self,
        app: object,
        settings: RateLimitSettings,
    ) -> None:
        """Initialise with rate limit rules derived from settings."""
        super().__init__(app)  # type: ignore[arg-type]
        self._rules: dict[str, tuple[int, int]] = {
            "/api/v1/auth/login": (settings.login_max_requests, settings.login_window_seconds),
            "/api/v1/auth/setup": (settings.setup_max_requests, settings.setup_window_seconds),
            "/api/v1/auth/keys": (
                settings.api_key_max_requests,
                settings.api_key_window_seconds,
            ),
            "/api/v1/auth/refresh": (
                settings.refresh_max_requests,
                settings.refresh_window_seconds,
            ),
            "/api/v1/auth/register": (
                settings.register_max_requests,
                settings.register_window_seconds,
            ),
        }
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._check_count = 0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Check rate limits for POST requests to configured paths."""
        path = request.url.path
        if request.method == "POST" and path in self._rules:
            max_requests, window = self._rules[path]
            ip = client_ip(request)
            key = f"{ip}:{path}"

            now = time.monotonic()
            async with self._lock:
                cutoff = now - window
                self._buckets[key] = [t for t in self._buckets[key] if t > cutoff]

                # Periodically purge keys with no recent activity
                self._check_count += 1
                if self._check_count % 1000 == 0:
                    stale = [k for k, v in self._buckets.items() if not v or v[-1] < cutoff]
                    for k in stale:
                        del self._buckets[k]

                if len(self._buckets[key]) >= max_requests:
                    logger.warning(
                        "rate_limit_exceeded",
                        client_ip=ip,
                        path=path,
                        limit=f"{max_requests}/{window}s",
                    )
                    from chaoscypher_cortex.shared.errors import (
                        negotiated_error_response,
                    )

                    return negotiated_error_response(
                        request,
                        status_code=429,
                        error_code="rate_limited",
                        json_payload={
                            "error": "rate_limited",
                            "message": "Too many requests. Please try again later.",
                            "details": {
                                "limit": max_requests,
                                "window_seconds": window,
                                "retry_after_seconds": window,
                            },
                        },
                        html_kwargs={
                            "title": "Slow down — too many requests",
                            "lead": (
                                f"This endpoint accepts at most {max_requests} "
                                f"request(s) per {window} seconds. Try again "
                                f"shortly."
                            ),
                            "details": [
                                ("Limit", f"{max_requests} per {window}s"),
                                ("Retry after", f"{window} seconds"),
                            ],
                            "why": (
                                "Rate limiting protects authentication endpoints "
                                "against credential-stuffing and brute-force "
                                "attacks. Limits reset automatically — no admin "
                                "action is required."
                            ),
                            "fix": [
                                f"Wait {window} seconds, then retry.",
                                "An admin can adjust `rate_limit.*` in Settings.",
                            ],
                            "http_label": "HTTP 429 Too Many Requests",
                        },
                        headers={"Retry-After": str(window)},
                    )
                self._buckets[key].append(now)

        return await call_next(request)
