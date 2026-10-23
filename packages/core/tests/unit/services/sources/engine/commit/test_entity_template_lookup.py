# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Entity commit looks templates up by id, not by a per-entity linear scan.

``all_templates`` is every node template in the database. Template ids are
per-source, so the list grows with the library; scanning it once per
committed entity made a commit O(templates x entities) of pure-Python CPU
on the shared operations event loop.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from chaoscypher_core.services.sources.engine.commit.entity import EntityCommitHandler


class _CountingTemplate:
    """Template stand-in that counts reads of ``id``."""

    reads = 0

    def __init__(self, template_id: str, properties: list[Any]) -> None:
        self._id = template_id
        self.properties = properties

    @property
    def id(self) -> str:
        type(self).reads += 1
        return self._id


class _EmptyEmbeddingRepo:
    def get_entity_embeddings(self, source_id: str) -> list[dict[str, Any]]:
        return []


class _FixedMatcher:
    def __init__(self, template_id: str) -> None:
        self._template_id = template_id

    def match(self, *_args: Any, **_kwargs: Any) -> str:
        return self._template_id


def test_required_title_filled_from_template_with_one_index_pass() -> None:
    target_id = "tmpl_target"
    title_prop = SimpleNamespace(name="title", required=True)
    templates = [_CountingTemplate(f"tmpl_{i}", []) for i in range(200)]
    templates.append(_CountingTemplate(target_id, [title_prop]))
    _CountingTemplate.reads = 0

    handler = EntityCommitHandler(
        graph_repository=None,
        source_repository=_EmptyEmbeddingRepo(),
        entity_matcher=_FixedMatcher(target_id),
        database_name="test",
    )
    entities = [{"id": f"ent_{i}", "name": f"Entity {i}", "type": "Thing"} for i in range(50)]

    nodes, _data, _ids = handler.prepare_entity_nodes(
        entities,
        all_templates=templates,
        suggested_templates=None,
        template_name_to_id={"thing": target_id},
        file_info={},
        file_id="src-1",
        source_id="src-1",
    )

    assert len(nodes) == 50
    assert all(n.properties["title"] == f"Entity {i}" for i, n in enumerate(nodes))
    # One pass to index the templates, not one scan per entity
    # (a per-entity scan reads ~201 ids x 50 entities).
    assert _CountingTemplate.reads <= len(templates)
