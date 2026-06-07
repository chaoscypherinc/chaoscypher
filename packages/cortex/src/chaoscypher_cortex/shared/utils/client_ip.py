# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Resolve the originating client IP behind the nginx edge proxy.

`request.client.host` is the immediate TCP peer — which, in the all-in-one
container and any reverse-proxy deployment, is always the proxy itself.
Every caller would otherwise share a single rate-limit bucket and every
auth-failure log would point at the proxy. nginx sets `X-Real-IP` to
`$remote_addr` (see ``proxy-common.conf.j2``); we prefer that, then the
leftmost ``X-Forwarded-For`` entry, then fall back to the TCP peer.

The forwarded headers are honoured ONLY when the request also carries a
valid ``X-Auth-Edge-Token`` — the same marker nginx injects to prove a
request passed through the trusted edge (see ``shared/auth/dependencies.py``).
A direct-to-cortex attacker cannot mint that token, so they cannot spoof
``X-Real-IP`` / ``X-Forwarded-For`` to poison an auth rate-limit bucket or
forge an audit-log origin; such requests fall back to the real TCP peer. In
``dev_mode`` (uvicorn-direct, no nginx) the headers are trusted so local
development keeps working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from starlette.requests import Request


def client_ip(request: Request) -> str:
    """Return the originating client IP, falling back to ``"unknown"``.

    Trusts ``X-Real-IP`` / ``X-Forwarded-For`` only when the request is
    edge-verified (valid ``X-Auth-Edge-Token``) or ``dev_mode`` is set;
    otherwise returns the immediate TCP peer so spoofed headers cannot
    forge the caller's source IP.
    """
    # Imported lazily: dependencies.py imports this module, so a top-level
    # import would create a cycle.
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_cortex.shared.auth.dependencies import has_valid_edge_token

    settings = get_settings()
    edge_verified = settings.dev_mode or has_valid_edge_token(request.headers, settings)

    if edge_verified:
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            leftmost = forwarded_for.split(",", 1)[0].strip()
            if leftmost:
                return leftmost

    if request.client is not None:
        return request.client.host

    return "unknown"
