# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tool schema pieces taken from the types that define them.

Tool schemas (MCP ``input_schema``, the engine's ``TOOL_SCHEMAS``) are checked
against the arguments before a tool runs, so a hand-copied ``enum`` or object
that falls behind its model refuses values the model accepts (0.7.0:
``create_template`` refused ``property_type: "datetime"``). Every schema enum
that mirrors a model ``StrEnum`` or ``Literal`` builds its list with
:func:`schema_enum`, and every schema object that describes a Pydantic model
builds it with :func:`schema_object`.
"""

from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, get_args, get_origin


if TYPE_CHECKING:
    from pydantic import BaseModel


def schema_enum(source: type[Enum] | Any) -> list[str]:
    """The values of a ``StrEnum`` class or a ``Literal[...]`` alias, in definition order."""
    if isinstance(source, type) and issubclass(source, Enum):
        return [str(member.value) for member in source]
    if get_origin(source) is Literal:
        return [str(value) for value in get_args(source)]
    msg = f"schema_enum takes a StrEnum class or a Literal alias, not {source!r}"
    raise TypeError(msg)


_NULL = {"type": "null"}


def schema_object(model: type[BaseModel]) -> dict[str, Any]:
    """A Pydantic model's JSON schema, shaped like the hand-written tool schemas.

    ``model_json_schema()`` is the source; three things change so every tool
    consumer (the MCP SDK's validator, OpenAI / Anthropic / Gemini / Ollama
    function calling through LangChain) reads it the same way:

    - ``$ref``s are inlined and ``$defs`` dropped (a nested object or enum is
      written out where it is used; the property's own description wins);
    - ``title``s are dropped (no other tool schema carries them);
    - an optional field's ``anyOf: [X, null]`` becomes ``X`` and its
      ``default: null`` goes: leave the field out instead of sending ``null``.

    ``required``, descriptions, defaults and ``additionalProperties: false``
    (a model with ``extra="forbid"``) stay as the model states them.
    """
    raw = model.model_json_schema()
    defs: dict[str, Any] = raw.get("$defs", {})

    def clean(node: Any) -> Any:
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = defs[node["$ref"].rsplit("/", 1)[-1]]
            return clean({**target, **{k: v for k, v in node.items() if k != "$ref"}})
        out = {k: clean(v) for k, v in node.items() if k not in ("title", "$defs")}
        branches = out.get("anyOf")
        if isinstance(branches, list) and _NULL in branches:
            rest = [b for b in branches if b != _NULL]
            if len(rest) == 1:
                del out["anyOf"]
                out = {**rest[0], **out}
                if "default" in out and out["default"] is None:
                    del out["default"]
        return out

    result: dict[str, Any] = clean(raw)
    # The model's own docstring describes the class, not the tool argument.
    result.pop("description", None)
    return result
