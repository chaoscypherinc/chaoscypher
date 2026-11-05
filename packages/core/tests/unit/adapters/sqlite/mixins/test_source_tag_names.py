# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tag storage by name: lookups, edits, tagged-source ids and the document listing.

Runs on the shared file-backed ``sqlite_adapter`` (database ``default``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from chaoscypher_core.models import MEMORY_SOURCE_TYPE
from chaoscypher_core.services.sources.tags import TagMatch


if TYPE_CHECKING:
    from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter


DB = "default"
_BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _source(
    adapter: SqliteAdapter,
    source_id: str,
    *,
    minutes: int = 0,
    title: str | None = None,
    source_type: str = "pdf",
    summary: str | None = None,
) -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": DB,
            "filename": f"{source_id}.pdf",
            "filepath": "",
            "title": title,
            "source_type": source_type,
            "summary": summary,
            "status": "committed",
            "created_at": _BASE + timedelta(minutes=minutes),
        }
    )


def _tag(adapter: SqliteAdapter, name: str) -> str:
    adapter.create_tag({"id": f"tag-{name}", "database_name": DB, "name": name})
    return f"tag-{name}"


def _tagged(adapter: SqliteAdapter, source_id: str, *names: str) -> None:
    ids = []
    for name in names:
        existing = adapter.get_tags_by_names(DB, [name])
        ids.append(existing[0]["id"] if existing else _tag(adapter, name))
    adapter.add_source_tags(source_id, ids, DB)


def _names(tags: list[dict[str, Any]]) -> list[str]:
    return sorted(t["name"] for t in tags)


def test_get_tags_by_names(sqlite_adapter: SqliteAdapter) -> None:
    _tag(sqlite_adapter, "alpha")
    _tag(sqlite_adapter, "beta")
    sqlite_adapter.create_tag({"id": "other-db", "database_name": "elsewhere", "name": "alpha"})

    found = sqlite_adapter.get_tags_by_names(DB, ["alpha", "beta", "gamma"])

    assert _names(found) == ["alpha", "beta"]
    assert {t["id"] for t in found} == {"tag-alpha", "tag-beta"}
    assert sqlite_adapter.get_tags_by_names(DB, []) == []


def test_add_remove_set_source_tags(sqlite_adapter: SqliteAdapter) -> None:
    _source(sqlite_adapter, "doc-a")
    a, b, c = (_tag(sqlite_adapter, n) for n in ("alpha", "beta", "gamma"))

    sqlite_adapter.add_source_tags("doc-a", [a, b], DB)
    sqlite_adapter.add_source_tags("doc-a", [b], DB)  # an existing pair is skipped
    assert _names(sqlite_adapter.get_source_tags("doc-a")) == ["alpha", "beta"]

    sqlite_adapter.remove_source_tags("doc-a", [a, c])  # c is not there: no-op
    assert _names(sqlite_adapter.get_source_tags("doc-a")) == ["beta"]

    sqlite_adapter.set_source_tags("doc-a", [a, c], DB)
    assert _names(sqlite_adapter.get_source_tags("doc-a")) == ["alpha", "gamma"]

    sqlite_adapter.set_source_tags("doc-a", [], DB)
    assert sqlite_adapter.get_source_tags("doc-a") == []


def test_tagged_source_ids_any_and_all(sqlite_adapter: SqliteAdapter) -> None:
    for sid in ("doc-a", "doc-b", "doc-c"):
        _source(sqlite_adapter, sid)
    _tagged(sqlite_adapter, "doc-a", "alpha", "beta")
    _tagged(sqlite_adapter, "doc-b", "alpha")
    _tagged(sqlite_adapter, "doc-c", "beta")

    any_ids = sqlite_adapter.tagged_source_ids(DB, ["alpha", "beta"], TagMatch.ANY)
    all_ids = sqlite_adapter.tagged_source_ids(DB, ["alpha", "beta"], TagMatch.ALL)

    assert sorted(any_ids) == ["doc-a", "doc-b", "doc-c"]
    assert all_ids == ["doc-a"]
    assert sqlite_adapter.tagged_source_ids(DB, ["nobody"], TagMatch.ANY) == []
    assert sqlite_adapter.tagged_source_ids("elsewhere", ["alpha"], TagMatch.ANY) == []


def test_list_documents_shape_and_order(sqlite_adapter: SqliteAdapter) -> None:
    _source(sqlite_adapter, "doc-b", minutes=0, title="Bee")
    _source(sqlite_adapter, "doc-a", minutes=0, title="Ay", summary="About ay.")
    _source(sqlite_adapter, "doc-c", minutes=5)  # newest; no title
    _tagged(sqlite_adapter, "doc-a", "zeta", "alpha")

    documents, total = sqlite_adapter.list_documents(DB, limit=10, offset=0)

    assert total == 3
    assert [d["source_id"] for d in documents] == ["doc-c", "doc-a", "doc-b"]
    newest, ay, bee = documents
    assert newest == {
        "source_id": "doc-c",
        "title": "doc-c.pdf",
        "summary": None,
        "tags": [],
        "added": newest["added"],
    }
    added = datetime.fromisoformat(newest["added"])
    assert added == _BASE + timedelta(minutes=5)
    assert added.utcoffset() == timedelta(0)
    assert ay["summary"] == "About ay."
    assert ay["tags"] == ["alpha", "zeta"]
    assert bee["title"] == "Bee"


def test_list_documents_excludes_memories(sqlite_adapter: SqliteAdapter) -> None:
    _source(sqlite_adapter, "doc-a")
    _source(sqlite_adapter, "mem-a", source_type=MEMORY_SOURCE_TYPE)

    documents, total = sqlite_adapter.list_documents(DB, limit=10, offset=0)

    assert [d["source_id"] for d in documents] == ["doc-a"]
    assert total == 1


def test_list_documents_filters_and_pages_in_sql(sqlite_adapter: SqliteAdapter) -> None:
    for minutes, sid in enumerate(("doc-a", "doc-b", "doc-c", "doc-d")):
        _source(sqlite_adapter, sid, minutes=minutes)
    _tagged(sqlite_adapter, "doc-a", "alpha", "beta")
    _tagged(sqlite_adapter, "doc-b", "alpha")
    _tagged(sqlite_adapter, "doc-c", "beta")
    # doc-d carries nothing

    page, total = sqlite_adapter.list_documents(
        DB, tags=["alpha", "beta"], match=TagMatch.ANY, limit=1, offset=1
    )
    assert total == 3
    assert [d["source_id"] for d in page] == ["doc-b"]

    both, total_all = sqlite_adapter.list_documents(
        DB, tags=["alpha", "beta"], match=TagMatch.ALL, limit=10, offset=0
    )
    assert [d["source_id"] for d in both] == ["doc-a"]
    assert total_all == 1

    nothing, none_total = sqlite_adapter.list_documents(DB, tags=["nobody"], limit=10, offset=0)
    assert nothing == []
    assert none_total == 0


def test_list_sources_filters_by_tag_names(sqlite_adapter: SqliteAdapter) -> None:
    for minutes, sid in enumerate(("doc-a", "doc-b", "doc-c")):
        _source(sqlite_adapter, sid, minutes=minutes, summary=f"{sid} summary")
    _tagged(sqlite_adapter, "doc-a", "alpha", "beta")
    _tagged(sqlite_adapter, "doc-b", "alpha")

    sources, total = sqlite_adapter.list_sources(tags=["alpha", "beta"], match=TagMatch.ALL)
    assert [s["id"] for s in sources] == ["doc-a"]
    assert total == 1
    assert sources[0]["summary"] == "doc-a summary"

    sources_any, total_any = sqlite_adapter.list_sources(tags=["alpha"])
    assert sorted(s["id"] for s in sources_any) == ["doc-a", "doc-b"]
    assert total_any == 2

    _nothing, total_none = sqlite_adapter.list_sources(tags=["nobody"])
    assert total_none == 0


def test_two_builders_share_one_statement(sqlite_adapter: SqliteAdapter) -> None:
    """Two tag filters with different prefixes bind side by side in one statement."""
    from sqlalchemy import text

    from chaoscypher_core.adapters.sqlite.repos.source_tag_scope import tagged_sources_sql

    for sid in ("doc-a", "doc-b", "doc-c"):
        _source(sqlite_adapter, sid)
    _tagged(sqlite_adapter, "doc-a", "alpha", "beta")
    _tagged(sqlite_adapter, "doc-b", "alpha")
    _tagged(sqlite_adapter, "doc-c", "beta")
    first, first_params = tagged_sources_sql(["alpha"], TagMatch.ANY, param_prefix="left")
    second, second_params = tagged_sources_sql(
        ["beta"], TagMatch.ANY, param_prefix="right", database_name=DB
    )
    sql = f"SELECT id FROM sources WHERE id IN ({first}) AND id IN ({second}) ORDER BY id"

    assert sqlite_adapter.session is not None
    rows = sqlite_adapter.session.execute(text(sql), {**first_params, **second_params}).all()

    assert [row[0] for row in rows] == ["doc-a"]


def test_the_builder_refuses_a_prefix_that_is_not_an_identifier() -> None:
    import pytest

    from chaoscypher_core.adapters.sqlite.repos.source_tag_scope import tagged_sources_sql
    from chaoscypher_core.exceptions import ValidationError

    # A typed error, not an assert: it holds under ``python -O`` too.
    with pytest.raises(ValidationError, match="param_prefix must be an identifier"):
        tagged_sources_sql(["alpha"], TagMatch.ANY, param_prefix="bad prefix; --")
