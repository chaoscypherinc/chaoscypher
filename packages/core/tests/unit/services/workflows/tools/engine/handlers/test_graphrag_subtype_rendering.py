# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The context line's ``({type})`` renders the pack subtype when present, else the core type (spec 2026-10-07 § 6).

Since the restructure a node's ``entity_type`` is one of the eight core
types and its template is the source's core-type template; the subtype a
pack gave it (``character``, ``military_unit``) rides in
``properties.entity_subtype``. ``Person`` alone would tell the chat less
than the old ``Character`` did, so the line shows the subtype.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
    GraphRAGToolHandlers,
)


def _node(nid: str, label: str, entity_type: str | None, **props: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=nid, label=label, template_id="tpl-person", entity_type=entity_type, properties=props
    )


def _template_names(nodes: list[SimpleNamespace]) -> dict[str, str]:
    graph = SimpleNamespace(get_template=lambda tid: SimpleNamespace(name="Person"))
    handler = SimpleNamespace(graph=graph)
    return GraphRAGToolHandlers._resolve_template_names(handler, nodes)  # type: ignore[arg-type]


def test_the_line_reads_the_subtype() -> None:
    nodes = [
        _node("pierre", "Pierre", "Person", entity_subtype="character"),
        _node("count", "Count Bezúkhov", "Person", entity_subtype="character"),
    ]
    summary = GraphRAGToolHandlers._build_summary(
        [
            {
                "source": "Pierre",
                "source_id": "pierre",
                "label": "child_of",
                "target": "Count Bezúkhov",
                "target_id": "count",
            }
        ],
        _template_names(nodes),
    )
    assert summary == "Pierre (character) child_of Count Bezúkhov (character)."


def test_no_subtype_reads_the_core_type_and_a_snake_case_subtype_reads_as_words() -> None:
    names = _template_names(
        [
            _node("moscow", "Moscow", "Location"),
            _node(
                "regiment", "the Pávlograd regiment", "Organization", entity_subtype="military_unit"
            ),
            _node("blank", "Somebody", None, entity_subtype="  "),
        ]
    )
    assert names == {
        "moscow": "Location",
        "regiment": "military unit",
        "blank": "Person",  # neither: the template's name
    }
