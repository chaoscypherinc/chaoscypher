---
id: building-tools
title: Building Tool Plugins
description: Extend Chaos Cypher's workflow engine with custom tool plugins — self-contained units with validated inputs, service access via execution context, and structured outputs.
---

# Building Tool Plugins

Tool plugins extend Chaos Cypher's workflow engine with new capabilities. Each tool is a self-contained unit that receives validated inputs, has access to platform services through an execution context, and returns structured results. Tools are used as steps in automated workflows.

## The Tool Plugin Interface

Every tool plugin must satisfy the `BaseToolPlugin` protocol defined in `packages/core/src/chaoscypher_core/services/workflows/tools/engine/base.py`. The protocol uses structural typing -- no inheritance is required, just implement the right properties and methods.

### Required Interface

```python
from typing import Any

class MyPlugin:
    """A custom tool plugin."""

    @property
    def tool_id(self) -> str:
        """Unique identifier in 'category.name' format."""
        ...

    @property
    def category(self) -> str:
        """Tool category for organization."""
        ...

    @property
    def name(self) -> str:
        """Human-readable display name."""
        ...

    @property
    def description(self) -> str:
        """Brief description of what the tool does."""
        ...

    @property
    def input_schema(self) -> dict[str, Any]:
        """JSON Schema defining accepted inputs."""
        ...

    @property
    def output_schema(self) -> dict[str, Any]:
        """JSON Schema describing the output structure (optional)."""
        ...

    async def execute(
        self, inputs: dict[str, Any], context: "ToolExecutionContext"
    ) -> dict[str, Any]:
        """Execute the tool with validated inputs."""
        ...
```

| Member | Type | Description |
|--------|------|-------------|
| `tool_id` | `property` | Unique identifier using dot notation: `"category.tool_name"` (e.g., `"text.summarize"`, `"data.transform"`). |
| `category` | `property` | Category string for grouping tools in the UI. Standard categories: `"ai"`, `"data"`, `"logic"`, `"http"`, `"graph"`, `"template"`. You can define custom categories. |
| `name` | `property` | Human-readable name shown in the workflow builder (e.g., `"Text Summarizer"`). |
| `description` | `property` | One-sentence description of the tool's purpose. |
| `input_schema` | `property` | JSON Schema (Draft 7) defining the tool's input parameters, types, and validation rules. |
| `output_schema` | `property` (optional) | JSON Schema (Draft 7) describing the structure of the returned dictionary. The registry's duck-typing check does not require it, but defining it helps workflow validation and UI documentation. |
| `execute(inputs, context)` | `async method` | Core logic. Receives pre-validated inputs and a `ToolExecutionContext` with access to platform services. Must return a dictionary. |

### The Execution Context

The `ToolExecutionContext` (defined in `packages/core/src/chaoscypher_core/services/workflows/tools/engine/context.py`) is a dataclass that provides access to platform services during execution:

```python
@dataclass
class ToolExecutionContext:
    graph_manager: Any              # GraphRepository -- always present
    settings: Any | None            # Engine settings
    llm_service: Any | None         # LLM service for AI operations
    thinking_mode: str | None       # LLM thinking mode
    discovery_service: Any | None   # Graph analysis service
    import_service: Any | None      # Source processing service
    operations_service: Any | None  # Background task queue
    search_repository: Any | None   # Vector/fulltext search
    embedding_provider: EmbeddingProviderProtocol | None   # Direct text embedding
    structured_extractor: StructuredExtractorPort | None   # JSON-schema-typed extraction
    workflow_state: dict[str, Any]  # Outputs from previous workflow steps
    database_name: str | None       # Current database name
```

:::warning[Check for None before using optional services]

Services like `llm_service`, `search_repository`, `operations_service`, `discovery_service`, `embedding_provider`, and `structured_extractor` may be `None` depending on the workflow configuration. Always check before use:
```python
if not context.llm_service:
    raise RuntimeError("This tool requires the LLM service")
```

:::

Two of the optional services are protocol-typed injection points worth knowing about:

- **`embedding_provider`** (`EmbeddingProviderProtocol`) lets a tool embed text directly -- `await context.embedding_provider.embed("some text")` -- without routing through the LLM service queue. `None` when no embedding provider is configured.
- **`structured_extractor`** (`StructuredExtractorPort`) performs JSON-schema-typed structured extraction: `await context.structured_extractor.extract_structured(text, json_schema)` returns data validated against the schema you pass. `None` when not configured.

## Step-by-Step Example: Building a Text Summarizer

This example builds a tool that summarizes text input using the platform's LLM service.

### 1. Create the plugin file

Create a file named `text_summarize_plugin.py`. The filename must end with `_plugin.py` for auto-discovery.

```python
"""Text Summarize Plugin - Summarize text using AI."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chaoscypher_core import ToolExecutionContext


class SummarizePlugin:
    """Text summarization tool plugin."""

    @property
    def tool_id(self) -> str:
        return "text.summarize"

    @property
    def category(self) -> str:
        return "text"

    @property
    def name(self) -> str:
        return "Text Summarizer"

    @property
    def description(self) -> str:
        return "Summarize text into a concise overview using AI"

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to summarize"},
                "max_sentences": {"type": "integer", "default": 3},
            },
            "required": ["text"],
        }

    async def execute(
        self, inputs: dict[str, Any], context: "ToolExecutionContext"
    ) -> dict[str, Any]:
        if not context.llm_service:
            raise RuntimeError("Text summarization requires the LLM service")

        text = inputs["text"]
        n = inputs.get("max_sentences", 3)

        task_id = await context.llm_service.queue_operation(
            task_type="chat",
            operation_name="chat_completion",
            messages=[
                {"role": "system", "content": "You are a precise text summarizer."},
                {"role": "user", "content": f"Summarize in {n} sentences or fewer:\n\n{text}"},
            ],
            temperature=0.3,
        )

        result = await context.llm_service.wait_for_result(task_id, timeout=120)
        return {"summary": result.get("content", ""), "model": result.get("model", "")}
```

:::tip[Optional: `output_schema`]

You can also define an `output_schema` property (same format as `input_schema`) to describe the return value. This is optional but useful for workflow validation and UI documentation.

:::

### 2. Place the file

You have two options:

| Location | Scope | Survives updates |
|----------|-------|------------------|
| `data/plugins/tools/text_summarize_plugin.py` | User plugin directory | Yes |
| `packages/core/src/chaoscypher_core/services/workflows/tools/plugins/text_summarize_plugin.py` | Built-in | No (overwritten on upgrade) |

For custom tools, use the **user plugin directory**: `data/plugins/tools/`.

### 3. Restart the application

The `ToolRegistry` discovers plugins at startup. Restart the Cortex and Neuron services to pick up your new tool.

```bash
make docker-dev   # Restart services
```

Your tool will appear in the logs:

```
tool_registered  tool_id=text.summarize  category=text  name=Text Summarizer  path_type=user
```

## Plugin Discovery and Registration

The `ToolRegistry` (defined in `packages/core/src/chaoscypher_core/services/workflows/tools/engine/registry.py`) discovers tools through this process:

1. **Scan built-in directory** -- `packages/core/src/chaoscypher_core/services/workflows/tools/plugins/` for files matching `*_plugin.py`.
2. **Scan user plugin directory** -- `data/plugins/tools/` for files matching `*_plugin.py`.
3. **Import each file** -- Built-in plugins use standard Python imports; user plugins use `importlib.util.spec_from_file_location`.
4. **Find plugin class** -- For each class defined in the module (not imported classes), check for the required attributes: `tool_id`, `category`, `name`, `description`, `input_schema`, and `execute`.
5. **Instantiate** -- Create an instance with no arguments (tools use a parameterless constructor).
6. **Register by `tool_id`** -- The tool is registered under its `tool_id` for O(1) lookup.

:::warning[User plugins override built-in plugins]

If a user plugin has the same `tool_id` as a built-in plugin, the user plugin takes precedence. This lets you replace or customize any built-in tool.

Overriding a built-in is the only permitted collision: if **two user plugins** claim the same `tool_id`, registration raises `DuplicatePluginError`. Discovery order is non-deterministic across operating systems, so silently letting one win would make behavior platform-dependent -- rename one of the plugins instead.

:::

### File Naming Rules

- The file **must** end with `_plugin.py` (e.g., `text_summarize_plugin.py`).
- The class name can be anything (e.g., `SummarizePlugin`, `MyCustomTool`).
- Only the **first** qualifying class in the file is registered.

### `tool_id` Conventions

Tool IDs follow a `category.name` dot-notation pattern:

| Category | Examples | Description |
|----------|----------|-------------|
| `ai` | `ai.prompt`, `ai.extract_json` | LLM and AI operations |
| `data` | `data.extract`, `data.merge` | Data transformation |
| `logic` | `logic.conditional`, `logic.loop` | Control flow |
| `http` | `http.request` | HTTP requests |
| `text` | `text.summarize` | Text processing |

## Accessing Workflow State

The `context.workflow_state` dictionary holds outputs from previous workflow steps. Use it to chain tool results:

```python
async def execute(self, inputs: dict[str, Any], context: "ToolExecutionContext") -> dict[str, Any]:
    # Access output from a previous step named "fetch_data"
    previous = context.workflow_state.get("fetch_data", {})
    data = previous.get("result", [])

    # Process the data from the previous step
    ...
```

## Testing Your Tool

### Unit Test Template

```python
"""Tests for SummarizePlugin."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from text_summarize_plugin import SummarizePlugin


class TestSummarizePlugin:
    def test_metadata(self):
        plugin = SummarizePlugin()
        assert plugin.tool_id == "text.summarize"
        assert plugin.category == "text"
        assert "text" in plugin.input_schema["required"]

    @pytest.mark.asyncio
    async def test_execute_calls_llm(self):
        plugin = SummarizePlugin()

        llm_service = AsyncMock()
        llm_service.queue_operation.return_value = "task-123"
        llm_service.wait_for_result.return_value = {
            "content": "This is a summary.", "model": "test-model",
        }

        context = MagicMock()
        context.llm_service = llm_service

        result = await plugin.execute({"text": "A long document..."}, context)
        assert result["summary"] == "This is a summary."
        llm_service.queue_operation.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_without_llm(self):
        plugin = SummarizePlugin()
        context = MagicMock()
        context.llm_service = None

        with pytest.raises(RuntimeError, match="LLM service"):
            await plugin.execute({"text": "test"}, context)
```

## Best Practices

- **Use dot-notation for `tool_id`.** Follow the `category.name` pattern (e.g., `"text.summarize"`, `"data.validate"`). This keeps tools organized and avoids ID collisions.

- **Define thorough schemas.** The `input_schema` and `output_schema` serve as both validation rules and documentation. Include descriptions, types, enums, min/max constraints, and defaults.

- **Check for required services.** Always verify that optional context services (like `llm_service`) are not `None` before using them. Raise a clear `RuntimeError` with a descriptive message.

- **Keep `execute` focused.** Each tool should do one thing well. If your logic is growing complex, consider splitting it into multiple tools that can be chained in a workflow.

- **Use structlog for logging.** Follow the project convention: `logger.info("event_name", key=value)`. Log the start, completion, and any notable metrics of your operation.

- **Return consistent output.** Always return a dictionary matching your `output_schema`. Include metadata fields (like `model` or processing time) that help with debugging and monitoring.

- **Handle errors gracefully.** Catch expected exceptions and return meaningful error information rather than letting raw exceptions propagate. For unexpected failures, let them raise so the workflow engine can handle retries.

- **Use `TYPE_CHECKING` for context imports.** Import `ToolExecutionContext` under `if TYPE_CHECKING:` to avoid circular import issues:

    ```python
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from chaoscypher_core import ToolExecutionContext
    ```

## See also

- [Architecture: Plugin System](../architecture/plugins.md) — registry pattern, plugin types, and auto-discovery mechanism overview
- [User guide: Tool Plugins](../user-guide/tool-plugins.md) — built-in tool reference and configuration options
