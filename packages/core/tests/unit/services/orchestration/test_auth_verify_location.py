# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract pins for the ``location = /auth/verify`` subrequest block.

The bearer arm reached through this subrequest throttles repeated failures
per client, so the block must forward a client identity Cortex can trust:

- ``X-Real-IP`` from ``$remote_addr`` — nginx-set, so a client-supplied value
  is always overwritten and cannot be spoofed through this path.
- ``X-Auth-Edge-Token`` — same reasoning as ``proxy-public.conf``: it asserts
  only "came through the trusted edge", and it is what lets ``client_ip()``
  honour the nginx-set ``X-Real-IP``. Blanked, every caller collapses into a
  single bucket keyed to the nginx loopback peer, and one attacker's failures
  would throttle the operator's own API keys.
- ``X-Auth-User`` MUST stay blanked — this subrequest carries no verified
  identity yet; that is what it is being run to decide.
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


def _verify_block(text: str) -> str:
    """Return the body of the ``location = /auth/verify`` block."""
    match = re.search(r"location\s*=\s*/auth/verify\s*\{(.*?)\n\s*\}", text, re.DOTALL)
    assert match is not None, "location = /auth/verify block not found"
    return match.group(1)


@pytest.mark.parametrize("template_name", _TEMPLATES)
def test_verify_block_forwards_a_trustable_client_address(template_name: str) -> None:
    block = _verify_block(render_template(template_name, Settings()))
    assert "proxy_set_header X-Real-IP $remote_addr;" in block, template_name
    assert "proxy_set_header X-Auth-Edge-Token $chaoscypher_edge_auth_token;" in block, (
        template_name
    )
    assert 'proxy_set_header X-Auth-Edge-Token "";' not in block, (
        f"{template_name}: blanking the edge token collapses the bearer "
        f"throttle into one global bucket keyed to the nginx loopback peer"
    )


@pytest.mark.parametrize("template_name", _TEMPLATES)
def test_verify_block_still_blanks_the_identity_header(template_name: str) -> None:
    block = _verify_block(render_template(template_name, Settings()))
    assert 'proxy_set_header X-Auth-User "";' in block, template_name


def test_static_multi_interface_conf_stays_in_sync() -> None:
    """The checked-in stub must match its ``.j2`` twin on these directives."""
    assert _STATIC_CONF.is_file(), f"config moved? {_STATIC_CONF}"
    block = _verify_block(_STATIC_CONF.read_text(encoding="utf-8"))
    assert "proxy_set_header X-Real-IP $remote_addr;" in block
    assert "proxy_set_header X-Auth-Edge-Token $chaoscypher_edge_auth_token;" in block
    assert 'proxy_set_header X-Auth-User "";' in block
