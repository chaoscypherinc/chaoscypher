# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings Service.

Business logic for settings management and database reset operations.

SRP REFACTORED: Now focuses on core settings CRUD.
- Logging operations → LoggingService
- Trigger synchronization → TriggerSyncService
- Reset operations → ResetService (already delegated)
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.services.reset import ResetOperations
from chaoscypher_core.utils.url_safety import validate_url_safety
from chaoscypher_cortex.features.settings.models import (
    ApplyPresetResponse,
    LoggingLevelResponse,
    PresetListResponse,
    ResetResponse,
    SetLoggingLevelResponse,
    SettingsWarning,
    VRAMPresetResponse,
)
from chaoscypher_cortex.features.settings.trigger_sync_service import TriggerSyncService


if TYPE_CHECKING:
    from chaoscypher_core.app_config import ConfigManager, Settings
    from chaoscypher_core.services.workflows import WorkflowService
    from chaoscypher_core.services.workflows.triggers import TriggerService
    from chaoscypher_cortex.features.settings.logging_service import LoggingService

logger = structlog.get_logger(__name__)


class SettingsService:
    """Service for settings management.

    SRP: Focused on core settings CRUD operations.
    Delegates to specialized services for logging, triggers, and resets.

    Example:
        >>> from chaoscypher_cortex.features.settings.api import get_settings_service
        >>> from chaoscypher_core.database import get_sqlite_adapter
        >>> from chaoscypher_core.app_config import get_settings
        >>>
        >>> # Get service instance via factory
        >>> adapter = get_sqlite_adapter(database_name="my_database")
        >>> try:
        ...     settings = get_settings()
        ...     service = get_settings_service(adapter.session, settings)
        ...
        ...     # Get current settings
        ...     current = service.get_settings()
        ...     print(current.current_database)
        ...     "my_database"
        ...
        ...     # Update settings
        ...     updated = service.update_settings({
        ...         "enable_auto_embedding": True,
        ...         "dark_mode": True
        ...     })
        ...     print(updated.enable_auto_embedding)
        ...     True
        ...
        ...     # Set logging level
        ...     response = service.set_logging_level("DEBUG")
        ...     print(response.new_level)
        ...     "DEBUG"
        ...
        ...     # Reset workflow system to defaults
        ...     result = await service.reset_workflow_system()
        ...     print(result.data["status"])
        ...     "success"
        ... finally:
        ...     adapter.disconnect()

    """

    def __init__(
        self,
        settings_manager: ConfigManager,
        database_name: str,
        trigger_service: TriggerService | None = None,
        workflow_service: WorkflowService | None = None,
        adapter: Any = None,
        *,
        logging_service: LoggingService,
    ):
        """Initialize settings service.

        Args:
            settings_manager: ConfigManager instance
            database_name: Current database name
            trigger_service: Optional TriggerService for auto-embedding sync
            workflow_service: Optional WorkflowService for workflow lookups
            adapter: Optional storage adapter exposing transaction() — required
                to enable auto-embedding trigger sync (see TriggerSyncService).
            logging_service: LoggingService instance (injected by factory).
                Keyword-only required dependency; callers must construct and
                supply it. Enables mocking in tests.

        """
        self.settings_manager = settings_manager
        self.database_name = database_name

        # SRP: Delegate to specialized services
        self.reset_ops = ResetOperations(database_name, settings_manager)
        self.logging_service = logging_service

        # Initialize trigger sync service if dependencies provided
        self.trigger_sync_service: TriggerSyncService | None
        if trigger_service and workflow_service and adapter is not None:
            self.trigger_sync_service = TriggerSyncService(
                trigger_service=trigger_service,
                workflow_service=workflow_service,
                adapter=adapter,
            )
        else:
            self.trigger_sync_service = None

    # ========================================================================
    # Core Settings Operations
    # ========================================================================

    def get_settings(self) -> Settings:
        """Get current application settings."""
        return self.settings_manager.get_settings()

    async def update_settings(self, settings_update: dict[str, Any]) -> Settings:
        """Update application settings (partial update).

        Automatically syncs auto-embedding trigger states when enable_auto_embedding changes.
        Reloads load balancer when Ollama instances change.

        Args:
            settings_update: Dictionary of settings to update

        Returns:
            Updated settings

        """
        # Get current enable_auto_embedding value before update
        old_settings = self.settings_manager.get_settings()
        old_auto_embedding = old_settings.search.enable_auto_embedding

        # Update settings
        updated_settings = self.settings_manager.update_settings(settings_update)

        # Check if enable_auto_embedding changed
        new_auto_embedding = updated_settings.search.enable_auto_embedding

        # SRP: Delegate trigger synchronization to TriggerSyncService.
        # Atomic transaction (see TriggerSyncService) raises on partial failure;
        # surface a warning log so the settings update itself does not fail.
        if old_auto_embedding != new_auto_embedding and self.trigger_sync_service:
            try:
                self.trigger_sync_service.sync_auto_embedding_triggers(new_auto_embedding)
            except Exception:
                logger.warning("auto_embedding_trigger_sync_failed", exc_info=True)

        # Reload LLM services if settings might have changed
        if "llm" in settings_update:
            await self._maybe_reload_load_balancer(updated_settings)
            self._reload_llm_services()
            # Note: Worker notification is handled by the async API endpoint

        return updated_settings

    def get_update_warnings(
        self, old_settings: Settings, new_settings: Settings
    ) -> list[SettingsWarning]:
        """Detect warnings for settings changes that may impact existing data.

        Args:
            old_settings: Settings before the update.
            new_settings: Settings after the update.

        Returns:
            List of warnings about the impact of changes.

        """
        warnings: list[SettingsWarning] = []

        old_dim = old_settings.search.vector_dimensions
        new_dim = new_settings.search.vector_dimensions

        if old_dim != new_dim:
            # Try to get existing vector count
            try:
                from chaoscypher_core.repo_factories import get_search_repository

                search_repo = get_search_repository(database_name=self.database_name)
                vector_count = search_repo.vector.get_vector_count()  # type: ignore[attr-defined]
            except Exception:
                vector_count = 0

            if vector_count > 0:
                warnings.append(
                    SettingsWarning(
                        field="search.vector_dimensions",
                        message=(
                            f"Vector dimensions changed from {old_dim} to {new_dim}. "
                            f"You have {vector_count:,} existing embeddings that were "
                            f"generated at {old_dim} dimensions. These will be cleared "
                            f"and only newly indexed data will appear in vector search. "
                            f"To restore search for existing sources, re-extract or "
                            f"re-import them after this change."
                        ),
                        severity="warning",
                    )
                )
            else:
                warnings.append(
                    SettingsWarning(
                        field="search.vector_dimensions",
                        message=(
                            f"Vector dimensions changed from {old_dim} to {new_dim}. "
                            f"No existing embeddings found, so this change has no impact."
                        ),
                        severity="info",
                    )
                )

        return warnings

    async def _maybe_reload_load_balancer(self, settings: Settings) -> None:
        """Reload load balancer if Ollama is configured with instances.

        This enables hot-reload: users can add/remove instances without
        restarting workers. Called from the async PATCH /settings path, so
        it simply awaits the reload; no defensive sync fallback is needed.

        Args:
            settings: Updated settings object
        """
        try:
            # Only reload if Ollama is the chat provider
            if settings.llm.chat_provider != "ollama":
                return

            instances = settings.llm.ollama_instances or []
            if not instances:
                return

            # Import here to avoid circular imports
            from chaoscypher_core.adapters.llm.load_balancer import get_ollama_load_balancer

            load_balancer = get_ollama_load_balancer()
            await load_balancer.reload_config(settings.llm)

            logger.info(
                "load_balancer_reload_triggered",
                instance_count=len(instances),
                strategy=settings.llm.ollama_load_balancing,
            )
        except Exception as e:
            logger.warning(
                "load_balancer_reload_failed",
                error=str(e),
                error_type=type(e).__name__,
            )

    def _reload_llm_services(self) -> None:
        """Reload LLM provider factory and queue service.

        This enables hot-reload for the Cortex API process.
        Worker notification must be called separately via notify_workers_llm_settings_changed().
        """
        try:
            from chaoscypher_core.llm_queue.queue_factory import reload_llm_queue_service

            reload_llm_queue_service()
            logger.info("llm_services_reloaded")
        except Exception as e:
            logger.warning(
                "llm_services_reload_failed",
                error=str(e),
                error_type=type(e).__name__,
            )

    async def notify_workers_llm_settings_changed(self) -> None:
        """Publish a settings-change notification to Valkey for worker hot-reload."""
        from chaoscypher_cortex.shared.worker_notify import publish_settings_change

        await publish_settings_change("v1:llm_settings_updated")

    async def verify_ollama_url(self, url: str, timeout: int) -> dict[str, Any]:  # noqa: PLR0911
        """Verify that an Ollama instance is running at the given URL.

        Makes requests to Ollama's root endpoint and /api/tags to verify
        connectivity and retrieve available models.

        Args:
            url: The Ollama base URL to verify (e.g., http://localhost:11434)
            timeout: Request timeout in seconds

        Returns:
            Dict with success status, message, version, models, and response time
        """
        import time

        import httpx

        # Normalize URL (remove trailing slash)
        url = url.rstrip("/")

        if not validate_url_safety(url):
            return {
                "success": False,
                "message": "URL is not allowed (blocked scheme or cloud metadata endpoint)",
                "error_type": "blocked_url",
            }

        start_time = time.perf_counter()

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                # First, check root endpoint for basic connectivity
                root_response = await client.get(url)
                root_text = root_response.text.strip()

                # Check if it's Ollama (root returns "Ollama is running")
                if "ollama" not in root_text.lower():
                    return {
                        "success": False,
                        "message": "Not an Ollama instance",
                        "error_type": "invalid_response",
                    }

                # Try to get models list from /api/tags
                models: list[str] = []
                version: str | None = None
                try:
                    tags_response = await client.get(f"{url}/api/tags")
                    if tags_response.status_code == 200:
                        tags_data = tags_response.json()
                        models = [
                            m.get("name", m.get("model", "unknown"))
                            for m in tags_data.get("models", [])
                        ]
                except Exception:
                    # Models endpoint failed, but basic connectivity works
                    pass

                # Try to get version from /api/version
                try:
                    version_response = await client.get(f"{url}/api/version")
                    if version_response.status_code == 200:
                        version_data = version_response.json()
                        version = version_data.get("version")
                except Exception:
                    # Version endpoint not available
                    pass

                elapsed_ms = int((time.perf_counter() - start_time) * 1000)

                logger.info(
                    "ollama_url_verified",
                    url=url,
                    version=version,
                    model_count=len(models),
                    response_time_ms=elapsed_ms,
                )

                return {
                    "success": True,
                    "message": "Ollama is running",
                    "version": version,
                    "models": models,
                    "model_count": len(models),
                    "response_time_ms": elapsed_ms,
                }

        except httpx.ConnectError:
            logger.warning("ollama_url_connection_refused", url=url)
            return {
                "success": False,
                "message": "Connection refused - is Ollama running?",
                "error_type": "connection_refused",
            }
        except httpx.TimeoutException:
            logger.warning("ollama_url_timeout", url=url, timeout=timeout)
            return {
                "success": False,
                "message": f"Connection timed out after {timeout}s",
                "error_type": "timeout",
            }
        except httpx.InvalidURL:
            return {
                "success": False,
                "message": "Invalid URL format",
                "error_type": "invalid_url",
            }
        except Exception as e:
            logger.warning(
                "ollama_url_verification_failed",
                url=url,
                error=str(e),
                error_type=type(e).__name__,
            )
            return {
                "success": False,
                "message": "Verification failed",
                "error_type": "unexpected_error",
            }

    def reset_to_defaults(self) -> Settings:
        """Reset settings to default values."""
        return self.settings_manager.reset_to_defaults()

    # ========================================================================
    # Logging Operations (Delegated to LoggingService)
    # ========================================================================

    def get_logging_level(self) -> LoggingLevelResponse:
        """Get current logging level.

        SRP: Delegates to LoggingService.
        """
        return self.logging_service.get_logging_level()

    def set_logging_level(self, level: str) -> SetLoggingLevelResponse:
        """Set logging level for the application in real-time.

        SRP: Delegates to LoggingService.

        Args:
            level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)

        Returns:
            Response with old and new levels

        """
        return self.logging_service.set_logging_level(level)

    async def notify_workers_logging_level(self, level: str) -> None:
        """Notify workers to sync log level via pub/sub.

        SRP: Delegates to LoggingService.

        Args:
            level: The new log level string.

        """
        await self.logging_service.notify_workers_logging_level(level)

    # ========================================================================
    # Database Reset Operations (Delegated to ResetService)
    # ========================================================================

    def reset_workflow_system(self) -> ResetResponse:
        """Reset workflow system (tools, workflows, triggers) to defaults."""
        return ResetResponse(data=self.reset_ops.reset_workflow_system())

    def reset_source_processing_history(self) -> ResetResponse:
        """Reset source_processing history."""
        return ResetResponse(data=self.reset_ops.reset_source_processing_history())

    def reset_chats(self) -> ResetResponse:
        """Reset all chats."""
        return ResetResponse(data=self.reset_ops.reset_chats())

    def cleanup_orphaned_graph_items(self) -> ResetResponse:
        """Clean up orphaned items from the graph."""
        return ResetResponse(data=self.reset_ops.cleanup_orphaned_graph_items())

    async def reset_queue_stats(self) -> ResetResponse:
        """Reset queue system."""
        return ResetResponse(data=await self.reset_ops.reset_queue_stats())

    async def reset_knowledge_base(self) -> ResetResponse:
        """Reset entire knowledge base (combined reset)."""
        return ResetResponse(data=await self.reset_ops.reset_knowledge_base())

    async def reset_all(self) -> ResetResponse:
        """Nuclear option - delete app.db and reinitialize."""
        return ResetResponse(data=await self.reset_ops.reset_all())

    def seed_templates(self) -> ResetResponse:
        """Re-seed default system templates."""
        return ResetResponse(data=self.reset_ops.seed_templates())

    # ========================================================================
    # VRAM Preset Operations
    # ========================================================================

    def list_presets(self) -> PresetListResponse:
        """List all available VRAM presets.

        Returns:
            PresetListResponse with all presets sorted by VRAM size.
        """
        from chaoscypher_core.app_config.engine_factory import build_engine_settings
        from chaoscypher_core.services.presets import get_preset_registry

        settings = self.settings_manager.get_settings()
        registry = get_preset_registry(build_engine_settings(settings))
        preset_dicts = registry.list_presets()

        presets = [VRAMPresetResponse(**p) for p in preset_dicts]

        return PresetListResponse(presets=presets, count=len(presets))

    def get_preset(self, preset_id: str) -> VRAMPresetResponse | None:
        """Get a specific preset by ID.

        Args:
            preset_id: Preset identifier (e.g., "vram_24gb").

        Returns:
            VRAMPresetResponse or None if not found.
        """
        from chaoscypher_core.app_config.engine_factory import build_engine_settings
        from chaoscypher_core.services.presets import get_preset_registry

        settings = self.settings_manager.get_settings()
        registry = get_preset_registry(build_engine_settings(settings))
        preset = registry.get_preset(preset_id)

        if preset is None:
            return None

        # Manually construct response from Protocol properties
        return VRAMPresetResponse(
            name=preset.name,
            display_name=preset.display_name,
            description=preset.description,
            vram_gb=preset.vram_gb,
            gpu_examples=preset.gpu_examples,
            version=preset.metadata.version,
            author=preset.metadata.author,
            builtin=preset.metadata.builtin,
            ollama_settings=preset.get_ollama_settings(),
            llm_settings=preset.get_llm_settings(),
        )

    def apply_preset(self, preset_id: str) -> ApplyPresetResponse:
        """Apply a VRAM preset to current settings.

        Only updates Ollama-related settings, preserving all other configuration.

        Args:
            preset_id: Preset identifier (e.g., "vram_24gb").

        Returns:
            ApplyPresetResponse with applied settings.

        Raises:
            KeyError: If preset not found.
        """
        from chaoscypher_core.app_config.engine_factory import build_engine_settings
        from chaoscypher_core.services.presets import get_preset_registry

        settings = self.settings_manager.get_settings()
        registry = get_preset_registry(build_engine_settings(settings))
        preset = registry.get_required(preset_id)

        # Get all settings from preset
        updates = preset.get_all_settings()

        # Also remember which preset was applied for UI display
        updates["ollama_quick_preset"] = preset_id

        # Apply to settings.yaml via ConfigManager (nested under "llm")
        self.settings_manager.update_settings({"llm": updates})

        # Reload LLM services to apply new model/settings immediately
        self._reload_llm_services()

        logger.info(
            "preset_applied",
            preset_id=preset_id,
            preset_name=preset.display_name,
            settings_updated=list(updates.keys()),
        )

        return ApplyPresetResponse(
            success=True,
            preset_id=preset_id,
            preset_name=preset.display_name,
            settings_updated=updates,
            message=f"Applied {preset.display_name} preset successfully",
        )
