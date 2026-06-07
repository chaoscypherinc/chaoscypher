---
id: tool-plugins
title: Tool Plugins
description: Workflow step implementations for Chaos Cypher's automation engine — built-in AI tools, graph tools, and notification tools, plus custom Python plugins with no registration needed.
---

# Tool Plugins

Tool plugins are workflow step implementations for the automation system. Each tool defines its inputs, outputs, and execution logic. Custom tools are Python files -- no registration needed.

## Built-In Tools

### AI Tools

| Tool ID | Name | Description |
|---------|------|-------------|
| `ai.prompt` | AI Prompt | Execute LLM prompts with chunking support for long text |
| `ai.extract_json` | AI Extract JSON | Structured data extraction using LLM with schema validation |
| `ai.generate_embedding` | AI Generate Embedding | Generate vector embeddings for text or entities |
| `ai.vector_search` | AI Vector Search | Semantic similarity search across indexed content |

### Data Tools

| Tool ID | Name | Description |
|---------|------|-------------|
| `data.extract` | Extract Data | Extract nested data using dot notation paths |
| `data.merge` | Merge Data | Merge multiple objects with shallow or deep strategy |

### Logic Tools

| Tool ID | Name | Description |
|---------|------|-------------|
| `logic.conditional` | Conditional | Branch workflow based on boolean conditions |
| `logic.loop` | Loop | Iterate over collections with optional limit |

### HTTP Tools

| Tool ID | Name | Description |
|---------|------|-------------|
| `http.request` | HTTP Request | Make HTTP requests with auth, headers, and SSRF protection |

### Template Tools

| Tool ID | Name | Description |
|---------|------|-------------|
| `templates.list` | Templates List | List available graph templates |

---

## Tool Configuration Reference

Each tool is used as a workflow step. The `configuration` object maps tool-specific parameters.

### ai.prompt

Execute LLM prompts with optional chunking for long documents.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `prompt` | string | Yes | | Main prompt text |
| `system_prompt` | string | No | | System prompt for the LLM |
| `context` | string | No | | Additional context text |
| `output_format` | string | No | `"text"` | `"text"` or `"json"` |
| `temperature` | float | No | From settings | LLM temperature (0.0--2.0) |
| `max_tokens` | int | No | From settings | Max response tokens |
| `chunk_strategy` | string | No | `"none"` | `"none"`, `"quick"`, or `"full"` |
| `chunk_overlap` | int | No | 500 | Character overlap between chunks |
| `thinking_mode` | string | No | | Override LLM thinking mode |

**Output:** `result` (string), `model` (string), `tokens_used` (int)

### ai.extract_json

Structured data extraction from text using LLM with schema validation.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `text` | string | Yes | | Text to extract from |
| `json_schema` | object | Yes | | JSON Schema defining expected structure |
| `system_prompt` | string | No | Generic extraction prompt | System prompt |
| `user_instructions` | string | No | | Additional instructions |
| `temperature` | float | No | 0.1 | Low temperature for consistency |
| `max_retries` | int | No | From settings | Retry count on validation failure |
| `max_tokens` | int | No | 16384 | Max response tokens |
| `enable_quality_check` | bool | No | `true` | Validate output against schema |

**Output:** `extracted_data` (object matching schema)

### ai.generate_embedding

Generate vector embeddings for text or graph entities.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `text` | string | One of these | Direct text to embed |
| `entity_id` + `entity_type` | string + string | One of these | Entity ID with type (`"node"` or `"edge"`) |

**Output:** `embedding` (array), `model` (string), `text` (string)

### ai.vector_search

Semantic similarity search across indexed content.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | | Search query text |
| `template_id` | string | No | | Filter results by template |
| `limit` | int | No | 10 | Max results (1--100) |
| `threshold` | float | No | 0.7 | Min similarity score (0.0--1.0) |

**Output:** `nodes` (array of matching nodes), `similarities` (array of scores)

### data.extract

Extract nested data from objects using dot notation paths.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `source` | object | Yes | | Source object to extract from |
| `path` | string | Yes | | Dot notation path (e.g., `"user.name"`, `"items.0.title"`) |
| `default` | any | No | | Default value if path not found |

**Output:** `value` (any), `found` (boolean)

### data.merge

Merge multiple objects/dictionaries.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `objects` | array | Yes | | Objects to merge (later overrides earlier) |
| `strategy` | string | No | `"shallow"` | `"shallow"` (top-level) or `"deep"` (recursive) |

**Output:** `result` (merged object)

### logic.conditional

Evaluate conditions and branch workflow execution.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `condition` | boolean | Yes | Condition to evaluate |
| `if_true` | any | Yes | Value returned when condition is true |
| `if_false` | any | No | Value returned when condition is false |

**Output:** `result` (any), `branch_taken` (`"true"` or `"false"`), `condition_value` (boolean)

### logic.loop

Iterate over collections with optional iteration limit.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `collection` | array | Yes | | Collection to iterate over |
| `iterator_name` | string | No | `"item"` | Variable name for current item |
| `max_iterations` | int | No | Collection length | Max iterations |

**Output:** `results` (array of items), `iterations` (int)

### http.request

Make HTTP requests with security validation and timeout.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `url` | string | Yes | | Request URL (SSRF-validated) |
| `method` | string | No | `"GET"` | HTTP method (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS) |
| `headers` | object | No | | Request headers |
| `body` | any | No | | Request body (object sent as JSON, string as raw) |
| `auth` | object | No | | `{ "type": "bearer"/"basic", "credentials": "..." }` |
| `timeout` | number | No | 60 | Request timeout in seconds (1--300) |

**Output:** `status` (int), `headers` (object), `body` (any), `success` (boolean)

### templates.list

List all graph templates with optional system template filtering.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `include_system` | bool | No | `true` | Include system templates |

**Output:** `templates` (array of template objects)

---

## Custom Tools

Custom tools are Python files placed in `data/plugins/tools/`. Files must end with `_plugin.py` for auto-discovery. User plugins override built-in plugins with the same `tool_id`.

```
data/
  plugins/
    tools/
      my_tool_plugin.py
```

**Minimal plugin:**

```python
from typing import Any

class MyToolPlugin:
    @property
    def tool_id(self) -> str:
        return "custom.my_tool"

    @property
    def category(self) -> str:
        return "custom"

    @property
    def icon(self) -> str:
        return "Extension"  # MUI icon name

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
            "properties": {
                "input": {"type": "string", "description": "Input text"}
            },
            "required": ["input"]
        }

    @property
    def output_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "result": {"type": "string"}
            },
            "required": ["result"]
        }

    async def execute(self, inputs: dict[str, Any], context) -> dict[str, Any]:
        value = inputs["input"]
        return {"result": f"Processed: {value}"}
```

The `context` parameter provides access to graph operations (`context.graph_manager`), LLM services (`context.llm_service`), search (`context.search_repository`), settings (`context.settings`), and outputs from previous workflow steps (`context.workflow_state`).

## See also

- [Architecture: Plugin System](../architecture/plugins.md) — how the registry auto-discovers tool plugins and other plugin types
- [Developer guide: Building Tool Plugins](../developer-guide/building-tools.md) — full `BaseToolPlugin` interface, execution context reference, and testing patterns

For the full plugin interface and advanced patterns, see the [Building Tool Plugins](../developer-guide/building-tools.md) guide.
