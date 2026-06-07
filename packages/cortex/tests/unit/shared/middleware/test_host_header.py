# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for HostHeaderCheckMiddleware."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import SecuritySettings, Settings
from chaoscypher_cortex.shared.middleware.host_header import (
    HostHeaderCheckMiddleware,
)


def _app(provider: Callable[[], Settings]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(HostHeaderCheckMiddleware, settings_provider=provider)

    @app.get("/ok")
    def ok() -> dict:
        return {"ok": True}

    return app


def _static(security: SecuritySettings, *, setup_completed: bool = True) -> Callable[[], Settings]:
    """Build a constant Settings provider for tests.

    Defaults to ``setup_completed=True`` so the existing test bodies exercise
    the post-setup policy paths. Tests targeting the pre-setup bypass pass
    ``setup_completed=False`` explicitly.
    """
    return lambda: Settings(security=security, setup_completed=setup_completed)


def test_accepts_allowed_host() -> None:
    client = TestClient(_app(_static(SecuritySettings(allowed_hosts=["localhost"]))))
    r = client.get("/ok", headers={"Host": "localhost"})
    assert r.status_code == 200


def test_accepts_allowed_host_with_port() -> None:
    client = TestClient(_app(_static(SecuritySettings(allowed_hosts=["localhost"]))))
    r = client.get("/ok", headers={"Host": "localhost:8080"})
    assert r.status_code == 200


def test_accepts_ipv6_loopback() -> None:
    sec = SecuritySettings(allowed_hosts=["localhost", "127.0.0.1", "::1"])
    client = TestClient(_app(_static(sec)))
    r = client.get("/ok", headers={"Host": "[::1]:8080"})
    assert r.status_code == 200


def test_rejects_other_host_returns_421_json_by_default() -> None:
    client = TestClient(_app(_static(SecuritySettings(allowed_hosts=["localhost"]))))
    r = client.get("/ok", headers={"Host": "evil.example"})
    assert r.status_code == 421
    body = r.json()
    assert body["error"] == "host_not_allowed"
    assert "evil.example" in body["message"]
    assert body["details"]["attempted_host"] == "evil.example"


def test_rejects_other_host_returns_html_for_browser() -> None:
    client = TestClient(_app(_static(SecuritySettings(allowed_hosts=["localhost"]))))
    r = client.get(
        "/ok",
        headers={"Host": "evil.example", "Accept": "text/html,application/xhtml+xml"},
    )
    assert r.status_code == 421
    assert r.headers["content-type"].startswith("text/html")
    assert "Chaos Cypher" in r.text
    assert "evil.example" in r.text


def test_rejects_localhost_subdomain_attack() -> None:
    client = TestClient(_app(_static(SecuritySettings(allowed_hosts=["localhost"]))))
    r = client.get("/ok", headers={"Host": "localhost.evil.com"})
    assert r.status_code == 421


def test_case_insensitive_match() -> None:
    client = TestClient(_app(_static(SecuritySettings(allowed_hosts=["localhost"]))))
    r = client.get("/ok", headers={"Host": "LOCALHOST:8080"})
    assert r.status_code == 200


def test_wildcard_allows_all() -> None:
    client = TestClient(_app(_static(SecuritySettings(allowed_hosts=["*"]))))
    r = client.get("/ok", headers={"Host": "anything.example.com"})
    assert r.status_code == 200


def test_allow_external_access_bypasses_check() -> None:
    sec = SecuritySettings(allowed_hosts=["localhost"], allow_external_access=True)
    client = TestClient(_app(_static(sec)))
    r = client.get("/ok", headers={"Host": "anything.example.com"})
    assert r.status_code == 200


def test_empty_host_header_rejected() -> None:
    client = TestClient(_app(_static(SecuritySettings(allowed_hosts=["localhost"]))))
    r = client.get("/ok", headers={"Host": ""})
    assert r.status_code == 421


def test_settings_provider_is_called_per_request() -> None:
    state = {
        "settings": Settings(
            security=SecuritySettings(allowed_hosts=["localhost"]),
            setup_completed=True,
        )
    }

    def provider() -> Settings:
        return state["settings"]

    client = TestClient(_app(provider))

    r1 = client.get("/ok", headers={"Host": "lan.local"})
    assert r1.status_code == 421

    state["settings"] = Settings(
        security=SecuritySettings(allowed_hosts=["localhost"], allow_external_access=True),
        setup_completed=True,
    )

    r2 = client.get("/ok", headers={"Host": "lan.local"})
    assert r2.status_code == 200


def test_pre_setup_bypasses_host_check() -> None:
    """Pre-setup (setup_completed=False) accepts any Host header so the
    operator can reach /setup from any device on their LAN to complete
    first-run setup.
    """
    sec = SecuritySettings(allowed_hosts=["localhost"])
    client = TestClient(_app(_static(sec, setup_completed=False)))

    # Non-loopback Host that would normally be rejected
    r = client.get("/ok", headers={"Host": "192.168.1.20"})
    assert r.status_code == 200

    # Empty Host header (would also normally be rejected)
    r_empty = client.get("/ok", headers={"Host": ""})
    assert r_empty.status_code == 200


def test_post_setup_resumes_host_check() -> None:
    """Flipping setup_completed True restores the normal allow-list check."""
    sec = SecuritySettings(allowed_hosts=["localhost"])
    state = {
        "settings": Settings(security=sec, setup_completed=False),
    }

    def provider() -> Settings:
        return state["settings"]

    client = TestClient(_app(provider))

    # Pre-setup: any host allowed
    r1 = client.get("/ok", headers={"Host": "192.168.1.20"})
    assert r1.status_code == 200

    # Flip to post-setup
    state["settings"] = Settings(security=sec, setup_completed=True)

    # Now the same host is rejected
    r2 = client.get("/ok", headers={"Host": "192.168.1.20"})
    assert r2.status_code == 421
