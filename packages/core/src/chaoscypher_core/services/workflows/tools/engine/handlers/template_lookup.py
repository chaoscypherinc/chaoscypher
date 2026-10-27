# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The template a tool call names, by its id or its exact name.

``create_node``, ``create_edge`` and ``list_nodes`` take a template by id or name. A write
naming a shipped template the database lost restores it first (``templates.restore``), so
its foreign key holds. A name finds the database's own template before a shipped one of
that name; a name or id that is neither answers ``UNKNOWN_TEMPLATE``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_core.templates.restore import restore_shipped_template


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol


def resolve_template(
    graph: GraphRepositoryProtocol,
    template_id: str,
    template_type: str,
    *,
    restore_shipped: bool = False,
) -> str | dict[str, Any]:
    """The template's id, given its id or its exact name; else the failure to answer.

    By id first. With ``restore_shipped`` (a write is about to name it), a shipped template's
    id the database lacks is restored and answered. By name, among the templates of
    ``template_type``: the database's own (not shipped) template wins, since a database an
    earlier release built holds the client's ``Person`` or ``related_to`` and its rows, and
    every open now adds the shipped one of the same name; two of its own of that name answer
    ``AMBIGUOUS_TEMPLATE`` with their ids. A shipped one answers when the database has none
    of its own. Otherwise ``UNKNOWN_TEMPLATE``.
    """
    if graph.get_template(template_id) is not None:
        return template_id
    if restore_shipped and restore_shipped_template(graph, template_id):
        return template_id
    named = [t for t in graph.list_templates(template_type=template_type) if t.name == template_id]
    own = [t.id for t in named if not t.is_system]
    if len(own) == 1:
        return own[0]
    if len(own) > 1:
        return ambiguous_template(template_id, own)
    if named:
        return named[0].id
    return unknown_template(template_id)


def ambiguous_template(name: str, template_ids: list[str]) -> dict[str, Any]:
    """The answer to a call naming a template by a name more than one of the database's own has."""
    return {
        "success": False,
        "error": f"Ambiguous template: {name} names {', '.join(template_ids)}; give one id.",
        "code": "AMBIGUOUS_TEMPLATE",
        "template_ids": template_ids,
    }


def unknown_template(template_id: str) -> dict[str, Any]:
    """The answer to a call naming a template the database does not have."""
    return {
        "success": False,
        "error": f"Unknown template: {template_id}",
        "code": "UNKNOWN_TEMPLATE",
    }
