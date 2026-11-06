# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chat's model-chosen ``tags`` narrow the chat's own source scope; they never widen it.

A chat's scope (``chat --tag``, ``tag_ids``, ``source_ids``) reaches the tool
handlers as ``source_ids``, injected by ``ToolExecutorService._apply_scope``
over whatever the model sent. ``tags`` and ``match`` from the model then
intersect with it: ``search_chunks`` hands both to the search (one SQL scope),
``graphrag_search`` resolves the tags with ``tag_source_scope`` (strict:
``attested_only``). Without a chat scope the tags alone are the scope. Tags
matching nothing answer nothing, never an unscoped search. An invalid tag is
a tool error in the rule's words.

The searches run against a real SQLite search repository, so the tag lookup
and the intersection are the ones the app runs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel

from chaoscypher_core.adapters.sqlite.engine import evict_engine, get_engine
from chaoscypher_core.adapters.sqlite.models import SourceRow
from chaoscypher_core.adapters.sqlite.safe_session import SafeSession
from chaoscypher_core.services.workflows.tools.engine.executor import ToolExecutorService
from tests.fakes.graphrag_world import make_chunk, walk_handler
from tests.unit.adapters.sqlite.repos.conftest import open_search_repo


LAYOUT: dict[str, tuple[str, ...]] = {
    "doc-harbour": ("harbour",),
    "doc-ledger": ("harbour", "ledger"),
    "doc-orchard": ("orchard",),
    "doc-plain": (),
}
"""Each invented document and its tags; every one holds one ``lantern`` chunk."""


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Path]:
    """A fresh app.db holding :data:`LAYOUT`; its cached engine is evicted afterwards."""
    path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(path), checkfirst=True)
    for sid, tags in LAYOUT.items():
        with SafeSession(get_engine(path)) as session:
            session.add(SourceRow(id=sid, database_name="db", filename=f"{sid}.txt", filepath=sid))
            session.commit()
        with sqlite3.connect(path) as con:
            for name in tags:
                con.execute(
                    "INSERT OR IGNORE INTO source_tags (id, database_name, name, created_at) "
                    "VALUES (?, 'db', ?, '2026-11-05')",
                    (f"tag-{name}", name),
                )
                con.execute(
                    "INSERT INTO source_tag_assignments (id, source_id, tag_id, database_name, "
                    "assigned_at) VALUES (?, ?, ?, 'db', '2026-11-05')",
                    (f"as-{sid}-{name}", sid, f"tag-{name}"),
                )
            con.execute(
                "INSERT INTO document_chunks (id, database_name, source_id, chunk_index, "
                "content, status, created_at, citation_offset_method) "
                "VALUES (?, 'db', ?, 0, ?, 'indexed', '2026-11-05', 'none')",
                (f"{sid}-c", sid, f"lantern notes of {sid}"),
            )
    yield path
    evict_engine(path)


def _chat_executor(db: Path, session_source_ids: list[str] | None) -> ToolExecutorService:
    """An executor as chat builds it (``streaming/chat/utils.py``): its scope is the chat's."""
    indexing = MagicMock()
    indexing.get_chunks_by_ids_batch.side_effect = lambda ids: [
        {"id": cid, "content": f"lantern notes of {cid[:-2]}", "source_id": cid[:-2]} for cid in ids
    ]
    indexing.get_source_filenames_by_ids.return_value = {}
    indexing.get_source_titles_by_ids.return_value = {}
    executor = ToolExecutorService(
        graph_repository=MagicMock(),
        search_repository=open_search_repo(db),
        indexing_repository=indexing,
        scope={"source_ids": session_source_ids} if session_source_ids else None,
    )
    executor.node_handlers.search_settings = None
    return executor


def _sources(result: dict[str, Any]) -> set[str]:
    return {chunk["source_id"] for chunk in result["chunks"]}


# ------------------------------------------------------------------ #
#  search_chunks
# ------------------------------------------------------------------ #


async def test_without_a_chat_scope_the_models_tags_are_the_scope(db: Path) -> None:
    executor = _chat_executor(db, None)
    unscoped = await executor.execute_tool("search_chunks", {"query": "lantern"})
    assert _sources(unscoped) == set(LAYOUT)

    tagged = await executor.execute_tool("search_chunks", {"query": "lantern", "tags": ["harbour"]})
    assert _sources(tagged) == {"doc-harbour", "doc-ledger"}
    both = await executor.execute_tool(
        "search_chunks", {"query": "lantern", "tags": ["harbour", "ledger"], "match": "all"}
    )
    assert _sources(both) == {"doc-ledger"}


async def test_under_a_chat_scope_the_models_tags_intersect_it(db: Path) -> None:
    executor = _chat_executor(db, ["doc-ledger", "doc-orchard", "doc-plain"])
    result = await executor.execute_tool(
        "search_chunks", {"query": "lantern", "tags": ["harbour", "orchard"]}
    )
    # doc-harbour carries harbour but is outside the chat: never added
    assert _sources(result) == {"doc-ledger", "doc-orchard"}


async def test_the_chat_scope_wins_over_the_models_source_ids(db: Path) -> None:
    executor = _chat_executor(db, ["doc-orchard"])
    result = await executor.execute_tool(
        "search_chunks",
        {"query": "lantern", "source_ids": ["doc-harbour", "doc-orchard"], "tags": ["harbour"]},
    )
    assert result["success"] is True
    assert result["chunks"] == []


async def test_tags_outside_the_chat_scope_find_nothing(db: Path) -> None:
    executor = _chat_executor(db, ["doc-orchard"])
    result = await executor.execute_tool("search_chunks", {"query": "lantern", "tags": ["harbour"]})
    assert result["success"] is True
    assert result["chunks"] == []


@pytest.mark.parametrize("session", [None, ["doc-harbour", "doc-plain"]])
async def test_tags_matching_no_document_find_nothing(db: Path, session: list[str] | None) -> None:
    executor = _chat_executor(db, session)
    result = await executor.execute_tool("search_chunks", {"query": "lantern", "tags": ["nobody"]})
    assert result["success"] is True
    assert result["chunks"] == []


async def test_a_chat_scoped_to_deleted_documents_finds_nothing(db: Path) -> None:
    """A tag-scoped chat stores the tagged source ids; once those documents are deleted the
    ids remain, and the chat searches nothing rather than everything.
    """
    executor = _chat_executor(db, ["doc-deleted"])
    for args in ({"query": "lantern"}, {"query": "lantern", "tags": ["harbour"]}):
        result = await executor.execute_tool("search_chunks", args)
        assert result["success"] is True
        assert result["chunks"] == []


async def test_an_invalid_tag_is_a_tool_error_in_the_rules_words(db: Path) -> None:
    executor = _chat_executor(db, ["doc-harbour"])
    result = await executor.execute_tool(
        "search_chunks", {"query": "lantern", "tags": ["Harbour Notes"]}
    )
    assert result["success"] is False
    assert result["error"].startswith('tag "Harbour Notes" is not allowed: use a-z, 0-9')
    bad_match = await executor.execute_tool(
        "search_chunks", {"query": "lantern", "tags": ["harbour"], "match": "most"}
    )
    assert bad_match == {"success": False, "error": 'match is "any" or "all", not "most"'}


# ------------------------------------------------------------------ #
#  graphrag_search
# ------------------------------------------------------------------ #

QUESTION = "Who keeps the harbour lantern?"


def _graphrag_executor(db: Path, session_source_ids: list[str] | None) -> tuple[Any, Any]:
    """A chat executor whose ``graphrag_search`` is the walk fixture's, its tag lookup the real one."""
    executor = _chat_executor(db, session_source_ids)
    handler = walk_handler(router="rules", chunks=[make_chunk("c1", source_id="doc-ledger")])
    search: Any = handler.search  # the walk fixture's MagicMock
    search.tagged_source_ids.side_effect = open_search_repo(db).tagged_source_ids
    executor._tool_handlers["graphrag_search"] = handler.graphrag_search
    return executor, handler


def _seed_scope(handler: Any) -> tuple[list[str], bool]:
    kwargs = handler.search.keyword_search.call_args.kwargs
    return sorted(kwargs["source_ids"]), kwargs["attested_only"]


async def test_graphrag_without_a_chat_scope_tags_give_a_strict_tag_scope(db: Path) -> None:
    executor, handler = _graphrag_executor(db, None)
    await executor.execute_tool("graphrag_search", {"query": QUESTION, "tags": ["harbour"]})
    assert sorted(handler.search.hybrid_search.call_args.kwargs["source_ids"]) == [
        "doc-harbour",
        "doc-ledger",
    ]
    assert _seed_scope(handler) == (["doc-harbour", "doc-ledger"], True)


async def test_graphrag_under_a_chat_scope_tags_intersect_it(db: Path) -> None:
    executor, handler = _graphrag_executor(db, ["doc-ledger", "doc-plain"])
    await executor.execute_tool("graphrag_search", {"query": QUESTION, "tags": ["harbour"]})
    assert handler.search.hybrid_search.call_args.kwargs["source_ids"] == ["doc-ledger"]
    assert _seed_scope(handler) == (["doc-ledger"], True)


async def test_graphrag_chat_scope_alone_stays_a_plain_source_scope(db: Path) -> None:
    executor, handler = _graphrag_executor(db, ["doc-ledger", "doc-plain"])
    await executor.execute_tool("graphrag_search", {"query": QUESTION})
    assert _seed_scope(handler) == (["doc-ledger", "doc-plain"], False)
    handler.search.tagged_source_ids.assert_not_called()


@pytest.mark.parametrize(
    ("session", "tags"),
    [(None, ["nobody"]), (["doc-orchard"], ["harbour"]), (["doc-plain"], ["nobody"])],
)
async def test_graphrag_tags_holding_no_chat_document_run_no_retrieval(
    db: Path, session: list[str] | None, tags: list[str]
) -> None:
    executor, handler = _graphrag_executor(db, session)
    result = await executor.execute_tool("graphrag_search", {"query": QUESTION, "tags": tags})
    handler.search.hybrid_search.assert_not_called()
    handler.search.keyword_search.assert_not_called()
    handler.search.vector_search.assert_not_called()
    assert result["chunks"] == []
    assert result["retrieval_stats"]["scope_empty"] is True


async def test_graphrag_invalid_tag_is_a_tool_error_in_the_rules_words(db: Path) -> None:
    executor, handler = _graphrag_executor(db, ["doc-ledger"])
    result = await executor.execute_tool(
        "graphrag_search", {"query": QUESTION, "tags": ["Harbour Notes"]}
    )
    assert result["success"] is False
    assert result["error"].startswith('tag "Harbour Notes" is not allowed: use a-z, 0-9')
    handler.search.hybrid_search.assert_not_called()


# ------------------------------------------------------------------ #
#  What the chat model is offered
# ------------------------------------------------------------------ #


@pytest.mark.parametrize("tool", ["graphrag_search", "search_chunks"])
def test_chat_offers_the_mcp_tools_tag_scope_word_for_word(tool: str) -> None:
    from chaoscypher_core.mcp.tools import TOOL_DEFINITIONS
    from chaoscypher_core.services.workflows.tools.engine.schema_registry import (
        TOOL_SCHEMAS,
        tag_scope_schema,
    )

    chat = TOOL_SCHEMAS[tool]["function"]
    mcp = next(t for t in TOOL_DEFINITIONS if t.name == tool).input_schema["properties"]
    for name in ("tags", "match"):
        assert chat["parameters"]["properties"][name] == mcp[name] == tag_scope_schema()[name]
    assert "never add others" in chat["description"]


def test_the_chat_prompt_tells_the_model_tags_exist_and_only_narrow() -> None:
    from chaoscypher_core.services.chat.engine.constants import ESSENTIAL_TOOL_NAMES, SYSTEM_PROMPT

    assert {"graphrag_search", "search_chunks"} <= set(ESSENTIAL_TOOL_NAMES)
    assert "graphrag_search and search_chunks take tags" in SYSTEM_PROMPT
    assert "Tags only narrow" in SYSTEM_PROMPT


def test_the_tag_rule_sentence_states_the_policy_limits() -> None:
    from chaoscypher_core.policy import TAG_NAME_MAX_LENGTH, TAGS_PER_SOURCE_MAX
    from chaoscypher_core.services.workflows.tools.engine.schema_registry import TAG_RULE

    assert f"up to {TAG_NAME_MAX_LENGTH} characters" in TAG_RULE
    assert f"at most {TAGS_PER_SOURCE_MAX} per document" in TAG_RULE
