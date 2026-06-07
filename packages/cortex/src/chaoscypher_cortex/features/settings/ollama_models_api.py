# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama Models API.

REST endpoints for Ollama model lifecycle management:
list, pull (SSE), remove, and show model details.
"""

import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse

from chaoscypher_cortex.shared.api.errors import operation_error, validation_error
from chaoscypher_cortex.shared.api.responses import (
    AUTH_ERROR_RESPONSES,
    COMMON_ERROR_RESPONSES,
    NOT_FOUND_RESPONSE,
    SERVICE_UNAVAILABLE_RESPONSE,
)


logger = structlog.get_logger(__name__)

from chaoscypher_core.app_config import Settings, get_settings
from chaoscypher_cortex.features.settings.models import (
    OllamaModelPullRequest,
    OllamaModelRemoveRequest,
    OllamaModelRemoveResponse,
    OllamaModelShowResponse,
    OllamaModelsListResponse,
)
from chaoscypher_cortex.features.settings.ollama_models_service import (
    OllamaModelsService,
)
from chaoscypher_cortex.shared.auth.dependencies import (
    CurrentUsername,  # noqa: TC001 - FastAPI runtime dep
)


router = APIRouter()


def get_ollama_models_service(
    settings: Annotated[Settings, Depends(get_settings)],
) -> OllamaModelsService:
    """Create OllamaModelsService with current instance config.

    Args:
        settings: Application settings with Ollama instance config.

    Returns:
        Configured OllamaModelsService.
    """
    # ollama_instances is non-empty by default — LLMSettings seeds a single
    # "default" instance pointed at the Docker host.
    instance_dicts: list[dict[str, Any]] = [
        inst.model_dump() for inst in settings.llm.ollama_instances
    ]

    return OllamaModelsService(
        instances=instance_dicts,
        timeout=settings.timeouts.ollama_http_request,
    )


@router.get(
    "/models",
    response_model=OllamaModelsListResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def list_ollama_models(
    _: CurrentUsername,
    service: Annotated[OllamaModelsService, Depends(get_ollama_models_service)],
) -> OllamaModelsListResponse:
    """List models installed across all Ollama instances.

    **Returns:**
    - Per-instance model lists with name, size, digest, and details
    - Instance health status
    """
    return await service.list_models()


@router.post(
    "/models/pull",
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def pull_ollama_model(
    _: CurrentUsername,
    request: Request,
    pull_request: OllamaModelPullRequest,
    service: Annotated[OllamaModelsService, Depends(get_ollama_models_service)],
) -> EventSourceResponse:
    """Pull a model to one or all Ollama instances.

    Streams progress via server-sent events. On client disconnect (tab
    closed, network drop), the server stops pulling — there is no
    background continuation.

    **Request body:**
    - model: Model name to pull (e.g., "qwen3:30b")
    - instance_id: Target instance (optional, pulls to all if omitted)

    **SSE Events:**
    - `{"status": "downloading", "completed": N, "total": N, "instance_id": "..."}`
    - `{"status": "success", "instance_id": "..."}`
    - `{"status": "error", "error": "...", "instance_id": "..."}`
    """

    async def event_stream() -> AsyncIterator[str]:
        """Yield SSE lines from the Ollama pull stream until done or disconnected."""
        try:
            async for line in service.pull_model(
                model=pull_request.model, instance_id=pull_request.instance_id
            ):
                if await request.is_disconnected():
                    logger.info(
                        "ollama_pull_client_disconnected",
                        model=pull_request.model,
                    )
                    return
                yield line
        except Exception:
            logger.exception(
                "ollama_pull_event_stream_failed",
                model=pull_request.model,
            )
            yield json.dumps({"status": "error", "error": "Unexpected pull stream failure"})

    return EventSourceResponse(event_stream())


@router.delete(
    "/models/remove",
    response_model=OllamaModelRemoveResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **AUTH_ERROR_RESPONSES,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def remove_ollama_model(
    _: CurrentUsername,
    request: OllamaModelRemoveRequest,
    service: Annotated[OllamaModelsService, Depends(get_ollama_models_service)],
) -> OllamaModelRemoveResponse:
    """Remove a model from one or all Ollama instances.

    **Request body:**
    - model: Model name to remove
    - instance_id: Target instance (optional, removes from all if omitted)

    **Returns:**
    - success: Whether removal succeeded on all targeted instances
    - results: Per-instance removal results
    """
    try:
        result = await service.remove_model(model=request.model, instance_id=request.instance_id)
    except ValueError as e:
        raise validation_error("ollama_model_operation", internal_error=e) from e
    return OllamaModelRemoveResponse.model_validate(result)


@router.get(
    "/models/{model:path}/details",
    response_model=OllamaModelShowResponse,
    responses={
        **COMMON_ERROR_RESPONSES,
        **NOT_FOUND_RESPONSE,
        **SERVICE_UNAVAILABLE_RESPONSE,
    },
)
async def show_ollama_model(
    _: CurrentUsername,
    model: str,
    service: Annotated[OllamaModelsService, Depends(get_ollama_models_service)],
    instance_id: str = Query(default="default", description="Ollama instance ID"),
) -> OllamaModelShowResponse:
    """Get detailed information about an Ollama model.

    **Path parameter:**
    - model: Model name (URL-encoded, e.g., qwen3%3A30b)

    **Query parameter:**
    - instance_id: Which instance to query (default: "default")

    **Returns:**
    - Model file, parameters, template, architecture details
    """
    try:
        return await service.show_model(model=model, instance_id=instance_id)
    except ValueError as e:
        raise validation_error("ollama_model_operation", internal_error=e) from e
    except Exception as e:
        raise operation_error("ollama_model_details", internal_error=e) from e
