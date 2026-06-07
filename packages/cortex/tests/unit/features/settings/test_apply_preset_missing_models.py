# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""apply_preset must surface missing models in the response body."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chaoscypher_cortex.features.settings.api import router as settings_router
from chaoscypher_cortex.features.settings.models import ApplyPresetResponse
from chaoscypher_cortex.shared.auth.dependencies import get_current_username


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(settings_router, prefix="/api/v1/settings")
    # Bypass auth for the test.
    app.dependency_overrides[get_current_username] = lambda: "test-user"
    return app


@pytest.mark.asyncio
async def test_apply_preset_includes_missing_models_in_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The vram_24gb preset selects qwen3:30b-instruct as the extraction
    model. Mock the /api/tags fetch to return only an unrelated model;
    apply_preset's response must list qwen3:30b-instruct in missing_models.
    """

    async def fake_pulled(_settings):  # type: ignore[no-untyped-def]
        return {"qwen3:8b"}  # unrelated model

    monkeypatch.setattr(
        "chaoscypher_core.services.llm.health._ollama_pulled_models",
        fake_pulled,
    )

    # Build a mock preset response that the service's apply_preset returns.
    mock_preset_response = ApplyPresetResponse(
        success=True,
        preset_id="vram_24gb",
        preset_name="24 GB VRAM",
        settings_updated={"ollama_extraction_model": "qwen3:30b-instruct"},
        message="Applied 24 GB VRAM preset successfully",
    )

    # Mock settings with ollama as the provider and qwen3:30b-instruct configured.
    mock_settings = MagicMock()
    mock_settings.llm.chat_provider = "ollama"
    mock_settings.llm.ollama_chat_model = "qwen3:30b-instruct"
    mock_settings.llm.ollama_extraction_model = "qwen3:30b-instruct"
    mock_settings.llm.ollama_vision_model = None
    mock_settings.llm.ollama_instances = [
        MagicMock(enabled=True, base_url="http://localhost:11434")
    ]
    mock_settings.timeouts.ollama_verify_timeout = 5

    mock_settings_manager = MagicMock()
    mock_settings_manager.get_settings.return_value = mock_settings

    mock_service = MagicMock()
    mock_service.apply_preset.return_value = mock_preset_response
    mock_service.notify_workers_llm_settings_changed = AsyncMock()
    mock_service.settings_manager = mock_settings_manager

    app = _make_app()

    from chaoscypher_cortex.features.settings.api import get_settings_service

    app.dependency_overrides[get_settings_service] = lambda: mock_service

    # No tracker patch needed — get_llm_health is real-time as of 2026-05-22.
    # The monkeypatched _ollama_pulled_models returning a non-None set is
    # already sufficient to make verified=True for this Ollama snapshot.
    client = TestClient(app, raise_server_exceptions=True)
    response = client.post(
        "/api/v1/settings/presets/apply",
        json={"preset_id": "vram_24gb"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "qwen3:30b-instruct" in body["missing_models"]
