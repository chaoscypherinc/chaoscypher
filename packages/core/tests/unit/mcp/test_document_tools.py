# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The MCP document-tag tools: ``tag_document``, ``list_documents``, and tags on the others.

A real engine (the embedder faked, the extraction model scripted:
``memory_world.py``) and its in-process server, every call through the
server's ``CallToolRequest`` handler the way a host calls it. Tsukumo reads
these replies, so their shapes are asserted exactly.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chaoscypher_core.mcp.tools import get_tools
from chaoscypher_core.services.sources.tags import source_tag_names

from .memory_world import build_world, call_raw, list_tools, settings, store_pending


BAD_TAG = (
    "tag \"Work Docs\" is not allowed: use a-z, 0-9, ':', '_' or '-', "
    "starting with a letter or digit, at most 64 characters"
)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    w = build_world(tmp_path, monkeypatch)
    yield w
    w.engine.close()


async def _json(world: SimpleNamespace, name: str, args: dict[str, Any]) -> tuple[dict, bool]:
    text, is_error = await call_raw(world.server, name, args)
    return json.loads(text), is_error


async def _ok(world: SimpleNamespace, name: str, args: dict[str, Any]) -> dict:
    answer, is_error = await _json(world, name, args)
    assert not is_error, answer
    assert answer["success"] is True, answer
    return answer


async def _refused(world: SimpleNamespace, name: str, args: dict[str, Any]) -> str:
    answer, is_error = await _json(world, name, args)
    assert is_error, answer
    assert answer["success"] is False, answer
    return str(answer["error"])


async def _doc(
    world: SimpleNamespace, name: str, tags: list[str] | None = None, **labels: Any
) -> str:
    result = await world.engine.process_document(
        f"{name} is a document about {name}.",
        filename=f"{name}.txt",
        extract=False,
        tags=tags,
        **labels,
    )
    return result.source_id


def _sandbox_file(world: SimpleNamespace, name: str, text: str) -> str:
    sandbox = Path(world.engine.settings.paths.mcp_dir)
    sandbox.mkdir(parents=True, exist_ok=True)
    path = sandbox / name
    path.write_text(text)
    return str(path)


def _tags(world: SimpleNamespace, source_id: str) -> list[str]:
    return source_tag_names(world.storage, source_id)


# ---------------------------------------------------------------- tag_document


async def test_tag_document_adds_removes_and_answers_sorted(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", ["beta"])

    answer = await _ok(world, "tag_document", {"source_id": sid, "add": ["Zeta", "alpha"]})
    assert answer == {"success": True, "source_id": sid, "tags": ["alpha", "beta", "zeta"]}

    answer = await _ok(world, "tag_document", {"source_id": sid, "remove": ["beta", "absent"]})
    assert answer["tags"] == ["alpha", "zeta"]
    assert _tags(world, sid) == ["alpha", "zeta"]


async def test_tag_document_replaces_and_clears(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", ["alpha", "beta"])

    answer = await _ok(world, "tag_document", {"source_id": sid, "replace": ["gamma"]})
    assert answer["tags"] == ["gamma"]

    answer = await _ok(world, "tag_document", {"source_id": sid, "replace": []})
    assert answer == {"success": True, "source_id": sid, "tags": []}
    assert _tags(world, sid) == []


async def test_tag_document_sets_and_clears_summary(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", ["alpha"])

    answer = await _ok(
        world, "tag_document", {"source_id": sid, "add": ["beta"], "summary": "  A short note. "}
    )
    assert answer == {"success": True, "source_id": sid, "tags": ["alpha", "beta"]}
    row = world.storage.get_source(sid, "test")
    assert row["summary"] == "A short note."

    await _ok(world, "tag_document", {"source_id": sid, "summary": ""})
    assert world.storage.get_source(sid, "test")["summary"] is None
    assert _tags(world, sid) == ["alpha", "beta"]


async def test_tag_document_refuses_replace_with_add(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", ["alpha"])

    error = await _refused(
        world, "tag_document", {"source_id": sid, "replace": ["beta"], "add": ["gamma"]}
    )

    assert error == "replace sets the whole list; it cannot be combined with add or remove"
    assert _tags(world, sid) == ["alpha"]


async def test_tag_document_refuses_an_unknown_id(world: SimpleNamespace) -> None:
    error = await _refused(world, "tag_document", {"source_id": "nope", "add": ["alpha"]})
    assert error == 'No document has the id "nope".'


async def test_tag_document_refuses_a_bad_tag(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", ["alpha"])

    error = await _refused(world, "tag_document", {"source_id": sid, "add": ["Work Docs"]})

    assert error == BAD_TAG
    assert _tags(world, sid) == ["alpha"]


async def test_tag_document_refuses_a_33rd_tag(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", [f"t{i}" for i in range(32)])

    error = await _refused(world, "tag_document", {"source_id": sid, "add": ["one-more"]})

    assert error == "a document can carry at most 32 tags; this would give it 33"
    assert len(_tags(world, sid)) == 32


async def test_tag_document_summary_refused_changes_no_tag(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", ["alpha"])
    too_long = "x" * (world.engine.settings.source_processing.summary_max_chars + 1)

    error = await _refused(
        world, "tag_document", {"source_id": sid, "add": ["beta"], "summary": too_long}
    )

    assert "summary is too long" in error
    assert _tags(world, sid) == ["alpha"]


def test_tag_document_is_not_served_in_read_mode() -> None:
    read = {t.name for t in get_tools("read")}
    assert "tag_document" not in read
    assert "list_documents" in read


# -------------------------------------------------------------- list_documents


async def test_list_documents_any_and_all(world: SimpleNamespace) -> None:
    a = await _doc(world, "doc-a", ["alpha"])
    b = await _doc(world, "doc-b", ["alpha", "beta"])
    c = await _doc(world, "doc-c", ["beta"])

    any_ = await _ok(world, "list_documents", {"tags": ["alpha", "beta"]})
    assert {d["source_id"] for d in any_["documents"]} == {a, b, c}
    assert any_["total"] == 3

    all_ = await _ok(world, "list_documents", {"tags": ["ALPHA", "beta"], "match": "all"})
    assert [d["source_id"] for d in all_["documents"]] == [b]
    assert all_["total"] == 1


async def test_list_documents_without_tags_lists_every_document_but_memories(
    world: SimpleNamespace,
) -> None:
    a = await _doc(world, "doc-a", ["alpha"])
    b = await _doc(world, "doc-b")
    await store_pending(world.engine, "Pierre Bezukhov moved to Lyon.")

    answer = await _ok(world, "list_documents", {})

    assert [d["source_id"] for d in answer["documents"]] == [b, a]  # newest first
    assert answer["total"] == 2


async def test_list_documents_pages_and_counts(world: SimpleNamespace) -> None:
    ids = [await _doc(world, f"doc-{n}") for n in "abcde"]
    newest_first = list(reversed(ids))

    page = await _ok(world, "list_documents", {"limit": 2, "offset": 1})

    assert [d["source_id"] for d in page["documents"]] == newest_first[1:3]
    assert page["total"] == 5


async def test_list_documents_reply_shape(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", ["beta", "alpha"])

    [doc] = (await _ok(world, "list_documents", {}))["documents"]

    assert set(doc) == {"source_id", "title", "summary", "tags", "added"}
    assert doc["source_id"] == sid
    assert doc["title"]
    assert doc["summary"] is None
    assert doc["tags"] == ["alpha", "beta"]
    assert datetime.fromisoformat(doc["added"]).tzinfo is not None


async def test_list_documents_refuses_a_page_over_the_maximum(world: SimpleNamespace) -> None:
    most = world.engine.settings.pagination.max_page_size

    error = await _refused(world, "list_documents", {"limit": most + 1})

    assert error == f"limit is at most {most}, not {most + 1}"


async def test_list_documents_refuses_a_bad_tag_and_a_bad_match(world: SimpleNamespace) -> None:
    assert await _refused(world, "list_documents", {"tags": ["Work Docs"]}) == BAD_TAG
    # The schema's enum refuses a bad match before the handler runs (the MCP SDK's check).
    text, is_error = await call_raw(
        world.server, "list_documents", {"tags": ["alpha"], "match": "some"}
    )
    assert is_error
    assert "'some' is not one of ['any', 'all']" in text


async def test_list_documents_makes_no_model_call(
    world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _doc(world, "doc-a", ["alpha"])

    def _no_model(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("list_documents called a model")

    from chaoscypher_core.adapters.llm.provider import LLMProvider
    from chaoscypher_core.services.sources import summaries

    monkeypatch.setattr(LLMProvider, "chat", _no_model)
    monkeypatch.setattr(summaries, "write_summary", _no_model)

    answer = await _ok(world, "list_documents", {"tags": ["alpha"]})
    assert answer["total"] == 1


async def test_the_read_mode_server_routes_list_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = build_world(tmp_path, monkeypatch, s=settings(mode="read"))
    try:
        sid = await _doc(w, "doc-a", ["alpha"])
        names = {t.name for t in await list_tools(w.server)}
        assert "list_documents" in names and "tag_document" not in names

        answer = await _ok(w, "list_documents", {"tags": ["alpha"]})
        assert [d["source_id"] for d in answer["documents"]] == [sid]

        refused, _ = await call_raw(w.server, "tag_document", {"source_id": sid})
        assert "NOT_AUTHORIZED" in refused
        assert _tags(w, sid) == ["alpha"]
    finally:
        w.engine.close()


# ---------------------------------------------------------------- add_document


async def test_add_document_with_tags_answers_them(world: SimpleNamespace) -> None:
    path = _sandbox_file(world, "doc-a.txt", "Doc A explains alpha in some words.")

    answer = await _ok(
        world,
        "add_document",
        {
            "file_path": path,
            "tags": ["Beta", "alpha"],
            "summary": "About alpha.",
            "auto_confirm": True,
        },
    )

    assert answer["tags"] == ["alpha", "beta"]
    assert _tags(world, answer["source_id"]) == ["alpha", "beta"]
    assert world.storage.get_source(answer["source_id"], "test")["summary"] == "About alpha."


async def test_add_document_re_add_appends_or_replaces(world: SimpleNamespace) -> None:
    path = _sandbox_file(world, "doc-a.txt", "Doc A explains alpha in some words.")
    first = await _ok(world, "add_document", {"file_path": path, "tags": ["alpha"]})
    sid = first["source_id"]

    again = await _ok(
        world, "add_document", {"file_path": path, "tags": ["beta"], "skip_duplicates": True}
    )
    assert again["source_id"] == sid
    assert again["tags"] == ["alpha", "beta"]

    replaced = await _ok(
        world,
        "add_document",
        {"file_path": path, "tags": ["gamma"], "replace_tags": True, "skip_duplicates": True},
    )
    assert replaced["source_id"] == sid
    assert replaced["tags"] == ["gamma"]
    assert _tags(world, sid) == ["gamma"]


async def test_bad_tag_is_refused_before_anything_is_stored(world: SimpleNamespace) -> None:
    path = _sandbox_file(world, "doc-a.txt", "Doc A explains alpha in some words.")
    before = world.storage.count_sources(database_name="test")

    answer, _ = await _json(world, "add_document", {"file_path": path, "tags": ["Work Docs"]})

    assert answer == {"success": False, "error_code": "INVALID_TAGS", "error": BAD_TAG}
    assert world.storage.count_sources(database_name="test") == before


async def test_bad_summary_is_refused_before_anything_is_stored(world: SimpleNamespace) -> None:
    path = _sandbox_file(world, "doc-a.txt", "Doc A explains alpha in some words.")
    before = world.storage.count_sources(database_name="test")
    too_long = "x" * (world.engine.settings.source_processing.summary_max_chars + 1)

    answer, _ = await _json(world, "add_document", {"file_path": path, "summary": too_long})

    assert answer["success"] is False and answer["error_code"] == "INVALID_SUMMARY"
    assert "summary is too long" in answer["error"]
    assert world.storage.count_sources(database_name="test") == before


async def test_add_document_without_waiting_answers_the_requested_tags(
    world: SimpleNamespace,
) -> None:
    path = _sandbox_file(world, "doc-a.txt", "Doc A explains alpha in some words.")

    answer = await _ok(
        world, "add_document", {"file_path": path, "tags": ["Beta", "alpha"], "wait": False}
    )

    assert answer["tags"] == ["alpha", "beta"]  # sorted, like every reply


async def test_remove_document_drops_its_tags(world: SimpleNamespace) -> None:
    sid = await _doc(world, "doc-a", ["alpha"])
    keep = await _doc(world, "doc-b", ["alpha"])

    await _ok(world, "remove_document", {"source_id": sid})

    assert world.storage.get_source_tags(sid) == []
    answer = await _ok(world, "list_documents", {"tags": ["alpha"]})
    assert [d["source_id"] for d in answer["documents"]] == [keep]


# ------------------------------------------------------------- the two searches


async def test_search_chunks_takes_a_tag_scope(world: SimpleNamespace) -> None:
    await _doc(world, "doc-a", ["alpha"])
    b = await _doc(world, "doc-b", ["beta"])

    answer = await _ok(world, "search_chunks", {"query": "document", "tags": ["beta"]})

    assert answer["chunks"]
    assert {c["source_id"] for c in answer["chunks"]} == {b}

    none = await _ok(world, "search_chunks", {"query": "document", "tags": ["gamma"]})
    assert none["chunks"] == []

    error = await _refused(world, "search_chunks", {"query": "document", "tags": ["Work Docs"]})
    assert error == BAD_TAG


async def test_graphrag_search_takes_a_tag_scope(world: SimpleNamespace) -> None:
    a = await _doc(world, "doc-a", ["alpha"])
    await _doc(world, "doc-b", ["beta"])

    scoped = await _ok(world, "graphrag_search", {"query": "document", "tags": ["alpha"]})
    assert scoped["chunks"]
    assert {c["source_id"] for c in scoped["chunks"]} == {a}

    empty = await _ok(
        world, "graphrag_search", {"query": "document", "tags": ["alpha", "gamma"], "match": "all"}
    )
    assert empty["chunks"] == []
    assert empty["retrieval_stats"]["scope_empty"] is True

    assert (
        await _refused(world, "graphrag_search", {"query": "x", "tags": ["Work Docs"]}) == BAD_TAG
    )
