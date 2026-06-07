# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Host header check — blocks DNS rebinding attacks on localhost apps.

Rejects requests whose Host header isn't in the allow-list. The allow-list
is re-read from settings on every request so admin toggles in the UI take
effect immediately without a process restart.

IPv6 hosts arrive as ``[::1]:port``; the leading bracket is stripped during
hostname extraction so ``::1`` matches exactly if it's in the allow-list.

**Pre-setup bypass:** when ``settings.setup_completed`` is False, the check
is skipped entirely so users on a headless box can reach ``/setup`` from
another device on their LAN to complete first-run setup. This is the same
posture as the documented "first-arrival admin race" — pre-setup there are
no credentials or data to compromise. The user's allow-external-access
selection in the wizard then governs post-setup behaviour.

Browser clients (``Accept: text/html``) get a branded 421 page. API clients
get the canonical ``UnifiedErrorResponse`` JSON envelope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from chaoscypher_cortex.shared.errors import negotiated_error_response
from chaoscypher_cortex.shared.errors.host_blocked import build_host_blocked_html


if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request
    from starlette.responses import Response
    from starlette.types import ASGIApp

    from chaoscypher_core.app_config import Settings


def _extract_hostname(host_header: str) -> str:
    """Strip port and brackets from a raw Host header."""
    if host_header.startswith("["):
        end = host_header.find("]")
        return host_header[1:end] if end != -1 else host_header
    return host_header.split(":", 1)[0]


class HostHeaderCheckMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Host header is not in the allow-list.

    The allow-list is resolved per-request via ``settings_provider`` so live
    PATCHes to ``security.allow_external_access`` / ``security.allowed_hosts``
    take effect on the next request without a restart.
    """

    def __init__(
        self,
        app: ASGIApp,
        settings_provider: Callable[[], Settings],
    ) -> None:
        """Initialise with a callable that returns the current Settings."""
        super().__init__(app)
        self._settings_provider = settings_provider

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Validate the request's Host header against the allow-list."""
        settings = self._settings_provider()

        # Pre-setup: open the doorway so the operator can reach /setup from
        # any device. After the wizard's Account step persists the user's
        # decision (security.allow_external_access), normal policy resumes.
        if not settings.setup_completed:
            return await call_next(request)

        sec = settings.security
        allowed = {h.lower() for h in sec.allowed_hosts}

        if sec.allow_external_access or "*" in allowed:
            return await call_next(request)

        host_header = request.headers.get("host", "").lower()
        if not host_header:
            return self._blocked(request, "", sec.allowed_hosts)

        hostname = _extract_hostname(host_header)
        if hostname not in allowed:
            return self._blocked(request, hostname, sec.allowed_hosts)

        return await call_next(request)

    def _blocked(
        self,
        request: Request,
        attempted_host: str,
        allowed_hosts: list[str],
    ) -> Response:
        """Build a 421 response (HTML for browsers, JSON for API clients)."""
        json_payload = {
            "error": "host_not_allowed",
            "message": (
                f"This Chaos Cypher instance accepts requests for "
                f"{', '.join(allowed_hosts) or '(none)'}. The request came "
                f"in for '{attempted_host or '(no Host header)'}'. Enable "
                f"'Allow external access' in Settings or add this host to "
                f"the allow-list."
            ),
            "details": {
                "attempted_host": attempted_host,
                "allowed_hosts": list(allowed_hosts),
            },
        }
        return negotiated_error_response(
            request,
            status_code=421,
            error_code="host_not_allowed",
            json_payload=json_payload,
            html_kwargs=build_host_blocked_html(attempted_host, allowed_hosts),
        )
