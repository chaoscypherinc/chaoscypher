# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for OllamaModelsService."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from chaoscypher_cortex.features.settings.ollama_models_service import (
    OllamaModelsService,
)


@pytest.fixture
def service() -> OllamaModelsService:
    """Create service with test config."""
    instances = [
        {
            "id": "default",
            "name": "Default",
            "base_url": "http://localhost:11434",
            "enabled": True,
            "healthy": True,
        }
    ]
    return OllamaModelsService(instances=instances, timeout=5)


class TestListModels:
    """Tests for listing models across instances."""

    @pytest.mark.asyncio
    async def test_list_models_returns_instance_models(self, service: OllamaModelsService) -> None:
        """List models returns models grouped by instance."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {
                    "name": "qwen3:30b",
                    "size": 18_400_000_000,
                    "modified_at": "2026-03-15T10:30:00Z",
                    "digest": "sha256:abc123",
                    "details": {
                        "parameter_size": "30B",
                        "quantization_level": "Q4_K_M",
                        "family": "qwen3",
                    },
                }
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await service.list_models()

        assert len(result.instances) == 1
        assert result.instances[0].instance_id == "default"
        assert len(result.instances[0].models) == 1
        assert result.instances[0].models[0].name == "qwen3:30b"

    @pytest.mark.asyncio
    async def test_list_models_marks_unhealthy_on_connection_error(
        self, service: OllamaModelsService
    ) -> None:
        """Instance marked unhealthy when Ollama is unreachable."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_client_cls.return_value = mock_client

            result = await service.list_models()

        assert len(result.instances) == 1
        assert result.instances[0].healthy is False
        assert result.instances[0].models == []


class TestRemoveModel:
    """Tests for removing models from instances."""

    @pytest.mark.asyncio
    async def test_remove_model_calls_ollama_delete(self, service: OllamaModelsService) -> None:
        """Remove model sends DELETE to Ollama API."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await service.remove_model("qwen3:30b", instance_id="default")

        assert result["success"] is True
        mock_client.request.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_model_nonexistent_instance_raises(
        self, service: OllamaModelsService
    ) -> None:
        """Remove model raises ValueError for unknown instance."""
        with pytest.raises(ValueError, match="Instance 'nonexistent' not found"):
            await service.remove_model("qwen3:30b", instance_id="nonexistent")


class TestShowModel:
    """Tests for getting model details."""

    @pytest.mark.asyncio
    async def test_show_model_returns_details(self, service: OllamaModelsService) -> None:
        """Show model returns Ollama model information."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = lambda: None
        mock_response.json.return_value = {
            "modelfile": "FROM qwen3:30b",
            "parameters": "temperature 0.7",
            "template": "{{ .Prompt }}",
            "details": {
                "parameter_size": "30B",
                "quantization_level": "Q4_K_M",
                "family": "qwen3",
                "format": "gguf",
            },
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            result = await service.show_model("qwen3:30b", instance_id="default")

        assert result.details is not None
        assert result.details.parameter_size == "30B"
