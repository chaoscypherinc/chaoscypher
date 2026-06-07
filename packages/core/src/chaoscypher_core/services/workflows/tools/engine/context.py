# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool Execution Context.

Provides plugins with access to services, repositories, and workflow state during execution.

The ToolExecutionContext is a dependency injection container that gives plugins access
to everything they need without coupling them to specific implementations.

Architecture:
    - Immutable context passed to all plugin execute() methods
    - Contains all services plugins might need
    - Optional services (None if not available)
    - Workflow state for access to step outputs

Example Usage:
    ```python
    # In a plugin
    async def execute(self, inputs: Dict[str, Any], context: ToolExecutionContext) -> Dict[str, Any]:
        # Access LLM service
        if context.llm_service:
            result = await context.llm_service.queue_operation(...)

        # Access graph repository
        nodes = await context.graph_manager.search_nodes(...)

        # Access workflow state
        previous_output = context.workflow_state.get("step1", {})

        return {"result": "..."}
    ```

Note:
    Plugins should check for None before using optional services:
    - llm_service (required for AI tools)
    - discovery_service (required for discovery tools)
    - import_service (required for import tools)
    - operations_service (required for queuing background tasks)

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from chaoscypher_core.ports.embedding import EmbeddingProviderProtocol
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol
    from chaoscypher_core.ports.structured_extraction import StructuredExtractorPort
    from chaoscypher_core.settings import EngineSettings


@dataclass
class ToolExecutionContext:
    """Tool execution context provided to tool plugins.

    Contains all services and state that plugins need to execute.
    Services are optional (can be None) depending on workflow configuration.

    Attributes:
        graph_manager: GraphRepository for graph operations (always present)
        settings: Engine settings for configuration (None if not provided)
        llm_service: Queue-aware LLM service exposing ``.queue_operation``
            / ``.wait_for_result`` (None if not configured). Distinct from
            ``embedding_provider`` / ``structured_extractor`` which are
            direct-call ports.
        thinking_mode: LLM thinking mode ('quick', 'extended', etc.)
        discovery_service: Discovery service for analysis (None if not available)
        source processing_service: Source processing service for file operations (None if not available)
        operations_service: Operations service for background tasks (None if not available)
        search_repository: Search repository for vector/fulltext search (None if not available)
        embedding_provider: EmbeddingProviderProtocol for plugins that
            embed text directly (None if not configured).
        structured_extractor: StructuredExtractorPort for plugins that
            extract JSON-schema-typed structured data (None if not configured).
        workflow_state: Current workflow state (step outputs)
        database_name: Current database name

    Example:
        context = ToolExecutionContext(
            graph_manager=graph_repo,
            llm_service=llm_svc,
            thinking_mode="extended",
            workflow_state={"step1": {"entities": [...]}}
        )

        # In plugin
        if context.llm_service:
            result = await context.llm_service.chat(...)

    """

    # Required services
    graph_manager: GraphRepositoryProtocol  # GraphRepository implementation

    # Configuration
    settings: EngineSettings | None = None

    # Optional services (None if not available)
    llm_service: Any | None = None
    thinking_mode: str | None = None
    discovery_service: Any | None = None
    import_service: Any | None = None
    operations_service: Any | None = None
    search_repository: Any | None = None
    embedding_provider: EmbeddingProviderProtocol | None = None
    structured_extractor: StructuredExtractorPort | None = None

    # Workflow state
    workflow_state: dict[str, Any] | None = None
    database_name: str | None = None

    def __post_init__(self) -> None:
        """Initialize workflow_state if not provided."""
        if self.workflow_state is None:
            self.workflow_state = {}


__all__ = ["ToolExecutionContext"]
