# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The tag lookups and edits in ``services.sources.tags`` and the SourceService tag methods."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.exceptions import ConflictError, NotFoundError, ValidationError
from chaoscypher_core.models import MEMORY_SOURCE_TYPE
from chaoscypher_core.services.graph.management.source import SourceService
from chaoscypher_core.services.sources import tags as tags_module
from chaoscypher_core.services.sources.tags import (
    REPLACE_WITH_ADD_OR_REMOVE,
    TagRuleError,
    as_pydantic_error,
    check_tag_edit,
    edit_source_tags,
    find_tags,
    get_or_create_tag,
    get_or_create_tags,
    normalize_filter_tags,
    source_tag_names,
)


DB = "default"


def _source(adapter: SqliteAdapter, source_id: str, source_type: str = "pdf") -> None:
    adapter.create_source(
        {
            "id": source_id,
            "database_name": DB,
            "filename": f"{source_id}.pdf",
            "filepath": "",
            "source_type": source_type,
            "status": "committed",
        }
    )


def _all_tags(adapter: SqliteAdapter) -> list[str]:
    return sorted(t["name"] for t in adapter.list_tags(DB))


def test_get_or_create_reuses_whatever_case(integration_adapter: SqliteAdapter) -> None:
    first = get_or_create_tag(integration_adapter, DB, "Research")
    second = get_or_create_tag(integration_adapter, DB, "research")
    [third] = get_or_create_tags(integration_adapter, DB, ["  RESEARCH "])

    assert first["name"] == "research"
    assert first["id"] == second["id"] == third["id"]
    assert first["id"].startswith("tag_")
    assert _all_tags(integration_adapter) == ["research"]


def test_get_or_create_survives_a_concurrent_create(
    integration_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    existing = integration_adapter.create_tag(
        {"id": "tag-research", "database_name": DB, "name": "research"}
    )
    real = integration_adapter.get_tags_by_names
    calls: list[int] = []

    def stale_once(database_name: str, names: Any) -> list[dict[str, Any]]:
        calls.append(1)
        if len(calls) == 1:
            return []  # another writer created it after this read
        return real(database_name, names)

    monkeypatch.setattr(integration_adapter, "get_tags_by_names", stale_once)

    tags = get_or_create_tags(integration_adapter, DB, ["research", "legal"])

    assert [t["name"] for t in tags] == ["research", "legal"]
    assert tags[0]["id"] == existing["id"]
    assert _all_tags(integration_adapter) == ["legal", "research"]


def test_find_tags_is_lookup_only(integration_adapter: SqliteAdapter) -> None:
    get_or_create_tag(integration_adapter, DB, "alpha")

    found, unknown = find_tags(integration_adapter, DB, ["ALPHA", "beta", "Bad Tag"])

    assert [t["name"] for t in found] == ["alpha"]
    assert unknown == ["beta", "Bad Tag"]
    assert _all_tags(integration_adapter) == ["alpha"]


def test_edit_add_remove_replace_clear(integration_adapter: SqliteAdapter) -> None:
    _source(integration_adapter, "doc-a")

    assert edit_source_tags(integration_adapter, DB, "doc-a", add=["Beta", "alpha"]) == [
        "alpha",
        "beta",
    ]
    assert edit_source_tags(integration_adapter, DB, "doc-a", add=["gamma"], remove=["alpha"]) == [
        "beta",
        "gamma",
    ]
    assert edit_source_tags(integration_adapter, DB, "doc-a", replace=["delta", "beta"]) == [
        "beta",
        "delta",
    ]
    assert source_tag_names(integration_adapter, "doc-a") == ["beta", "delta"]
    assert edit_source_tags(integration_adapter, DB, "doc-a", replace=[]) == []
    assert source_tag_names(integration_adapter, "doc-a") == []


def test_replace_with_add_is_refused(integration_adapter: SqliteAdapter) -> None:
    _source(integration_adapter, "doc-a")

    for extra in ({"add": ["alpha"]}, {"remove": ["alpha"]}):
        with pytest.raises(TagRuleError) as exc_info:
            edit_source_tags(integration_adapter, DB, "doc-a", replace=["beta"], **extra)
        assert exc_info.value.message == (
            "replace sets the whole list; it cannot be combined with add or remove"
        )
    assert source_tag_names(integration_adapter, "doc-a") == []


def test_check_tag_edit_is_the_one_replace_rule() -> None:
    for edit in (
        {"replace": ["beta"], "add": ["alpha"]},
        {"replace": [], "remove": ["alpha"]},
    ):
        with pytest.raises(TagRuleError) as exc_info:
            check_tag_edit(
                add=edit.get("add", ()), remove=edit.get("remove", ()), replace=edit["replace"]
            )
        assert exc_info.value.message == REPLACE_WITH_ADD_OR_REMOVE
    check_tag_edit(add=["alpha"], remove=["beta"], replace=None)
    check_tag_edit(add=(), remove=(), replace=[])


def test_edit_source_tags_refuses_through_check_tag_edit(
    integration_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source(integration_adapter, "doc-a")
    seen: list[dict[str, Any]] = []

    def spy(**edit: Any) -> None:
        seen.append(edit)
        raise TagRuleError("spied")

    monkeypatch.setattr(tags_module, "check_tag_edit", spy)

    with pytest.raises(TagRuleError, match="spied"):
        edit_source_tags(integration_adapter, DB, "doc-a", add=["alpha"])
    assert seen == [{"add": ["alpha"], "remove": (), "replace": None}]


def test_as_pydantic_error_carries_the_rule_text_verbatim() -> None:
    from pydantic_core import PydanticCustomError

    def refuse(_value: object) -> None:
        raise TagRuleError('tag "{x}" is refused')

    with pytest.raises(PydanticCustomError) as exc_info:
        as_pydantic_error(refuse)("anything")
    assert exc_info.value.type == "tag_rule"
    assert exc_info.value.message() == 'tag "{x}" is refused'


def test_unknown_document(integration_adapter: SqliteAdapter) -> None:
    with pytest.raises(NotFoundError) as exc_info:
        edit_source_tags(integration_adapter, DB, "doc-nobody", add=["alpha"])

    assert exc_info.value.message == 'No document has the id "doc-nobody".'
    assert str(exc_info.value) == 'No document has the id "doc-nobody".'
    assert _all_tags(integration_adapter) == []


def test_memory_is_refused(integration_adapter: SqliteAdapter) -> None:
    _source(integration_adapter, "mem-a", source_type=MEMORY_SOURCE_TYPE)

    with pytest.raises(ValidationError) as exc_info:
        edit_source_tags(integration_adapter, DB, "mem-a", add=["alpha"])

    assert exc_info.value.message == '"mem-a" is a memory; memories carry no tags.'
    assert _all_tags(integration_adapter) == []


def test_cap_applies_to_the_result(integration_adapter: SqliteAdapter) -> None:
    _source(integration_adapter, "doc-a")
    thirty = [f"t{i:02d}" for i in range(30)]
    edit_source_tags(integration_adapter, DB, "doc-a", add=thirty)

    with pytest.raises(TagRuleError) as exc_info:
        edit_source_tags(integration_adapter, DB, "doc-a", add=["new-1", "new-2", "new-3"])

    assert exc_info.value.message == ("a document can carry at most 32 tags; this would give it 33")
    assert source_tag_names(integration_adapter, "doc-a") == thirty
    assert "new-1" not in _all_tags(integration_adapter)


def test_removing_a_tag_the_document_lacks_is_a_no_op(integration_adapter: SqliteAdapter) -> None:
    _source(integration_adapter, "doc-a")
    _source(integration_adapter, "doc-b")
    edit_source_tags(integration_adapter, DB, "doc-a", add=["alpha"])
    edit_source_tags(integration_adapter, DB, "doc-b", add=["beta"])

    assert edit_source_tags(integration_adapter, DB, "doc-a", remove=["beta", "never"]) == ["alpha"]
    assert source_tag_names(integration_adapter, "doc-b") == ["beta"]


def test_update_tag_to_an_existing_name_is_a_conflict(integration_adapter: SqliteAdapter) -> None:
    service = SourceService(integration_adapter, DB)
    service.create_tag("research")
    legal = service.create_tag("legal")

    with pytest.raises(ConflictError) as exc_info:
        service.update_tag(legal["id"], name="Research")

    assert exc_info.value.message == 'a tag named "research" already exists'
    renamed = service.update_tag(legal["id"], name="LEGAL", color="#00ff00")
    assert renamed is not None
    assert renamed["name"] == "legal"
    assert renamed["color"] == "#00ff00"
    assert _all_tags(integration_adapter) == ["legal", "research"]


def test_a_rename_racing_another_is_still_a_conflict(
    integration_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name check passed, then another writer took the name: the unique index refuses it."""
    service = SourceService(integration_adapter, DB)
    service.create_tag("research")
    legal = service.create_tag("legal")
    monkeypatch.setattr(integration_adapter, "get_tags_by_names", lambda *_args: [])

    with pytest.raises(ConflictError) as exc_info:
        service.update_tag(legal["id"], name="research")

    assert exc_info.value.message == 'a tag named "research" already exists'
    assert _all_tags(integration_adapter) == ["legal", "research"]  # the session still works


def test_create_tag_refuses_a_bad_name(integration_adapter: SqliteAdapter) -> None:
    service = SourceService(integration_adapter, DB)

    with pytest.raises(TagRuleError) as exc_info:
        service.create_tag("Work Docs")

    assert exc_info.value.message == (
        "tag \"Work Docs\" is not allowed: use a-z, 0-9, ':', '_' or '-', "
        "starting with a letter or digit, at most 64 characters"
    )
    with pytest.raises(TagRuleError):
        service.update_tag(service.create_tag("alpha")["id"], name="Work Docs")
    assert _all_tags(integration_adapter) == ["alpha"]


def test_service_tag_source_and_list_documents(integration_adapter: SqliteAdapter) -> None:
    service = SourceService(integration_adapter, DB)
    _source(integration_adapter, "doc-a")
    _source(integration_adapter, "doc-b")

    assert service.tag_source("doc-a", add=["Alpha"]) == ["alpha"]
    documents, total = service.list_documents(tags=["ALPHA"], limit=10, offset=0)

    assert total == 1
    assert [d["source_id"] for d in documents] == ["doc-a"]


# ---------------------------------------------------------------------------
# Fix round 1: one transaction per edit, replace refusals, filter tags
# ---------------------------------------------------------------------------


def test_a_failure_after_the_add_step_leaves_the_tags_as_they_were(
    integration_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source(integration_adapter, "doc-a")
    edit_source_tags(integration_adapter, DB, "doc-a", add=["alpha"])

    def broken(*_args: Any) -> None:
        raise ConflictError("injected after the add step")

    monkeypatch.setattr(integration_adapter, "remove_source_tags", broken)
    with pytest.raises(ConflictError, match="injected"):
        edit_source_tags(integration_adapter, DB, "doc-a", add=["beta"], remove=["alpha"])

    assert source_tag_names(integration_adapter, "doc-a") == ["alpha"]
    assert _all_tags(integration_adapter) == ["alpha"]  # the new tag row went too


def test_add_and_remove_at_the_cap_is_atomic_under_an_injected_failure(
    integration_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source(integration_adapter, "doc-a")
    full = [f"t{i:02d}" for i in range(32)]
    edit_source_tags(integration_adapter, DB, "doc-a", add=full)

    def broken(*_args: Any) -> None:
        raise ConflictError("injected after the add step")

    with monkeypatch.context() as patched:
        patched.setattr(integration_adapter, "remove_source_tags", broken)
        with pytest.raises(ConflictError):
            edit_source_tags(integration_adapter, DB, "doc-a", add=["new"], remove=["t00"])
    assert source_tag_names(integration_adapter, "doc-a") == full

    result = edit_source_tags(integration_adapter, DB, "doc-a", add=["new"], remove=["t00"])
    assert len(result) == 32
    assert "new" in result
    assert "t00" not in result


def test_a_concurrent_create_inside_the_edit_rolls_the_edit_back(
    integration_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Inside the edit's transaction a lost create race cannot be undone alone: it fails loudly."""
    _source(integration_adapter, "doc-a")
    edit_source_tags(integration_adapter, DB, "doc-a", add=["alpha"])
    integration_adapter.create_tag({"id": "tag-research", "database_name": DB, "name": "research"})
    real = integration_adapter.get_tags_by_names
    calls: list[int] = []

    def stale_once(database_name: str, names: Any) -> list[dict[str, Any]]:
        calls.append(1)
        return [] if len(calls) == 1 else real(database_name, names)

    monkeypatch.setattr(integration_adapter, "get_tags_by_names", stale_once)
    with pytest.raises(ConflictError) as exc_info:
        edit_source_tags(integration_adapter, DB, "doc-a", add=["research", "beta"])

    assert exc_info.value.message == (
        'another writer created tag "research" at the same time; try again'
    )
    assert source_tag_names(integration_adapter, "doc-a") == ["alpha"]
    assert _all_tags(integration_adapter) == ["alpha", "research"]
    # The session is usable again, and a retry succeeds.
    assert edit_source_tags(integration_adapter, DB, "doc-a", add=["research"]) == [
        "alpha",
        "research",
    ]


def test_replace_against_a_memory_or_an_unknown_source_is_refused(
    integration_adapter: SqliteAdapter,
) -> None:
    _source(integration_adapter, "mem-a", source_type=MEMORY_SOURCE_TYPE)

    with pytest.raises(ValidationError) as memory:
        edit_source_tags(integration_adapter, DB, "mem-a", replace=["alpha"])
    with pytest.raises(NotFoundError) as unknown:
        edit_source_tags(integration_adapter, DB, "doc-nobody", replace=["alpha"])

    assert memory.value.message == '"mem-a" is a memory; memories carry no tags.'
    assert unknown.value.message == 'No document has the id "doc-nobody".'
    assert _all_tags(integration_adapter) == []


def test_replace_over_32_changes_nothing(integration_adapter: SqliteAdapter) -> None:
    _source(integration_adapter, "doc-a")
    edit_source_tags(integration_adapter, DB, "doc-a", add=["alpha"])

    with pytest.raises(TagRuleError) as exc_info:
        edit_source_tags(integration_adapter, DB, "doc-a", replace=[f"t{i:02d}" for i in range(33)])

    assert exc_info.value.message == ("a document can carry at most 32 tags; this would give it 33")
    assert source_tag_names(integration_adapter, "doc-a") == ["alpha"]
    assert _all_tags(integration_adapter) == ["alpha"]


def test_normalize_filter_tags_has_no_cap() -> None:
    forty = [f"T{i:02d}" for i in range(40)]

    assert normalize_filter_tags([*forty, "t00"]) == [t.lower() for t in forty]
    with pytest.raises(TagRuleError):
        normalize_filter_tags("alpha")
    with pytest.raises(TagRuleError):
        normalize_filter_tags(["Bad Tag"])


def test_a_40_tag_filter_is_accepted(integration_adapter: SqliteAdapter) -> None:
    service = SourceService(integration_adapter, DB)
    _source(integration_adapter, "doc-a")
    service.tag_source("doc-a", add=["t39"])

    forty = [f"T{i:02d}" for i in range(40)]

    documents, total = service.list_documents(tags=forty, limit=10, offset=0)

    assert total == 1
    assert [d["source_id"] for d in documents] == ["doc-a"]

    listed = service.list_sources(tags=forty)
    assert listed["total"] == 1
    assert [s["id"] for s in listed["sources"]] == ["doc-a"]


# ---------------------------------------------------------------------------
# Fix round 2: the edit holds the writer lock from its first read
# ---------------------------------------------------------------------------


def test_a_concurrent_edit_waits_for_the_lock_and_sees_the_first(tmp_path: Path) -> None:
    """Two adapters on one file (two processes): B's edit, started at A's cap-check point,
    cannot pass its own check on the stale count; it waits, then sees A's tag and is refused.
    """
    db_path = tmp_path / "race" / "app.db"
    db_path.parent.mkdir()
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    first = SqliteAdapter(str(db_path), database_name=DB)
    second = SqliteAdapter(str(db_path), database_name=DB)
    first.connect()
    second.connect()
    try:
        _source(first, "doc-a")
        edit_source_tags(first, DB, "doc-a", add=[f"t{i:02d}" for i in range(31)])
        outcome: dict[str, object] = {}

        def second_edit() -> None:
            try:
                outcome["tags"] = edit_source_tags(second, DB, "doc-a", add=["from-b"])
            except TagRuleError as exc:
                outcome["refused"] = exc.message

        racer = threading.Thread(target=second_edit)
        real = first.get_tags_by_names

        def at_the_cap_check(*args: Any) -> list[dict[str, Any]]:
            if not racer.is_alive() and not outcome:
                racer.start()
                # Without the lock B finishes here (and A then stores 33);
                # with it B is still waiting for A's lock when A goes on.
                racer.join(timeout=0.5)
            return real(*args)

        first.get_tags_by_names = at_the_cap_check  # type: ignore[method-assign]
        result = edit_source_tags(first, DB, "doc-a", add=["from-a"])
        racer.join(timeout=30)

        assert not racer.is_alive()
        assert len(result) == 32
        assert outcome == {"refused": "a document can carry at most 32 tags; this would give it 33"}
        assert len(source_tag_names(second, "doc-a")) == 32
    finally:
        first.disconnect()
        second.disconnect()


def test_restoring_package_tags_reads_under_the_writer_lock(
    integration_adapter: SqliteAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The importer's read (the local tags) and its write are one locked unit, like an edit's."""
    from chaoscypher_core.repo_factories.graph_factory import get_transaction_graph_repository
    from chaoscypher_core.services.package.importer import CcxImporter
    from chaoscypher_core.services.package.importer.models import ImportStats

    _source(integration_adapter, "doc-a")
    assert integration_adapter.session is not None
    session = integration_adapter.session
    locked_reads: list[bool] = []
    real = integration_adapter.get_source_tags

    def watched(source_id: str) -> list[dict[str, Any]]:
        driver = session.connection().connection.driver_connection
        locked_reads.append(bool(driver.in_transaction))
        return real(source_id)

    monkeypatch.setattr(integration_adapter, "get_source_tags", watched)
    importer = CcxImporter(
        graph_repository=get_transaction_graph_repository(integration_adapter, DB),
        sources_repository=integration_adapter,
    )

    importer._restore_source_tags("doc-a", ["alpha"], DB, ImportStats())

    assert locked_reads
    assert locked_reads[0] is True
    assert source_tag_names(integration_adapter, "doc-a") == ["alpha"]
