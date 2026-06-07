# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""GET /api/v1/settings/public — operator-tunable config for the SPA.

Also hosts `/api/v1/settings/host` — auth-exempt, returns the
client's Host header + loopback flag so the setup wizard can pre-tick
the "Allow external access" checkbox when the user reached /setup over
a LAN address.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.settings_public.models import PublicSettings
from chaoscypher_cortex.features.settings_public.service import build_public_settings


router = APIRouter(prefix="/api/v1/settings", tags=["settings-public"])


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


@router.get(
    "/public",
    response_model=PublicSettings,
    summary="Operator-tunable config exposed to the SPA",
    description=(
        "Returns the subset of `Settings` that the SPA needs to render UI and "
        "make API calls with the correct defaults (page sizes, polling intervals, "
        "upload timeouts, recovery thresholds, validation limits). Never includes "
        "secrets. Reachable without auth so the SPA can render the login screen."
    ),
)
async def get_public_settings(
    settings: Annotated[Settings, Depends(get_settings)],
) -> PublicSettings:
    """Return the public settings DTO."""
    return build_public_settings(settings)


@router.get(
    "/host",
    summary="Hostname + loopback flag for the wizard's external-access default",
    description=(
        "Returns the hostname the client used to reach this server and whether "
        "it is a loopback address. The setup wizard uses this to default the "
        "'Allow external access' checkbox to ON for users arriving over a LAN. "
        "Auth-exempt — the wizard calls this before authentication is set up."
    ),
)
async def get_access_hint(request: Request) -> dict[str, Any]:
    """Return ``{request_host, is_loopback}`` for the current request."""
    host = request.headers.get("host", "").lower()
    hostname = host.split("]", 1)[0].lstrip("[") if host.startswith("[") else host.split(":", 1)[0]
    return {"request_host": hostname, "is_loopback": hostname in _LOOPBACK_HOSTS}
