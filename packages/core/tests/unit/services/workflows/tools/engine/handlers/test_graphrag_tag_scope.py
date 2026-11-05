# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""``graphrag_search`` scoped by document tags: the tagged sources become the source scope.

A tag set that matches no document runs no retrieval and returns no chunks;
it never widens to the whole graph. Every chunk carries ``source_title``.
"""

from __future__ import annotations

from tests.fakes.graphrag_world import make_chunk, walk_handler


QUESTION = "How is Lise connected to Prince Andrew?"


async def test_tags_become_the_source_scope() -> None:
    h = walk_handler(router="rules", chunks=[make_chunk("c1", source_id="doc-a")])
    h.search.tagged_source_ids.return_value = ["doc-a", "doc-b"]
    await h.graphrag_search(QUESTION, tags=["Ref"], match="all")
    h.search.tagged_source_ids.assert_called_once()
    tags, match = h.search.tagged_source_ids.call_args.args
    assert (list(tags), match.value) == (["ref"], "all")
    assert sorted(h.search.hybrid_search.call_args.kwargs["source_ids"]) == ["doc-a", "doc-b"]

    # with a source list too, the scope is their intersection
    h.search.hybrid_search.reset_mock()
    await h.graphrag_search(QUESTION, source_ids=["doc-b", "doc-c"], tags=["ref"])
    assert h.search.hybrid_search.call_args.kwargs["source_ids"] == ["doc-b"]


async def test_graphrag_tags_matching_nothing_runs_no_retrieval() -> None:
    h = walk_handler(router="rules", chunks=[make_chunk("c1", source_id="doc-a")])
    h.search.tagged_source_ids.return_value = []
    result = await h.graphrag_search(QUESTION, tags=["nobody"])
    h.search.hybrid_search.assert_not_called()
    h.search.keyword_search.assert_not_called()
    h.search.vector_search.assert_not_called()
    assert result["success"] is True
    assert result["chunks"] == []
    assert not result["graph_context"]
    assert result["context_text"] == ""
    assert result["retrieval_stats"]["scope_empty"] is True
    assert result["query"] == QUESTION
    assert "shape" in result


async def test_graphrag_tags_disjoint_from_source_ids_run_no_retrieval() -> None:
    h = walk_handler(router="rules", chunks=[make_chunk("c1", source_id="doc-a")])
    h.search.tagged_source_ids.return_value = ["doc-a"]
    result = await h.graphrag_search(QUESTION, source_ids=["doc-b"], tags=["ref"])
    h.search.hybrid_search.assert_not_called()
    assert result["chunks"] == []
    assert result["retrieval_stats"]["scope_empty"] is True


async def test_graphrag_bad_tag_refused_in_plain_words() -> None:
    h = walk_handler(router="rules")
    result = await h.graphrag_search(QUESTION, tags=["Work Docs"])
    assert result["success"] is False
    assert result["error"].startswith('tag "Work Docs" is not allowed')
    h.search.hybrid_search.assert_not_called()


async def test_graphrag_bad_match_refused_in_plain_words() -> None:
    h = walk_handler(router="rules")
    result = await h.graphrag_search(QUESTION, tags=["ref"], match="some")
    assert result == {"success": False, "error": 'match is "any" or "all", not "some"'}
    h.search.tagged_source_ids.assert_not_called()
    h.search.hybrid_search.assert_not_called()


async def test_graphrag_chunks_carry_source_title() -> None:
    h = walk_handler(
        router="rules",
        chunks=[make_chunk("c1", source_id="doc-a"), make_chunk("c2", source_id="doc-b")],
    )
    h.indexing.get_source_filenames_by_ids.return_value = {
        "doc-a": "doc-a.txt",
        "doc-b": "doc-b.txt",
    }
    h.indexing.get_source_titles_by_ids.return_value = {
        "doc-a": "Alpha Notes",
        "doc-b": "doc-b.txt",
    }
    result = await h.graphrag_search(QUESTION)
    by_source = {c["source_id"]: c for c in result["chunks"]}
    assert by_source["doc-a"]["source_title"] == "Alpha Notes"
    assert by_source["doc-a"]["filename"] == "doc-a.txt"
    assert by_source["doc-b"]["source_title"] == "doc-b.txt"
