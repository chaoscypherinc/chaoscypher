# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Host-blocked HTML payload — kwargs for ``render_branded_error``.

Split out from the middleware so the middleware file stays focused on
request handling. Returns a kwargs dict the ``negotiated_error_response``
helper forwards into ``render_branded_error``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def build_host_blocked_html(
    attempted_host: str,
    allowed_hosts: Sequence[str],
) -> dict[str, Any]:
    """Build the HTML kwargs for a 421 host-not-allowed page.

    Args:
        attempted_host: The Host header value the client sent.
        allowed_hosts: The current allow-list (for display only).

    Returns:
        Kwargs ready to forward to ``render_branded_error``.
    """
    return {
        "title": "Chaos Cypher can't accept requests for this address",
        "lead": (
            "This server is configured to only accept requests for a fixed "
            "set of hostnames. The address you used isn't on that list."
        ),
        "details": [
            ("You tried", attempted_host or "(no Host header)"),
            ("Accepted", ", ".join(allowed_hosts) or "(none)"),
        ],
        "why": (
            "Chaos Cypher checks the Host header on every request to prevent "
            "DNS-rebinding attacks. By default it only trusts loopback "
            "hostnames. To use it from another device, an admin needs to "
            "enable external access."
        ),
        "fix": [
            "Open http://localhost in a browser on the machine running Chaos Cypher.",
            "Sign in and go to Settings → Network access.",
            "Turn on 'Allow access from any host'.",
        ],
        "http_label": "HTTP 421 Misdirected Request",
    }
