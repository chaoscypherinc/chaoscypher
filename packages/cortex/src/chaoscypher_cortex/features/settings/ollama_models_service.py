# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Ollama Models Service.

Manages Ollama model lifecycle operations: list, pull, remove, and show.
Proxies requests to the Ollama HTTP API across configured instances.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import structlog

from chaoscypher_cortex.features.settings.models import (
    OllamaInstanceModels,
    OllamaModelDetails,
    OllamaModelInfo,
    OllamaModelShowResponse,
    OllamaModelsListResponse,
)


logger = structlog.get_logger(__name__)


class OllamaModelsService:
    """Service for managing Ollama models across instances.

    Proxies model operations to the Ollama HTTP API. Supports
    multi-instance configurations by targeting specific instances
    or broadcasting to all.
    """

    def __init__(self, instances: list[dict[str, Any]], timeout: int) -> None:
        """Initialize with Ollama instance configurations.

        Args:
            instances: List of instance dicts with id, name, base_url, enabled, healthy.
            timeout: HTTP request timeout in seconds (from settings.timeouts).
        """
        self._instances = {inst["id"]: inst for inst in instances if inst.get("enabled", True)}
        self._timeout = timeout

    def _get_instance(self, instance_id: str) -> dict[str, Any]:
        """Get instance config by ID.

        Args:
            instance_id: The instance identifier.

        Returns:
            Instance configuration dict.

        Raises:
            ValueError: If instance not found.
        """
        if instance_id not in self._instances:
            msg = f"Instance '{instance_id}' not found"
            raise ValueError(msg)
        return self._instances[instance_id]

    def _resolve_instances(self, instance_id: str | None) -> list[dict[str, Any]]:
        """Resolve target instances from optional ID.

        Args:
            instance_id: Specific instance or None for all.

        Returns:
            List of instance config dicts.
        """
        if instance_id:
            return [self._get_instance(instance_id)]
        return list(self._instances.values())

    async def list_models(self) -> OllamaModelsListResponse:
        """List models across all configured instances.

        Returns:
            Response with per-instance model lists.
        """
        results: list[OllamaInstanceModels] = []

        for inst in self._instances.values():
            models: list[OllamaModelInfo] = []
            healthy = True

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(f"{inst['base_url']}/api/tags")
                    if response.status_code == 200:
                        data = response.json()
                        for m in data.get("models", []):
                            details_data = m.get("details", {})
                            models.append(
                                OllamaModelInfo(
                                    name=m.get("name", "unknown"),
                                    size=m.get("size", 0),
                                    modified_at=m.get("modified_at"),
                                    digest=m.get("digest"),
                                    details=OllamaModelDetails(
                                        parameter_size=details_data.get("parameter_size"),
                                        quantization_level=details_data.get("quantization_level"),
                                        family=details_data.get("family"),
                                        format=details_data.get("format"),
                                    )
                                    if details_data
                                    else None,
                                )
                            )
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                healthy = False
                logger.warning(
                    "ollama_instance_unreachable",
                    instance_id=inst["id"],
                    error=str(e),
                )
            except Exception as e:
                healthy = False
                logger.warning(
                    "ollama_list_models_failed",
                    instance_id=inst["id"],
                    error=str(e),
                )

            results.append(
                OllamaInstanceModels(
                    instance_id=inst["id"],
                    instance_name=inst.get("name", inst["id"]),
                    base_url=inst["base_url"],
                    healthy=healthy,
                    models=models,
                )
            )

        return OllamaModelsListResponse(instances=results)

    async def pull_model(self, model: str, instance_id: str | None = None) -> AsyncIterator[str]:
        """Pull a model, streaming progress as SSE lines.

        Args:
            model: Model name to pull (e.g., "qwen3:30b").
            instance_id: Target instance or None for all.

        Yields:
            SSE-formatted data lines with pull progress.

        Raises:
            ValueError: If specified instance not found.
            httpx.ConnectError: If Ollama is unreachable.
        """
        targets = self._resolve_instances(instance_id)

        for inst in targets:
            inst_id = inst["id"]
            logger.info("ollama_pull_started", model=model, instance_id=inst_id)

            try:
                async with (
                    httpx.AsyncClient(
                        timeout=httpx.Timeout(
                            connect=float(self._timeout),
                            read=None,
                            write=float(self._timeout),
                            pool=float(self._timeout),
                        )
                    ) as client,
                    client.stream(
                        "POST",
                        f"{inst['base_url']}/api/pull",
                        json={"name": model, "stream": True},
                    ) as response,
                ):
                    if response.status_code != 200:
                        yield json.dumps(
                            {
                                "status": "error",
                                "instance_id": inst_id,
                                "error": f"Model pull failed (HTTP {response.status_code})",
                            }
                        )
                        continue

                    async for line in response.aiter_lines():
                        if line.strip():
                            # Inject instance_id for multi-instance tracking
                            if line.startswith("{"):
                                try:
                                    data = json.loads(line)
                                    data["instance_id"] = inst_id
                                    yield json.dumps(data)
                                except json.JSONDecodeError:
                                    yield line
                            else:
                                yield line

                logger.info("ollama_pull_completed", model=model, instance_id=inst_id)

            except httpx.ConnectError:
                yield json.dumps(
                    {
                        "status": "error",
                        "instance_id": inst_id,
                        "error": "Connection refused",
                    }
                )
                logger.warning(
                    "ollama_pull_connection_refused",
                    model=model,
                    instance_id=inst_id,
                )
            except httpx.HTTPError as exc:
                yield json.dumps(
                    {
                        "status": "error",
                        "instance_id": inst_id,
                        "error": "Upstream HTTP error during pull",
                    }
                )
                logger.warning(
                    "ollama_pull_http_error",
                    model=model,
                    instance_id=inst_id,
                    error=str(exc),
                )
            except Exception:
                yield json.dumps(
                    {
                        "status": "error",
                        "instance_id": inst_id,
                        "error": "Unexpected pull failure",
                    }
                )
                logger.exception(
                    "ollama_pull_unexpected_failure",
                    model=model,
                    instance_id=inst_id,
                )

    async def remove_model(self, model: str, instance_id: str | None = None) -> dict[str, Any]:
        """Remove a model from one or all instances.

        Args:
            model: Model name to remove.
            instance_id: Target instance or None for all.

        Returns:
            Dict with success status and per-instance results.

        Raises:
            ValueError: If specified instance not found.
        """
        targets = self._resolve_instances(instance_id)
        results: list[dict[str, Any]] = []

        for inst in targets:
            inst_id = inst["id"]
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.request(
                        "DELETE",
                        f"{inst['base_url']}/api/delete",
                        json={"name": model},
                    )
                    success = response.status_code == 200
                    results.append({"instance_id": inst_id, "success": success})

                    if success:
                        logger.info(
                            "ollama_model_removed",
                            model=model,
                            instance_id=inst_id,
                        )
                    else:
                        logger.warning(
                            "ollama_model_remove_failed",
                            model=model,
                            instance_id=inst_id,
                            status_code=response.status_code,
                        )

            except Exception:
                results.append(
                    {"instance_id": inst_id, "success": False, "error": "Model removal failed"}
                )

        all_success = all(r["success"] for r in results)
        return {"success": all_success, "results": results}

    async def show_model(self, model: str, instance_id: str = "default") -> OllamaModelShowResponse:
        """Get detailed model information from Ollama.

        Args:
            model: Model name to inspect.
            instance_id: Instance to query.

        Returns:
            Model details response.

        Raises:
            ValueError: If instance not found.
            httpx.HTTPError: If Ollama request fails.
        """
        inst = self._get_instance(instance_id)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{inst['base_url']}/api/show",
                json={"name": model},
            )
            response.raise_for_status()
            data = response.json()

            details_data = data.get("details", {})
            return OllamaModelShowResponse(
                modelfile=data.get("modelfile"),
                parameters=data.get("parameters"),
                template=data.get("template"),
                details=OllamaModelDetails(
                    parameter_size=details_data.get("parameter_size"),
                    quantization_level=details_data.get("quantization_level"),
                    family=details_data.get("family"),
                    format=details_data.get("format"),
                )
                if details_data
                else None,
                model_info=data.get("model_info"),
            )
