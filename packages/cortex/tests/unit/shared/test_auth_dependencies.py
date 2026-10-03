# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for trusted edge-auth dependency behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from chaoscypher_core.app_config import LocalAuthSettings, Settings, get_settings
from chaoscypher_cortex.shared.auth.dependencies import (
    CurrentUsername,
    has_valid_edge_token,
)


if TYPE_CHECKING:
    from pathlib import Path


def _client(settings: Settings) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings

    @app.get("/protected")
    def protected(username: CurrentUsername) -> dict[str, str]:
        return {"username": username}

    return TestClient(app)


def test_rejects_spoofed_auth_user_without_edge_token() -> None:
    settings = Settings(
        local_auth=LocalAuthSettings(edge_auth_token=SecretStr("edge-secret")),
    )
    client = _client(settings)

    response = client.get("/protected", headers={"X-Auth-User": "admin"})

    assert response.status_code == 401


def test_accepts_auth_user_with_matching_edge_token() -> None:
    settings = Settings(
        local_auth=LocalAuthSettings(edge_auth_token=SecretStr("edge-secret")),
    )
    client = _client(settings)

    response = client.get(
        "/protected",
        headers={"X-Auth-User": "admin", "X-Auth-Edge-Token": "edge-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"username": "admin"}


def test_rejects_auth_user_with_wrong_edge_token() -> None:
    settings = Settings(
        local_auth=LocalAuthSettings(edge_auth_token=SecretStr("edge-secret")),
    )
    client = _client(settings)

    response = client.get(
        "/protected",
        headers={"X-Auth-User": "admin", "X-Auth-Edge-Token": "wrong"},
    )

    assert response.status_code == 401


def test_dev_mode_still_allows_direct_requests_without_header() -> None:
    settings = Settings(dev_mode=True)
    client = _client(settings)

    response = client.get("/protected")

    assert response.status_code == 200
    assert response.json() == {"username": "dev"}


# --- Fail-closed when the edge token is not configured ----------------------
#
# ``has_valid_edge_token`` must return False whenever no expected token can be
# resolved (unset, empty, blank file, unreadable file). Otherwise a deployment
# whose token file went missing would trust a forged ``X-Auth-User`` from
# anyone who reaches Cortex directly.

_UNCONFIGURED_CASES = ["missing_file", "empty_secret", "blank_file", "unreadable_file"]


def _unconfigured_settings(tmp_path: Path, case: str) -> Settings:
    if case == "missing_file":
        local_auth = LocalAuthSettings(edge_auth_token_path=tmp_path / "absent_edge_token")
    elif case == "empty_secret":
        local_auth = LocalAuthSettings(edge_auth_token=SecretStr(""))
    elif case == "blank_file":
        token_file = tmp_path / "edge_auth_token"
        token_file.write_text("  \n", encoding="utf-8")
        local_auth = LocalAuthSettings(edge_auth_token_path=token_file)
    elif case == "unreadable_file":
        # A directory at the token path exists() but read_text() raises OSError.
        token_dir = tmp_path / "edge_auth_token_dir"
        token_dir.mkdir()
        local_auth = LocalAuthSettings(edge_auth_token_path=token_dir)
    else:  # pragma: no cover - guards against typos in parametrize ids
        raise AssertionError(case)
    return Settings(dev_mode=False, local_auth=local_auth)


@pytest.mark.parametrize("case", _UNCONFIGURED_CASES)
def test_has_valid_edge_token_fails_closed_when_token_unconfigured(
    tmp_path: Path, case: str
) -> None:
    settings = _unconfigured_settings(tmp_path, case)
    headers = {settings.local_auth.edge_auth_header: "forged-token"}

    assert has_valid_edge_token(headers, settings) is False


@pytest.mark.parametrize("case", _UNCONFIGURED_CASES)
def test_current_username_rejects_header_when_token_unconfigured(tmp_path: Path, case: str) -> None:
    settings = _unconfigured_settings(tmp_path, case)
    client = _client(settings)

    response = client.get(
        "/protected",
        headers={"X-Auth-User": "admin", "X-Auth-Edge-Token": "forged-token"},
    )

    assert response.status_code == 401
