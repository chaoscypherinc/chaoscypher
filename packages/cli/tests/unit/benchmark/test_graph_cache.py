# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from chaoscypher_cli.benchmark.graph_cache import (
    GraphCache,
    cache_key,
)
from chaoscypher_cli.benchmark.models import ModelConfig


DEDUP = "ollama/qwen3-embedding:4b"


def test_cache_key_stable():
    m = ModelConfig(provider="ollama", model="llama3.1:8b", label="L")
    k1 = cache_key(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        dedup_embedder=DEDUP,
    )
    k2 = cache_key(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        dedup_embedder=DEDUP,
    )
    assert k1 == k2


def test_cache_key_changes_on_extractor():
    a = ModelConfig(provider="ollama", model="llama", label="L")
    b = ModelConfig(provider="ollama", model="qwen", label="Q")
    assert cache_key(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=a,
        seed=42,
        temperature=0.0,
        dedup_embedder=DEDUP,
    ) != cache_key(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=b,
        seed=42,
        temperature=0.0,
        dedup_embedder=DEDUP,
    )


def test_cache_key_changes_on_corpus_version():
    m = ModelConfig(provider="ollama", model="llama", label="L")
    assert cache_key(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        dedup_embedder=DEDUP,
    ) != cache_key(
        corpus_id="ds1",
        corpus_version="2.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        dedup_embedder=DEDUP,
    )


def test_cache_key_changes_on_seed_and_temperature():
    """The pins are part of the key: a ``--seed 7`` run, or a graph extracted
    before #650 pinned temperature 0.0, must not be served to stages 2/3 of a
    run that asked for different decoding (2026-09-24 llm audit).
    """
    m = ModelConfig(provider="ollama", model="llama", label="L")
    base = cache_key(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        dedup_embedder=DEDUP,
    )
    assert base != cache_key(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=7,
        temperature=0.0,
        dedup_embedder=DEDUP,
    )
    assert base != cache_key(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.1,
        dedup_embedder=DEDUP,
    )


def test_cache_key_changes_with_the_dedup_embedder():
    """The embedder that merged the entities is part of the graph: the Book One
    graph re-indexed with qwen3-embedding:4b on 2026-10-02 still carried merges
    made with the local 0.6B model, and the key could not tell the two apart.
    """
    m = ModelConfig(provider="ollama", model="llama", label="L")
    pins = {"corpus_id": "ds1", "corpus_version": "1.0", "extractor": m, "seed": 42}
    base = cache_key(**pins, temperature=0.0, dedup_embedder=DEDUP)
    assert base != cache_key(
        **pins, temperature=0.0, dedup_embedder="local/Qwen/Qwen3-Embedding-0.6B"
    )
    assert base != cache_key(**pins, temperature=0.0, dedup_embedder="exact")
    cache = GraphCache(root=Path("x"), dedup_embedder=DEDUP)
    assert cache.key_for(**pins, temperature=0.0) == cache_key(
        **pins,
        temperature=0.0,
        dedup_embedder=DEDUP,
        extraction_recipe=cache.recipe_for("ds1"),
    )


@pytest.mark.asyncio
async def test_get_or_build_calls_builder_on_miss(tmp_path):
    cache = GraphCache(root=tmp_path / "cache", dedup_embedder=DEDUP)
    m = ModelConfig(provider="ollama", model="llama", label="L")

    async def fake_build(target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"sqlite-snapshot-data")  # noqa: ASYNC240

    out = await cache.get_or_build(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        builder=fake_build,
    )
    assert out.exists()
    assert out.read_bytes() == b"sqlite-snapshot-data"


@pytest.mark.asyncio
async def test_get_or_build_skips_builder_on_hit(tmp_path):
    cache = GraphCache(root=tmp_path / "cache", dedup_embedder=DEDUP)
    m = ModelConfig(provider="ollama", model="llama", label="L")

    builder = AsyncMock()

    async def fake_build(target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"v1")  # noqa: ASYNC240

    await cache.get_or_build(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        builder=fake_build,
    )
    await cache.get_or_build(
        corpus_id="ds1",
        corpus_version="1.0",
        extractor=m,
        seed=42,
        temperature=0.0,
        builder=builder,
    )
    builder.assert_not_called()


def test_clear_removes_root(tmp_path):
    cache = GraphCache(root=tmp_path / "cache", dedup_embedder=DEDUP)
    (cache._root).mkdir(parents=True)
    (cache._root / "marker").write_text("x")
    cache.clear()
    assert not (cache._root / "marker").exists()


def test_has_reports_whether_slot_holds_a_snapshot(tmp_path):
    cache = GraphCache(root=tmp_path / "cache", dedup_embedder=DEDUP)
    ext = ModelConfig(provider="ollama", model="m", label="M")
    other = ModelConfig(provider="ollama", model="n", label="N")
    pins = {"seed": 42, "temperature": 0.0}
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=ext, **pins)

    key = cache.key_for(corpus_id="c", corpus_version="1", extractor=ext, **pins)
    assert key == cache_key(
        corpus_id="c",
        corpus_version="1",
        extractor=ext,
        dedup_embedder=DEDUP,
        extraction_recipe=cache.recipe_for("c"),
        **pins,
    )
    # An empty slot directory is not a snapshot.
    (tmp_path / "cache" / key).mkdir(parents=True)
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=ext, **pins)

    (tmp_path / "cache" / key / "app.db").write_bytes(b"x")
    assert cache.has(corpus_id="c", corpus_version="1", extractor=ext, **pins)
    assert not cache.has(corpus_id="c", corpus_version="2", extractor=ext, **pins)
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=other, **pins)
    # The decoding pins are part of the slot: another seed or temperature is a miss.
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=ext, seed=7, temperature=0.0)
    assert not cache.has(corpus_id="c", corpus_version="1", extractor=ext, seed=42, temperature=0.7)


def test_cache_key_changes_with_the_resolver_recipe_and_not_without_it():
    """A resolved graph is another graph; a key built before the resolver existed still holds."""
    base = {
        "corpus_id": "c",
        "corpus_version": "1.0",
        "extractor": ModelConfig(provider="ollama", model="m", label="m"),
        "seed": 42,
        "temperature": 0.0,
        "dedup_embedder": "exact",
    }
    assert cache_key(**base) == cache_key(**base, resolution=None)
    assert cache_key(**base, resolution="abc123") != cache_key(**base)
    assert cache_key(**base, resolution="abc123") != cache_key(**base, resolution="def456")


def test_resolution_for_follows_the_graph_setting():
    from chaoscypher_cli.benchmark.graph_cache import resolution_for
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    assert len(resolution_for(settings) or "") == 12  # on by default since 2026-10-06
    settings.graph.resolve_identities = False
    assert resolution_for(settings) is None


# ---------------------------------------------------------------------------
# The extraction recipe keys the slot (audit H7, 2026-10-06)
# ---------------------------------------------------------------------------


def test_the_cache_keys_its_slots_with_the_settings_extraction_recipe():
    """Every GraphCache computes the recipe from the configured settings when not given one."""
    from chaoscypher_cli.benchmark.graph_cache import extraction_recipe_for
    from chaoscypher_core.app_config import get_settings

    cache = GraphCache(root=Path("x"), dedup_embedder=DEDUP)
    assert cache.recipe_for("c") == extraction_recipe_for(get_settings(), domain=None)
    assert len(cache.recipe_for("c")) == 12
    ext = ModelConfig(provider="ollama", model="m", label="M")
    pins = {"corpus_id": "c", "corpus_version": "1", "extractor": ext, "seed": 42}
    other = GraphCache(root=Path("x"), dedup_embedder=DEDUP, extraction_recipe="another")
    assert cache.key_for(**pins, temperature=0.0) != other.key_for(**pins, temperature=0.0)


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("source_processing", "dedup_require_type_compatibility", False),
        ("extraction", "dedup_borderline_penalty", 0.42),
        ("extraction", "semantic_dedup_threshold", 0.5),
        ("chunking", "max_chunk_size", 777),
        ("llm", "ollama_num_ctx", 4096),
        ("graph", "default_relationship_type", "linked_to"),
    ],
)
def test_the_extraction_recipe_moves_with_a_setting_that_shapes_the_graph(section, field, value):
    from chaoscypher_cli.benchmark.graph_cache import extraction_recipe_for
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    before = extraction_recipe_for(settings, domain=None)
    setattr(getattr(settings, section), field, value)
    assert extraction_recipe_for(settings, domain=None) != before


def test_the_extraction_recipe_ignores_retry_timing_and_the_settings_own_thinking():
    """The stage pins extraction thinking itself; a backoff only times the calls."""
    from chaoscypher_cli.benchmark.graph_cache import extraction_recipe_for
    from chaoscypher_core.settings import EngineSettings

    settings = EngineSettings()
    before = extraction_recipe_for(settings, domain=None)
    settings.extraction.llm_backoff_max_seconds += 5
    settings.llm.thinking_for_extraction = not settings.llm.thinking_for_extraction
    assert extraction_recipe_for(settings, domain=None) == before


def test_the_extraction_recipe_moves_with_the_extraction_prompt(monkeypatch):
    """Rendered on a fixed sample: an edit to the template moves it with no version bump."""
    from chaoscypher_cli.benchmark.graph_cache import extraction_recipe_for
    from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
        AIEntityExtractor,
    )
    from chaoscypher_core.settings import EngineSettings

    before = extraction_recipe_for(EngineSettings(), domain=None)
    monkeypatch.setattr(
        AIEntityExtractor,
        "RELATIONSHIP_HARVEST_TEMPLATE",
        AIEntityExtractor.RELATIONSHIP_HARVEST_TEMPLATE + "\nOne more rule.",
    )
    assert extraction_recipe_for(EngineSettings(), domain=None) != before


def _manifest(**over):
    fields = {
        "extractor": "ollama/gemma4:31b",
        "extractor_label": "Gemma 4 31B",
        "corpus_id": "war_and_peace_book1",
        "fixture_version": "1.1",
        "dedup_embedder": DEDUP,
        "graph_cache_key": "0" * 16,
    }
    fields.update(over)
    return fields


def test_a_pack_slot_is_the_slot_a_sweep_computes_under_the_same_recipe():
    """install.py seeds pack_slot; the sweep's GraphCache looks there (H7)."""
    from chaoscypher_cli.benchmark.graph_cache import dataset_corpus_identity, pack_slot

    cache = GraphCache(root=Path("x"), dedup_embedder=DEDUP, resolution="r1")
    domain, corpus_sha256 = dataset_corpus_identity("war_and_peace_book1")
    assert domain == "literary" and corpus_sha256 and len(corpus_sha256) == 64
    manifest = _manifest(
        resolution="r1",
        extraction_recipe=cache.recipe_for("war_and_peace_book1"),
        extraction_domain=domain,
        corpus_sha256=corpus_sha256,
    )
    key, recipe = pack_slot(manifest, seed=42, temperature=0.0)
    ext = ModelConfig(provider="ollama", model="gemma4:31b", label="Gemma 4 31B")
    assert recipe == cache.recipe_for("war_and_peace_book1")
    sweep = cache.key_for(
        corpus_id="war_and_peace_book1",
        corpus_version="1.1",
        extractor=ext,
        seed=42,
        temperature=0.0,
    )
    assert key == sweep
    # a pack that records neither (exported before 2026-10-06) keeps its legacy slot
    del manifest["extraction_domain"], manifest["corpus_sha256"]
    assert pack_slot(manifest, seed=42, temperature=0.0)[0] != sweep


def test_a_legacy_pack_is_credited_with_todays_recipe_only_when_its_key_proves_it():
    from chaoscypher_cli.benchmark.graph_cache import pack_cache_key, pack_extraction_recipe

    cache = GraphCache(root=Path("x"), dedup_embedder=DEDUP)
    proved = pack_cache_key(
        _manifest(),
        seed=42,
        temperature=0.0,
        resolution=None,
        extraction_recipe=cache.recipe_for("an-unknown-corpus"),  # no domain, as _manifest()
    )
    assert pack_extraction_recipe(
        _manifest(graph_cache_key=proved), seed=42, temperature=0.0
    ) == cache.recipe_for("an-unknown-corpus")
    # exported before the recipe: its graph's recipe is unknown, its slot the legacy one
    assert pack_extraction_recipe(_manifest(), seed=42, temperature=0.0) is None


# ---------------------------------------------------------------------------
# The extraction domain and the corpus bytes key the slot (audit wave 2)
# ---------------------------------------------------------------------------

EXT = ModelConfig(provider="ollama", model="gemma4:31b", label="G")
PINS = {
    "corpus_id": "book",
    "corpus_version": "1.0",
    "extractor": EXT,
    "seed": 42,
    "temperature": 0.0,
}


def _cache(tmp_path: Path, identity: tuple[str | None, str | None]) -> GraphCache:
    return GraphCache(
        root=tmp_path / "gc",
        dedup_embedder=DEDUP,
        extraction_recipe="e1",
        corpus_identity=lambda _cid: identity,
    )


def test_the_key_changes_with_the_extraction_domain_and_the_corpus_bytes(tmp_path: Path) -> None:
    """A literary and a generic extraction of one corpus were one slot: the A/B's second run reused the first."""
    literary = _cache(tmp_path, ("literary", "a" * 64)).key_for(**PINS)
    generic = _cache(tmp_path, ("generic", "a" * 64)).key_for(**PINS)
    edited = _cache(tmp_path, ("literary", "b" * 64)).key_for(**PINS)
    assert len({literary, generic, edited}) == 3
    base = {
        "corpus_id": "book",
        "corpus_version": "1.0",
        "extractor": EXT,
        "seed": 42,
        "temperature": 0.0,
        "dedup_embedder": DEDUP,
    }
    assert cache_key(**base, domain="literary") != cache_key(**base, domain="generic")
    assert cache_key(**base, corpus_sha256="a" * 64) != cache_key(**base, corpus_sha256="b" * 64)
    assert cache_key(**base) == cache_key(
        **base, domain=None, corpus_sha256=None
    )  # legacy slots hold


def test_the_dataset_identity_is_the_bundles_domain_and_its_corpus_sha(tmp_path: Path) -> None:
    import hashlib

    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
    from chaoscypher_cli.benchmark.graph_cache import dataset_corpus_identity, memo_file_sha256

    bundle = load_dataset_bundle("war_and_peace_book1")
    domain, sha = dataset_corpus_identity("war_and_peace_book1")
    assert domain == bundle.extraction_domain == "literary"
    assert sha == hashlib.sha256(bundle.corpus_path.read_bytes()).hexdigest()
    assert dataset_corpus_identity("no_such_dataset") == (None, None)
    corpus = tmp_path / "c.txt"
    corpus.write_text("one", encoding="utf-8")
    first = memo_file_sha256(corpus)
    corpus.write_text("two!", encoding="utf-8")
    assert memo_file_sha256(corpus) != first  # the memo follows the bytes


def test_a_dataset_of_several_packs_records_them_sorted_and_joined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``packs: [literary, historical]`` is ``historical+literary`` in the identity and the slot.

    One pack's label is its name and the floor's is ``generic``, so every slot
    and pack recorded before packs existed keeps its identity.
    """
    from chaoscypher_cli.benchmark.discovery import user_dataset_root
    from chaoscypher_cli.benchmark.graph_cache import (
        dataset_corpus_identity,
        pack_corpus_identity,
        pack_slot_record,
    )

    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    pdir = user_dataset_root() / "two_packs"
    pdir.mkdir(parents=True)
    (pdir / "c.txt").write_text("Pierre visited Moscow.", encoding="utf-8")
    (pdir / "manifest.yaml").write_text(
        "id: two_packs\nkind: extraction\nversion: '1.0'\npacks: [literary, historical]\n"
        "corpus_path: c.txt\n",
        encoding="utf-8",
    )
    domain, _ = dataset_corpus_identity("two_packs")
    assert domain == "historical+literary"
    manifest = {
        "corpus_id": "two_packs",
        "fixture_version": "1.0",
        "extractor": "ollama/gemma4:31b",
        "dedup_embedder": "exact",
        "extraction_domain": "literary+historical",  # a hand-written order reads the same
    }
    assert pack_corpus_identity(manifest)[0] == "historical+literary"
    record = pack_slot_record(manifest, seed=42, temperature=0.0, extraction_recipe=None)
    assert record["extraction_domain"] == "historical+literary"


def test_the_recipe_of_several_packs_is_their_stacks_and_read_sorted() -> None:
    """Sources carry a list of packs: a two-pack recipe hashes that stack, in any spelling."""
    from chaoscypher_cli.benchmark.graph_cache import extraction_recipe_for
    from chaoscypher_core.app_config import get_settings

    settings = get_settings()
    two = extraction_recipe_for(settings, domain="historical+literary")
    assert two == extraction_recipe_for(settings, domain="literary+historical")
    assert two != extraction_recipe_for(settings, domain="literary")
    assert two != extraction_recipe_for(settings, domain="historical")


@pytest.mark.asyncio
async def test_a_built_slot_records_what_keyed_it_and_a_hit_is_checked_against_it(
    tmp_path: Path,
) -> None:
    from chaoscypher_cli.benchmark.graph_cache import SLOT_RECORD, GraphCacheMismatchError

    cache = _cache(tmp_path, ("literary", "a" * 64))

    async def build(target: Path) -> None:
        target.write_bytes(b"graph")  # noqa: ASYNC240

    snapshot = await cache.get_or_build(**PINS, builder=build)
    import json

    record = json.loads((snapshot.parent / SLOT_RECORD).read_text(encoding="utf-8"))
    assert (record["extraction_domain"], record["corpus_sha256"]) == ("literary", "a" * 64)
    assert record["extraction_recipe"] == "e1" and record["extractor"] == "ollama/gemma4:31b"
    builder = AsyncMock()
    assert await cache.get_or_build(**PINS, builder=builder) == snapshot  # a matching hit
    builder.assert_not_called()
    # a slot filled with another domain's graph (seeded by hand, or a key that could not see it)
    record["extraction_domain"] = "generic"
    (snapshot.parent / SLOT_RECORD).write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(
        GraphCacheMismatchError, match=r"extraction_domain: slot 'generic', run 'literary'"
    ):
        await cache.get_or_build(**PINS, builder=builder)
    builder.assert_not_called()  # never rebuilt over, never served


@pytest.mark.asyncio
async def test_a_slot_without_a_record_is_served_with_a_warning(tmp_path: Path) -> None:
    cache = _cache(tmp_path, (None, None))
    slot = cache.slot_path(cache.key_for(**PINS))
    slot.mkdir(parents=True)
    (slot / "app.db").write_bytes(b"old")
    assert await cache.get_or_build(**PINS, builder=AsyncMock()) == slot / "app.db"


@pytest.mark.asyncio
async def test_a_fresh_build_replaces_an_adopted_slot_and_reuse_still_serves_it(
    tmp_path: Path,
) -> None:
    """Audit wave 3: install.py --adopt-current-recipe seeded today's slot with an older pack's
    graph; a run that extracted (REUSE_GRAPH unset) was served that graph and its own dropped.
    """
    import json

    from chaoscypher_cli.benchmark.graph_cache import SLOT_RECORD

    cache = _cache(tmp_path, ("literary", "a" * 64))
    key = cache.key_for(**PINS)
    slot = cache.slot_path(key)
    slot.mkdir(parents=True)
    (slot / "app.db").write_bytes(b"the v3 pack's graph")
    (slot / "app.db-wal").write_bytes(b"stale wal")
    cache.write_slot_record(key, {**cache.slot_record(**PINS), "adopted_from": "pack-v3"})
    assert cache.slot_adopted_from(key) == "pack-v3"

    # a REUSE_GRAPH run (no fresh graph) is served the adopted graph: that is what adopting is for
    noop = AsyncMock()
    assert await cache.get_or_build(**PINS, builder=noop) == slot / "app.db"
    noop.assert_not_called()

    async def fresh(target: Path) -> None:
        target.write_bytes(b"freshly extracted")  # noqa: ASYNC240

    snapshot = await cache.get_or_build(**PINS, builder=fresh, building=True)
    assert snapshot.read_bytes() == b"freshly extracted"  # the build wins
    assert not (slot / "app.db-wal").exists()
    record = json.loads((slot / SLOT_RECORD).read_text(encoding="utf-8"))
    assert "adopted_from" not in record and cache.slot_adopted_from(key) is None
    # a slot this cache built is not adopted: a later build keeps it (the cache's purpose)
    again = AsyncMock()
    assert await cache.get_or_build(**PINS, builder=again, building=True) == snapshot
    again.assert_not_called()


def test_a_domain_config_edit_moves_the_cache_recipe_of_that_domains_corpus(monkeypatch):
    """Audit wave 4 (M3): the slot's recipe is the corpus's domain's, as the cache computes it.

    Emptying the literary domain's edge type constraints moved the literary
    recipe but not the cache's, which hashed the generic plan for every corpus.
    """
    from chaoscypher_cli.benchmark.graph_cache import extraction_recipe_for
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        ConfigurableDomain,
    )

    def literary_corpus(_corpus_id: str) -> tuple[str | None, str | None]:
        return "literary", "corpus-sha"

    ext = ModelConfig(provider="ollama", model="m", label="M")
    pins = {"corpus_id": "c", "corpus_version": "1", "extractor": ext, "seed": 42}
    before_cache = GraphCache(root=Path("x"), dedup_embedder=DEDUP, corpus_identity=literary_corpus)
    before = before_cache.recipe_for("c")
    before_key = before_cache.key_for(**pins, temperature=0.0)
    assert before == extraction_recipe_for(get_settings(), domain="literary")
    assert before_cache.slot_record(**pins, temperature=0.0)["extraction_recipe"] == before
    original = ConfigurableDomain.get_edge_type_constraints
    monkeypatch.setattr(
        ConfigurableDomain,
        "get_edge_type_constraints",
        lambda self: {} if self.name == "literary" else original(self),
    )
    after_cache = GraphCache(root=Path("x"), dedup_embedder=DEDUP, corpus_identity=literary_corpus)
    assert after_cache.recipe_for("c") != before
    assert after_cache.key_for(**pins, temperature=0.0) != before_key
