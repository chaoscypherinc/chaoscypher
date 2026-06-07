# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Settings Feature.

Application configuration, logging control, database management, and TLS.

This feature provides centralized application settings management including LLM
provider configuration, embedding models, search parameters, and system defaults.
Follows SRP with specialized services for settings, logging, trigger sync, and TLS.
Supports runtime configuration updates, database reset operations, logging
level control, and TLS certificate management. Settings persist to
/data/settings.yaml for user customization.

Components:
- SettingsService: Core settings CRUD and validation
- LoggingService: Runtime logging level management and configuration
- TriggerSyncService: Auto-embedding trigger synchronization
- TLSService: TLS certificate management (self-signed, custom, disable)
- ResetResponse: Database reset operation response DTO
- router: FastAPI endpoints for /api/v1/settings

Architecture:
VSA pattern with SRP-compliant service decomposition. SettingsService handles
core config, LoggingService manages logging levels, TriggerSyncService ensures
auto-embedding triggers stay synchronized, TLSService manages certificates.
Factory functions provide dependency injection for each service.

Example:
    from chaoscypher_cortex.features.settings import SettingsService, LoggingService

    # Update LLM provider and logging
    settings_svc = SettingsService(repository)
    settings_svc.update_llm_provider("ollama", "llama3.2")
    logging_svc = LoggingService()
    logging_svc.set_level("DEBUG")

"""

from chaoscypher_cortex.features.settings.api import router
from chaoscypher_cortex.features.settings.logging_service import LoggingService
from chaoscypher_cortex.features.settings.models import ResetResponse, SettingsUpdateResponse
from chaoscypher_cortex.features.settings.ollama_models_api import (
    router as ollama_models_router,
)
from chaoscypher_cortex.features.settings.service import SettingsService
from chaoscypher_cortex.features.settings.tls_service import TLSService
from chaoscypher_cortex.features.settings.trigger_sync_service import TriggerSyncService


__all__ = [
    "LoggingService",
    "ResetResponse",
    "SettingsService",
    "SettingsUpdateResponse",
    "TLSService",
    "TriggerSyncService",
    "ollama_models_router",
    "router",
]
