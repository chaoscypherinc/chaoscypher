# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Handler-level coverage tests for the settings API.

Each async route function is imported and invoked directly with a
``MagicMock`` / ``AsyncMock`` ``SettingsService`` (and ``TLSService`` /
``Settings`` where needed) plus ``_="test-user"`` standing in for the
``CurrentUsername`` dependency. We assert delegation, response-model
construction, and the 404/400 paths via ``raise_if_not_found`` /
``validation_error``. Lazy imports inside handlers are patched at SOURCE.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from chaoscypher_cortex.features.settings import api
from chaoscypher_cortex.features.settings.models import (
    ApplyPresetRequest,
    ApplyPresetResponse,
    LLMVerifyRequest,
    LocalEmbeddingDownloadRequest,
    LoggingLevelRequest,
    LoggingLevelResponse,
    OllamaVerifyRequest,
    ResetResponse,
    SetLoggingLevelResponse,
    SettingsUpdateRequest,
    TLSStatusResponse,
    VRAMPresetResponse,
)


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _preset_response(preset_id: str = "vram_24gb") -> VRAMPresetResponse:
    return VRAMPresetResponse(
        name=preset_id,
        display_name="24 GB",
        description="desc",
        vram_gb=24,
        gpu_examples=["RTX 4090"],
        version="1.0.0",
        author="builtin",
        builtin=True,
        ollama_settings={},
        llm_settings={},
    )


def _settings_mock() -> MagicMock:
    settings = MagicMock()
    settings.timeouts.ollama_verify_timeout = 7
    settings.current_database = "default"
    settings.priorities.background = 5
    return settings


# ===========================================================================
# GET / (masked settings)
# ===========================================================================


@pytest.mark.asyncio
async def test_get_settings_masks_and_validates() -> None:
    from chaoscypher_core.app_config import Settings

    service = MagicMock()
    service.get_settings.return_value = Settings()

    result = await api.get_settings(_="test-user", settings_service=service)

    # The masked response carries the well-known top-level keys.
    assert hasattr(result, "current_database")
    service.get_settings.assert_called_once()


# ===========================================================================
# PATCH / (update_settings)
# ===========================================================================


@pytest.mark.asyncio
async def test_update_settings_returns_warnings_and_notifies() -> None:
    from chaoscypher_core.app_config import Settings

    old = Settings()
    updated = Settings()

    service = MagicMock()
    service.get_settings.return_value = old
    service.update_settings = AsyncMock(return_value=updated)
    service.get_update_warnings.return_value = []
    service.notify_workers_llm_settings_changed = AsyncMock()

    body = SettingsUpdateRequest.model_validate({"llm": {"chat_provider": "ollama"}})

    result = await api.update_settings(
        _="test-user", settings_update=body, settings_service=service
    )

    service.update_settings.assert_awaited_once()
    # llm changed → workers notified
    service.notify_workers_llm_settings_changed.assert_awaited_once()
    assert result.warnings == []


@pytest.mark.asyncio
async def test_update_settings_invalidates_singletons_on_embedding_change() -> None:
    from chaoscypher_core.app_config import Settings

    service = MagicMock()
    service.get_settings.return_value = Settings()
    service.update_settings = AsyncMock(return_value=Settings())
    service.get_update_warnings.return_value = []
    service.notify_workers_llm_settings_changed = AsyncMock()

    body = SettingsUpdateRequest.model_validate({"embedding": {"provider": "openai"}})

    with (
        patch(
            "chaoscypher_core.repo_factories.embedding_factory.invalidate_embedding_service"
        ) as inv_emb,
        patch(
            "chaoscypher_core.repo_factories.search_factory.invalidate_search_repository"
        ) as inv_search,
    ):
        await api.update_settings(_="test-user", settings_update=body, settings_service=service)

    inv_emb.assert_called_once()
    inv_search.assert_called_once()
    service.notify_workers_llm_settings_changed.assert_awaited_once()


# ===========================================================================
# POST /reset (reset_settings)
# ===========================================================================


@pytest.mark.asyncio
async def test_reset_settings_returns_masked_defaults() -> None:
    from chaoscypher_core.app_config import Settings

    service = MagicMock()
    service.reset_to_defaults.return_value = Settings()

    result = await api.reset_settings(_="test-user", settings_service=service)
    assert hasattr(result, "current_database")
    service.reset_to_defaults.assert_called_once()


# ===========================================================================
# Logging level handlers
# ===========================================================================


@pytest.mark.asyncio
async def test_get_logging_level_delegates() -> None:
    service = MagicMock()
    expected = LoggingLevelResponse(level="INFO", numeric_level=20, available_levels=["INFO"])
    service.get_logging_level.return_value = expected

    result = await api.get_logging_level(_="test-user", settings_service=service)
    assert result is expected


@pytest.mark.asyncio
async def test_set_logging_level_notifies_workers_on_success() -> None:
    service = MagicMock()
    service.set_logging_level.return_value = SetLoggingLevelResponse(
        success=True, old_level="INFO", new_level="DEBUG", message="ok"
    )
    service.notify_workers_logging_level = AsyncMock()

    request = LoggingLevelRequest(level="DEBUG")
    result = await api.set_logging_level(_="test-user", request=request, settings_service=service)

    assert result.new_level == "DEBUG"
    service.notify_workers_logging_level.assert_awaited_once_with("DEBUG")


@pytest.mark.asyncio
async def test_set_logging_level_skips_notify_on_failure() -> None:
    service = MagicMock()
    service.set_logging_level.return_value = SetLoggingLevelResponse(
        success=False, old_level="INFO", new_level="INFO", message="failed"
    )
    service.notify_workers_logging_level = AsyncMock()

    request = LoggingLevelRequest(level="DEBUG")
    await api.set_logging_level(_="test-user", request=request, settings_service=service)

    service.notify_workers_logging_level.assert_not_awaited()


# ===========================================================================
# Preset handlers
# ===========================================================================


@pytest.mark.asyncio
async def test_list_presets_delegates() -> None:
    service = MagicMock()
    sentinel = MagicMock()
    service.list_presets.return_value = sentinel
    assert await api.list_presets(_="test-user", settings_service=service) is sentinel


@pytest.mark.asyncio
async def test_get_preset_found() -> None:
    service = MagicMock()
    preset = _preset_response()
    service.get_preset.return_value = preset
    result = await api.get_preset(_="test-user", preset_id="vram_24gb", settings_service=service)
    assert result is preset


@pytest.mark.asyncio
async def test_get_preset_not_found_raises_404() -> None:
    service = MagicMock()
    service.get_preset.return_value = None
    with pytest.raises(HTTPException) as exc:
        await api.get_preset(_="test-user", preset_id="nope", settings_service=service)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_apply_preset_adds_missing_models() -> None:
    service = MagicMock()
    service.apply_preset.return_value = ApplyPresetResponse(
        success=True,
        preset_id="vram_24gb",
        preset_name="24 GB",
        settings_updated={},
        message="ok",
    )
    service.notify_workers_llm_settings_changed = AsyncMock()
    service.settings_manager.get_settings.return_value = _settings_mock()

    health = MagicMock()
    health.missing_models = ["qwen3:30b"]

    request = ApplyPresetRequest(preset_id="vram_24gb")
    with patch("chaoscypher_core.services.llm.get_llm_health", AsyncMock(return_value=health)):
        result = await api.apply_preset(_="test-user", request=request, settings_service=service)

    assert result.missing_models == ["qwen3:30b"]
    service.notify_workers_llm_settings_changed.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_preset_unknown_raises_404() -> None:
    service = MagicMock()
    service.apply_preset.side_effect = KeyError("nope")

    request = ApplyPresetRequest(preset_id="nope")
    with patch("chaoscypher_core.services.llm.get_llm_health", AsyncMock()):
        with pytest.raises(HTTPException) as exc:
            await api.apply_preset(_="test-user", request=request, settings_service=service)
    assert exc.value.status_code == 404


# ===========================================================================
# Embedding models handlers
# ===========================================================================


@pytest.mark.asyncio
async def test_get_embedding_models_builds_registry_response() -> None:
    result = await api.get_embedding_models(_="test-user")
    # Real registry: curated is non-empty, cloud keyed by provider.
    assert len(result.curated) > 0
    assert "openai" in result.cloud


@pytest.mark.asyncio
async def test_list_local_embedding_models_scans_cache(tmp_path) -> None:
    cache = tmp_path / "models" / "embeddings"
    cache.mkdir(parents=True)
    (cache / "models--Qwen--Qwen3-Embedding-0.6B").mkdir()
    (cache / "not-a-model").mkdir()  # skipped — no models-- prefix

    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)

    result = await api.list_local_embedding_models(_="test-user", settings=settings)
    ids = [m.id for m in result.models]
    assert "Qwen/Qwen3-Embedding-0.6B" in ids
    assert len(result.models) == 1


@pytest.mark.asyncio
async def test_download_local_embedding_model_success(tmp_path) -> None:
    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)
    settings.search.vector_dimensions = 768

    fake_provider = MagicMock()
    fake_provider.download_model = AsyncMock(
        return_value={
            "model_name": "Qwen/Qwen3-Embedding-0.6B",
            "native_dimensions": 1024,
            "download_time_ms": 1500,
        }
    )

    request = LocalEmbeddingDownloadRequest(model="Qwen/Qwen3-Embedding-0.6B")
    with patch(
        "chaoscypher_core.adapters.embedding.local_provider.LocalEmbeddingProvider",
        return_value=fake_provider,
    ):
        result = await api.download_local_embedding_model(
            _="test-user", request=request, settings=settings
        )

    assert result.native_dimensions == 1024
    assert result.download_time_ms == 1500


@pytest.mark.asyncio
async def test_download_local_embedding_model_value_error_raises_400(tmp_path) -> None:
    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)
    settings.search.vector_dimensions = 768

    fake_provider = MagicMock()
    fake_provider.download_model = AsyncMock(side_effect=ValueError("bad model"))

    request = LocalEmbeddingDownloadRequest(model="bad/model")
    with patch(
        "chaoscypher_core.adapters.embedding.local_provider.LocalEmbeddingProvider",
        return_value=fake_provider,
    ):
        with pytest.raises(HTTPException) as exc:
            await api.download_local_embedding_model(
                _="test-user", request=request, settings=settings
            )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_local_embedding_model_removes_dir(tmp_path) -> None:
    cache = tmp_path / "models" / "embeddings"
    model_dir = cache / "models--Qwen--Qwen3-Embedding-0.6B"
    model_dir.mkdir(parents=True)

    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)

    await api.delete_local_embedding_model(
        _="test-user", model_id="Qwen/Qwen3-Embedding-0.6B", settings=settings
    )
    assert not model_dir.exists()


@pytest.mark.asyncio
async def test_delete_local_embedding_model_not_found_raises_404(tmp_path) -> None:
    cache = tmp_path / "models" / "embeddings"
    cache.mkdir(parents=True)

    settings = MagicMock()
    settings.paths.data_dir = str(tmp_path)

    with pytest.raises(HTTPException) as exc:
        await api.delete_local_embedding_model(
            _="test-user", model_id="Missing/Model", settings=settings
        )
    assert exc.value.status_code == 404


# ===========================================================================
# Cloud model handlers
# ===========================================================================


@pytest.mark.asyncio
async def test_get_cloud_models_builds_providers() -> None:
    registry = MagicMock()
    registry.get_providers.return_value = ["openai"]
    registry.get_provider_info.return_value = {
        "display_name": "OpenAI",
        "models": [
            {
                "id": "gpt-4o",
                "display_name": "GPT-4o",
                "context_window": 128000,
                "max_output_tokens": 16384,
            }
        ],
    }

    with patch(
        "chaoscypher_core.adapters.llm.model_registry.get_model_registry",
        return_value=registry,
    ):
        result = await api.get_cloud_models(_="test-user")

    assert "openai" in result.providers
    assert result.providers["openai"].display_name == "OpenAI"
    assert result.providers["openai"].models[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_get_provider_models_found() -> None:
    registry = MagicMock()
    registry.get_models.return_value = [
        {
            "id": "gpt-4o",
            "display_name": "GPT-4o",
            "context_window": 128000,
            "max_output_tokens": 16384,
        }
    ]
    with patch(
        "chaoscypher_core.adapters.llm.model_registry.get_model_registry",
        return_value=registry,
    ):
        result = await api.get_provider_models(_="test-user", provider="openai")
    assert result[0].id == "gpt-4o"


@pytest.mark.asyncio
async def test_get_provider_models_not_found_raises_404() -> None:
    registry = MagicMock()
    registry.get_models.return_value = []
    with patch(
        "chaoscypher_core.adapters.llm.model_registry.get_model_registry",
        return_value=registry,
    ):
        with pytest.raises(HTTPException) as exc:
            await api.get_provider_models(_="test-user", provider="nope")
    assert exc.value.status_code == 404


# ===========================================================================
# Ollama verify + LLM verify + health handlers
# ===========================================================================


@pytest.mark.asyncio
async def test_verify_ollama_url_uses_settings_default_timeout() -> None:
    service = MagicMock()
    service.verify_ollama_url = AsyncMock(
        return_value={"success": True, "message": "Ollama is running"}
    )
    settings = _settings_mock()  # ollama_verify_timeout = 7

    request = OllamaVerifyRequest(url="http://localhost:11434", timeout=None)
    result = await api.verify_ollama_url(
        _="test-user", request=request, settings_service=service, settings=settings
    )

    service.verify_ollama_url.assert_awaited_once_with("http://localhost:11434", 7)
    assert result.success is True


@pytest.mark.asyncio
async def test_verify_llm_provider_unknown_raises_400() -> None:
    request = LLMVerifyRequest(provider="madeup", api_key="sk-x")
    with pytest.raises(HTTPException) as exc:
        await api.verify_llm_provider(_="test-user", request=request)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_verify_llm_provider_success() -> None:
    request = LLMVerifyRequest(provider="openai", api_key="sk-secret")
    with patch(
        "chaoscypher_core.services.llm.connectivity.verify_cloud_key",
        AsyncMock(return_value=(True, "Verified")),
    ):
        result = await api.verify_llm_provider(_="test-user", request=request)
    assert result.success is True
    assert result.provider == "openai"
    assert result.message == "Verified"


@pytest.mark.asyncio
async def test_get_llm_health_endpoint_maps_fields() -> None:
    settings = _settings_mock()
    health = MagicMock()
    health.provider = "ollama"
    health.configured = True
    health.verified = False
    health.last_verified_at_iso = None
    health.missing_models = ["qwen3:8b"]

    with patch("chaoscypher_core.services.llm.get_llm_health", AsyncMock(return_value=health)):
        result = await api.get_llm_health_endpoint(_="test-user", settings=settings)

    assert result.provider == "ollama"
    assert result.configured is True
    assert result.verified is False
    assert result.missing_models == ["qwen3:8b"]


# ===========================================================================
# Reset / cleanup / seed handlers
# ===========================================================================


@pytest.mark.asyncio
async def test_reset_workflows_delegates() -> None:
    service = MagicMock()
    expected = ResetResponse(data={"status": "success"})
    service.reset_workflow_system.return_value = expected
    assert await api.reset_workflows(_="test-user", settings_service=service) is expected


@pytest.mark.asyncio
async def test_reset_chats_delegates() -> None:
    service = MagicMock()
    expected = ResetResponse(data={"chats": 0})
    service.reset_chats.return_value = expected
    assert await api.reset_chats(_="test-user", settings_service=service) is expected


@pytest.mark.asyncio
async def test_reset_source_processing_delegates() -> None:
    service = MagicMock()
    expected = ResetResponse(data={"files": 0})
    service.reset_source_processing_history.return_value = expected
    assert await api.reset_source_processing(_="test-user", settings_service=service) is expected


@pytest.mark.asyncio
async def test_reset_queue_delegates_async() -> None:
    service = MagicMock()
    expected = ResetResponse(data={"cancelled": 0})
    service.reset_queue_stats = AsyncMock(return_value=expected)
    assert await api.reset_queue(_="test-user", settings_service=service) is expected


@pytest.mark.asyncio
async def test_seed_templates_delegates() -> None:
    service = MagicMock()
    expected = ResetResponse(data={"created": 3})
    service.seed_templates.return_value = expected
    assert await api.seed_templates(_="test-user", settings_service=service) is expected


@pytest.mark.asyncio
async def test_cleanup_orphans_queues_task() -> None:
    settings = _settings_mock()
    with patch(
        "chaoscypher_core.operations.queue_utils.queue_cleanup_orphans",
        AsyncMock(return_value="task-123"),
    ):
        result = await api.cleanup_orphaned_graph_items(_="test-user", settings=settings)
    assert result.task_id == "task-123"


@pytest.mark.asyncio
async def test_reset_knowledge_queues_task() -> None:
    settings = _settings_mock()
    with patch(
        "chaoscypher_core.operations.queue_utils.queue_reset_knowledge_base",
        AsyncMock(return_value="task-kb"),
    ):
        result = await api.reset_knowledge(_="test-user", settings=settings)
    assert result.task_id == "task-kb"


@pytest.mark.asyncio
async def test_reset_all_requires_confirmation() -> None:
    settings = _settings_mock()
    with pytest.raises(HTTPException) as exc:
        await api.reset_all_data(_="test-user", settings=settings, confirmation="nope")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_reset_all_queues_task_when_confirmed() -> None:
    settings = _settings_mock()
    with patch(
        "chaoscypher_core.operations.queue_utils.queue_reset_all",
        AsyncMock(return_value="task-all"),
    ):
        result = await api.reset_all_data(_="test-user", settings=settings, confirmation="CONFIRM")
    assert result.task_id == "task-all"


# ===========================================================================
# TLS handlers
# ===========================================================================


@pytest.mark.asyncio
async def test_get_tls_status_reports_enabled() -> None:
    service = MagicMock()
    service.is_enabled.return_value = True
    result = await api.get_tls_status(_="test-user", service=service)
    assert result == TLSStatusResponse(enabled=True)


@pytest.mark.asyncio
async def test_enable_self_signed_tls() -> None:
    service = MagicMock()
    service.enable_self_signed = AsyncMock()
    result = await api.enable_self_signed_tls(
        _="test-user", service=service, hostname="example.com"
    )
    assert result.mode == "self-signed"
    service.enable_self_signed.assert_awaited_once_with(hostname="example.com")


@pytest.mark.asyncio
async def test_enable_custom_tls_success() -> None:
    service = MagicMock()
    service.enable_custom = AsyncMock()

    cert_file = MagicMock()
    cert_file.read = AsyncMock(return_value=b"cert-bytes")
    key_file = MagicMock()
    key_file.read = AsyncMock(return_value=b"key-bytes")

    settings = MagicMock()
    settings.batching.tls_cert_max_size = 1024

    result = await api.enable_custom_tls(
        _="test-user",
        cert_file=cert_file,
        key_file=key_file,
        service=service,
        settings=settings,
    )
    assert result.mode == "custom"
    service.enable_custom.assert_awaited_once_with(b"cert-bytes", b"key-bytes")


@pytest.mark.asyncio
async def test_enable_custom_tls_cert_too_large_raises_413() -> None:
    service = MagicMock()
    service.enable_custom = AsyncMock()

    cert_file = MagicMock()
    # read(max_size + 1) returns more than max_size bytes → 413
    cert_file.read = AsyncMock(return_value=b"x" * 11)
    key_file = MagicMock()
    key_file.read = AsyncMock(return_value=b"key")

    settings = MagicMock()
    settings.batching.tls_cert_max_size = 10

    with pytest.raises(HTTPException) as exc:
        await api.enable_custom_tls(
            _="test-user",
            cert_file=cert_file,
            key_file=key_file,
            service=service,
            settings=settings,
        )
    assert exc.value.status_code == 413
    service.enable_custom.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_tls() -> None:
    service = MagicMock()
    service.disable = AsyncMock()
    result = await api.disable_tls(_="test-user", service=service)
    assert result is None
    service.disable.assert_awaited_once()
