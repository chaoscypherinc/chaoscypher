# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract pins for the upload locations that lift nginx's 1m body default.

``configuration.md`` calls ``batching.max_upload_bytes`` the single source of
truth for the upload routes, but a route only gets the cap if its own
``location`` block carries ``client_max_body_size``; anything else inherits the
server-level ``1m`` and nginx answers 413 before Cortex sees the request.
Until 2026-09-28 ``/api/v1/lexicon/upload`` had no block in any edge and the
multi-interface edge had none for ``/api/v1/exports/import`` either, so a
>1 MB Lexicon publish (and, in multi-container, a >1 MB ``.ccx`` import) failed
on every shipped deployment. ``test_drift_invariants`` checks the rendered
*value* only; these pins check which locations carry it, per edge, and that the
hand-synced static copy the multi-container image ships keeps both.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from chaoscypher_core.app_config import Settings
from chaoscypher_core.services.orchestration.renderer import render_template


_REPO = Path(__file__).resolve().parents[6]
_STATIC_CONF = _REPO / "packages" / "docker" / "config" / "multi-interface-nginx.conf"

_TEMPLATES = ("nginx-http.conf", "nginx-https.conf", "multi-interface-nginx.conf")
# Restated on purpose rather than imported from Cortex's middleware: Core must
# not depend on Cortex (CC014). The source-upload regex location is covered by
# test_drift_invariants; these two are the exact-match locations.
_UPLOAD_LOCATIONS = ("/api/v1/exports/import", "/api/v1/lexicon/upload")


def _location_block(text: str, path: str) -> str:
    """Return the body of ``location = <path> { ... }``."""
    match = re.search(rf"location\s*=\s*{re.escape(path)}\s*\{{(.*?)\n\s*\}}", text, re.DOTALL)
    assert match is not None, f"location = {path} block not found"
    return match.group(1)


@pytest.mark.parametrize("template_name", _TEMPLATES)
@pytest.mark.parametrize("path", _UPLOAD_LOCATIONS)
def test_upload_location_carries_the_configured_body_cap(template_name: str, path: str) -> None:
    settings = Settings()
    settings.batching.max_upload_bytes = 5 * 1024 * 1024 * 1024  # 5 GB -> "5g"
    out = render_template(template_name, settings)
    # The server default stays small; only the upload locations lift it.
    assert "client_max_body_size 1m;" in out, template_name
    block = _location_block(out, path)
    assert "client_max_body_size 5g;" in block, f"{template_name}: {path}"
    # Still behind the edge auth subrequest — lifting the cap never opens the route.
    assert "include /etc/nginx/edge-auth-proxy.conf;" in block, f"{template_name}: {path}"


@pytest.mark.parametrize("path", _UPLOAD_LOCATIONS)
def test_static_multi_interface_conf_carries_both_upload_locations(path: str) -> None:
    """The checked-in copy is what the multi-container image ships (Dockerfile:35)."""
    assert _STATIC_CONF.is_file(), f"config moved? {_STATIC_CONF}"
    block = _location_block(_STATIC_CONF.read_text(encoding="utf-8"), path)
    size = re.search(r"client_max_body_size\s+(\d+)([kmg]);", block)
    assert size is not None, path
    assert size.group(2) == "g", f"{path}: static cap is not in the gigabyte range"
    assert "include /etc/nginx/edge-auth-proxy.conf;" in block, path
