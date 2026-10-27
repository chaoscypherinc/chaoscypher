# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The commit's post-commit identity pass: merges on the real graph, re-embeds the merged node, never fails the commit."""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import SQLModel
from structlog.testing import capture_logs

from chaoscypher_core.adapters.embedding import factory
from chaoscypher_core.adapters.sqlite.adapter import SqliteAdapter
from chaoscypher_core.adapters.sqlite.engine import get_engine
from chaoscypher_core.adapters.sqlite.repos.graph.sqlite_repository import GraphRepository
from chaoscypher_core.models import NodeCreate, TemplateCreate
from chaoscypher_core.services.sources.engine.commit.service import SourceCommitService
from chaoscypher_core.settings import EngineSettings


@pytest.fixture
def repo(tmp_path: Path) -> Generator[GraphRepository]:
    db_path = tmp_path / "app.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    repo = GraphRepository(adapter.session, database_name="test")
    repo.create_template(
        TemplateCreate(name="character", template_type="node"), custom_id="character"
    )
    for nid, label in (
        ("anna", "Anna Pávlovna Schérer"),
        ("mlle", "Mlle Schérer"),
        ("pierre", "Pierre"),
    ):
        repo.create_node(
            NodeCreate(
                template_id="character",
                label=label,
                entity_type="character",
                properties={"aliases": []},
            ),
            custom_id=nid,
        )
    yield repo
    adapter.disconnect()


def _service(
    repo: GraphRepository, provider: object, source_row: dict[str, object] | None = None
) -> SourceCommitService:
    settings = EngineSettings()
    settings.graph.resolve_identities = True
    sources = MagicMock()
    sources.get_source.return_value = (
        {"forced_domain": "literary"} if source_row is None else source_row
    )
    return SourceCommitService(
        graph_repository=repo,
        source_repository=MagicMock(),
        sources_repository=sources,
        indexing_repository=MagicMock(),
        search_repository=MagicMock(),
        settings=settings,
        embedding_provider=provider,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_the_pass_merges_and_reembeds_the_kept_node(repo: GraphRepository) -> None:
    provider = SimpleNamespace(
        batch_embed=AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]]))
    )
    await _service(repo, provider)._resolve_identities("file-1")
    assert repo.get_node("mlle") is None
    anna = repo.get_node("anna", include_embedding=True)
    assert anna is not None
    assert anna.properties["aliases"] == ["Mlle Schérer"]
    (texts,), _ = provider.batch_embed.call_args
    assert texts == ["Anna Pávlovna Schérer | Mlle Schérer"] or texts[0].startswith(
        "Anna Pávlovna Schérer"
    )
    assert anna.embedding == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_a_failing_pass_does_not_fail_the_commit(repo: GraphRepository) -> None:
    provider = SimpleNamespace(batch_embed=AsyncMock(side_effect=RuntimeError("embedder down")))
    await _service(repo, provider)._resolve_identities("file-1")  # logs, returns
    assert repo.get_node("mlle") is None  # the merge itself stood


@pytest.mark.asyncio
async def test_a_pass_failing_after_a_merge_committed_still_reembeds_and_unindexes_it(
    repo: GraphRepository,
) -> None:
    """A merge group commits on its own: a later step failing leaves it applied (#795 follow-up).

    Its kept node is still re-embedded and the merged-away node still leaves the
    search index; until then the pass returned on the failure and both stayed stale.
    """
    from chaoscypher_core.services.graph.resolution import apply as apply_mod

    provider = SimpleNamespace(
        batch_embed=AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]]))
    )
    service = _service(repo, provider)
    with patch.object(apply_mod, "_apply_flags", side_effect=RuntimeError("boom")):
        await service._resolve_identities("file-1")  # logs, returns
    assert repo.get_node("mlle") is None  # the merge group committed
    service.search_repository.delete_nodes_batch.assert_called_once_with(["mlle"])  # type: ignore[attr-defined]
    anna = repo.get_node("anna", include_embedding=True)
    assert anna is not None and anna.embedding == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_the_pass_is_on_by_default() -> None:
    assert EngineSettings().graph.resolve_identities is True  # since 2026-10-06


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "merged"),
    [
        ({"forced_domain": "literary"}, True),
        ({"extraction_domain": "literary"}, True),
        # no domain: "Mlle" is a title of the universal floor too (audit wave 7) and says a
        # woman, but without the literary surname forms nothing says "Anna Pávlovna Schérer"
        # is one: a woman's title on a family name fits no unmarked name
        ({}, False),
    ],
)
async def test_the_pass_drops_the_title_words_of_the_sources_domain(
    repo: GraphRepository, row: dict[str, object], merged: bool
) -> None:
    """The pass runs under the title words the source's deduplication used (audit W2-1)."""
    provider = SimpleNamespace(
        batch_embed=AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2, 0.3]]))
    )
    await _service(repo, provider, row)._resolve_identities("file-1")
    assert (repo.get_node("mlle") is None) is merged


def _graph_of_two_sources(tmp_path: Path, domains: dict[str, str]) -> tuple[Any, GraphRepository]:
    """A graph whose nodes come from two real source rows, one per ``domains`` entry (id -> domain)."""
    db_path = tmp_path / "two.db"
    SQLModel.metadata.create_all(get_engine(str(db_path)), checkfirst=True)
    adapter = SqliteAdapter(str(db_path), database_name="test")
    adapter.connect()
    for source_id in domains:
        adapter.create_source(
            {
                "id": source_id,
                "database_name": "test",
                "filename": f"{source_id}.txt",
                "filepath": f"/tmp/{source_id}.txt",
                "file_type": "text",
                "file_size": 1,
                "content_hash": f"hash-{source_id}",
                "status": "extracted",
            }
        )
    with sqlite3.connect(str(db_path)) as conn:  # the domain the source extracted under
        conn.executemany(
            "UPDATE sources SET forced_domain = ? WHERE id = ?",
            [(domain, source_id) for source_id, domain in domains.items()],
        )
    repo = GraphRepository(adapter.session, database_name="test")
    repo.create_template(
        TemplateCreate(name="character", template_type="node"), custom_id="character"
    )
    for nid, label, source_id in (
        ("anna", "Anna Pávlovna Schérer", "book"),
        ("mlle", "Mlle Schérer", "book"),
        ("pierre", "Pierre", "book"),
        ("andrew", "Prince Andrew Bolkónski", "book"),
        ("andrew-short", "Prince Andrew", "book"),
        ("report", "Pierre Bezúkhov", "report"),
    ):
        repo.create_node(
            NodeCreate(
                template_id="character",
                label=label,
                entity_type="character",
                properties={"aliases": []},
                source_id=source_id,
            ),
            custom_id=nid,
        )
    return adapter, repo


def _real_service(adapter: Any, repo: GraphRepository, provider: object) -> SourceCommitService:
    """The commit service over the real source rows."""
    settings = EngineSettings()
    settings.graph.resolve_identities = True
    settings.current_database = "test"
    return SourceCommitService(
        graph_repository=repo,
        source_repository=MagicMock(),
        sources_repository=adapter,
        indexing_repository=MagicMock(),
        search_repository=MagicMock(),
        settings=settings,
        embedding_provider=provider,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_mixed_packs_union_their_conventions(tmp_path: Path) -> None:
    """A historical report committed beside a literary book: the pass runs under both packs' tables.

    Until 2026-10-07 a graph whose sources chose different domains was
    skipped (``identity_resolution_skipped_mixed_domains``). A pack only adds
    to the floor, so the union of the sources' packs is one coherent set of
    tables (spec 2026-10-07 § 6).
    """
    from chaoscypher_core.services.graph import resolution
    from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
    from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack

    adapter, repo = _graph_of_two_sources(tmp_path, {"book": "literary", "report": "historical"})
    provider = SimpleNamespace(
        batch_embed=AsyncMock(
            side_effect=lambda texts: SimpleNamespace(embeddings=[[0.1, 0.2]] * len(texts))
        )
    )
    seen: list[Any] = []
    real = resolution.resolve_graph

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["conventions"])
        return real(*args, **kwargs)

    try:
        service = _real_service(adapter, repo, provider)
        assert service._graph_packs("report") == ("historical", "literary")
        with capture_logs() as logs, patch.object(resolution, "resolve_graph", spy):
            await service._resolve_identities("report", scope={"report"})
        events = [e["event"] for e in logs]
        assert "identity_resolution_skipped_mixed_domains" not in events
        (applied,) = [e for e in logs if e["event"] == "identity_resolution_applied"]
        assert applied["packs"] == ["historical", "literary"]
        registry = get_domain_registry(service.settings)
        (conventions,) = seen
        assert (
            conventions == DomainStack.resolve(registry, ["historical", "literary"]).conventions()
        )
        for pack in ("historical", "literary"):
            own = {t.lower() for t in registry.get_domain(pack).get_title_words()}
            assert own <= conventions.titles, pack
        assert {"character", "historical figure"} <= (conventions.person_types or frozenset())
        assert repo.get_node("pierre") is None  # the pass ran: "Pierre" joined "Pierre Bezúkhov"
        assert repo.get_node("report") is not None
    finally:
        adapter.disconnect()


@pytest.mark.asyncio
async def test_the_pass_keeps_to_the_committed_nodes_and_their_namesakes(tmp_path: Path) -> None:
    """One domain: the committed node joins its namesake; an unrelated split elsewhere is left alone."""
    adapter, repo = _graph_of_two_sources(tmp_path, {"book": "literary", "report": "literary"})
    provider = SimpleNamespace(
        batch_embed=AsyncMock(return_value=SimpleNamespace(embeddings=[[0.1, 0.2]]))
    )
    try:
        await _real_service(adapter, repo, provider)._resolve_identities("report", scope={"report"})
        assert repo.get_node("pierre") is None  # "Pierre" shares a word with the report's node
        assert repo.get_node("report") is not None
        # the hostess and Prince Andrew share nothing with the report: not this pass's business
        assert repo.get_node("mlle") is not None
        assert repo.get_node("andrew-short") is not None
    finally:
        adapter.disconnect()


def _raise(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("an embedding provider was built behind the vector switch")


@pytest.mark.asyncio
async def test_the_pass_with_no_provider_reindexes_the_kept_node_for_keywords(
    repo: GraphRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(factory, "create_embedding_provider", _raise)
    service = _service(repo, None)
    service.settings.search.enable_vector_search = False
    with capture_logs() as logs:
        await service._resolve_identities("file-1")
    assert repo.get_node("mlle") is None
    (batch,), _ = service.search_repository.index_nodes_batch.call_args  # type: ignore[attr-defined]
    assert [n.id for n in batch] == ["anna"]
    assert "Mlle Schérer" in batch[0].properties["aliases"]
    assert not [e for e in logs if e["event"] == "identity_resolution_reembed_failed_non_fatal"]
    assert [e for e in logs if e["event"] == "nodes_reindexed_for_keywords_only"]


@pytest.mark.asyncio
async def test_a_commit_service_with_vectors_off_builds_no_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(factory, "create_embedding_provider", _raise)
    settings = EngineSettings()
    settings.search.enable_vector_search = False
    graph = MagicMock()
    search = MagicMock()
    search.has_pending_reindex = True
    service = SourceCommitService(
        graph_repository=graph,
        source_repository=MagicMock(),
        sources_repository=MagicMock(),
        indexing_repository=MagicMock(),
        search_repository=search,
        settings=settings,
        embedding_provider=None,
    )
    assert service._get_embedding_provider() is None
    with capture_logs() as logs:
        await service._embed_created_templates(["t1"])
        await service._flush_pending_reindex()
    graph.get_template.assert_not_called()
    search.index_template.assert_not_called()
    search.flush_reindex_with_service.assert_not_called()
    events = [e["event"] for e in logs]
    assert "commit_template_embedding_skipped_keyword_only" in events
    assert "reindex_flush_skipped_keyword_only" in events
