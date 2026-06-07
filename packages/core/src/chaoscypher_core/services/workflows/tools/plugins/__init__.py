# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Built-in Workflow Tool Plugins.

Contains all built-in tool plugin implementations that ship with ChaosCypher.
Users can add custom plugins by creating *_plugin.py files in this directory.

Built-in Plugins:
    - ai_prompt_plugin.py: AI prompt with chunking (ai.prompt)
    - ai_extract_json_plugin.py: Extract structured JSON (ai.extract_json)
    - ai_generate_embedding_plugin.py: Generate embeddings (ai.generate_embedding)
    - ai_vector_search_plugin.py: Vector/hybrid search (ai.vector_search)
    - data_extract_plugin.py: Extract nested data (data.extract)
    - data_merge_plugin.py: Merge objects (data.merge)
    - http_request_plugin.py: HTTP requests (http.request)
    - logic_conditional_plugin.py: Conditional logic (logic.conditional)
    - logic_loop_plugin.py: Loop/iteration (logic.loop)
    - templates_list_plugin.py: List templates (templates.list)

Example Custom Plugin:
    ```python
    # plugins/custom_plugin.py
    from typing import Any

    class CustomPlugin:
        @property
        def tool_id(self) -> str:
            return "custom.my_tool"

        @property
        def category(self) -> str:
            return "custom"

        @property
        def name(self) -> str:
            return "My Custom Tool"

        @property
        def description(self) -> str:
            return "Does something custom"

        @property
        def input_schema(self) -> dict[str, Any]:
            return {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
            }

        async def execute(self, inputs: dict, context) -> dict:
            return {"result": f"Processed: {inputs['input']}"}
    ```

The plugin will be auto-discovered and available via ToolRegistry.
"""

# Re-export infrastructure for convenient imports
from chaoscypher_core.services.workflows.tools.engine.base import BaseToolPlugin
from chaoscypher_core.services.workflows.tools.engine.context import ToolExecutionContext
from chaoscypher_core.services.workflows.tools.engine.registry import (
    ToolRegistry,
)
from chaoscypher_core.services.workflows.tools.engine.validators import (
    ValidationResult,
    get_optional_fields,
    get_required_fields,
    validate_inputs,
)


__all__ = [
    # Core plugin system
    "BaseToolPlugin",
    "ToolExecutionContext",
    "ToolRegistry",
    # Validation
    "ValidationResult",
    "get_optional_fields",
    "get_required_fields",
    "validate_inputs",
]
