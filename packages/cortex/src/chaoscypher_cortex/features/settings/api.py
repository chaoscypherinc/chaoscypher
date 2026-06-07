# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings API Endpoints.

GET    /api/v1/settings - Get current settings
PATCH  /api/v1/settings - Update settings
POST   /api/v1/settings/reset - Reset settings to defaults
GET    /api/v1/settings/logging/level - Get logging level
POST   /api/v1/settings/logging/level - Set logging level
GET    /api/v1/settings/presets - List VRAM presets
GET    /api/v1/settings/presets/{preset_id} - Get specific preset
POST   /api/v1/settings/presets/apply - Apply a VRAM preset
GET    /api/v1/settings/embedding/models - Get curated embedding models
GET    /api/v1/settings/embedding/local/models - List downloaded local models
POST   /api/v1/settings/embedding/local/models - Download a local model
DELETE /api/v1/settings/embedding/local/models/{model_id} - Delete a local model
GET    /api/v1/settings/cloudmodels - Get all cloud LLM models
GET    /api/v1/settings/cloudmodels/{provider} - Get models for a provider
POST   /api/v1/settings/ollama/verify - Verify Ollama URL connectivity
GET    /api/v1/settings/llm/health - Snapshot of LLM verified state (drives import/chat gates)
POST   /api/v1/settings/reset/workflows - Reset workflow system
POST   /api/v1/settings/reset/chats - Reset chats
POST   /api/v1/settings/reset/queue - Reset queue stats
POST   /api/v1/settings/reset/knowledge - Reset knowledge base
POST   /api/v1/settings/reset/source_processing - Reset source_processing history
POST   /api/v1/settings/reset/all - Nuclear reset (requires CONFIRM)
POST   /api/v1/settings/cleanup/orphans - Clean up orphaned graph items
POST   /api/v1/settings/seed/templates - Re-seed default templates
GET    /api/v1/settings/tls/status - Check if TLS is enabled
POST   /api/v1/settings/tls/selfsigned - Generate self-signed cert (admin)
POST   /api/v1/settings/tls/custom - Upload custom cert/key (admin)
DELETE /api/v1/settings/tls - Disable TLS (admin)
"""

import asyncio
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, status


if TYPE_CHECKING:
    from chaoscypher_cortex.features.settings.tls_service import TLSService

from chaoscypher_core.app_config import (
    ConfigManager,
    Settings,
    get_config_manager,
    get_current_database_name,
    mask_settings_dict,
    strip_masked_values,
)
from chaoscypher_core.app_config import (
    get_settings as get_settings_dep,
)
from chaoscypher_cortex.features.settings.logging_service import LoggingService
from chaoscypher_cortex.features.settings.models import (
    ApplyPresetRequest,
    ApplyPresetResponse,
    CloudEmbeddingModelInfo,
    CloudModelInfo,
    CloudModelsResponse,
    CloudProviderInfo,
    CuratedEmbeddingModelInfo,
    EmbeddingModelsResponse,
    LLMHealthResponse,
    LLMVerifyRequest,
    LLMVerifyResponse,
    LocalEmbeddingDownloadRequest,
    LocalEmbeddingDownloadResponse,
    LocalEmbeddingModelInfo,
    LocalEmbeddingModelsResponse,
    LoggingLevelRequest,
    LoggingLevelResponse,
    MaskedSettingsResponse,
    OllamaVerifyRequest,
    OllamaVerifyResponse,
    PresetListResponse,
    ResetResponse,
    SetLoggingLevelResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
    TLSEnableResponse,
    TLSStatusResponse,
    VRAMPresetResponse,
)
from chaoscypher_cortex.features.settings.service import SettingsService
from chaoscypher_cortex.shared.api.errors import (
    raise_if_not_found,
    resource_not_found_error,
    validation_error,
)
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    CONFLICT_RESPONSE,
    NOT_FOUND_RESPONSE,
    SERVICE_UNAVAILABLE_RESPONSE,
    ErrorDetail,
    QueuedResetResponse,
)
from chaoscypher_cortex.shared.auth.dependencies import CurrentUsername


router = APIRouter()


def get_tls_service(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TLSService:
    """Factory for the TLS management service."""
    from chaoscypher_cortex.features.settings.tls_service import TLSService

    return TLSService(settings.tls)


def get_settings_service(
    settings_manager: Annotated[ConfigManager, Depends(get_config_manager)],
    database_name: Annotated[str, Depends(get_current_database_name)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SettingsService:
    """Get SettingsService instance with trigger/workflow services."""
    from chaoscypher_core.database import get_sqlite_adapter
    from chaoscypher_core.services.workflows import (
        WorkflowService as EngineWorkflowService,
    )
    from chaoscypher_core.services.workflows.tools import (
        ToolService as EngineToolService,
    )
    from chaoscypher_core.services.workflows.triggers import (
        TriggerService as EngineTriggerService,
    )

    # Get singleton storage adapter
    adapter = get_sqlite_adapter(database_name=database_name)

    # Create engine services using storage protocol
    trigger_service = EngineTriggerService(storage=adapter, database_name=database_name)
    tool_service = EngineToolService(storage=adapter, database_name=database_name)
    workflow_service = EngineWorkflowService(
        storage=adapter, database_name=database_name, tool_service=tool_service
    )

    return SettingsService(
        settings_manager,
        database_name,
        trigger_service=trigger_service,
        workflow_service=workflow_service,
        adapter=adapter,
        logging_service=LoggingService(),
    )


@router.get(
    "",
    response_model=MaskedSettingsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_settings(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> MaskedSettingsResponse:
    """Get current application settings with secrets masked.

    **Returns:**
    - Complete settings object with secret fields masked for security
    """
    settings = settings_service.get_settings()
    return MaskedSettingsResponse.model_validate(mask_settings_dict(settings.model_dump()))


@router.patch(
    "",
    response_model=SettingsUpdateResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def update_settings(
    _: CurrentUsername,
    settings_update: SettingsUpdateRequest,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> SettingsUpdateResponse:
    """Update application settings (partial update).

    **Automatic Trigger Sync:**
    - When `enable_auto_embedding` changes, automatically updates system triggers
    - Syncs triggers for node.created and node.updated events
    - Only affects system workflows (not user-created workflows)

    **Hot-Reload:**
    - When LLM settings change, workers are notified via Valkey pub/sub
    - Workers reload their LLM providers without requiring restart

    **Warnings:**
    - When vector_dimensions changes, returns a warning about orphaned embeddings

    **Request Body (``SettingsUpdateRequest``):**
    - Any valid settings fields to update as nested dicts keyed by group
      (e.g. ``llm``, ``search``, ``local_auth``).
    - Unknown top-level keys are rejected (422) at Pydantic validation.
      Allowed keys are derived from ``Settings.model_fields`` so the
      DTO cannot drift from the real schema.
    - Example: ``{"search": {"enable_auto_embedding": true}}``.

    **Returns:**
    - Updated settings object with any warnings about the impact of changes
    """
    old_settings = settings_service.get_settings()
    # The typed body rejects unknown top-level keys and strips security-/
    # startup-sensitive ones (dev_mode, local_auth secrets) before they reach
    # the merge; convert to a sparse dict for the downstream ConfigManager
    # (which expects the same shape as settings.yaml).
    update_dict = settings_update.model_dump(exclude_none=True, exclude_unset=True)
    strip_masked_values(update_dict)
    updated = await settings_service.update_settings(update_dict)

    # Detect warnings (e.g., dimension changes with existing data)
    warnings = settings_service.get_update_warnings(old_settings, updated)

    # Notify workers if LLM, search, or embedding settings changed
    if "llm" in update_dict or "search" in update_dict or "embedding" in update_dict:
        await settings_service.notify_workers_llm_settings_changed()

    # Invalidate Cortex singletons if embedding/search settings changed
    if "embedding" in update_dict or "search" in update_dict:
        from chaoscypher_core.repo_factories.embedding_factory import (
            invalidate_embedding_service,
        )
        from chaoscypher_core.repo_factories.search_factory import (
            invalidate_search_repository,
        )

        invalidate_embedding_service()
        invalidate_search_repository()

    return SettingsUpdateResponse(
        settings=mask_settings_dict(updated.model_dump()),
        warnings=warnings,
    )


@router.post(
    "/reset",
    response_model=MaskedSettingsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def reset_settings(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> MaskedSettingsResponse:
    """Reset settings to default values.

    **RESTful Design:**
    - POST /reset is an action operation on the settings resource
    - Not a resource deletion, but a reset operation

    **Returns:**
    - Default settings object with secrets masked
    """
    result = settings_service.reset_to_defaults()
    return MaskedSettingsResponse.model_validate(mask_settings_dict(result.model_dump()))


@router.get(
    "/logging/level",
    response_model=LoggingLevelResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_logging_level(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> LoggingLevelResponse:
    """Get current logging level for the application.

    **Returns:**
    - Current level name (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    - Numeric level value
    - List of available levels
    """
    return settings_service.get_logging_level()


@router.post(
    "/logging/level",
    response_model=SetLoggingLevelResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def set_logging_level(
    _: CurrentUsername,
    request: LoggingLevelRequest,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> SetLoggingLevelResponse:
    """Set logging level for the application in real-time.

    **No restart required** - Change is immediate.

    **Request Body:**
    - `level`: DEBUG, INFO, WARNING, ERROR, or CRITICAL

    **Example:**
    ```json
    {"level": "WARNING"}
    ```

    **Returns:**
    - Old and new logging levels
    - Success status
    """
    result = settings_service.set_logging_level(request.level)
    if result.success:
        await settings_service.notify_workers_logging_level(request.level)
    return result


# ============================================================================
# VRAM Preset Endpoints
# ============================================================================


@router.get(
    "/presets",
    response_model=PresetListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_presets(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> PresetListResponse:
    """List all available VRAM presets.

    **Returns:**
    - List of presets sorted by VRAM size (ascending)
    - Each preset includes: name, display_name, description, vram_gb,
      gpu_examples, ollama_settings, llm_settings

    **Presets are loaded from:**
    - Built-in presets (shipped with package)
    - User presets in data/plugins/presets/ (override built-in with same name)
    """
    return settings_service.list_presets()


@router.get(
    "/presets/{preset_id}",
    response_model=VRAMPresetResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_preset(
    _: CurrentUsername,
    preset_id: str,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> VRAMPresetResponse:
    """Get a specific VRAM preset by ID.

    **Path Parameters:**
    - `preset_id`: Preset identifier (e.g., "vram_24gb")

    **Returns:**
    - Full preset configuration

    **Raises:**
    - 404 if preset not found
    """
    preset = settings_service.get_preset(preset_id)
    return raise_if_not_found(preset, f"Preset not found: {preset_id}")


@router.post(
    "/presets/apply",
    response_model=ApplyPresetResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def apply_preset(
    _: CurrentUsername,
    request: ApplyPresetRequest,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> ApplyPresetResponse:
    """Apply a VRAM preset to update LLM settings.

    **Request Body:**
    - `preset_id`: Preset to apply (e.g., "vram_24gb")

    **Example:**
    ```json
    {"preset_id": "vram_24gb"}
    ```

    **Updates these settings:**
    - ollama_chat_model
    - ollama_num_ctx
    - ollama_num_batch
    - ai_max_tokens
    - thinking_for_chat
    - thinking_for_tools
    - thinking_for_extraction

    **Preserves:**
    - All other settings (API keys, URLs, instances, etc.)

    **Hot-Reload:**
    - Workers are notified via Valkey pub/sub to reload their LLM providers

    **Returns:**
    - Applied preset info and updated settings
    """
    try:
        from chaoscypher_core.services.llm import get_llm_health

        result = settings_service.apply_preset(request.preset_id)
        # Notify workers to reload LLM providers
        await settings_service.notify_workers_llm_settings_changed()

        # Compute missing_models on the POST-APPLY settings so the response
        # tells the user immediately which models they need to pull. Saves a
        # useLLMHealth refetch and a confused-user round-trip ("I clicked
        # Apply, why is Add Source still disabled?").
        post_apply_settings = settings_service.settings_manager.get_settings()
        health = await get_llm_health(post_apply_settings)

        # Pydantic model_copy lets us add the field without mutating the
        # service-layer return value (the service is unit-tested separately
        # and shouldn't know about missing_models).
        return result.model_copy(update={"missing_models": list(health.missing_models)})
    except KeyError as e:
        raise resource_not_found_error("preset", request.preset_id) from e


# ============================================================================
# Cloud Model Registry Endpoints
# ============================================================================


@router.get(
    "/embedding/models",
    response_model=EmbeddingModelsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_embedding_models(_: CurrentUsername) -> EmbeddingModelsResponse:
    """Get curated embedding models and cloud provider models.

    **Returns:**
    - `curated`: List of vetted local/Ollama embedding models with dimensions
    - `cloud`: Dictionary of cloud provider embedding models (openai, gemini)

    **Use cases:**
    - Populate embedding model selection dropdowns in UI
    - Auto-fill dimensions when a curated model is selected
    """
    from chaoscypher_core.adapters.embedding.registry import (
        CLOUD_EMBEDDING_MODELS,
        CURATED_EMBEDDING_MODELS,
    )

    return EmbeddingModelsResponse(
        curated=[CuratedEmbeddingModelInfo(**m.model_dump()) for m in CURATED_EMBEDDING_MODELS],
        cloud={
            provider: [CloudEmbeddingModelInfo(**m.model_dump()) for m in models]
            for provider, models in CLOUD_EMBEDDING_MODELS.items()
        },
    )


@router.get(
    "/embedding/local/models",
    response_model=LocalEmbeddingModelsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def list_local_embedding_models(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> LocalEmbeddingModelsResponse:
    """List locally downloaded HuggingFace embedding models.

    Scans the model cache directory for downloaded models. Each subdirectory
    named ``models--{org}--{model}`` represents a downloaded model.

    **Returns:**
    - List of downloaded model IDs with names and paths
    """
    from pathlib import Path

    cache_dir = Path(settings.paths.data_dir) / "models" / "embeddings"
    models: list[LocalEmbeddingModelInfo] = []
    if cache_dir.exists():
        for entry in sorted(cache_dir.iterdir()):
            if entry.is_dir() and entry.name.startswith("models--"):
                parts = entry.name.split("--")[1:]
                if len(parts) >= 2:
                    model_id = "/".join(parts)
                    models.append(
                        LocalEmbeddingModelInfo(
                            id=model_id,
                            name=parts[-1],
                            path=str(entry),
                        )
                    )
    return LocalEmbeddingModelsResponse(models=models)


@router.post(
    "/embedding/local/models",
    response_model=LocalEmbeddingDownloadResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def download_local_embedding_model(
    _: CurrentUsername,
    request: LocalEmbeddingDownloadRequest,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> LocalEmbeddingDownloadResponse:
    """Download a HuggingFace embedding model to the local cache.

    This is a blocking operation that can take minutes for large models.
    The model is downloaded and validated before returning.

    **Request Body:**
    - ``model``: HuggingFace model ID (e.g. "Qwen/Qwen3-Embedding-0.6B")

    **Returns:**
    - Model name, native dimensions, and download time
    """
    from pathlib import Path

    from chaoscypher_core.adapters.embedding.local_provider import LocalEmbeddingProvider

    cache_dir = Path(settings.paths.data_dir) / "models" / "embeddings"
    provider = LocalEmbeddingProvider(
        model_name=request.model,
        vector_dimensions=settings.search.vector_dimensions,
        cache_dir=cache_dir,
    )
    try:
        result = await provider.download_model(request.model)
    except ValueError as e:
        raise validation_error("embedding_download", internal_error=e) from e
    return LocalEmbeddingDownloadResponse(**result)


@router.delete(
    "/embedding/local/models/{model_id:path}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def delete_local_embedding_model(
    _: CurrentUsername,
    model_id: str,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> None:
    """Delete a locally cached HuggingFace embedding model.

    **Path Parameters:**
    - ``model_id``: Model ID in org/name format (e.g. "Qwen/Qwen3-Embedding-0.6B")

    **Returns:**
    - 204 No Content on success
    """
    import shutil
    from pathlib import Path

    cache_dir = Path(settings.paths.data_dir) / "models" / "embeddings"
    # Convert "Qwen/Qwen3-Embedding-0.6B" to "models--Qwen--Qwen3-Embedding-0.6B"
    dir_name = "models--" + model_id.replace("/", "--")
    model_path = cache_dir / dir_name

    # Path traversal protection
    resolved = model_path.resolve()
    if not resolved.is_relative_to(cache_dir.resolve()):
        raise validation_error("model_deletion", internal_error=ValueError("Invalid model path"))

    raise_if_not_found(model_path.exists() and model_path.is_dir(), f"Model not found: {model_id}")

    await asyncio.to_thread(shutil.rmtree, model_path)


@router.get(
    "/cloudmodels",
    response_model=CloudModelsResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_cloud_models(_: CurrentUsername) -> CloudModelsResponse:
    """Get all available cloud LLM models grouped by provider.

    **Returns:**
    - Dictionary of providers (gemini, openai, anthropic)
    - Each provider includes list of models with:
      - Model ID
      - Display name
      - Context window size
      - Max output tokens
      - Vision/tools support flags
      - Pricing (input/output per million tokens)
      - Optional notes

    **Use cases:**
    - Populate model selection dropdowns in UI
    - Show model capabilities and pricing
    - Auto-populate context/output limits when model selected
    """
    from chaoscypher_core.adapters.llm.model_registry import get_model_registry

    registry = get_model_registry()
    providers = {}

    for provider_id in registry.get_providers():
        provider_data = registry.get_provider_info(provider_id)
        if provider_data:
            models = [CloudModelInfo(**model) for model in provider_data.get("models", [])]
            providers[provider_id] = CloudProviderInfo(
                display_name=provider_data.get("display_name", provider_id),
                models=models,
            )

    return CloudModelsResponse(providers=providers)


@router.get(
    "/cloudmodels/{provider}",
    response_model=list[CloudModelInfo],
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
    },
)
async def get_provider_models(_: CurrentUsername, provider: str) -> list[CloudModelInfo]:
    """Get available models for a specific cloud provider.

    **Path Parameters:**
    - `provider`: Provider ID (gemini, openai, anthropic)

    **Returns:**
    - List of models for the specified provider

    **Raises:**
    - 404 if provider not found
    """
    from chaoscypher_core.adapters.llm.model_registry import get_model_registry

    registry = get_model_registry()
    models = raise_if_not_found(registry.get_models(provider), f"Provider not found: {provider}")

    return [CloudModelInfo(**model) for model in models]


# ============================================================================
# Ollama Verification Endpoints
# ============================================================================


@router.post(
    "/ollama/verify",
    response_model=OllamaVerifyResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def verify_ollama_url(
    _: CurrentUsername,
    request: OllamaVerifyRequest,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> OllamaVerifyResponse:
    """Verify that an Ollama instance is running at the given URL.

    **Request Body:**
    - `url`: The Ollama base URL to verify (e.g., "http://localhost:11434")
    - `timeout`: Request timeout in seconds (uses settings default if not provided)

    **Example:**
    ```json
    {"url": "http://localhost:11434", "timeout": 5}
    ```

    **Checks:**
    - Basic connectivity (root endpoint)
    - Available models via /api/tags
    - Version info via /api/version

    **Returns:**
    - Success status and message
    - Ollama version (if available)
    - List of installed models
    - Response time in milliseconds
    """
    timeout = request.timeout or settings.timeouts.ollama_verify_timeout
    result = await settings_service.verify_ollama_url(request.url, timeout)

    # As of 2026-05-22, /llm/health is real-time (probes /api/tags every
    # refetch). This endpoint just returns the probe result for the
    # settings page's "Test Connection" button — no side effects.
    return OllamaVerifyResponse(**result)


@router.post(
    "/llm/verify",
    response_model=LLMVerifyResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def verify_llm_provider(
    _: CurrentUsername,
    request: LLMVerifyRequest,
) -> LLMVerifyResponse:
    """Verify a cloud LLM provider's API key against its public endpoint.

    Backs the wizard's "Test connection" affordance and the action-gating
    model: a successful response here records into the in-memory verify
    tracker so subsequent import / chat actions can proceed for this
    provider.

    **Request Body:**
    - ``provider``: ``"openai"`` | ``"anthropic"`` | ``"gemini"``
    - ``api_key``: Cloud API key to probe with

    **Returns:**
    - ``success``: True when the provider returned 200
    - ``message``: User-actionable explanation (e.g. "Invalid API key")
    - ``provider``: Echoed back

    For Ollama use ``POST /settings/ollama/verify`` (different shape).
    """
    from chaoscypher_core.services.llm.connectivity import (
        CLOUD_PROVIDERS,
        verify_cloud_key,
    )

    if request.provider not in CLOUD_PROVIDERS:
        raise validation_error(operation=f"verify_llm_provider({request.provider!r})")

    # As of 2026-05-22, /llm/health is real-time and the cloud-provider
    # predicate is an API-key format check. This endpoint just returns the
    # probe result for the settings page's "Test Connection" button — no
    # side effects.
    success, message = await verify_cloud_key(request.provider, request.api_key.get_secret_value())

    return LLMVerifyResponse(
        success=success,
        message=message,
        provider=request.provider,
    )


@router.get(
    "/llm/health",
    response_model=LLMHealthResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def get_llm_health_endpoint(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> LLMHealthResponse:
    """Snapshot of the currently-selected LLM chat provider's health.

    Drives the frontend's action-gating UX. The same predicate is
    enforced server-side at the action surface (``POST /sources`` and
    chat send return 409 ``llm_not_verified`` when ``verified`` is
    False) — keeping computation in one place ensures the banner state
    and the gate state can't drift.

    **Returns:**
    - ``provider``: the selected chat provider
    - ``configured``: True when the provider's required config (API
      key or Ollama instance) is populated
    - ``verified``: True when at least one successful verify has been
      recorded this process for the provider
    - ``last_verified_at``: ISO-8601 UTC timestamp or null
    """
    from chaoscypher_core.services.llm import get_llm_health

    health = await get_llm_health(settings)
    return LLMHealthResponse(
        provider=health.provider,
        configured=health.configured,
        verified=health.verified,
        last_verified_at=health.last_verified_at_iso,
        missing_models=list(health.missing_models),
    )


# ============================================================================
# Database Reset Endpoints
# ============================================================================


@router.post(
    "/reset/workflows",
    response_model=ResetResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def reset_workflows(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> ResetResponse:
    """Reset workflow system (tools, workflows, triggers) to defaults.

    **WARNING:** This operation cannot be undone!

    **Deletes:**
    - All custom workflows and execution history
    - All user tools
    - All triggers and execution history

    **Recreates:**
    - System tools (40+)
    - Default workflows (3)
    - Default triggers (2)

    **Returns:**
    - Reset statistics
    """
    return settings_service.reset_workflow_system()


@router.post(
    "/reset/chats",
    response_model=ResetResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def reset_chats(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> ResetResponse:
    """Reset all chats.

    **WARNING:** This operation cannot be undone!

    **Deletes:**
    - All chats
    - All messages

    **Returns:**
    - Reset statistics
    """
    return settings_service.reset_chats()


@router.post(
    "/reset/source_processing",
    response_model=ResetResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def reset_source_processing(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> ResetResponse:
    """Reset source_processing history (imports, chunks, extraction jobs).

    **WARNING:** This operation cannot be undone!

    **Deletes:**
    - All source_processing file records
    - All staged document chunks (not yet committed to sources)
    - All entity embeddings from source_processing
    - All chunk extraction jobs and tasks
    - Uploaded import files directory

    **Preserves:**
    - Committed sources and their chunks
    - Knowledge graph (nodes, edges)
    - Workflows, tools, triggers
    - Conversations

    **Returns:**
    - Reset statistics
    """
    return settings_service.reset_source_processing_history()


# ============================================================================
# Cleanup Endpoints
# ============================================================================


@router.post(
    "/cleanup/orphans",
    response_model=QueuedResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def cleanup_orphaned_graph_items(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> QueuedResetResponse:
    """Queue a cleanup pass for orphaned graph items.

    Enqueues a background task that removes graph items with invalid
    references. Previously ran inline on the API thread (could take
    tens of seconds on large graphs). Poll
    ``GET /queue/tasks/{task_id}/result`` for the final cleanup stats.

    **Removes:**
    - Edges pointing to non-existent nodes
    - Nodes with source_id pointing to non-existent sources
    - Templates with source_id pointing to non-existent sources

    **Preserves:**
    - Nodes/edges with source_id=NULL (intentionally unlinked)
    - System templates

    **Returns:**
    - ``task_id`` — poll for the cleanup-stats payload when complete.
    """
    from chaoscypher_core.constants import OP_CLEANUP_ORPHANS
    from chaoscypher_core.operations.queue_utils import queue_cleanup_orphans

    task_id = await queue_cleanup_orphans(
        database_name=settings.current_database,
        priority=settings.priorities.background,
    )
    return QueuedResetResponse(task_id=task_id, operation_type=OP_CLEANUP_ORPHANS)


@router.post(
    "/reset/queue",
    response_model=ResetResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def reset_queue(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> ResetResponse:
    """Reset queue system (jobs, statistics, and history).

    **WARNING:** This operation cannot be undone!

    **Cancels/Deletes:**
    - All active/queued jobs (cancelled)
    - All completed/failed/cancelled task records
    - All token usage statistics
    - All cost tracking data
    - Task history

    **Preserves:**
    - Queue configuration

    **Returns:**
    - Reset statistics including cancelled and cleared task counts
    """
    return await settings_service.reset_queue_stats()


@router.post(
    "/reset/knowledge",
    response_model=QueuedResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def reset_knowledge(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> QueuedResetResponse:
    """Queue a knowledge-base reset.

    **WARNING:** This operation cannot be undone!

    Enqueues a background task. Previously ran inline on the API thread
    (could take 30+ seconds on large datasets). Poll
    ``GET /queue/tasks/{task_id}/result`` for final reset statistics.

    **Deletes (on the worker):**
    - Import history and file records
    - Discovery sessions and AI suggestions
    - Knowledge graph (nodes, edges, templates, lenses, RDF triples)
    - Document sources (sources, chunks, citations, tags)
    - Search indices (full-text and vector)

    **Preserves:**
    - Workflows, tools, triggers
    - Conversations
    - Queue statistics

    **Returns:**
    - ``task_id`` — poll for combined reset-stats when complete.
    """
    from chaoscypher_core.constants import OP_RESET_KNOWLEDGE_BASE
    from chaoscypher_core.operations.queue_utils import queue_reset_knowledge_base

    task_id = await queue_reset_knowledge_base(
        database_name=settings.current_database,
        priority=settings.priorities.background,
    )
    return QueuedResetResponse(task_id=task_id, operation_type=OP_RESET_KNOWLEDGE_BASE)


@router.post(
    "/reset/all",
    response_model=QueuedResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def reset_all_data(
    _: CurrentUsername,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    confirmation: str = Body(..., embed=True),
) -> QueuedResetResponse:
    """Reset ALL database data.

    **DANGER:** This deletes EVERYTHING and recreates with defaults!

    **This operation CANNOT be undone!**

    **Requires:** `confirmation` field set to "CONFIRM" in request body

    **Example:**
    ```json
    {"confirmation": "CONFIRM"}
    ```

    **Deletes:**
    - Entire app.db file (workflows, tools, triggers, imports, chats, discovery)
    - All RDF graphs (knowledge, templates, lenses)
    - All search indices (full-text and vector)
    - Uploaded import files
    - Queue history

    **Recreates:**
    - Fresh database with system defaults
    - System tools (40+)
    - Default workflows (3)
    - Default triggers (2)

    **Returns:**
    - Reset statistics
    """
    if confirmation != "CONFIRM":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorDetail(
                code="VALIDATION_FAILED",
                message="Must provide confirmation='CONFIRM' to reset all data",
            ).model_dump(),
        )

    from chaoscypher_core.constants import OP_RESET_ALL
    from chaoscypher_core.operations.queue_utils import queue_reset_all

    task_id = await queue_reset_all(
        database_name=settings.current_database,
        priority=settings.priorities.background,
    )
    return QueuedResetResponse(task_id=task_id, operation_type=OP_RESET_ALL)


@router.post(
    "/seed/templates",
    response_model=ResetResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def seed_templates(
    _: CurrentUsername,
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
) -> ResetResponse:
    """Re-seed default system templates.

    **Safe operation:** Only creates templates that don't already exist.

    **Creates (if missing):**
    - Default node templates (Note, Item, Person, Organization, etc.)
    - Default edge templates (link, works_at, located_in, etc.)
    - System templates (Workflow, Lens, etc.)

    **Returns:**
    - Number of templates created
    """
    return settings_service.seed_templates()


# ============================================================================
# TLS Management Endpoints
# ============================================================================


@router.get(
    "/tls/status",
    response_model=TLSStatusResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
    },
)
async def get_tls_status(
    _: CurrentUsername,
    service: Annotated[TLSService, Depends(get_tls_service)],
) -> TLSStatusResponse:
    """Check if TLS is enabled."""
    return TLSStatusResponse(enabled=service.is_enabled())


@router.post(
    "/tls/selfsigned",
    response_model=TLSEnableResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **CONFLICT_RESPONSE,
    },
)
async def enable_self_signed_tls(
    _: CurrentUsername,
    service: Annotated[TLSService, Depends(get_tls_service)],
    hostname: str | None = None,
) -> TLSEnableResponse:
    """Generate self-signed cert and enable TLS. Admin only."""
    await service.enable_self_signed(hostname=hostname)
    return TLSEnableResponse(status="enabled", mode="self-signed")


@router.post(
    "/tls/custom",
    response_model=TLSEnableResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **CONFLICT_RESPONSE,
    },
)
async def enable_custom_tls(
    _: CurrentUsername,
    cert_file: UploadFile,
    key_file: UploadFile,
    service: Annotated[TLSService, Depends(get_tls_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> TLSEnableResponse:
    """Upload custom cert and key. Admin only."""
    max_size = settings.batching.tls_cert_max_size
    cert_pem = await cert_file.read(max_size + 1)
    if len(cert_pem) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=ErrorDetail(
                code="PAYLOAD_TOO_LARGE",
                message=f"Certificate file exceeds {max_size // 1024 // 1024}MB limit",
            ).model_dump(),
        )
    key_pem = await key_file.read(max_size + 1)
    if len(key_pem) > max_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=ErrorDetail(
                code="PAYLOAD_TOO_LARGE",
                message=f"Key file exceeds {max_size // 1024 // 1024}MB limit",
            ).model_dump(),
        )

    await service.enable_custom(cert_pem, key_pem)
    return TLSEnableResponse(status="enabled", mode="custom")


@router.delete(
    "/tls",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
    },
)
async def disable_tls(
    _: CurrentUsername,
    service: Annotated[TLSService, Depends(get_tls_service)],
) -> None:
    """Disable TLS. Admin only."""
    await service.disable()
