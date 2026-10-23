# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pinned retrieval: one stored context per question, served to every model."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from chaoscypher_core.benchmark.pins import (
    PinError,
    QueryVectorCache,
    RetrievalPins,
    dir_digest,
    file_sha256,
    find_pins,
    index_digest,
    new_pins,
    retrieval_fingerprint,
    router_model_record,
)
from chaoscypher_core.settings import EngineSettings


if TYPE_CHECKING:
    from pathlib import Path

EMB = "ollama/qwen3-embedding:4b"


def _pins(tmp_path: Path, retrieval: str = "graphrag", **over: Any) -> RetrievalPins:
    fields: dict[str, Any] = {
        "retrieval": retrieval,
        "embedder": EMB,
        "settings": EngineSettings(),
        "graph_sha256": "g" * 64,
        "index_digest": "i" * 16,
        "pack": "tiny",
    }
    fields.update(over)
    return new_pins(tmp_path / "pins", **fields)


def test_the_fingerprint_follows_retrieval_settings_and_ignores_the_citation_ask() -> None:
    settings = EngineSettings()
    base = retrieval_fingerprint("graphrag", EMB, settings)
    settings.graphrag.ask_for_citations = not settings.graphrag.ask_for_citations
    assert retrieval_fingerprint("graphrag", EMB, settings) == base
    settings.graphrag.max_triples += 1
    assert retrieval_fingerprint("graphrag", EMB, settings) != base
    assert retrieval_fingerprint("hybrid", EMB, EngineSettings()) != base
    assert (
        retrieval_fingerprint("graphrag", "ollama/qwen3-embedding:0.6b", EngineSettings()) != base
    )


def test_the_fingerprint_ignores_prompt_only_fields_and_profile_definitions() -> None:
    """The answer policy changes no retrieval; profiles are how levers are chosen, not levers."""
    from chaoscypher_core.settings import GraphRAGProfileRule

    settings = EngineSettings()
    base = retrieval_fingerprint("graphrag", EMB, settings)
    settings.graphrag.answer_policy = not settings.graphrag.answer_policy
    settings.graphrag.profiles = {"rich": {"entity_aliases": True}}
    settings.graphrag.profile_rules = [GraphRAGProfileRule(profile="rich", max_params_b=27)]
    assert retrieval_fingerprint("graphrag", EMB, settings) == base
    # a resolved profile moves the levers themselves, and so the fingerprint
    settings.graphrag.entity_aliases = True
    assert retrieval_fingerprint("graphrag", EMB, settings) != base


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("router", "off"),
        ("shape_levers", {"path": {"vector_reserve": 2}}),
        ("evidence_chunks", 3),
        ("max_hops", 2),
        ("aggregate_per_label", 5),
        ("question_aware_descriptions", False),
    ],
)
def test_the_fingerprint_follows_the_shape_levers(field: str, value: Any) -> None:
    """The router and the per-shape bundles decide what a question retrieves, so they are pinned."""
    settings = EngineSettings()
    base = retrieval_fingerprint("graphrag", EMB, settings)
    setattr(settings.graphrag, field, value)
    assert retrieval_fingerprint("graphrag", EMB, settings) != base


def test_the_fingerprint_ignores_where_this_machine_keeps_its_caches() -> None:
    """Two machines with different data dirs and the same retrieval settings share pins."""
    settings = EngineSettings()
    base = retrieval_fingerprint("graphrag", EMB, settings)
    settings.search.rerank_cache_dir = "/elsewhere/rerankers"
    assert retrieval_fingerprint("graphrag", EMB, settings) == base


def test_the_naive_fingerprint_does_not_move_with_graph_settings() -> None:
    settings = EngineSettings()
    base = retrieval_fingerprint("naive", EMB, settings)
    settings.graphrag.max_triples += 1
    settings.search.min_similarity_threshold = 0.1
    assert retrieval_fingerprint("naive", EMB, settings) == base


def test_a_saved_file_serves_each_question_as_stored(tmp_path: Path) -> None:
    pins = _pins(tmp_path)
    pins.put("q1", "Who?", {"chunks": [{"content": "A"}], "entities": [{"id": "n1"}]})
    pins.save()
    found = find_pins(
        tmp_path / "pins", retrieval="graphrag", embedder=EMB, settings=EngineSettings()
    )
    assert found is not None and found.path == pins.path
    assert found.header["count"] == 1
    assert found.lookup("q1", "Who?") == {"chunks": [{"content": "A"}], "entities": [{"id": "n1"}]}
    served = found.lookup("q1", "Who?")
    assert served is not None
    served["chunks"].clear()  # a caller's edit never reaches the next model
    assert found.lookup("q1", "Who?") == {"chunks": [{"content": "A"}], "entities": [{"id": "n1"}]}


def test_a_reworded_or_new_question_is_not_served(tmp_path: Path) -> None:
    pins = _pins(tmp_path)
    pins.put("q1", "Who?", {"chunks": []})
    assert pins.lookup("q1", "Who is it?") is None
    assert pins.missing([("q1", "Who?"), ("q1", "Who is it?"), ("q2", "When?")]) == ["q1", "q2"]


def test_other_settings_find_no_file(tmp_path: Path) -> None:
    _pins(tmp_path).save()
    variant = EngineSettings()
    variant.graphrag.max_triples += 1
    assert (
        find_pins(tmp_path / "pins", retrieval="graphrag", embedder=EMB, settings=variant) is None
    )


def test_check_names_every_field_built_for_another_run(tmp_path: Path) -> None:
    pins = _pins(tmp_path)
    pins.check(graph_sha256="g" * 64, embedder=EMB, retrieval="graphrag")
    with pytest.raises(PinError, match=r"graph_sha256.*embedder"):
        pins.check(graph_sha256="x" * 64, embedder="ollama/other", retrieval="graphrag")


def test_a_file_of_another_schema_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "graphrag-x.json"
    path.write_text('{"schema": 99, "pins": {}}', encoding="utf-8")
    with pytest.raises(PinError, match="not a schema-1 pin file"):
        RetrievalPins.load(path)


def test_provenance_names_the_file_and_what_built_it(tmp_path: Path) -> None:
    pins = _pins(tmp_path)
    prov = pins.provenance()
    assert prov["file"] == pins.path.name
    assert prov["graph_sha256"] == "g" * 64 and prov["index_digest"] == "i" * 16
    assert prov["fingerprint"] == retrieval_fingerprint("graphrag", EMB, EngineSettings())


@pytest.mark.asyncio
async def test_the_vector_cache_embeds_each_text_once_and_serves_the_stored_value(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    async def embed(text: str) -> Any:
        calls.append(text)

        class _Result:
            embedding = [0.1, 0.2, 1 / 3]

        return _Result()

    cache = QueryVectorCache.for_embedder(tmp_path, EMB)
    cached = cache.wrap(embed)
    first = await cached("Who?")
    second = await cached("Who?")
    assert calls == ["Who?"]
    # stored as float32 and served from the store on the first call too
    assert first == second
    assert first["embedding"][2] != 1 / 3
    assert first["embedding"][2] == pytest.approx(1 / 3, rel=1e-6)
    cache.save()
    reloaded = QueryVectorCache.for_embedder(tmp_path, EMB)

    async def never(text: str) -> Any:
        raise AssertionError(text)

    assert await reloaded.wrap(never)("Who?") == first


def test_the_vector_cache_refuses_another_embedders_file(tmp_path: Path) -> None:
    cache = QueryVectorCache.for_embedder(tmp_path, EMB)
    cache.save()
    cache.path.rename(tmp_path / "query-vectors-ollama_other.json")
    with pytest.raises(PinError, match="not 'ollama/other'"):
        QueryVectorCache.for_embedder(tmp_path, "ollama/other")


def _vec_db(path: Path, vectors: bytes) -> None:
    con = sqlite3.connect(path)
    for table in ("vec_search_chunks", "vec_search_nodes"):
        con.execute(f'CREATE TABLE "{table}_rowids" (rowid INTEGER PRIMARY KEY, id TEXT)')
        con.execute(
            f'CREATE TABLE "{table}_vector_chunks00" (rowid INTEGER PRIMARY KEY, vectors BLOB)'
        )
        con.execute(f'INSERT INTO "{table}_rowids" VALUES (1, "a")')
        con.execute(f'INSERT INTO "{table}_vector_chunks00" VALUES (1, ?)', (vectors,))
    con.commit()
    con.close()


def test_the_index_digest_moves_when_a_vector_does(tmp_path: Path) -> None:
    a, b, c = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "c.db"
    _vec_db(a, b"\x00\x01")
    _vec_db(b, b"\x00\x01")
    _vec_db(c, b"\x00\x02")
    assert index_digest(a) == index_digest(b) != index_digest(c)


def test_the_index_digest_covers_the_keyword_index(tmp_path: Path) -> None:
    """Same vectors, other node keyword rows (a resolver merge never re-indexed): another digest."""
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    for db, alias in ((a, "the hostess"), (b, "Annette")):
        _vec_db(db, b"\x00\x01")
        with sqlite3.connect(db) as con:
            con.execute(
                "CREATE TABLE fulltext_content (node_id TEXT PRIMARY KEY, label TEXT, "
                "properties TEXT, searchable_text TEXT)"
            )
            con.execute(
                "INSERT INTO fulltext_content VALUES ('anna', 'Anna', ?, 'Anna')",
                (f'{{"aliases": ["{alias}"]}}',),
            )
    assert index_digest(a) != index_digest(b)
    plain = tmp_path / "plain.db"
    _vec_db(plain, b"\x00\x01")  # a copy without the keyword tables still digests
    assert len(index_digest(plain)) == 16


def test_the_index_digest_covers_the_fts_index_over_intact_content(tmp_path: Path) -> None:
    """Same content rows, the index dropped and recreated empty or emptied: another digest (wave 4)."""

    def build(db: Path, *after: str) -> None:
        _vec_db(db, b"\x00\x01")
        with sqlite3.connect(db) as con:
            con.execute(
                "CREATE TABLE fulltext_content (node_id TEXT PRIMARY KEY, label TEXT, "
                "properties TEXT, searchable_text TEXT)"
            )
            con.execute(
                "CREATE VIRTUAL TABLE fulltext_index USING fts5(label, properties, "
                "searchable_text, content='fulltext_content', content_rowid='rowid')"
            )
            con.execute("INSERT INTO fulltext_content VALUES ('anna', 'Anna', '{}', 'Anna')")
            con.execute("INSERT INTO fulltext_index(fulltext_index) VALUES ('rebuild')")
            for statement in after:
                con.execute(statement)

    healthy, again, emptied, dropped = (tmp_path / f"{n}.db" for n in ("h", "a", "e", "d"))
    build(healthy)
    build(again)
    build(emptied, "INSERT INTO fulltext_index(fulltext_index) VALUES ('delete-all')")
    build(
        dropped,
        "DROP TABLE fulltext_index",
        "CREATE VIRTUAL TABLE fulltext_index USING fts5(label, properties, searchable_text, "
        "content='fulltext_content', content_rowid='rowid')",
    )
    assert index_digest(healthy) == index_digest(again)
    assert len({index_digest(healthy), index_digest(emptied), index_digest(dropped)}) == 2
    assert index_digest(emptied) != index_digest(healthy)


def test_digests_of_files_and_directories(tmp_path: Path) -> None:
    d = tmp_path / "idx"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "v.json").write_text("1", encoding="utf-8")
    first = dir_digest(d)
    (d / "sub" / "v.json").write_text("2", encoding="utf-8")
    assert dir_digest(d) != first
    assert len(file_sha256(d / "sub" / "v.json")) == 64


def test_the_fingerprint_follows_the_reindex_recipe(monkeypatch: pytest.MonkeyPatch) -> None:
    """A change to what the index embeds asks for new graphrag and hybrid pins, not naive ones."""
    from chaoscypher_core.benchmark import reindex

    settings = EngineSettings()
    before = {m: retrieval_fingerprint(m, EMB, settings) for m in ("graphrag", "hybrid", "naive")}
    monkeypatch.setattr(reindex, "reindex_recipe", lambda: "another-text")
    after = {m: retrieval_fingerprint(m, EMB, settings) for m in ("graphrag", "hybrid", "naive")}
    assert after["graphrag"] != before["graphrag"] and after["hybrid"] != before["hybrid"]
    assert after["naive"] == before["naive"]


# ---------------------------------------------------------------------------
# The fingerprint and the check cover the code that renders a retrieval (audit H6, C3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["graphrag", "hybrid", "naive"])
def test_a_renderer_change_moves_every_rungs_fingerprint(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Pins built under another renderer are not found; the naive rung's [S] labels count too."""
    from chaoscypher_core.services.workflows.tools.engine import context_render

    settings = EngineSettings()
    before = retrieval_fingerprint(mode, EMB, settings)
    original = context_render.render_retrieved_context
    monkeypatch.setattr(
        context_render,
        "render_retrieved_context",
        lambda retrieved, **kw: original(retrieved, **kw).replace("- ", "* "),
    )
    assert retrieval_fingerprint(mode, EMB, settings) != before


@pytest.mark.parametrize(
    ("module", "name"),
    [
        (
            "chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter",
            "split_into_sentences",
        ),
        (
            "chaoscypher_core.services.workflows.tools.engine.chunk_hydration",
            "format_chunk_content",
        ),
    ],
)
def test_the_recipe_moves_with_the_splitter_and_the_chunk_formatter(
    monkeypatch: pytest.MonkeyPatch, module: str, name: str
) -> None:
    import importlib

    from chaoscypher_core.benchmark.pins import retrieval_recipe

    before = retrieval_recipe()
    target = importlib.import_module(module)
    original = getattr(target, name)
    monkeypatch.setattr(target, name, lambda *a, **kw: (original(*a, **kw), "changed"))
    assert retrieval_recipe() != before


def test_the_recipe_moves_with_the_handlers_word_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every constant the recipe hashes, changed on its own, moves it: the handlers', the router's, the blocks', the renderer's.

    A frozenset gains a word; a dict gains a key; a tuple, a string or a number is doubled.
    The retrieval sample is held fixed: the claim is that each constant is hashed, and the
    sample (about 7 s a run) ran 29 times here, past five minutes on a CI runner
    (2026-10-15). What the sample's behaviour covers is the every-stage test's.
    """
    from chaoscypher_core.benchmark import pins, retrieval_sample
    from chaoscypher_core.benchmark.pins import (
        BLOCK_RECIPE_CONSTANTS,
        RENDER_RECIPE_CONSTANTS,
        RETRIEVAL_RECIPE_CONSTANTS,
        SHAPE_RECIPE_CONSTANTS,
        SHAPES_RECIPE_CONSTANTS,
        recipe_constants,
        retrieval_recipe,
    )

    monkeypatch.setattr(retrieval_sample, "sample_retrievals", lambda: ["the fixed sample"])
    monkeypatch.setattr(pins, "_RECIPE_MEMO", {})

    hashed = [(module, name) for module, names in recipe_constants() for name in names]
    assert [name for _, name in hashed] == [
        *RETRIEVAL_RECIPE_CONSTANTS,
        *SHAPE_RECIPE_CONSTANTS,
        *BLOCK_RECIPE_CONSTANTS,
        *SHAPES_RECIPE_CONSTANTS,
        *RENDER_RECIPE_CONSTANTS,
    ]
    before = retrieval_recipe()
    for module, name in hashed:
        value = getattr(module, name)
        if isinstance(value, frozenset):
            changed: Any = frozenset({*value, "x"})
        elif isinstance(value, dict):
            changed = {**value, "x": "x"}
        else:
            changed = value * 2
        assert changed != value, name
        with monkeypatch.context() as m:
            m.setattr(module, name, changed)
            assert retrieval_recipe() != before, name
    assert retrieval_recipe() == before


_REACHED_BY_THE_SAMPLE: dict[str, frozenset[str]] = {
    # module name -> public constants the recipe does not hash because the sample's own
    # behaviour moves with them; each entry says which sample case reaches it. None today:
    # every public constant of these modules is hashed (recipe_constants).
}
"""Public constants of the shape modules pinned by the sample's behaviour instead of the hash."""


def test_every_public_constant_of_the_shape_modules_is_hashed_or_reached() -> None:
    """A public UPPER_CASE constant of the blocks, the router, the shape names or the renderer is hashed or allowlisted.

    The walk's degree caps decided which edges a walk expands and moved no
    recipe (2026-10-09 review: no sample node is a hub), so a constant added
    to these modules must be hashed (:func:`recipe_constants`) or listed in
    :data:`_REACHED_BY_THE_SAMPLE` with the sample case that reaches it.
    """
    import ast
    import re
    from pathlib import Path

    from chaoscypher_core import question_shapes
    from chaoscypher_core.benchmark.pins import recipe_constants
    from chaoscypher_core.services.workflows.tools.engine import context_render
    from chaoscypher_core.services.workflows.tools.engine.handlers import (
        graphrag_blocks,
        question_shape,
    )

    hashed = {(m.__name__, name) for m, names in recipe_constants() for name in names}
    for module in (graphrag_blocks, question_shape, question_shapes, context_render):
        reached = _REACHED_BY_THE_SAMPLE.get(module.__name__, frozenset())
        # the module's own top-level assignments (an imported constant is its own module's)
        tree = ast.parse(Path(str(module.__file__)).read_text(encoding="utf-8"))
        targets = [
            target
            for node in tree.body
            if isinstance(node, ast.Assign | ast.AnnAssign)
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        ]
        public = {
            t.id
            for t in targets
            if isinstance(t, ast.Name) and re.fullmatch(r"[A-Z][A-Z0-9_]*", t.id)
        }
        assert public, module.__name__
        unpinned = {n for n in public if (module.__name__, n) not in hashed} - reached
        assert not unpinned, f"{module.__name__}: {sorted(unpinned)}"
        assert reached <= public, f"{module.__name__}: stale {sorted(reached - public)}"
        assert not {(module.__name__, n) for n in reached} & hashed


def test_a_pin_built_under_another_recipe_or_index_is_refused(tmp_path: Path) -> None:
    pins = _pins(tmp_path)
    pins.header["settings"]["retrieval_recipe"] = "older-recipe"
    with pytest.raises(PinError, match=r"retrieval_recipe: pinned 'older-recipe'"):
        pins.check(graph_sha256="g" * 64, embedder=EMB, retrieval="graphrag")
    fresh = _pins(tmp_path)
    fresh.check(graph_sha256="g" * 64, embedder=EMB, retrieval="graphrag", index_digest="i" * 16)
    with pytest.raises(PinError, match="index_digest"):
        fresh.check(graph_sha256="g" * 64, embedder=EMB, retrieval="graphrag", index_digest="j")


def test_provenance_marks_an_oracle_file_and_digests_what_it_serves(tmp_path: Path) -> None:
    """Oracle pins keep the real file's fingerprint: only the flag tells a ceiling row apart (H11)."""
    pins = _pins(tmp_path)
    pins.put("q1", "Who?", {"chunks": [{"content": "a"}]})
    real = pins.provenance()
    assert real["oracle"] is False and len(real["content_digest"]) == 16
    pins.header["oracle"] = True
    pins.put("q1", "Who?", {"chunks": [{"content": "the answer"}]})
    oracle = pins.provenance()
    assert oracle["oracle"] is True and oracle["fingerprint"] == real["fingerprint"]
    assert oracle["content_digest"] != real["content_digest"]


@pytest.mark.asyncio
async def test_the_vector_cache_keys_by_the_providers_query_prefix_and_dimensions(
    tmp_path: Path,
) -> None:
    """The same text under another query prefix or truncation is another vector."""
    from types import SimpleNamespace

    calls: list[str] = []

    async def embed(text: str) -> Any:
        calls.append(text)
        return {"embedding": [float(len(calls))]}

    cache = QueryVectorCache.for_embedder(tmp_path, EMB)
    plain = SimpleNamespace(query_prefix="", vector_dimensions=1024)
    prefixed = SimpleNamespace(query_prefix="query: ", vector_dimensions=1024)
    wider = SimpleNamespace(query_prefix="", vector_dimensions=2560)
    a = await cache.wrap(embed, provider=plain)("Who?")
    assert await cache.wrap(embed, provider=plain)("Who?") == a and calls == ["Who?"]
    assert await cache.wrap(embed, provider=prefixed)("Who?") != a
    assert await cache.wrap(embed, provider=wider)("Who?") != a
    assert len(calls) == 3


def test_the_graph_rungs_record_the_embedders_own_vector_size() -> None:
    """search.vector_dimensions holds the 1024 base; a pack indexes qwen3-embedding:4b at 2560 (R1)."""
    from chaoscypher_core.benchmark.pins import retrieval_settings

    settings = EngineSettings()
    assert settings.search.vector_dimensions == 1024
    assert retrieval_settings("graphrag", EMB, settings)["search"]["vector_dimensions"] == 2560
    small = retrieval_settings("hybrid", "ollama/qwen3-embedding:0.6b", settings)
    assert small["search"]["vector_dimensions"] == 1024
    with pytest.raises(PinError, match="native vector size"):
        retrieval_settings("graphrag", "ollama/unknown-embedder", settings)


@pytest.mark.asyncio
async def test_the_vector_cache_is_per_size_and_refuses_a_vector_of_another(
    tmp_path: Path,
) -> None:
    """1024-d cached question vectors must never reach a 2560-d index (R8)."""

    async def embed(text: str) -> Any:
        return {"embedding": [0.5] * 1024}

    small = QueryVectorCache.for_embedder(tmp_path, EMB, dimensions=1024)
    await small.wrap(embed)("Who?")
    small.save()
    assert small.path.name.endswith("-1024d.json")
    wide = QueryVectorCache.for_embedder(tmp_path, EMB, dimensions=2560)
    assert wide.path != small.path and wide.vectors == {}
    with pytest.raises(PinError, match="a 1024-d vector for a 2560-d index"):
        await wide.wrap(embed)("Who?")
    small.path.rename(wide.path)
    with pytest.raises(PinError, match="holds 1024-d vectors, not 2560-d"):
        QueryVectorCache.for_embedder(tmp_path, EMB, dimensions=2560)


@pytest.mark.parametrize(
    ("module", "name", "value"),
    [
        ("chaoscypher_core.adapters.sqlite.repos.search", "RRF_K", 61),
        ("chaoscypher_core.adapters.sqlite.repos.search", "_FTS5_STOP_WORDS", frozenset({"x"})),
        # another valid MATCH expression (an invalid one only logs failures)
        (
            "chaoscypher_core.adapters.sqlite.repos.search",
            "_fts5_query",
            lambda q: '"' + q.split()[-2] + '"',
        ),
    ],
)
def test_the_recipe_moves_with_the_keyword_arm_and_the_fusion(
    monkeypatch: pytest.MonkeyPatch, module: str, name: str, value: Any
) -> None:
    import importlib

    from chaoscypher_core.benchmark.pins import retrieval_recipe

    before = retrieval_recipe()
    monkeypatch.setattr(importlib.import_module(module), name, value)
    assert retrieval_recipe() != before


def test_the_recipe_moves_with_the_hybrid_reserve_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from chaoscypher_core.benchmark.pins import retrieval_recipe
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )

    before = retrieval_recipe()
    monkeypatch.setattr(
        GraphRAGToolHandlers,
        "_reserve_hybrid_hits",
        staticmethod(lambda kept, rest, order, reserve, evidence_ids=(): list(kept)),
    )
    assert retrieval_recipe() != before


# ---------------------------------------------------------------------------
# The recipe runs the retrieval itself (audit wave 2: the handler fixes moved
# 83 of 120 chunk sets under an unchanged fingerprint)
# ---------------------------------------------------------------------------

_HANDLERS = "chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers"
_BLOCKS = "chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_blocks"
_SEARCH = "chaoscypher_core.adapters.sqlite.repos.search"
_TRAVERSAL = "chaoscypher_core.services.graph.engine.traversal"
_SHAPE = "chaoscypher_core.services.workflows.tools.engine.handlers.question_shape"
_RENDER = "chaoscypher_core.services.workflows.tools.engine.context_render"


def _always_path(query: str, *, named_seeds: int, relations: Any) -> Any:
    """A rules classifier that calls every question a path question."""
    from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import (
        QuestionShape,
        ShapeDecision,
    )

    return ShapeDecision(QuestionShape.PATH, "always a path", "rules")


async def _no_evidence(self: Any, chunk_ids: Any, source_ids: Any) -> list[Any]:
    """An evidence arm that reads no chunk."""
    return []


def _no_chains(original: Any) -> Any:
    """``walk_chains`` finding no chain for any pair (what it left out still counted)."""

    def walk_chains(**kw: Any) -> Any:
        _, skipped = original(**kw)
        return {pair: [] for pair in kw["pairs"]}, skipped

    return walk_chains


def _folded_words(name: str) -> list[str]:
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold

    return fold(name).split()


def _substring_gate(text: str, terms: set[str], conventions: Any) -> bool:
    """The question gate before wave 3: a term anywhere in the folded text."""
    from chaoscypher_core.services.sources.engine.deduplication.name_identity import fold

    folded = fold(text)
    return any(t in folded for t in terms)


def _reserve_uncut(
    kept: Any, rest: Any, hybrid_order: Any, reserve: int, evidence_ids: Any = ()
) -> Any:
    """The reserve rule before wave 4: hybrid's top ``reserve`` protected even past the budget."""
    result = list(kept)
    reserved = hybrid_order[:reserve]
    protected = set(reserved) | set(evidence_ids)
    present = {c["chunk_id"] for c in result}
    beyond = {c["chunk_id"]: c for c in rest}
    for chunk_id in reserved:
        if chunk_id in present or chunk_id not in beyond:
            continue
        outside = [i for i in range(len(result)) if result[i]["chunk_id"] not in protected]
        provenance = [i for i in outside if result[i].get("retrieval_origin") == "provenance"]
        pool = provenance or outside
        if not pool:
            break
        del result[pool[-1]]
        result.append(beyond[chunk_id])
        present.add(chunk_id)
    return result


def _named_only(candidates: Any, nodes: Any) -> Any:
    """The vector seed rule of 2026-10-07: a flagged reference never seeds by similarity."""
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        _seedable,
    )

    return [(nid, score) for nid, score in candidates if _seedable(nodes.get(nid))]


def _within(delta: float) -> Any:
    """A looser similarity rule: a reference within ``delta`` of the best named candidate seeds too."""
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        _seedable,
    )

    def rule(candidates: Any, nodes: Any) -> Any:
        best = max((s for nid, s in candidates if _seedable(nodes.get(nid))), default=0.0)
        return [(nid, s) for nid, s in candidates if _seedable(nodes.get(nid)) or s >= best - delta]

    return rule


def _reverse_scores(original: Any) -> Any:
    def pagerank(nodes: Any, edges: Any, **kw: Any) -> dict[str, Any]:
        out: dict[str, Any] = original(nodes, edges, **kw)
        scores = out["pagerank_scores"]
        ranked = sorted(scores, key=scores.__getitem__)
        out["pagerank_scores"] = {nid: float(i + 1) for i, nid in enumerate(ranked)}
        return out

    return pagerank


@pytest.mark.parametrize(
    ("module", "name", "replace"),
    [
        # seed selection: the vector seeds, the keyword seeds' matcher
        (_HANDLERS, "GraphRAGToolHandlers._match_seed_entities", lambda f: lambda *a, **k: []),
        (_HANDLERS, "_names_a_term", lambda f: lambda node, terms, conventions: False),
        # keyword seeds off
        (
            _HANDLERS,
            "GraphRAGToolHandlers._add_keyword_seeds",
            lambda f: lambda self, query, seeds, limit, source_ids=None: seeds,
        ),
        # a name of titles alone matching as a phrase (the rule before wave 3)
        (
            _HANDLERS,
            "_name_key_list",
            lambda f: lambda name, conventions: f(name, conventions) or _folded_words(name),
        ),
        # the matcher's surname-form and plural folds, and short names
        (_HANDLERS, "_word_key", lambda f: lambda word, conventions: word),
        (_HANDLERS, "_singular", lambda f: lambda word, conventions: word),
        (_HANDLERS, "_query_words", lambda f: lambda q: [w for w in f(q) if len(w) >= 4]),
        # the question gate, and the gate back to substrings
        (_HANDLERS, "_shares_a_term", lambda f: lambda text, terms, conventions: True),
        (_HANDLERS, "_shares_a_term", lambda f: _substring_gate),
        # PageRank
        (_HANDLERS, "calculate_pagerank", _reverse_scores),
        # triple selection, and the connected filter off
        (
            _HANDLERS,
            "GraphRAGToolHandlers._select_triples",
            lambda f: lambda self, *a, **k: list(reversed(f(self, *a, **k))),
        ),
        (_HANDLERS, "_connected_edge", lambda f: lambda *a: True),
        # the summary's cut
        (
            _HANDLERS,
            "GraphRAGToolHandlers._build_summary",
            lambda f: staticmethod(lambda triples, types: "\n".join(t["source"] for t in triples)),
        ),
        # an unevidenced edge (keep, flag, never delete, 2026-10-07): let into the context by
        # default, into PageRank by default, or ranked among the evidenced lines
        (_HANDLERS, "_edge_in_context", lambda f: lambda edge, *, include_unevidenced: True),
        (_BLOCKS, "is_unevidenced_record", lambda f: lambda props: False),
        # the max_triples selection, whose order the summary cuts, ranking by the whole line
        # standing (2026-10-08): a capped, unmatched-label or type-mismatched line among the
        # others, or by the edge's own flags only (the endpoints' standing unread)
        (
            _HANDLERS,
            "_line_standing",
            lambda f: lambda edge, nodes: f(edge, nodes)._replace(doubted_edge=False),
        ),
        (
            _HANDLERS,
            "_line_standing",
            lambda f: lambda edge, nodes: f(edge, {}),
        ),
        # the budget and the reserve
        (
            _HANDLERS,
            "GraphRAGToolHandlers._reserve_hybrid_hits",
            lambda f: staticmethod(lambda kept, rest, order, reserve, evidence_ids=(): list(kept)),
        ),
        # the reserve past the budget (the rule before wave 4)
        (
            _HANDLERS,
            "GraphRAGToolHandlers._reserve_hybrid_hits",
            lambda f: staticmethod(_reserve_uncut),
        ),
        # a repository's row order trusted for a rank (wave 4: citations by node id)
        (_HANDLERS, "_in_lookup_order", lambda f: lambda rows, ids, key, then=None: list(rows)),
        (_HANDLERS, "_by_edge_id", lambda f: lambda edges: list(reversed(edges))),
        # a descriptor alias read as a name (wave 4: "Bezúkhov's son" named Pierre)
        (_HANDLERS, "is_descriptor", lambda f: lambda name, conventions=None: False),
        # a flagged reference seeding by similarity or name (2026-10-07: 84 of 120 B0
        # questions), its written label not seeding it, and its rank among the names
        (_HANDLERS, "_seedable", lambda f: lambda node: True),
        # a reference seeding by similarity: every one above the threshold (the rule
        # before 2026-10-07), none (the 2026-10-07 rule: B0 q088 lost), or one a name outscores
        (_HANDLERS, "_similarity_seeds", lambda f: lambda candidates, nodes: list(candidates)),
        (_HANDLERS, "_similarity_seeds", lambda f: _named_only),
        (_HANDLERS, "_similarity_seeds", lambda f: _within(0.05)),
        (_HANDLERS, "_writes_reference", lambda f: lambda words, node, conventions: False),
        (_HANDLERS, "_by_standing", lambda f: lambda entries: list(entries)),
        # a description's sentences: which are kept, their order (question_aware_descriptions)
        # and the cut
        (_HANDLERS, "_clean_description", lambda f: lambda text, limit, **kw: f(text, limit)[::-1]),
        # a flagged orphan (keep, flag, never delete, 2026-10-07): its flag unread (it ranks
        # among the connected seeds by PageRank), or read as a reference (it seeds by no name)
        (_HANDLERS, "node_standing", lambda f: lambda props: f(props)._replace(orphan=False)),
        (
            _HANDLERS,
            "node_standing",
            lambda f: (
                lambda props: f(props)._replace(reference=f(props).orphan or f(props).reference)
            ),
        ),
        # a doubted name (keep, flag, never delete, 2026-10-07: ``low_plausibility``) ranked
        # among the undoubted, and a capped edge walked at an edge's full weight
        (_HANDLERS, "node_standing", lambda f: lambda props: f(props)._replace(doubtful=False)),
        (_TRAVERSAL, "edge_walk_weight", lambda f: lambda props: 1.0),
        # ordering
        (_HANDLERS, "_rank_by_seed_mentions", lambda f: lambda chunks, labels, c: chunks[::-1]),
        (_HANDLERS, "_provenance_lookup_ids", lambda f: lambda gc, ppr: list(reversed(f(gc, ppr)))),
        # rendering (the handlers' own import of the renderer)
        (
            _HANDLERS,
            "render_retrieved_context",
            lambda f: lambda r, **kw: f(r, **kw).replace("- ", "* "),
        ),
        # the hybrid arm's k and min_similarity
        (_HANDLERS, "HYBRID_OVERFETCH", lambda f: 1),
        (_HANDLERS, "HYBRID_MIN_SIMILARITY", lambda f: 0.6),
        # the search repository: keyword SQL (BM25 weights), fusion
        (
            _SEARCH,
            "_KEYWORD_SQL",
            lambda f: {
                **f,
                "node": f["node"].replace(
                    "bm25(fulltext_index, 3.0, 1.0, 0.5)", "bm25(fulltext_index)"
                ),
            },
        ),
        (_SEARCH, "_fuse_rankings", lambda f: lambda *r, limit: f(*reversed(r), limit=limit)),
        # the question's shape: the rules (the handlers' own import), their aggregate cues,
        # and the shape's levers applied (the handlers' own import)
        (_HANDLERS, "classify_by_rules", lambda f: _always_path),
        (_SHAPE, "AGGREGATE_CUES", lambda f: frozenset()),
        (_HANDLERS, "apply_shape_levers", lambda f: lambda settings, shape: settings),
        # the walk: the search, the chains the handler reads (its own import), the inverse fold
        (_BLOCKS, "shortest_walks", lambda f: lambda *a, **k: []),
        (_HANDLERS, "walk_chains", _no_chains),
        (_BLOCKS, "_folds_onto_twin", lambda f: lambda edge, forward_keys: False),
        # the aggregate block (the handlers' own import) and the quoted sentence's length
        (_HANDLERS, "aggregate_blocks", lambda f: lambda **k: []),
        (_BLOCKS, "EVIDENCE_QUOTE_CHARS", lambda f: 20),
        # the evidence arm, and the reserve's ids it spends no slot on
        (_HANDLERS, "GraphRAGToolHandlers._retrieve_evidence_chunks", lambda f: _no_evidence),
        (_HANDLERS, "_reserved_hybrid_ids", lambda f: lambda order, reserve, budget: []),
        # question-aware descriptions
        (_HANDLERS, "_question_first", lambda f: lambda sentences, terms, conv: list(sentences)),
        # the blocks' rendering
        (_RENDER, "_walk_line", lambda f: lambda walk: "x"),
        (_RENDER, "_aggregate_lines", lambda f: lambda block: []),
    ],
)
def test_the_recipe_moves_with_every_stage_of_the_retrieval(
    monkeypatch: pytest.MonkeyPatch, module: str, name: str, replace: Any
) -> None:
    """Each stage replaced on its own moves the recipe; restored, the recipe is back."""
    import importlib

    from chaoscypher_core.benchmark.pins import retrieval_recipe

    before = retrieval_recipe()
    owner: Any = importlib.import_module(module)
    attr = name
    if "." in name:
        cls, attr = name.split(".")
        owner = getattr(owner, cls)
    original = owner.__dict__[attr] if isinstance(owner, type) else getattr(owner, attr)
    unwrapped = original.__func__ if isinstance(original, staticmethod) else original
    with monkeypatch.context() as m:
        m.setattr(owner, attr, replace(unwrapped))
        assert retrieval_recipe() != before, name
    assert retrieval_recipe() == before


def test_the_reserve_sample_is_one_the_rule_decides() -> None:
    """The recipe's reserve cases each need the rule they pin.

    The two-arm case needs the second tier (a both-arms chunk evicted for the
    reserve); the evidence case needs the evidence protection (the evidence
    chunk is the victim the two-arm rule would take, and with it protected
    the last reserved hit has no victim and stays out).
    """
    import copy

    from chaoscypher_core.benchmark.pins import _RECIPE_RESERVE
    from chaoscypher_core.services.workflows.tools.engine.handlers.graphrag_handlers import (
        GraphRAGToolHandlers,
    )

    two_arm, evidence = copy.deepcopy(_RECIPE_RESERVE)
    kept, rest, order, reserve, evidence_ids = two_arm
    assert evidence_ids == ()
    out = GraphRAGToolHandlers._reserve_hybrid_hits(kept, rest, order, reserve, evidence_ids)
    assert [c["chunk_id"] for c in out] == ["h1", "h2", "h3"]
    # the pre-2026-10-06 rule (provenance-only victims alone) keeps b1 and loses h3
    assert "b1" in [c["chunk_id"] for c in kept]

    e_kept, e_rest, e_order, e_reserve, e_ids = evidence
    assert e_ids
    out = GraphRAGToolHandlers._reserve_hybrid_hits(e_kept, e_rest, e_order, e_reserve, e_ids)
    assert [c["chunk_id"] for c in out] == ["h1", "e1", "h2"]
    # without the evidence ids the evidence chunk is the victim of the last reserved hit
    two_arm_rule = GraphRAGToolHandlers._reserve_hybrid_hits(e_kept, e_rest, e_order, e_reserve)
    assert [c["chunk_id"] for c in two_arm_rule] == ["h1", "h2", "h3"]


def test_the_retrieval_sample_runs_every_stage_it_is_meant_to_cover() -> None:
    """The sample reaches every rule: caps, filters, both fallbacks, the gate and the name forms.

    Until wave 3 it had 11 nodes (fewer than ``ppr_top_k``), 12 edges (fewer
    than ``max_triples``), nearly every node above the seed threshold and
    every edge touching a seed, so the gate, the connected filter and the
    keyword seeds could change without moving the recipe.
    """
    import json

    from chaoscypher_core.benchmark.retrieval_sample import (
        ABSTAIN_QUESTION,
        AGGREGATE_QUESTION,
        GAP_QUESTION,
        LEVER_SETS,
        PATH_QUESTION,
        RUNS,
        SAMPLE_CAPPED_EDGES,
        SAMPLE_DOUBTFUL,
        SAMPLE_EDGES,
        SAMPLE_INVERSE_EDGES,
        SAMPLE_MISFIT_EDGES,
        SAMPLE_NODES,
        SAMPLE_ORPHANS,
        SAMPLE_QUESTIONS,
        SAMPLE_REFERENCES,
        SAMPLE_UNEVIDENCED,
        sample_retrievals,
    )
    from chaoscypher_core.settings import GraphRAGSettings

    defaults = GraphRAGSettings()
    assert len(SAMPLE_NODES) > defaults.ppr_top_k
    assert len(SAMPLE_EDGES) > defaults.max_triples
    out = sample_retrievals()
    graphrag = [json.loads(o) for o in out if o.startswith("{")]
    assert len(graphrag) == len(SAMPLE_QUESTIONS) * len(RUNS)
    per_run = len(SAMPLE_QUESTIONS)
    by_run = [graphrag[i : i + per_run] for i in range(0, len(graphrag), per_run)]
    assert {g["stats"]["mode"] for g in graphrag} == {
        "full_graphrag",
        "vector_only",
        "keyword_only",
    }
    first = by_run[0][0]
    # a possessive seeds Teodór; "prince" seeds no princess
    assert "teodor" in first["seeds"] and "corvin" in first["seeds"]
    # the family name in its plural and feminine forms
    assert {"ostrovs", "ostrov", "ostrova", "wren"} <= set(by_run[0][1]["seeds"])
    # a flagged reference: a seed by similarity when it is the closest match (the steward
    # embeds as the first question does), ranked after the named seeds; below a name it
    # takes no vector slot and seeds on its written label last (the count, second question)
    assert first["seeds"][-1] == "steward"
    assert by_run[0][1]["seeds"][-1] == "thecount"
    assert all("steward" not in g["seeds"] for g in by_run[0][1:])
    # a flagged orphan: a vector seed like any named node, after every connected seed and
    # before the reference (keep, flag, never delete, 2026-10-07)
    assert {"orsa"} == SAMPLE_ORPHANS
    assert not {nid for edge in SAMPLE_EDGES for nid in (edge[0], edge[2])} & SAMPLE_ORPHANS
    assert by_run[0][1]["seeds"][-2:] == ["orsa", "thecount"]
    # the cap bites with the connected filter on and no gate
    assert all(len(g["relationships"]) == LEVER_SETS[1]["max_triples"] for g in by_run[1][:5])
    # every edge listed: the summary is cut
    assert all("more triples omitted" in (g["summary"] or "") for g in by_run[2][:5])
    # an unevidenced edge (keep, flag, never delete, 2026-10-07): out of every context by
    # default; under include_unevidenced_edges (the third lever set) listed after every line
    # between unflagged entities (only lines touching a reference, an orphan or a doubted
    # entity follow it, 2026-10-08: the selection ranks by the whole line standing), and cut
    # from the summary by its budget
    ((src, rel, tgt),) = SAMPLE_UNEVIDENCED
    assert rel not in {e[1] for e in SAMPLE_EDGES if e != (src, rel, tgt)}
    assert LEVER_SETS[2]["include_unevidenced_edges"] is True
    for i, run in enumerate(by_run):
        if i != 2:
            assert all(rel not in json.dumps(g, ensure_ascii=False) for g in run), i
    listed = [g for g in by_run[2] if g["relationships"]]
    assert listed
    flagged_nodes = SAMPLE_REFERENCES | SAMPLE_ORPHANS | SAMPLE_DOUBTFUL
    for g in listed:
        lines = [tuple(r[:3]) for r in g["relationships"]]
        after = lines[lines.index((src, rel, tgt)) + 1 :]
        assert all({s, t} & flagged_nodes for s, _, t in after), after
    # the type check's flagged edges (2026-10-08) rank with the capped one: after every
    # unflagged line between unflagged entities wherever they are listed
    doubted = {*SAMPLE_CAPPED_EDGES, *SAMPLE_MISFIT_EDGES}
    for g in graphrag:
        lines = [tuple(r[:3]) for r in g["relationships"]]
        first = min((lines.index(e) for e in doubted if e in lines), default=len(lines))
        assert all(
            {s, t} & flagged_nodes or (s, r, t) in doubted or (s, r, t) == (src, rel, tgt)
            for s, r, t in lines[first:]
        ), lines[first:]
    assert any(
        e in {tuple(r[:3]) for g in graphrag for r in g["relationships"]}
        for e in SAMPLE_MISFIT_EDGES
    )
    assert all(rel not in (g["summary"] or "") for g in listed)
    assert {"provenance", "both"} & {c[2] for g in graphrag for c in g["chunks"]}
    assert any(g["stats"]["vector_chunks"] < g["stats"]["total_chunks_returned"] for g in graphrag)

    # the question shapes (2026-10-09): the rules lever set routes by the rules alone
    questions = list(SAMPLE_QUESTIONS)

    def run_of(**want: Any) -> list[dict[str, Any]]:
        (index,) = [
            i
            for i, run in enumerate(RUNS)
            if all(getattr(run, k) == v for k, v in want.items() if k != "levers")
            and all(run.levers.get(k) == v for k, v in want.get("levers", {}).items())
        ]
        return by_run[index]

    rules = run_of(levers={"router": "rules"})
    aggregate = rules[questions.index(AGGREGATE_QUESTION)]
    assert aggregate["shape"] == aggregate["stats"]["shape"] == "aggregate"
    rows = [row for b in aggregate["aggregates"] for grp in b["groups"] for row in grp["rows"]]
    assert any(row.get("marker") for row in rows) and any(not row.get("marker") for row in rows)
    # one row a group (aggregate_per_label 1): the count above the rows listed
    assert any(
        grp["count"] > len(grp["rows"]) for b in aggregate["aggregates"] for grp in b["groups"]
    )
    assert "more" in aggregate["context_text"]
    path = rules[questions.index(PATH_QUESTION)]
    assert path["shape"] == "path" and path["walks"]
    (rules_levers,) = [levers for levers in LEVER_SETS if levers.get("router") == "rules"]
    assert all(len(w["hops"]) <= rules_levers["max_hops"] for w in path["walks"])
    assert "- walk: " in path["context_text"]
    # the synthetic inverses are folded onto their twins, by the walk and the blocks (the
    # count holds while both inverses are in the path question's loaded graph: the walk
    # counts the folds over every loaded edge, not only those of its chains)
    assert path["stats"]["walk_skipped"]["inverse"] == len(SAMPLE_INVERSE_EDGES)
    # the evidence arm: the cited chunks in the budget, the walk's sentences marked
    assert path["stats"]["evidence_chunks"] > 0
    assert any(h.get("marker") for w in path["walks"] for h in w["hops"])
    abstain = rules[questions.index(ABSTAIN_QUESTION)]
    assert abstain["shape"] == "lookup" and abstain["stats"]["shape_source"] == "default"
    # the default router (rules, since 2026-10-19) never asks a model: an abstention is a lookup
    assert by_run[0][questions.index(ABSTAIN_QUESTION)]["stats"]["shape_reason"] == "no decision"
    # the router off: every question a lookup, no block
    off = run_of(levers={"router": "off"})
    assert {g["shape"] for g in off} == {"lookup"}
    assert not any(g["aggregates"] or g["walks"] for g in off)
    # the router's model (a stub): its shape and reason recorded for every question
    asked = run_of(router="aggregate")
    assert {(g["stats"]["shape"], g["stats"]["shape_source"]) for g in asked} == {
        ("aggregate", "llm")
    }
    assert all(g["stats"]["shape_reason"].startswith("stub") for g in asked)
    assert any(g["aggregates"] for g in asked)
    # a global question (phase A): the lookup assembly, the community block said to wait
    broad = run_of(router="global")
    assert all(
        g["stats"]["shape_reason"] == "stub; no community block until phase B"
        and not (g["aggregates"] or g["walks"])
        for g in broad
    )
    # a question naming no seed: no block (never every seed's), the reason says so
    unnamed = [g for g in asked if g["stats"]["shape_reason"] != "stub"]
    assert unnamed
    assert all(
        g["stats"]["shape_reason"] == "stub; no named entity, lookup assembly"
        and not g["aggregates"]
        for g in unnamed
    )
    folded = [b["skipped"]["inverse"] for g in asked for b in g["aggregates"]]
    assert any(folded)
    # a path the model names for a question naming fewer than two entities: a lookup, said so
    walked = run_of(router="path", plain_graph=False)
    assert any(g["stats"]["shape_reason"].endswith("lookup assembly") for g in walked)
    assert any(g["walks"] for g in walked)
    # and over a node listing without labels: the walk fetches its nodes, the walks are read
    assert any(g["walks"] for g in run_of(router="path", plain_graph=True))
    # a pair no chain joins (an orphan): a gap, said in the context, never silence
    gaps = [g for run in by_run for g in run if g["walk_gaps"]]
    assert gaps
    # a gap is hedged exactly when its walk left out what could cut a route (an unevidenced
    # edge, one outside the graph, a hub's edges past the cap, a capped listing), never for
    # the folded inverses; each wording in the context's own words
    cutting = ("outside_graph", "unevidenced", "degree_capped", "listing_capped")
    for g in gaps:
        left_out = any(g["stats"]["walk_skipped"][key] for key in cutting)
        assert all(gap["left_out"] is left_out for gap in g["walk_gaps"])
        wording = "- no evidenced chain of up to " if left_out else "- no chain of up to "
        assert wording in g["context_text"]
    assert {gap["left_out"] for g in gaps for gap in g["walk_gaps"]} == {True, False}
    assert by_run[0][questions.index(GAP_QUESTION)]["walk_gaps"]
    # an embedder answering a bare vector: the handler reads none (no vector seeds). This pins
    # a known defect (the search repository and the vector cache read the vector): see
    # internal/TODO.md, "Three parsers of an embedding reply disagree on a bare vector"
    bare = run_of(embedding_reply="bare")
    assert any(b["seeds"] != d["seeds"] for b, d in zip(bare, by_run[0], strict=True))


def test_the_retrieval_sample_covers_the_retrieval_code() -> None:
    """Line coverage of the handler module from the sample alone, swallowed-exception paths aside.

    At least 95% of the function lines outside ``except`` handlers (a fault
    path needs a failing component; the fault record it writes is tested on
    its own). Skipped under an outer coverage run, which a nested one would
    disturb.
    """
    import ast
    from pathlib import Path

    coverage = pytest.importorskip("coverage")
    if coverage.Coverage.current() is not None:
        pytest.skip("an outer coverage run is active")
    from chaoscypher_core.benchmark.retrieval_sample import sample_retrievals
    from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers

    path = graphrag_handlers.__file__
    cov = coverage.Coverage(include=[path], data_file=None)
    cov.start()
    try:
        sample_retrievals()
    finally:
        cov.stop()
    _, statements, _, missing, _ = cov.analysis2(path)
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    bodies: set[int] = set()
    handlers: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            bodies.update(range(node.body[0].lineno, (node.end_lineno or 0) + 1))
        if isinstance(node, ast.ExceptHandler):
            handlers.update(range(node.lineno, (node.end_lineno or 0) + 1))
    counted = (set(statements) & bodies) - handlers
    lost = set(missing) & counted
    share = 1 - len(lost) / len(counted)
    assert share >= 0.95, f"{share:.1%}: lines {sorted(lost)}"


def test_the_recipe_memo_never_serves_a_replaced_objects_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patch, restore, patch again: each patch gets its own recipe, whatever addresses repeat.

    The memo holds the values it was computed under and compares them by
    identity; until wave 3 it kept their ``id()`` numbers, which a freed
    patch hands to the next one.
    """
    from chaoscypher_core.benchmark import pins
    from chaoscypher_core.services.workflows.tools.engine.handlers import graphrag_handlers

    before = pins.retrieval_recipe()
    original = graphrag_handlers._connected_edge
    with monkeypatch.context() as m:
        m.setattr(graphrag_handlers, "_connected_edge", lambda *a: True)
        open_filter = pins.retrieval_recipe()
    with monkeypatch.context() as m:
        m.setattr(graphrag_handlers, "_connected_edge", lambda *a: original(*a))
        assert pins.retrieval_recipe() == before
        assert pins._RECIPE_MEMO["state"] is not None
    assert open_filter != before
    assert pins.retrieval_recipe() == before


def test_the_router_model_record_names_the_model_only_when_the_router_asks_one() -> None:
    """Provider, model and seed under llm / rules_then_llm; None when the router never asks a model."""
    settings = EngineSettings()
    settings.llm.chat_provider = "ollama"
    settings.llm.ollama_chat_model = "gemma4:31b"
    for mode in ("llm", "rules_then_llm"):
        settings.graphrag.router = mode
        assert router_model_record(settings, seed=42) == {
            "provider": "ollama",
            "model": "gemma4:31b",
            "seed": 42,
        }
    for mode in ("rules", "off"):
        settings.graphrag.router = mode  # type: ignore[assignment]
        assert router_model_record(settings, seed=42) is None


def test_check_treats_a_missing_or_another_router_model_as_stale(tmp_path: Path) -> None:
    """Task 10 review: a pre-router pin, or one routed by another chat model, is not kept as fresh.

    A serving caller does not ask (the router model is in no fingerprint), so old pins still serve.
    """
    run = {"graph_sha256": "g" * 64, "embedder": EMB, "retrieval": "graphrag"}
    gemma = {"provider": "ollama", "model": "gemma4:31b", "seed": 42}
    pins = _pins(tmp_path)
    pins.header.pop("router_model", None)  # a file from before the field
    pins.check(**run)
    with pytest.raises(PinError, match="router_model"):
        pins.check(**run, router_model=None)
    with pytest.raises(PinError, match="router_model"):
        pins.check(**run, router_model=gemma)
    pins.header["router_model"] = gemma
    pins.check(**run, router_model=gemma)
    with pytest.raises(PinError, match="router_model"):
        pins.check(**run, router_model={**gemma, "model": "qwen3.5:27b"})
    with pytest.raises(PinError, match="router_model"):
        pins.check(**run, router_model=None)
    pins.header["router_model"] = None  # a rules-only build
    pins.check(**run, router_model=None)


@pytest.mark.asyncio
async def test_the_modes_the_record_names_are_the_modes_that_ask_the_model() -> None:
    """``ROUTER_MODEL_MODES`` is what ``route`` does: every router mode, the rules abstaining."""
    from typing import get_args

    from chaoscypher_core.benchmark.pins import ROUTER_MODEL_MODES
    from chaoscypher_core.services.workflows.tools.engine.handlers.question_shape import route
    from chaoscypher_core.settings import GraphRAGSettings

    asked: list[object] = []

    async def chat(*args: object, **kwargs: object) -> dict[str, str]:
        asked.append(args)
        return {"content": '{"shape": "path", "reason": "two people"}'}

    for mode in get_args(GraphRAGSettings.model_fields["router"].annotation):
        asked.clear()
        await route("q", mode=mode, rules=None, chat=chat)
        assert bool(asked) == (mode in ROUTER_MODEL_MODES), mode
