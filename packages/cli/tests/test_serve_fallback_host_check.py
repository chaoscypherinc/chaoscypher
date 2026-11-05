# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The ``serve`` fallback app refuses a request for another host (DNS rebinding).

The fallback (CLI installed without Cortex) has no auth. Without a Host check, a page
the user visits whose name re-resolves to 127.0.0.1 could read the whole graph
same-origin, where CORS is never consulted.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from chaoscypher_cli.commands.runtime import serve
from chaoscypher_core.app_config import get_settings


@pytest.fixture
def client() -> TestClient:
    return TestClient(serve._build_fallback_app("default"))


@pytest.mark.parametrize("host", ["evil.example:8000", "localhost.evil.example"])
def test_a_request_for_another_host_is_refused(client: TestClient, host: str) -> None:
    response = client.get("/health", headers={"host": host})
    assert response.status_code == 421
    assert response.json()["error"] == "host_not_allowed"


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_a_request_for_a_local_host_is_answered(client: TestClient, host: str) -> None:
    response = client.get("/health", headers={"host": host})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "default"}


def test_allow_external_access_answers_any_host(client: TestClient) -> None:
    settings = get_settings().model_copy(deep=True)
    settings.security.allow_external_access = True
    with patch.object(serve, "get_settings", return_value=settings):
        assert client.get("/health", headers={"host": "nas.lan:8000"}).status_code == 200


def test_every_http_app_the_repo_builds_checks_the_host_header() -> None:
    """The class: a second HTTP server path that misses the rebinding control."""
    packages = Path(__file__).resolve().parents[2]
    builders = [
        path
        for path in packages.glob("*/src/**/*.py")
        if re.search(r"\b(?:FastAPI|Starlette)\(", path.read_text(encoding="utf-8"))
    ]
    assert builders, "no app builder found: the scan is broken"
    unchecked = [
        str(path.relative_to(packages))
        for path in builders
        if not re.search(r"\b(?:host_allowed|HostHeaderCheckMiddleware)\b", path.read_text())
    ]
    assert not unchecked, unchecked
