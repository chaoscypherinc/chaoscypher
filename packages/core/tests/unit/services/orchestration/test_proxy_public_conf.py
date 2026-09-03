# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract pins for the shared public-route proxy include.

``proxy-public.conf`` is included by every unauthenticated auth location
(status/setup/login/logout, settings/public|host). Two invariants:

- ``X-Auth-User`` MUST be blanked — these routes carry no verified identity,
  and a client-supplied value must never reach Cortex.
- ``X-Auth-Edge-Token`` MUST be forwarded — it asserts only "came through
  the trusted edge" and is what lets ``client_ip()`` trust the nginx-set
  ``X-Real-IP``. Blanking it collapsed the per-IP auth rate limit into one
  global bucket keyed to the nginx loopback peer (LAN-wide login-starvation
  DoS against the single operator).
"""

from __future__ import annotations

from pathlib import Path


_CONF = Path(__file__).resolve().parents[6] / "packages" / "docker" / "config" / "proxy-public.conf"


def test_blanks_user_identity_but_forwards_edge_token() -> None:
    assert _CONF.is_file(), f"config moved? {_CONF}"
    text = _CONF.read_text(encoding="utf-8")
    assert 'proxy_set_header X-Auth-User "";' in text
    assert "proxy_set_header X-Auth-Edge-Token $chaoscypher_edge_auth_token;" in text
    assert 'proxy_set_header X-Auth-Edge-Token "";' not in text, (
        "blanking the edge token collapses the auth rate limit into a "
        "single global bucket keyed to the nginx loopback peer"
    )
