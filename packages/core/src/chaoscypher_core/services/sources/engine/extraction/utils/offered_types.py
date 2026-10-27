# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Types a memory's extraction is offered: templates the database already has (spec 2026-10-24 D26).

A scoped memory may use its scope's templates (``mcp/memory_seed.scope_templates``). They
reach its extraction prompt as known types (:func:`render_offered_types`, one line each
beside the core types the prompt lists), and an entity written with one of them is stamped
with the offered name right after its group is extracted (:func:`stamp_offered_types`),
while its written type is still on it. The commit binds a stamped entity to that template
(``commit/entity.py``) and pops the stamp: :data:`OFFERED_TEMPLATE_KEY` is never stored.

With no offered types nothing here runs, so documents and the extraction recipe do not move.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold
from chaoscypher_core.services.sources.engine.extraction.utils.known_entities import (
    KNOWN_LINE_LIMIT,
)
from chaoscypher_core.services.sources.engine.extraction.utils.template_formatter import (
    format_node_line,
)


MAX_OFFERED_TYPES = 12
"""The most offered types one memory's prompt shows (a scope's templates; the rest are not offered)."""
OFFERED_TEMPLATE_KEY = "offered_template"
"""The entity property naming the offered type it was written with; the commit pops it."""
OFFERED_TEMPLATES_KEY = "offered_templates"
"""The commit data key holding the offered templates: folded name -> template id."""


@dataclass(frozen=True)
class OfferedType:
    """A template offered to a memory's extraction as a known type."""

    name: str
    description: str
    template_id: str


def _one_line(text: str) -> str:
    """``text`` (a person's) on one line: every run of whitespace, newlines too, one space."""
    return " ".join(str(text or "").split())


def offered_in_prompt(
    types: Sequence[OfferedType], listed_types: Collection[str]
) -> list[OfferedType]:
    """The offered types the prompt shows: at most :data:`MAX_OFFERED_TYPES`, in order.

    A type whose name the prompt's type list already declares (``listed_types``, its core
    types) is left out, and so is one whose name cannot be a type-list name (empty, or
    holding ``:`` or ``|``, which the type list and the ``E|`` line split on). Names and
    descriptions are flattened to one line. Only these types are stamped
    (:func:`stamp_offered_types`): a type the model was not shown binds nothing.
    """
    listed = {fold(t) for t in listed_types}
    shown: list[OfferedType] = []
    for offered in types:
        name = _one_line(offered.name)
        if not name or ":" in name or "|" in name or fold(name) in listed:
            continue
        listed.add(fold(name))
        shown.append(OfferedType(name, _one_line(offered.description), offered.template_id))
        if len(shown) >= MAX_OFFERED_TYPES:
            break
    return shown


def render_offered_types(types: Sequence[OfferedType], listed_types: Collection[str]) -> str:
    """The type-list lines of :func:`offered_in_prompt`'s types, one each, "" when none.

    Each is a ``format_node_line`` line cut to :data:`KNOWN_LINE_LIMIT` characters, so a
    template's text never runs onto a line of its own.
    """
    return "\n".join(
        format_node_line({"name": t.name, "description": t.description})[:KNOWN_LINE_LIMIT]
        for t in offered_in_prompt(types, listed_types)
    )


def stamp_offered_types(entities: list[dict[str, Any]], types: Sequence[OfferedType]) -> None:
    """Stamp each entity written with an offered type with that type's name, in place.

    The written type is the one the type rescue kept as ``type_rescued_from``, else the
    entity's ``type``; it is compared folded.
    """
    if not types:
        return
    by_name = {fold(t.name): t.name for t in types}
    for entity in entities:
        written = str(entity.get("type_rescued_from") or entity.get("type") or "")
        name = by_name.get(fold(written))
        if name is None:
            continue
        properties = entity.get("properties")
        entity["properties"] = {
            **(properties if isinstance(properties, dict) else {}),
            OFFERED_TEMPLATE_KEY: name,
        }


def take_offered_template(
    entity: Mapping[str, Any], offered_templates: Mapping[str, str]
) -> tuple[dict[str, Any], str | None]:
    """``entity`` without its stamp, and the offered template id it binds to (None: none).

    The stamp is taken off a copy, so the caller's entity (a retried commit's data) is
    left as it was. An entity binds when its stamp names a template in
    ``offered_templates`` (folded name -> template id).
    """
    properties = entity.get("properties")
    if not isinstance(properties, Mapping) or OFFERED_TEMPLATE_KEY not in properties:
        return dict(entity), None
    rest = {k: v for k, v in properties.items() if k != OFFERED_TEMPLATE_KEY}
    template_id = offered_templates.get(fold(str(properties[OFFERED_TEMPLATE_KEY])))
    return {**entity, "properties": rest}, template_id


def offered_template_ids(types: Sequence[OfferedType]) -> dict[str, str]:
    """The offered templates as the commit reads them: folded name -> template id."""
    return {fold(t.name): t.template_id for t in types}


__all__ = [
    "MAX_OFFERED_TYPES",
    "OFFERED_TEMPLATES_KEY",
    "OFFERED_TEMPLATE_KEY",
    "OfferedType",
    "offered_in_prompt",
    "offered_template_ids",
    "render_offered_types",
    "stamp_offered_types",
    "take_offered_template",
]
