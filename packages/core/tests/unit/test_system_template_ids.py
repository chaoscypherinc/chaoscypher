# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""``is_system_template_id``: the one test for a shipped node template's id."""

from __future__ import annotations

from chaoscypher_core.constants import SYSTEM_TEMPLATE_IDS, is_system_template_id
from chaoscypher_core.templates.default_templates import get_all_default_templates


def test_every_shipped_entity_template_is_a_system_template() -> None:
    """Every shipped node template but the workflow and lens ones (``SYSTEM_TEMPLATE_IDS``)."""
    node_ids = [
        t["id"]
        for t in get_all_default_templates()
        if t["template_type"] == "node" and t["id"] not in SYSTEM_TEMPLATE_IDS
    ]
    assert node_ids and all(is_system_template_id(i) for i in node_ids)


def test_a_template_a_source_or_a_person_made_is_not() -> None:
    assert not is_system_template_id("template_a4ed6804")
    assert not is_system_template_id("")
    assert not is_system_template_id(None)
