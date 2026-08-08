# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The logs endpoints derive their line cap from settings, not a literal.

``lines`` was declared with a hardcoded ``le=10000`` that silently
duplicated ``settings.logs.max_log_lines`` (CC048). The cap now comes
from settings: oversized requests are clamped to ``max_log_lines`` in
the handler instead of 422-ing against a stale literal.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.logs.api import get_log_service, router
from chaoscypher_cortex.features.logs.models import LogResponse


def _client(service: MagicMock, settings: Settings) -> TestClient:
    """TestClient in dev_mode with the log service mocked out."""
    app = FastAPI()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_log_service] = lambda: service
    # The router uses "" as its root path (mounted under /logs in the app
    # factory) — include with the same prefix here.
    app.include_router(router, prefix="/logs")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def service() -> MagicMock:
    """Mock LogService returning an empty LogResponse."""
    svc = MagicMock()
    empty = LogResponse(service=None, lines=[], total_lines=0)
    svc.get_all_logs.return_value = empty
    svc.get_logs.return_value = empty
    return svc


@pytest.fixture
def settings() -> Settings:
    """dev_mode settings with the default max_log_lines (10000)."""
    return Settings(dev_mode=True)


class TestAllLogsLineCap:
    """GET /logs clamps lines to settings.logs.max_log_lines."""

    def test_oversized_lines_clamped_not_rejected(
        self, service: MagicMock, settings: Settings
    ) -> None:
        """Lines above max_log_lines is clamped, not a 422."""
        resp = _client(service, settings).get("/logs", params={"lines": 50000})

        assert resp.status_code == 200
        service.get_all_logs.assert_called_once_with(lines=settings.logs.max_log_lines)

    def test_lines_within_cap_passes_through(self, service: MagicMock, settings: Settings) -> None:
        resp = _client(service, settings).get("/logs", params={"lines": 250})

        assert resp.status_code == 200
        service.get_all_logs.assert_called_once_with(lines=250)

    def test_default_lines_from_pagination_settings(
        self, service: MagicMock, settings: Settings
    ) -> None:
        resp = _client(service, settings).get("/logs")

        assert resp.status_code == 200
        service.get_all_logs.assert_called_once_with(lines=settings.pagination.log_tail_lines)


class TestServiceLogsLineCap:
    """GET /logs/{service} clamps lines to settings.logs.max_log_lines."""

    def test_oversized_lines_clamped_not_rejected(
        self, service: MagicMock, settings: Settings
    ) -> None:
        resp = _client(service, settings).get("/logs/cortex", params={"lines": 50000})

        assert resp.status_code == 200
        service.get_logs.assert_called_once_with("cortex", lines=settings.logs.max_log_lines)

    def test_zero_lines_still_rejected(self, service: MagicMock, settings: Settings) -> None:
        """The ge=1 floor is a shape constraint and stays."""
        resp = _client(service, settings).get("/logs/cortex", params={"lines": 0})

        assert resp.status_code == 422
