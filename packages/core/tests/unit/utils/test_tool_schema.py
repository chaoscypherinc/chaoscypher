# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool schema enums and objects come from the types that define them.

0.7.0 checks MCP arguments against the advertised ``inputSchema`` before a tool runs, and
``create_template``'s hand-copied ``property_type`` enum (six values, one of them ``number``,
which ``PropertyType`` never had) refused ``datetime`` and seven more members the model accepts.

The sweep below walks every enum in the MCP tool list, the engine's ``TOOL_SCHEMAS`` and the
built-in workflow plugins. Each one is either derived (its values must equal its source's) or
local to the tool that reads it (with the reason). A new enum fails here until it is one of the
two, so a copy of a model's values cannot slip in unnoticed again.
"""

import importlib
import inspect
import json
import pkgutil
import re
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from chaoscypher_core.benchmark.retrieval import RETRIEVAL_MODES
from chaoscypher_core.mcp.benchmark.suites import SUITES, all_stages
from chaoscypher_core.models import (
    AnalysisDepth,
    EdgeDirection,
    ElementKind,
    PropertyDefinition,
    PropertyType,
    TagMatch,
)
from chaoscypher_core.ports.types import FilteringMode
from chaoscypher_core.utils.tool_schema import schema_enum, schema_object
from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


# ------------------------------------------------------------------ #
#  schema_enum itself
# ------------------------------------------------------------------ #


class _Colour(StrEnum):
    RED = "red"
    GREEN = "green"


def test_a_str_enum_gives_its_values_in_definition_order() -> None:
    assert schema_enum(_Colour) == ["red", "green"]
    assert all(type(v) is str for v in schema_enum(_Colour))  # plain JSON strings


def test_a_literal_alias_gives_its_arguments() -> None:
    assert schema_enum(Literal["a", "b"]) == ["a", "b"]


@pytest.mark.parametrize("bad", [str, ["a"], "a", None])
def test_anything_else_is_refused(bad: Any) -> None:
    with pytest.raises(TypeError):
        schema_enum(bad)


# ------------------------------------------------------------------ #
#  schema_object itself
# ------------------------------------------------------------------ #


class _Inner(BaseModel):
    """A nested object."""

    depth: int


class _Sample(BaseModel):
    """A docstring that is not the argument's description."""

    colour: _Colour = Field(..., description="The colour")
    label: str = Field(..., description="Shown to people")
    note: str | None = Field(default=None, description="Optional note")
    tags: list[str] | None = None
    anything: Any | None = Field(default=None, description="Any JSON value")
    inner: _Inner | None = None
    count: int = Field(default=0, description="How many")

    model_config = ConfigDict(extra="forbid")


def test_schema_object_inlines_refs_and_drops_titles() -> None:
    schema = schema_object(_Sample)
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text
    assert '"title"' not in text
    # the property's description wins over the enum class's docstring
    assert schema["properties"]["colour"] == {
        "type": "string",
        "enum": ["red", "green"],
        "description": "The colour",
    }
    assert schema["properties"]["inner"]["properties"] == {"depth": {"type": "integer"}}


def test_schema_object_keeps_what_the_model_states() -> None:
    schema = schema_object(_Sample)
    assert schema["type"] == "object"
    assert schema["required"] == ["colour", "label"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["count"] == {
        "type": "integer",
        "default": 0,
        "description": "How many",
    }
    assert "description" not in schema  # the class docstring is not the tool's text


def test_an_optional_field_is_its_type_without_null() -> None:
    props = schema_object(_Sample)["properties"]
    assert props["note"] == {"type": "string", "description": "Optional note"}
    assert props["tags"] == {"type": "array", "items": {"type": "string"}}
    assert props["anything"] == {"description": "Any JSON value"}
    assert "anyOf" not in json.dumps(props)


def test_the_template_property_item_is_the_property_definition_model() -> None:
    from chaoscypher_core.services.workflows.tools.engine.schema_registry import (
        TEMPLATE_PROPERTY_ITEM_SCHEMA,
    )

    assert schema_object(PropertyDefinition) == TEMPLATE_PROPERTY_ITEM_SCHEMA
    assert TEMPLATE_PROPERTY_ITEM_SCHEMA["required"] == ["name", "display_name", "property_type"]
    assert set(TEMPLATE_PROPERTY_ITEM_SCHEMA["properties"]) == set(PropertyDefinition.model_fields)


def test_every_llm_provider_reads_the_create_template_schema() -> None:
    """The engine hands ``TOOL_SCHEMAS`` to LangChain's ``bind_tools`` for each provider."""
    from langchain_anthropic.chat_models import convert_to_anthropic_tool
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from langchain_google_genai._function_utils import convert_to_genai_function_declarations

    from chaoscypher_core.services.workflows.tools.engine.schema_registry import TOOL_SCHEMAS

    tool = TOOL_SCHEMAS["create_template"]
    openai = convert_to_openai_tool(tool)["function"]["parameters"]
    assert openai["properties"]["properties"]["items"]["required"] == [
        "name",
        "display_name",
        "property_type",
    ]
    anthropic = convert_to_anthropic_tool(tool)["input_schema"]
    assert "enum_values" in anthropic["properties"]["properties"]["items"]["properties"]
    (gemini,) = convert_to_genai_function_declarations([tool])[0].function_declarations or []
    item: Any = gemini.parameters.properties["properties"].items  # type: ignore[union-attr, index]
    assert item.required == ["name", "display_name", "property_type"]
    assert item.properties["property_type"].enum == schema_enum(PropertyType)


# ------------------------------------------------------------------ #
#  The sweep
# ------------------------------------------------------------------ #


def _walk(node: Any, path: str = "") -> Iterator[tuple[str, list[Any]]]:
    if isinstance(node, dict):
        if "enum" in node:
            yield path, node["enum"]
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]")


def _plugins() -> Iterator[Any]:
    from chaoscypher_core.services.workflows.tools import plugins

    for info in pkgutil.iter_modules(plugins.__path__):
        module = importlib.import_module(f"{plugins.__name__}.{info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and hasattr(cls, "input_schema"):
                yield cls()


def _every_schema_enum() -> dict[tuple[str, str, str], list[Any]]:
    from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS
    from chaoscypher_core.services.workflows.tools.engine.schema_registry import TOOL_SCHEMAS

    found: dict[tuple[str, str, str], list[Any]] = {}
    for tool in TOOL_DEFINITIONS:
        for path, values in _walk(tool.input_schema):
            found["mcp", tool.name, path] = values
    for name, schema in TOOL_SCHEMAS.items():
        for path, values in _walk(schema["function"]["parameters"]):
            found["engine", name, path] = values
    for plugin in _plugins():
        for attr in ("input_schema", "output_schema"):
            for path, values in _walk(getattr(plugin, attr, None) or {}):
                found["plugin", plugin.tool_id, f"{attr}.{path}"] = values
    return found


_PROPERTY_TYPE = "properties.properties.items.properties.property_type"

DERIVED: dict[tuple[str, str, str], list[str]] = {
    ("mcp", "create_template", _PROPERTY_TYPE): schema_enum(PropertyType),
    ("engine", "create_template", _PROPERTY_TYPE): schema_enum(PropertyType),
    **{
        (surface, tool, "properties.template_type"): schema_enum(ElementKind)
        for surface in ("mcp", "engine")
        for tool in ("list_templates", "search_templates", "create_template")
    },
    ("plugin", "ai.generate_embedding", "input_schema.properties.entity_type"): schema_enum(
        ElementKind
    ),
    ("mcp", "get_node_edges", "properties.direction"): schema_enum(EdgeDirection),
    ("engine", "get_node_edges", "properties.direction"): schema_enum(EdgeDirection),
    ("mcp", "graphrag_search", "properties.match"): schema_enum(TagMatch),
    ("mcp", "search_chunks", "properties.match"): schema_enum(TagMatch),
    ("mcp", "list_documents", "properties.match"): schema_enum(TagMatch),
    ("mcp", "add_document", "properties.extraction_depth"): schema_enum(AnalysisDepth),
    ("mcp", "confirm_extraction", "properties.analysis_depth"): schema_enum(AnalysisDepth),
    ("mcp", "confirm_extraction", "properties.filtering_mode"): schema_enum(FilteringMode),
    ("mcp", "start_benchmark", "properties.suite"): list(SUITES),
    ("mcp", "start_benchmark", "properties.retrieval"): list(RETRIEVAL_MODES),
    ("mcp", "submit_benchmark_output", "properties.stage"): all_stages(),
}
"""Enums that mirror a model type or a shared constant, with the values they must list."""

LOCAL: dict[tuple[str, str, str], str] = {
    ("engine", "research_topic", "properties.depth"): (
        "no model defines a research depth (the callback is a stub); not AnalysisDepth"
    ),
    ("plugin", "ai.prompt", "input_schema.properties.output_format"): (
        "read only by AIPromptPlugin, in the same module"
    ),
    ("plugin", "ai.prompt", "input_schema.properties.chunk_strategy"): (
        "read only by AIPromptPlugin ('none' skips chunking)"
    ),
    ("plugin", "data.merge", "input_schema.properties.strategy"): (
        "read only by the merge plugin; unrelated to compose.MergeStrategy"
    ),
    ("plugin", "http.request", "input_schema.properties.method"): "HTTP methods",
    ("plugin", "http.request", "input_schema.properties.auth.properties.type"): (
        "the two auth schemes the plugin builds headers for"
    ),
    ("plugin", "logic.conditional", "output_schema.properties.branch_taken"): (
        "the plugin's own output, written in the same module"
    ),
}
"""Enums owned by the one tool that reads them; no model type carries their values."""


def test_every_schema_enum_is_derived_or_declared_local() -> None:
    found = set(_every_schema_enum())
    assert not DERIVED.keys() & LOCAL.keys()
    unclassified = found - DERIVED.keys() - LOCAL.keys()
    assert not unclassified, (
        "a tool schema enum that is neither derived nor declared local; build it with "
        f"schema_enum(<its model type>) and add it to DERIVED: {sorted(unclassified)}"
    )
    assert not (DERIVED.keys() | LOCAL.keys()) - found, "a listed enum is gone from its schema"


@pytest.mark.parametrize("key", sorted(DERIVED), ids=lambda k: ":".join(k))
def test_a_derived_enum_lists_exactly_its_sources_values(key: tuple[str, str, str]) -> None:
    assert _every_schema_enum()[key] == DERIVED[key]
    json.dumps(_every_schema_enum()[key])  # plain JSON on the wire


# ------------------------------------------------------------------ #
#  Tool schemas and the handlers they call
# ------------------------------------------------------------------ #


def _executor_handlers() -> dict[str, Any]:
    from unittest.mock import MagicMock

    from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService

    executor = ToolExecutorService(MagicMock(), MagicMock(), MagicMock())
    return dict(executor._tool_handlers)


def _tool_parameter_schemas() -> Iterator[tuple[str, str, dict[str, Any]]]:
    from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS
    from chaoscypher_core.services.workflows.tools.engine.schema_registry import TOOL_SCHEMAS

    for name, schema in TOOL_SCHEMAS.items():
        yield "engine", name, schema["function"]["parameters"]
    for tool in TOOL_DEFINITIONS:
        yield "mcp", tool.name, tool.input_schema


INJECTED = {"source_ids"}
"""Handler parameters the executor fills from its scope, never a client."""

NOT_ADVERTISED: dict[tuple[str, str], set[str]] = {
    ("engine", "graphrag_search"): {"tags", "match"},
    ("engine", "search_chunks"): {"tags", "match"},
}
"""Handler parameters a schema leaves out on purpose: the chat model is not offered tag scope."""


@pytest.mark.parametrize(
    ("surface", "name", "schema"),
    [s for s in _tool_parameter_schemas() if s[1] in _executor_handlers()],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_a_tool_schema_states_its_handlers_parameters(
    surface: str, name: str, schema: dict[str, Any]
) -> None:
    """The executor calls ``handler(**arguments)``: a schema field the handler lacks is a
    ``TypeError`` at call time, and a required parameter the schema leaves optional or out
    passes the schema check and fails there too.
    """
    variadic = (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
    params = {
        n: p
        for n, p in inspect.signature(_executor_handlers()[name]).parameters.items()
        if p.kind not in variadic  # get_node's **kwargs tolerates an alias it ignores
    }
    advertised = set(schema.get("properties", {}))
    assert advertised <= set(params), f"advertised, not accepted: {advertised - set(params)}"
    needed = {n for n, p in params.items() if p.default is inspect.Parameter.empty}
    assert needed == set(schema.get("required", [])), "required fields differ from the handler's"
    left_out = set(params) - advertised - INJECTED
    assert left_out == NOT_ADVERTISED.get((surface, name), set()), f"not advertised: {left_out}"


# ------------------------------------------------------------------ #
#  The interface's copies of backend types
# ------------------------------------------------------------------ #

# packages/interface/src is mounted in the Docker test runner (as test_context_render's sample).
INTERFACE_SRC = Path(__file__).resolve().parents[5] / "packages/interface/src"

INTERFACE_COPIES: list[tuple[str, str, list[str]]] = [
    ("components/PropertyEditor.tsx", r"const PROPERTY_TYPES = \[(.*?)\];", schema_enum(PropertyType)),
    ("components/upload/FilteringModeSelect.tsx", r"const FILTERING_MODES\b.*?= \[(.*?)\n\];", schema_enum(FilteringMode)),
    ("services/api/useVisionPages.ts", r"export type VisionPageStatus = (.*?);", schema_enum(VisionPageStatus)),
    ("services/api/useVisionPages.ts", r"export type VisionPageKind = (.*?);", schema_enum(VisionPageKind)),
]  # fmt: skip
"""TypeScript lists and unions written out from a backend type, with the values they must
list (the generated OpenAPI types do not carry these yet)."""


@pytest.mark.parametrize(
    ("path", "pattern", "expected"), INTERFACE_COPIES, ids=lambda v: str(v)[:40]
)
def test_the_interfaces_copy_lists_its_backend_types_values(
    path: str, pattern: str, expected: list[str]
) -> None:
    if not INTERFACE_SRC.exists():
        pytest.skip("packages/interface/src is not in this tree")
    source = (INTERFACE_SRC / path).read_text(encoding="utf-8")
    block = re.search(pattern, source, re.S)
    assert block, f"the copy in {path} moved; point this test at its new home"
    # a list of objects names its values as ``value: '...'``; a list or union, bare
    values = re.findall(r"value: '([a-z_]+)'", block.group(1)) or re.findall(
        r"'([a-z_]+)'", block.group(1)
    )
    assert values == expected
