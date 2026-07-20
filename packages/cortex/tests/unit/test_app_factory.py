# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the startup guard that refuses to start when dev_mode=True without ack."""

from __future__ import annotations

import pytest

from chaoscypher_core.app_config import Settings, get_settings, set_settings


@pytest.fixture(autouse=True)
def _restore_settings():
    """Restore the original settings singleton after each test."""
    original = get_settings()
    yield
    set_settings(original)


def test_dev_mode_refuses_to_start_without_ack(monkeypatch):
    """create_app must SystemExit when dev_mode=True and ALLOW_DEV_MODE unset."""
    monkeypatch.delenv("CHAOSCYPHER_ALLOW_DEV_MODE", raising=False)
    set_settings(Settings(dev_mode=True))

    from chaoscypher_cortex.app_factory import create_app

    with pytest.raises(SystemExit, match="dev_mode"):
        create_app()


def test_dev_mode_ok_with_ack(monkeypatch):
    """create_app must succeed when dev_mode=True and ALLOW_DEV_MODE=1."""
    monkeypatch.setenv("CHAOSCYPHER_ALLOW_DEV_MODE", "1")
    set_settings(Settings(dev_mode=True))

    from chaoscypher_cortex.app_factory import create_app

    create_app()  # should not raise


def test_schema_only_skips_guard_even_without_ack(monkeypatch):
    """schema_only=True must bypass the guard so the types-builder stage still works."""
    monkeypatch.delenv("CHAOSCYPHER_ALLOW_DEV_MODE", raising=False)
    set_settings(Settings(dev_mode=True))

    from chaoscypher_cortex.app_factory import create_app

    create_app(schema_only=True)  # should not raise


def test_no_dev_mode_needs_no_ack(monkeypatch):
    """Normal production mode (dev_mode=False) must not require the ack var."""
    monkeypatch.delenv("CHAOSCYPHER_ALLOW_DEV_MODE", raising=False)
    set_settings(Settings(dev_mode=False))

    from chaoscypher_cortex.app_factory import create_app

    create_app()  # should not raise


def test_is_within_static_root_rejects_prefix_sibling(tmp_path):
    """A sibling dir sharing the root's name prefix must not count as contained.

    Regression: the SPA static handler previously used ``str.startswith`` for
    containment, so ``/app/static_uploads/secret`` passed the check against a
    ``/app/static`` root. ``is_relative_to`` requires true ancestry.
    """
    from pathlib import Path

    from chaoscypher_cortex.app_factory import _is_within_static_root

    static_root = tmp_path / "static"
    static_root.mkdir()
    sibling = tmp_path / "static_uploads"
    sibling.mkdir()
    escaped = Path(static_root, "..", "static_uploads", "secret.txt").resolve()

    # Sibling with shared prefix is NOT contained (the startswith bug).
    assert _is_within_static_root(escaped, static_root.resolve()) is False
    # A genuine file inside the root IS contained.
    inside = Path(static_root, "assets", "app.js").resolve()
    assert _is_within_static_root(inside, static_root.resolve()) is True


def test_spa_fallback_returns_404_envelope_for_missing_api_path(monkeypatch, tmp_path):
    """Unknown ``/api/*`` paths must produce a real 404 with the unified envelope.

    Previously the SPA fallback returned ``{"error": "Not found"}`` with
    status 200, breaking both the status contract and the
    UnifiedErrorResponse envelope contract.
    """
    from fastapi.testclient import TestClient

    from chaoscypher_core.app_config import PathSettings, SecuritySettings

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    (static_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    (static_dir / "assets").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    monkeypatch.setenv("APP_NAME", "web_ui")
    monkeypatch.setenv("STATIC_DIR", str(static_dir))
    monkeypatch.setenv("CHAOSCYPHER_ALLOW_DEV_MODE", "1")
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(data_dir))
    set_settings(
        Settings(
            dev_mode=True,
            paths=PathSettings(data_dir=str(data_dir)),
            security=SecuritySettings(allowed_hosts=["testserver"]),
        )
    )

    from chaoscypher_cortex.app_factory import create_app

    app = create_app()
    client = TestClient(app)

    response = client.get("/api/v1/this-route-does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "NOT_FOUND"
    assert "api/v1/this-route-does-not-exist" in body["message"]


def test_security_settings_has_allow_external_access_default_false() -> None:
    from chaoscypher_core.app_config import SecuritySettings

    s = SecuritySettings()
    assert s.allow_external_access is False
    assert s.allowed_hosts == ["localhost", "127.0.0.1", "::1"]


def test_security_settings_accepts_allow_external_access_true() -> None:
    from chaoscypher_core.app_config import SecuritySettings

    s = SecuritySettings(allow_external_access=True)
    assert s.allow_external_access is True
