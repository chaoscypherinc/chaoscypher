# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM infrastructure for chaoscypher-engine.

Provides complete LLM integration with:
- Multiple provider support (Ollama, OpenAI, Anthropic, Gemini)
- Priority-based concurrency control (PrioritySemaphore)
- Multi-instance Ollama load balancing (OllamaLoadBalancer)
- Provider factory with caching
- Cost tracking
- Direct API calls (no queue dependencies)
- Structured data extraction with validation and repair

ONE SOURCE OF TRUTH - Used by both Docker backend and CLI applications.

Metrics collection (`LLMMetricsCollector`) lives in
``chaoscypher_core.analytics.llm_metrics`` — import it from there.

Concrete provider classes are not exported from this barrel — use
``get_provider()`` for the registry path, or import the concrete class
directly from its module (e.g. ``chaoscypher_core.adapters.llm.providers.ollama_provider``).
"""

from chaoscypher_core.adapters.llm.error_types import (
    LLMErrorType,
    map_exception_to_error_type,
)
from chaoscypher_core.adapters.llm.factory import ProviderFactory
from chaoscypher_core.adapters.llm.limit import (
    PrioritySemaphore,
    clear_llm_semaphore_waiting_queues,
    get_llm_semaphore,
)
from chaoscypher_core.adapters.llm.load_balancer import (
    OllamaLoadBalancer,
    get_ollama_load_balancer,
    reload_load_balancer_config,
)
from chaoscypher_core.adapters.llm.model_registry import (
    CloudModelRegistry,
    get_model_registry,
)
from chaoscypher_core.adapters.llm.provider import LLMProvider
from chaoscypher_core.adapters.llm.providers import (
    get_provider,
    list_available_providers,
)
from chaoscypher_core.adapters.llm.schema import StructuredExtractor, build_extraction_tools
from chaoscypher_core.exceptions import (
    LLMError,
    ModelCapabilityError,
    ToolCallingNotSupportedError,
)
from chaoscypher_core.services.workflows.tools.system_tools import execute_system_tool


__all__ = [
    # Model registry
    "CloudModelRegistry",
    # Exceptions
    "LLMError",
    # Error types
    "LLMErrorType",
    # Main classes
    "LLMProvider",
    "ModelCapabilityError",
    # Load Balancer
    "OllamaLoadBalancer",
    "PrioritySemaphore",
    "ProviderFactory",
    "StructuredExtractor",
    "ToolCallingNotSupportedError",
    # Factory functions
    "build_extraction_tools",
    "clear_llm_semaphore_waiting_queues",
    "execute_system_tool",
    "get_llm_semaphore",
    "get_model_registry",
    "get_ollama_load_balancer",
    "get_provider",
    "list_available_providers",
    "map_exception_to_error_type",
    "reload_load_balancer_config",
]
