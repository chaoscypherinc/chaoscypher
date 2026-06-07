# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Generate Embedding Plugin - Text/Entity Embedding Generation.

Generates vector embeddings for text or graph entities (nodes/edges). Supports
multiple input modes and auto-updates nodes with generated embeddings.

Extracted from executors/vector_operations.py and converted to plugin architecture.
"""

from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import NotFoundError, OperationError, ValidationError


if TYPE_CHECKING:
    from chaoscypher_core.services.workflows.tools.plugins import ToolExecutionContext

logger = structlog.get_logger(__name__)


class EmbeddingPlugin:
    """Generate Embedding tool plugin.

    Generate embeddings for text or entities. Supports direct text input,
    entity objects, or entity IDs. Automatically updates nodes with embeddings
    when entity_id is provided.
    """

    @property
    def tool_id(self) -> str:
        """Stub implementation - not yet implemented."""
        return "ai.generate_embedding"

    @property
    def category(self) -> str:
        """Stub implementation - not yet implemented."""
        return "ai"

    @property
    def icon(self) -> str:
        """MUI icon name for UI display."""
        return "Hub"

    @property
    def name(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Generate Embedding"

    @property
    def description(self) -> str:
        """Stub implementation - not yet implemented."""
        return "Generate vector embedding for text or graph entity"

    @property
    def input_schema(self) -> dict[str, Any]:
        """Stub implementation - not yet implemented."""
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Direct text to embed"},
                "entity": {
                    "type": "object",
                    "description": "Entity object to embed (node or edge)",
                },
                "entity_id": {"type": "string", "description": "Entity ID to fetch and embed"},
                "entity_type": {
                    "type": "string",
                    "enum": ["node", "edge"],
                    "description": "Type of entity (required with entity or entity_id)",
                },
            },
            "oneOf": [
                {"required": ["text"]},
                {"required": ["entity", "entity_type"]},
                {"required": ["entity_id", "entity_type"]},
            ],
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        """Output schema for Generate Embedding tool."""
        return {
            "type": "object",
            "properties": {
                "embedding": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Vector embedding array",
                },
                "model": {
                    "type": "string",
                    "description": "Model used for embedding generation",
                },
                "text": {
                    "type": "string",
                    "description": "Text that was embedded",
                },
                "success": {
                    "type": "boolean",
                    "description": "Whether the operation succeeded (when updating entity)",
                },
                "message": {
                    "type": "string",
                    "description": "Status message (when updating entity)",
                },
            },
            "required": ["embedding", "model"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: ToolExecutionContext
    ) -> dict[str, Any]:
        """Generate embedding for text or entity.

        Args:
            inputs: Tool inputs (text, entity, or entity_id)
            context: Execution context with services

        Returns:
            Dictionary with embedding and metadata

        Raises:
            OperationError: If engine settings not provided in context
            NotFoundError: If requested entity is not found
            ValidationError: If required input fields are missing or invalid

        """
        # Extract text from various input modes
        text = self._extract_text_from_inputs(inputs, context.graph_manager)

        # Prefer the port injected on the context; fall back to a one-shot
        # factory build from settings for non-Engine callers. The factory
        # import is allowlisted under CC012.
        provider = context.embedding_provider
        if provider is None:
            settings = context.settings
            if not settings:
                raise OperationError(
                    "Engine settings not provided in context - required for embedding",
                    operation="ai.generate_embedding",
                )
            from chaoscypher_core.adapters.embedding import create_embedding_provider

            provider = create_embedding_provider(settings)
        embed_result = await provider.embed(text)

        embedding = embed_result.embedding
        model = embed_result.provider

        # Auto-update node if entity_id provided
        if "entity_id" in inputs and inputs.get("entity_type") == "node":
            return await self._update_node_embedding(
                inputs["entity_id"], embedding, model, text, context.graph_manager
            )

        # Direct tool call - just return embedding
        return {"embedding": embedding, "model": model, "text": text}

    def _extract_text_from_inputs(self, inputs: dict[str, Any], graph_manager: Any) -> str:
        """Extract text to embed from various input formats."""
        from typing import cast

        # Direct text input
        if "text" in inputs:
            return cast("str", inputs["text"])

        # Entity object input
        if "entity" in inputs:
            entity = inputs["entity"]
            entity_type = inputs.get("entity_type", "unknown")
            return self._entity_to_text(entity, entity_type)

        # Entity ID input (fetch from database)
        if "entity_id" in inputs and "entity_type" in inputs:
            entity_id = inputs["entity_id"]
            entity_type = inputs["entity_type"]

            if entity_type == "node":
                node = graph_manager.get_node(entity_id)
                if not node:
                    raise NotFoundError("Node", entity_id)

                # Build text from node properties
                parts = [f"Label: {node.label}"]
                for key, value in node.properties.items():
                    if key not in ["embedding", "id", "created_at", "updated_at"]:
                        parts.append(f"{key}: {value}")
                return " | ".join(parts) if len(parts) > 1 else parts[0]

            from typing import cast

            if entity_type == "edge":
                edge = graph_manager.get_edge(entity_id)
                if not edge:
                    raise NotFoundError("Edge", entity_id)
                return cast("str", edge.label)

            msg = f"Unsupported entity_type for entity_id lookup: {entity_type}"
            raise ValidationError(
                msg,
                field="entity_type",
            )

        raise ValidationError(
            "Either 'text', 'entity', or 'entity_id' must be provided",
            field="text",
        )

    def _entity_to_text(self, entity: Any, entity_type: str) -> str:
        """Convert entity object to text for embedding."""
        if entity_type == "node":
            # For nodes, combine label and properties
            parts = []
            if isinstance(entity, dict):
                if "label" in entity:
                    parts.append(f"Label: {entity['label']}")
                if "title" in entity:
                    parts.append(f"Title: {entity['title']}")
                if "properties" in entity and isinstance(entity["properties"], dict):
                    for key, value in entity["properties"].items():
                        if key not in ["embedding", "id", "created_at", "updated_at"]:
                            parts.append(f"{key}: {value}")
            return " | ".join(parts) if parts else str(entity)

        from typing import cast

        if entity_type == "edge":
            # For edges, use the label
            if isinstance(entity, dict):
                return cast("str", entity.get("label", str(entity)))
            return str(entity)

        return str(entity)

    async def _update_node_embedding(
        self, entity_id: str, embedding: list, model: str, text: str, graph_manager: Any
    ) -> dict[str, Any]:
        """Update node with generated embedding."""
        try:
            from chaoscypher_core.models import NodeUpdate

            # Update the node with the embedding
            node_update = NodeUpdate(embedding=embedding)
            updated_node = graph_manager.update_node(entity_id, node_update, publish_events=False)

            if updated_node:
                return {
                    "success": True,
                    "embedding": embedding,
                    "model": model,
                    "text": text,
                    "message": f"Embedding generated and saved to node {entity_id}",
                }
            return {
                "success": False,
                "error": f"Node {entity_id} not found",
                "embedding": embedding,
                "model": model,
            }
        except Exception:
            return {
                "success": False,
                "error": "Embedding generation failed",
                "embedding": embedding,
                "model": model,
            }


__all__ = ["EmbeddingPlugin"]
