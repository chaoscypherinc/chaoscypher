# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``apply_source_labels``: the one step that gives a document its tags and a caller's summary."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from chaoscypher_core.exceptions import NotFoundError, ValidationError
from chaoscypher_core.models import MEMORY_SOURCE_TYPE
from chaoscypher_core.services.sources.labels import (
    apply_source_labels,
    clean_summary,
    validate_source_labels,
)
from chaoscypher_core.services.sources.tags import TagRuleError, source_tag_names
from chaoscypher_core.settings import SourceProcessingSettings


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


DB = "default"
MAX = SourceProcessingSettings().summary_max_chars


def _source(adapter: SqliteAdapter, source_id: str = "doc-a") -> str:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": DB,
            "filename": f"{source_id}.txt",
            "filepath": "",
            "source_type": "txt",
            "status": "committed",
        }
    )
    return source_id


def _summary(adapter: SqliteAdapter, source_id: str) -> str | None:
    row = adapter.get_source(source_id, DB)
    assert row is not None
    return row["summary"]


def test_summary_max_chars_defaults_to_300() -> None:
    assert MAX == 300


def test_tags_are_appended_by_default(in_memory_adapter: SqliteAdapter) -> None:
    doc = _source(in_memory_adapter)
    apply_source_labels(in_memory_adapter, DB, doc, tags=["Alpha"], max_summary_chars=MAX)

    after = apply_source_labels(
        in_memory_adapter, DB, doc, tags=["beta", "alpha"], max_summary_chars=MAX
    )

    assert after == ["alpha", "beta"]
    assert source_tag_names(in_memory_adapter, doc) == ["alpha", "beta"]


def test_replace_tags_makes_them_the_whole_set(in_memory_adapter: SqliteAdapter) -> None:
    doc = _source(in_memory_adapter)
    apply_source_labels(in_memory_adapter, DB, doc, tags=["alpha", "beta"], max_summary_chars=MAX)

    after = apply_source_labels(
        in_memory_adapter, DB, doc, tags=["gamma"], replace_tags=True, max_summary_chars=MAX
    )

    assert after == ["gamma"]


def test_replace_tags_with_an_empty_list_clears_them(in_memory_adapter: SqliteAdapter) -> None:
    doc = _source(in_memory_adapter)
    apply_source_labels(in_memory_adapter, DB, doc, tags=["alpha"], max_summary_chars=MAX)

    after = apply_source_labels(
        in_memory_adapter, DB, doc, tags=[], replace_tags=True, max_summary_chars=MAX
    )

    assert after == []
    assert source_tag_names(in_memory_adapter, doc) == []


def test_tags_none_leaves_them_alone(in_memory_adapter: SqliteAdapter) -> None:
    doc = _source(in_memory_adapter)
    apply_source_labels(in_memory_adapter, DB, doc, tags=["alpha"], max_summary_chars=MAX)

    after = apply_source_labels(
        in_memory_adapter, DB, doc, tags=None, replace_tags=True, max_summary_chars=MAX
    )

    assert after == ["alpha"]


def test_the_cap_is_checked_on_the_result(in_memory_adapter: SqliteAdapter) -> None:
    doc = _source(in_memory_adapter)
    apply_source_labels(
        in_memory_adapter, DB, doc, tags=[f"t{i}" for i in range(30)], max_summary_chars=MAX
    )

    with pytest.raises(TagRuleError, match="at most 32 tags; this would give it 33"):
        apply_source_labels(
            in_memory_adapter, DB, doc, tags=["x1", "x2", "x3"], max_summary_chars=MAX
        )
    assert len(source_tag_names(in_memory_adapter, doc)) == 30


def test_a_summary_replaces_omitted_keeps_empty_clears(in_memory_adapter: SqliteAdapter) -> None:
    doc = _source(in_memory_adapter)

    apply_source_labels(in_memory_adapter, DB, doc, summary="First note.", max_summary_chars=MAX)
    assert _summary(in_memory_adapter, doc) == "First note."

    apply_source_labels(in_memory_adapter, DB, doc, summary="Second note.", max_summary_chars=MAX)
    assert _summary(in_memory_adapter, doc) == "Second note."

    apply_source_labels(in_memory_adapter, DB, doc, tags=["alpha"], max_summary_chars=MAX)
    assert _summary(in_memory_adapter, doc) == "Second note."

    apply_source_labels(in_memory_adapter, DB, doc, summary="", max_summary_chars=MAX)
    assert _summary(in_memory_adapter, doc) is None


def test_a_summary_over_the_limit_is_refused_and_nothing_changes(
    in_memory_adapter: SqliteAdapter,
) -> None:
    doc = _source(in_memory_adapter)

    with pytest.raises(ValidationError) as caught:
        apply_source_labels(
            in_memory_adapter, DB, doc, tags=["alpha"], summary="x" * 301, max_summary_chars=MAX
        )

    assert caught.value.message == "summary is too long: at most 300 characters"
    assert source_tag_names(in_memory_adapter, doc) == []
    assert _summary(in_memory_adapter, doc) is None


def test_clean_summary_folds_whitespace_and_one_pair_of_quotes() -> None:
    assert clean_summary('  "A  guide.\n" ', max_chars=MAX) == "A guide."
    assert clean_summary("   ", max_chars=MAX) is None
    assert clean_summary("", max_chars=MAX) is None
    assert clean_summary("x" * 300, max_chars=MAX) == "x" * 300


def test_clean_summary_keeps_quotes_that_do_not_wrap_the_whole_text() -> None:
    """Outer quotes come off only when they are one pair around everything."""
    title = '"Attention Is All You Need" introduces the "Transformer"'
    assert clean_summary(title, max_chars=MAX) == title
    assert clean_summary("'Tis the 'season'", max_chars=MAX) == "'Tis the 'season'"
    curly = "\u201cA\u201d and \u201cB\u201d"
    assert clean_summary(curly, max_chars=MAX) == curly
    nested = "\u201cA guide to \u2018falcons\u2019.\u201d"
    assert clean_summary(nested, max_chars=MAX) == "A guide to \u2018falcons\u2019."
    assert clean_summary("\u201cA guide.\u201d", max_chars=MAX) == "A guide."
    assert clean_summary("'A guide.'", max_chars=MAX) == "A guide."
    assert clean_summary("\"A 'falcon' guide.\"", max_chars=MAX) == "A 'falcon' guide."


def test_validate_source_labels_refuses_before_any_row_exists() -> None:
    with pytest.raises(TagRuleError):
        validate_source_labels(tags=["Work Docs"], summary=None, max_summary_chars=MAX)
    with pytest.raises(TagRuleError, match="this would give it 33"):
        validate_source_labels(
            tags=[f"t{i}" for i in range(33)], summary=None, max_summary_chars=MAX
        )
    with pytest.raises(ValidationError, match="summary is too long"):
        validate_source_labels(tags=None, summary="x" * 301, max_summary_chars=MAX)
    validate_source_labels(tags=["alpha"], summary="A note.", max_summary_chars=MAX)


def test_a_summary_alone_is_refused_on_a_memory(in_memory_adapter: SqliteAdapter) -> None:
    in_memory_adapter.create_source(
        {
            "id": "memory-a",
            "database_name": DB,
            "filename": "memory-a",
            "filepath": "",
            "source_type": MEMORY_SOURCE_TYPE,
            "status": "committed",
        }
    )

    with pytest.raises(ValidationError, match="is a memory"):
        apply_source_labels(
            in_memory_adapter, DB, "memory-a", summary="A note.", max_summary_chars=MAX
        )

    assert _summary(in_memory_adapter, "memory-a") is None


def test_a_summary_alone_on_an_unknown_document_is_not_found(
    in_memory_adapter: SqliteAdapter,
) -> None:
    with pytest.raises(NotFoundError):
        apply_source_labels(
            in_memory_adapter, DB, "doc-z", summary="A note.", max_summary_chars=MAX
        )
