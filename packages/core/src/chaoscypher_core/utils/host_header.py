# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The Host-header rule that blocks DNS rebinding, for every HTTP server the repo runs.

A page whose name re-resolves to 127.0.0.1 reaches a local server same-origin, so CORS
is never consulted; only a check of the Host header refuses it. Cortex's
``HostHeaderCheckMiddleware`` and the CLI's fallback ``serve`` app both decide with
:func:`host_allowed`, so the two servers cannot drift apart (the fallback had no check
at all until 2026-11-01).
"""

from __future__ import annotations

from collections.abc import Sequence


def request_hostname(host_header: str) -> str:
    """The lower-cased hostname of a raw Host header: no port, no IPv6 brackets."""
    host = host_header.strip().lower()
    if host.startswith("["):
        end = host.find("]")
        return host[1:end] if end != -1 else host
    return host.split(":", 1)[0]


def host_allowed(
    host_header: str, *, allowed_hosts: Sequence[str], allow_external_access: bool
) -> bool:
    """Whether a request carrying ``host_header`` may be answered.

    Any host when ``allow_external_access`` is on or ``allowed_hosts`` holds ``*``;
    otherwise the header's hostname must be in ``allowed_hosts`` (case-insensitive),
    and a request with no Host header is refused.
    """
    allowed = {h.lower() for h in allowed_hosts}
    if allow_external_access or "*" in allowed:
        return True
    return bool(host_header) and request_hostname(host_header) in allowed


__all__ = ["host_allowed", "request_hostname"]
