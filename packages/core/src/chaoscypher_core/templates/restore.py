# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Restore one shipped template a database lost, where a write is about to name it.

Every open gives a database the shipped templates it lacks (``database.seed``). A write that
names one by id (the default edge template of ``create_edge``, the commit's fallback node
template) restores it first if it went missing since, so its foreign key holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.templates.default_templates import shipped_template


if TYPE_CHECKING:
    from chaoscypher_core.ports.graph import GraphRepositoryProtocol


logger = structlog.get_logger(__name__)


def restore_shipped_template(graph: GraphRepositoryProtocol, template_id: str) -> bool:
    """Make sure the shipped template ``template_id`` exists; False when none is shipped with it.

    Inserts it when the database lacks it, and logs that at warning level: something
    deleted a template every open gives the database, which is worth finding.
    """
    spec = shipped_template(template_id)
    if spec is None:
        return False
    if graph.ensure_default_templates_exist(default_templates_provider=lambda: [spec]):
        logger.warning("shipped_template_restored", template_id=template_id)
    return True
