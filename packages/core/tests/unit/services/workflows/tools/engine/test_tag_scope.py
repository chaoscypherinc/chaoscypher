# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``tag_source_scope`` hands back the strictness with the ids, so no caller converts tags and forgets it."""

from __future__ import annotations

from unittest.mock import MagicMock

from chaoscypher_core.services.workflows.tools.engine.tag_scope import tag_source_scope


def _search(tagged: list[str]) -> MagicMock:
    search = MagicMock()
    search.tagged_source_ids.return_value = tagged
    return search


def test_a_scope_from_tags_is_attested_only() -> None:
    scope = tag_source_scope(_search(["doc-a", "doc-b"]), ["doc-b", "doc-c"], ["alpha"], "any")
    assert scope.source_ids == ["doc-b"]
    assert scope.attested_only is True


def test_a_plain_source_scope_is_not() -> None:
    search = _search([])
    scope = tag_source_scope(search, ["doc-a"], None, "any")
    assert (scope.source_ids, scope.attested_only) == (["doc-a"], False)
    assert tag_source_scope(search, None, [], "any") == (None, False)
    search.tagged_source_ids.assert_not_called()


def test_tags_holding_no_document_are_an_empty_strict_scope() -> None:
    scope = tag_source_scope(_search([]), None, ["nobody"], "any")
    assert (scope.source_ids, scope.attested_only) == ([], True)
