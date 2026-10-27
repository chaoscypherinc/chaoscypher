# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""GraphCache: per-(corpus, extractor, pipeline) extracted-graph snapshots.

Stores the raw SQLite database file produced by an extraction run so that
follow-up embedding + chat benches can re-use it instead of re-extracting.

Key: SHA-256 of (corpus_id, corpus_version, extractor.provider,
extractor.model, seed, temperature, dedup_embedder, EXT_PIPELINE_VERSION),
plus the identity resolver's recipe and the extraction recipe
(:func:`~chaoscypher_core.benchmark.extraction_recipe.extraction_recipe`:
chunking, extraction, dedup thresholds, the context window, extraction
thinking and the extraction prompts rendered on a fixed sample) when known.
The recipes are computed from the code and settings; EXT_PIPELINE_VERSION is
a last resort for a change neither can see. Since wave 2 of the audit
(2026-10-06) the key also names the **extraction domain** the stage forces
(the label of the dataset's packs: one pack's name, several sorted and
joined with ``+``, ``generic`` for the floor alone) and the **corpus bytes**
(their sha256): a
literary and a generic extraction of one corpus, or a corpus edited under an
unchanged version string, were one slot until then, and the second run of the
literary/generic A/B would have scored the first one's graph.
:func:`dataset_corpus_identity` reads both for a corpus id; a reference pack
records them in its manifest (``extraction_domain``, ``corpus_sha256``).

Every slot :meth:`GraphCache.get_or_build` fills records what keyed it
(``slot.json``: corpus, domain, corpus sha256, extractor, pins, dedup
embedder, resolver and extraction recipes); a later hit whose expectations
differ from the record is refused (:class:`GraphCacheMismatchError`), never
served.

``dedup_embedder`` is the ``provider/model`` whose vectors decided the
semantic entity merges while the graph was extracted (``"exact"`` when
semantic deduplication is off). It is part of the graph, not of retrieval:
the 2026-10-02 switch to qwen3-embedding:4b re-indexed the Book One graph
but every pack still carried merges made with the local 0.6B model, and the
key had no way to tell them apart. :func:`dedup_embedder_for` picks it the
way the extraction stage does.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.cache.export import file_sha256


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.config import BenchmarkConfig
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


# Schema-level breaks only: bump when the snapshot's shape changes in a way no
# behaviour sample can see (a migration, new entity fields). Code and settings
# changes to extraction, dedup, evidence, orientation, prompts and domains move
# the extraction recipe on their own (extraction_recipe.extraction_code_digest);
# the resolver's move its recipe.
# "2" (2026-09-24): graphs cached before #650 were extracted unpinned
# (temperature 0.1, no seed) and keyed without the pins, so stages 2/3 of
# a `full` run kept scoring them after extraction was made deterministic.
EXT_PIPELINE_VERSION = "2"

EXACT_DEDUP = "exact"
"""The ``dedup_embedder`` of a graph whose entity merges used no embedder."""


def dedup_embedder_for(config: BenchmarkConfig, settings: EngineSettings | Settings) -> str:
    """The ``provider/model`` the extraction stage merges entities with.

    The config's ``defaults.embedder`` pins it (then its first embedder); a
    config with neither falls back to the settings' embedder, which is what
    an unpinned extraction would use. :data:`EXACT_DEDUP` when the settings
    turn semantic deduplication off, since no embedder touches the graph then.
    """
    if settings.source_processing.entity_deduplication_mode != "semantic":
        return EXACT_DEDUP
    if config.default_embedder:
        return config.default_embedder
    if config.embedders:
        return config.embedders[0].model_id
    return f"{settings.embedding.provider}/{settings.embedding.model}"


def cache_key(
    *,
    corpus_id: str,
    corpus_version: str,
    extractor: ModelConfig,
    seed: int,
    temperature: float,
    dedup_embedder: str,
    resolution: str | None = None,
    extraction_recipe: str | None = None,
    domain: str | None = None,
    corpus_sha256: str | None = None,
) -> str:
    """Return a 16-char hex digest uniquely identifying the cache slot.

    Args:
        corpus_id: Stable dataset identifier (e.g. ``"war_and_peace_tiny"``).
        corpus_version: Dataset version string (e.g. ``"1.0"``).
        extractor: The LLM used for extraction; provider and model are
            incorporated into the key so swapping models yields a new slot.
        seed: Decoding seed the extraction ran with.
        temperature: Decoding temperature the extraction ran with. Both
            pins are part of the key: a ``--seed 7`` run must not reuse the
            graph a seed-42 run produced.
        dedup_embedder: ``provider/model`` that decided the semantic entity
            merges, or :data:`EXACT_DEDUP`; a graph merged with another
            embedder is another graph.

        resolution: The identity resolver's recipe
            (:func:`~chaoscypher_core.services.graph.resolution.resolution_recipe`)
            when the graph is resolved after commit, None when it is not: a
            resolved graph is another graph. Left out of the payload when
            None, so every key built before the resolver existed still holds.
        extraction_recipe: :func:`extraction_recipe_for` the settings the graph
            was extracted under; None only for a pack exported before the
            recipe existed (its legacy slot).
        domain: The extraction domain the stage forces (the dataset's
            ``domain``); None only where it is unknown (a pack exported before
            it was recorded: its legacy slot).
        corpus_sha256: The sha256 of the corpus file's bytes; None only where
            unknown, as for ``domain``.

    Returns:
        First 16 hex characters of SHA-256 over the joined input fields.
    """
    payload = (
        f"{corpus_id}|{corpus_version}|{extractor.provider}|{extractor.model}"
        f"|{seed}|{temperature}|{dedup_embedder}|{EXT_PIPELINE_VERSION}"
    )
    if resolution:
        payload += f"|resolution={resolution}"
    if extraction_recipe:
        payload += f"|extraction={extraction_recipe}"
    if domain:
        payload += f"|domain={domain}"
    if corpus_sha256:
        payload += f"|corpus={corpus_sha256}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def memo_file_sha256(path: Path) -> str:
    """The hex sha256 of a file's bytes (:func:`~chaoscypher_core.cache.export.file_sha256`), memoised per (path, size, mtime)."""
    stat = path.stat()
    return _memo_file_sha256(str(path.resolve()), stat.st_size, stat.st_mtime_ns)


@cache
def _memo_file_sha256(path: str, _size: int, _mtime_ns: int) -> str:
    """:func:`memo_file_sha256` behind its memo key."""
    return file_sha256(Path(path))


CorpusIdentity = tuple[str | None, str | None]
"""``(extraction domain, corpus sha256)`` of a corpus; either None when unknown.

The extraction domain is the label of the packs the corpus is extracted with
(:func:`~chaoscypher_core.benchmark.packs.packs_label`: one pack's name, the
packs sorted and joined with ``+``, ``generic`` for the floor alone), so a
slot and a pack recorded with one domain name keep their identity."""


def dataset_corpus_identity(corpus_id: str) -> CorpusIdentity:
    """The extraction domain and corpus sha256 of a dataset, as the extraction stage reads them.

    The label of the dataset bundle's packs (what :class:`ExtractionDataset`
    extracts with: ``DatasetBundle.extraction_domain``) and the sha256 of its
    corpus file; ``(None, None)`` for an id no dataset has (a test's corpus),
    whose key then omits both.
    """
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    try:
        bundle = load_dataset_bundle(corpus_id)
    except FileNotFoundError, ValueError:
        return None, None
    return bundle.extraction_domain, memo_file_sha256(bundle.corpus_path)


def pack_corpus_identity(manifest: dict[str, Any]) -> CorpusIdentity:
    """A pack manifest's ``extraction_domain`` and ``corpus_sha256``, read defensively.

    Both are optional manifest fields (packs exported before 2026-10-06 carry
    neither and keep their legacy slot); a missing, empty or non-string value
    reads as unknown. A label naming several packs is read with its packs
    sorted (:func:`~chaoscypher_core.benchmark.packs.packs_of_label`), the one
    spelling :func:`dataset_corpus_identity` computes.
    """
    from chaoscypher_core.benchmark.packs import packs_label, packs_of_label

    def text(name: str) -> str | None:
        value = manifest.get(name)
        return value if isinstance(value, str) and value else None

    domain = text("extraction_domain")
    return (packs_label(packs_of_label(domain)) if domain else None), text("corpus_sha256")


def resolution_for(settings: EngineSettings | Settings) -> str | None:
    """The resolver recipe the extraction stage runs with; None when ``graph.resolve_identities`` is off."""
    if not settings.graph.resolve_identities:
        return None
    from chaoscypher_core.services.graph.resolution import resolution_recipe

    return resolution_recipe()


def extraction_stage_thinking() -> bool:
    """The extraction thinking switch the benchmark's extraction stage pins (its dataset's default)."""
    from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset

    return bool(ExtractionDataset.__dataclass_fields__["thinking"].default)


def extraction_recipe_for(settings: EngineSettings | Settings, *, domain: str | None) -> str:
    """The extraction recipe the benchmark's extraction stage runs under with ``settings``.

    :func:`~chaoscypher_core.benchmark.extraction_recipe.extraction_recipe` with
    the thinking switch the stage pins (:func:`extraction_stage_thinking`),
    since the stage overrides the settings' own, and the extraction ``domain``
    the stage forces for the corpus (the dataset's, as
    :func:`dataset_corpus_identity` reads it; a pack's ``extraction_domain``).
    ``domain`` is required: until audit wave 4 (M3) every caller omitted it, so
    the recipe hashed the generic fallback's plan and an edit to the literary
    domain's config (its ``edge_type_constraints`` emptied) moved the literary
    recipe but left every cache slot's key where it was. None is the generic
    fallback, for a corpus with no forced domain.

    ``domain`` is the packs' label (:data:`CorpusIdentity`), read sorted
    (:func:`~chaoscypher_core.benchmark.packs.packs_label`): the recipe
    samples the plan and the dedup pipeline under the stack the label names
    (``DomainStack.from_domain_name`` and the resolvers read a label), as the
    extraction itself runs with every pack.
    """
    from chaoscypher_core.benchmark.extraction_recipe import extraction_recipe
    from chaoscypher_core.benchmark.packs import packs_label, packs_of_label

    if domain is not None:
        domain = packs_label(packs_of_label(domain))
    return extraction_recipe(settings, thinking=extraction_stage_thinking(), domain=domain)


def pack_cache_key(
    manifest: dict[str, Any],
    *,
    seed: int,
    temperature: float,
    resolution: str | None,
    extraction_recipe: str | None,
) -> str:
    """The graph cache key of a reference pack's graph under the given recipes.

    The corpus, extractor and dedup embedder come from the manifest; the
    decoding pins and both recipes from the caller (:func:`pack_slot` passes
    the manifest's own, ``resolve_pack.py`` the resolver's for its copy).
    """
    from chaoscypher_cli.benchmark.models import ModelConfig
    from chaoscypher_core.benchmark.reference import split_model_id

    provider, model = split_model_id(str(manifest["extractor"]))
    domain, corpus_sha256 = pack_corpus_identity(manifest)
    return cache_key(
        corpus_id=str(manifest["corpus_id"]),
        corpus_version=str(manifest["fixture_version"]),
        extractor=ModelConfig(
            provider=provider,
            model=model,
            label=str(manifest.get("extractor_label") or manifest["extractor"]),
        ),
        seed=seed,
        temperature=temperature,
        dedup_embedder=str(manifest["dedup_embedder"]),
        resolution=resolution,
        extraction_recipe=extraction_recipe,
        domain=domain,
        corpus_sha256=corpus_sha256,
    )


def pack_extraction_recipe(
    manifest: dict[str, Any], *, seed: int, temperature: float
) -> str | None:
    """The extraction recipe a reference pack's graph was built under, or None when unknown.

    The manifest's ``extraction_recipe`` when it records one. A manifest
    without it (exported before the field) is credited with today's recipe
    only when its ``graph_cache_key`` is the key today's recipe computes: the
    export then keyed the graph under the current recipe already.
    """
    recorded = manifest.get("extraction_recipe")
    if recorded:
        return str(recorded)
    from chaoscypher_core.app_config import get_settings

    current = extraction_recipe_for(get_settings(), domain=pack_corpus_identity(manifest)[0])
    key = pack_cache_key(
        manifest,
        seed=seed,
        temperature=temperature,
        resolution=manifest.get("resolution") or None,
        extraction_recipe=current,
    )
    return current if manifest.get("graph_cache_key") == key else None


def pack_slot_record(
    manifest: dict[str, Any], *, seed: int, temperature: float, extraction_recipe: str | None
) -> dict[str, Any]:
    """The ``slot.json`` record of a pack's own slot: what :func:`pack_cache_key` keyed it with.

    The same fields :meth:`GraphCache.slot_record` writes for a slot a sweep
    built, from the manifest (its recorded domain and corpus sha256, None for
    a pack that records neither) and the recipe :func:`pack_slot` credits it with.
    """
    domain, corpus_sha256 = pack_corpus_identity(manifest)
    return {
        "corpus_id": str(manifest["corpus_id"]),
        "corpus_version": str(manifest["fixture_version"]),
        "extraction_domain": domain,
        "corpus_sha256": corpus_sha256,
        "extractor": str(manifest["extractor"]),
        "seed": seed,
        "temperature": temperature,
        "dedup_embedder": str(manifest["dedup_embedder"]),
        "resolution": manifest.get("resolution") or None,
        "extraction_recipe": extraction_recipe,
    }


def pack_slot(manifest: dict[str, Any], *, seed: int, temperature: float) -> tuple[str, str | None]:
    """``(key, extraction recipe)`` of the slot a pack's graph belongs in, from the manifest's own recipes.

    install.py seeds this slot and ``resolve_pack.py`` checks a source pack
    against it; a sweep finds the graph there when its own recipes match.
    """
    recipe = pack_extraction_recipe(manifest, seed=seed, temperature=temperature)
    key = pack_cache_key(
        manifest,
        seed=seed,
        temperature=temperature,
        resolution=manifest.get("resolution") or None,
        extraction_recipe=recipe,
    )
    return key, recipe


def default_workspace() -> Path:
    """The benchmark workspace whose ``graph_cache/`` every tool reads by default.

    ``benchmark run``, ``benchmark reference export`` and
    ``internal/benchmarks/reference/install.py`` share it (audit wave 3: install
    seeded ``<paths.data_dir>/benchmark/workspace`` while the CLI read
    ``user_benchmark_root()/workspace``, two directories unless
    ``CHAOSCYPHER_DATA_DIR`` was set).
    """
    from chaoscypher_cli.benchmark.discovery import user_benchmark_root

    return user_benchmark_root() / "workspace"


def _unlink_sqlite(path: Path) -> None:
    """Remove a SQLite file and its ``-wal`` / ``-shm`` siblings, when present."""
    for stale in (path, *(path.with_name(f"{path.name}{s}") for s in ("-wal", "-shm"))):
        stale.unlink(missing_ok=True)


class GraphCacheMissError(RuntimeError):
    """``REUSE_GRAPH`` asked for a cached graph and its slot is empty."""


class GraphCacheMismatchError(RuntimeError):
    """A filled slot records another domain, corpus or recipe than the run expects."""


SLOT_RECORD = "slot.json"
"""The file in a slot that records what keyed its graph (:meth:`GraphCache.slot_record`)."""

SNAPSHOT_SHA256 = "graph_snapshot_sha256"
"""The ``slot.json`` key caching the sha256 of the slot's ``app.db`` (:meth:`GraphCache.served_graph`).

Written by whoever writes the snapshot (the builder's :meth:`GraphCache.get_or_build`,
``install.py``), computed once and cached for a slot recorded before it; never
part of :meth:`GraphCache.slot_record`, which is what keys the slot. A reader
compares it against the snapshot's own sha256, never trusts it (audit wave 6).
"""


@dataclass
class GraphCache:
    """File-system cache of extracted graph snapshots.

    Storage layout: ``<root>/<key>/app.db``

    Attributes:
        root: Root directory for the cache. Need not exist yet; it is
            created on first write.
        dedup_embedder: The ``provider/model`` (or :data:`EXACT_DEDUP`) the
            graphs in this cache merged their entities with; every key the
            cache computes carries it.
        resolution: The identity resolver's recipe (:func:`resolution_for`),
            None when the graphs are not resolved.
        extraction_recipe: An extraction recipe every key of this cache uses
            (a caller that knows it: a test, a pack's own recipe). When not
            given, each corpus's recipe is :func:`extraction_recipe_for` the
            configured settings under the corpus's extraction domain
            (:attr:`corpus_identity`), computed once per domain
            (:meth:`recipe_for`): one recipe for the whole cache hashed the
            generic plan for every corpus (audit wave 4, M3).
        corpus_identity: ``corpus_id -> (extraction domain, corpus sha256)``
            (:func:`dataset_corpus_identity`); every key the cache computes
            carries both.
    """

    root: Path
    dedup_embedder: str
    resolution: str | None = None
    extraction_recipe: str | None = None
    corpus_identity: Callable[[str], CorpusIdentity] = field(
        default=dataset_corpus_identity, repr=False
    )

    def __post_init__(self) -> None:
        """Resolve `root` to a Path; the per-domain recipes are computed on first use."""
        self._root = Path(self.root)
        self._recipes: dict[str | None, str] = {}

    def recipe_for(self, corpus_id: str) -> str:
        """The extraction recipe a slot of ``corpus_id`` is keyed with.

        :attr:`extraction_recipe` when the cache was given one, else
        :func:`extraction_recipe_for` the configured settings under the
        corpus's extraction domain, once per domain.
        """
        if self.extraction_recipe is not None:
            return self.extraction_recipe
        domain = self.corpus_identity(corpus_id)[0]
        if domain not in self._recipes:
            from chaoscypher_core.app_config import get_settings

            self._recipes[domain] = extraction_recipe_for(get_settings(), domain=domain)
        return self._recipes[domain]

    def slot_path(self, key: str) -> Path:
        """The directory of a cache slot (``<root>/<key>``)."""
        return self._slot(key)

    def _slot(self, key: str) -> Path:
        """Return the on-disk path for a cache key."""
        return self._root / key

    def key_for(
        self,
        *,
        corpus_id: str,
        corpus_version: str,
        extractor: ModelConfig,
        seed: int,
        temperature: float,
    ) -> str:
        """Return the cache key for one (corpus, extractor, decoding pins) slot.

        Args:
            corpus_id: Stable dataset identifier.
            corpus_version: Dataset version string.
            extractor: The LLM used for extraction.
            seed: Decoding seed the extraction ran with (part of the key).
            temperature: Decoding temperature (part of the key).

        Returns:
            The same 16-char key :func:`cache_key` computes, with the corpus's
            extraction domain and sha256 (:attr:`corpus_identity`).
        """
        record = self.slot_record(
            corpus_id=corpus_id,
            corpus_version=corpus_version,
            extractor=extractor,
            seed=seed,
            temperature=temperature,
        )
        return cache_key(
            corpus_id=corpus_id,
            corpus_version=corpus_version,
            extractor=extractor,
            seed=seed,
            temperature=temperature,
            dedup_embedder=self.dedup_embedder,
            resolution=self.resolution,
            extraction_recipe=record["extraction_recipe"],
            domain=record["extraction_domain"],
            corpus_sha256=record["corpus_sha256"],
        )

    def slot_record(
        self,
        *,
        corpus_id: str,
        corpus_version: str,
        extractor: ModelConfig,
        seed: int,
        temperature: float,
    ) -> dict[str, Any]:
        """What keys one slot, as ``slot.json`` records it: every component of :func:`cache_key`."""
        domain, corpus_sha256 = self.corpus_identity(corpus_id)
        return {
            "corpus_id": corpus_id,
            "corpus_version": corpus_version,
            "extraction_domain": domain,
            "corpus_sha256": corpus_sha256,
            "extractor": f"{extractor.provider}/{extractor.model}",
            "seed": seed,
            "temperature": temperature,
            "dedup_embedder": self.dedup_embedder,
            "resolution": self.resolution,
            "extraction_recipe": self.recipe_for(corpus_id),
        }

    def check_slot(self, key: str, expected: dict[str, Any]) -> None:
        """Refuse a filled slot whose ``slot.json`` records anything other than ``expected``.

        A slot without a record (filled before records existed, or by hand)
        is served with a warning.

        Raises:
            GraphCacheMismatchError: Naming each field that differs.
        """
        record_path = self._slot(key) / SLOT_RECORD
        if not record_path.is_file():
            logger.warning("graph_cache_unrecorded_slot", key=key, path=str(record_path))
            return
        recorded = json.loads(record_path.read_text(encoding="utf-8"))
        differs = {
            name: (recorded.get(name), value)
            for name, value in expected.items()
            if recorded.get(name) != value
        }
        if differs:
            detail = "; ".join(
                f"{n}: slot {r!r}, run {v!r}" for n, (r, v) in sorted(differs.items())
            )
            msg = (
                f"graph cache slot {key} holds another graph than this run asks for ({detail}); "
                "never served: clear the slot or re-extract"
            )
            raise GraphCacheMismatchError(msg)

    def slot_adopted_from(self, key: str) -> str | None:
        """The pack a slot was seeded from by ``install.py --adopt-current-recipe``, or None.

        An adopted record says "this graph is pack X's, seeded under today's
        key, not extracted here" (``adopted_from`` in ``slot.json``).
        """
        record_path = self._slot(key) / SLOT_RECORD
        if not record_path.is_file():
            return None
        adopted = json.loads(record_path.read_text(encoding="utf-8")).get("adopted_from")
        return str(adopted) if adopted else None

    def served_graph(self, key: str) -> dict[str, Any]:
        """What a chat row records about the graph in slot ``key``, read from its ``slot.json``.

        :func:`~chaoscypher_core.benchmark.chat_run.graph_identity` of the
        slot record (an adopted slot names the pack's own fields) with the
        snapshot's sha256 and the key. Audit wave 5: the row read the domain
        and the corpus sha256 from the dataset, so a slot holding another
        domain's graph was recorded under the dataset's. A slot without a
        record (filled by hand) records its sha256 and nothing else it cannot
        know.

        The sha256 is always the snapshot's own (:func:`memo_file_sha256`, cached
        per size and mtime), compared against the one the record caches
        (:data:`SNAPSHOT_SHA256`). Audit wave 6: the cached value was trusted,
        so an ``app.db`` replaced by hand gave rows the old graph's sha256.
        On a mismatch the record describes another snapshot: the row records
        the sha256 alone, like a slot filled by hand, and a warning says so.
        """
        from chaoscypher_core.benchmark.chat_run import graph_identity

        slot = self._slot(key)
        record_path = slot / SLOT_RECORD
        record: dict[str, Any] = (
            json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
        )
        snapshot = slot / "app.db"
        recorded = record.get(SNAPSHOT_SHA256)
        if not snapshot.is_file():
            return graph_identity(record, snapshot_sha256=recorded, cache_key=key)
        sha = memo_file_sha256(snapshot)
        if record and not recorded:
            self.write_slot_record(key, {**record, SNAPSHOT_SHA256: sha})
        elif recorded and recorded != sha:
            logger.warning(
                "graph_cache_snapshot_replaced",
                cache_key=key,
                recorded_sha256=recorded,
                snapshot_sha256=sha,
                hint="app.db is not the snapshot slot.json describes; its identity is unknown",
            )
            record = {}
        return graph_identity(record, snapshot_sha256=sha, cache_key=key)

    def write_slot_record(self, key: str, record: dict[str, Any]) -> Path:
        """Write ``slot.json`` for a slot (the builder's, or install.py's for a seeded pack)."""
        path = self._slot(key) / SLOT_RECORD
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def has(
        self,
        *,
        corpus_id: str,
        corpus_version: str,
        extractor: ModelConfig,
        seed: int,
        temperature: float,
    ) -> bool:
        """Return whether the slot for this (corpus, extractor, pins) holds a snapshot.

        Args:
            corpus_id: Stable dataset identifier.
            corpus_version: Dataset version string.
            extractor: The LLM used for extraction.
            seed: Decoding seed the extraction ran with (part of the key).
            temperature: Decoding temperature (part of the key).

        Returns:
            True when ``<root>/<key>/app.db`` exists.
        """
        key = self.key_for(
            corpus_id=corpus_id,
            corpus_version=corpus_version,
            extractor=extractor,
            seed=seed,
            temperature=temperature,
        )
        return (self._slot(key) / "app.db").exists()

    async def get_or_build(
        self,
        *,
        corpus_id: str,
        corpus_version: str,
        extractor: ModelConfig,
        seed: int,
        temperature: float,
        builder: Callable[[Path], Awaitable[None]],
        building: bool = False,
    ) -> Path:
        """Return the cached snapshot path; call builder on cache miss.

        On a cache hit the builder is not called. On a miss, the builder
        is called with the *target* path where it must write a valid SQLite
        file. A ``RuntimeError`` is raised if the builder returns without
        producing the file.

        ``building`` says the caller holds a fresh extraction (a run without
        ``REUSE_GRAPH``). Then a slot ``install.py --adopt-current-recipe``
        seeded (:meth:`slot_adopted_from`) is never served: it holds an older
        pack's graph under today's key, and until audit wave 3 the fresh
        extraction was dropped for it without a word. The adopted graph is
        removed (logged) and the builder fills the slot, whose new record no
        longer names a pack.

        Args:
            corpus_id: Stable dataset identifier.
            corpus_version: Dataset version string.
            extractor: The LLM used for extraction.
            seed: Decoding seed the extraction ran with (part of the key).
            temperature: Decoding temperature (part of the key).
            builder: Async callable ``(target: Path) -> None`` that writes
                the SQLite snapshot to *target*.
            building: The caller has just extracted the graph (see above).

        Returns:
            Path to the cached ``app.db`` snapshot.

        Raises:
            RuntimeError: If the builder did not produce a file at the
                expected target path.
            GraphCacheMismatchError: If the filled slot records another
                domain, corpus or recipe (:meth:`check_slot`).
        """
        pins: dict[str, Any] = {
            "corpus_id": corpus_id,
            "corpus_version": corpus_version,
            "extractor": extractor,
            "seed": seed,
            "temperature": temperature,
        }
        key = self.key_for(**pins)
        record = self.slot_record(**pins)
        slot = self._slot(key)
        snapshot = slot / "app.db"
        adopted = self.slot_adopted_from(key) if snapshot.exists() else None
        if adopted is not None and building:
            logger.warning(
                "graph_cache_adopted_slot_replaced",
                key=key,
                adopted_from=adopted,
                corpus_id=corpus_id,
                extractor=f"{extractor.provider}/{extractor.model}",
            )
            for stale in (snapshot, *(slot / f"app.db{s}" for s in ("-wal", "-shm"))):
                stale.unlink(missing_ok=True)
            (slot / SLOT_RECORD).unlink(missing_ok=True)
        if snapshot.exists():
            self.check_slot(key, record)
            logger.info(
                "graph_cache_hit",
                key=key,
                corpus_id=corpus_id,
                extractor=f"{extractor.provider}/{extractor.model}",
            )
            return snapshot

        logger.info(
            "graph_cache_miss",
            key=key,
            corpus_id=corpus_id,
            extractor=f"{extractor.provider}/{extractor.model}",
        )
        slot.mkdir(parents=True, exist_ok=True)
        # Built under a temporary name and renamed in whole: a builder that dies
        # mid-write (a kill, Ctrl+C, a raised error) used to leave a partial
        # ``app.db`` that the next run served as a hit (2026-10-19 audit).
        build_target = snapshot.with_name(f"{snapshot.name}.building")
        _unlink_sqlite(build_target)
        try:
            await builder(build_target)
            if not build_target.exists():
                msg = f"builder did not produce a snapshot at {build_target}"
                raise RuntimeError(msg)
        except BaseException:
            _unlink_sqlite(build_target)
            raise
        build_target.replace(snapshot)
        self.write_slot_record(key, {**record, SNAPSHOT_SHA256: memo_file_sha256(snapshot)})
        return snapshot

    def clear(self) -> None:
        """Remove the entire cache directory tree.

        Idempotent — no error is raised if the root does not exist.
        """
        if self._root.exists():
            shutil.rmtree(self._root)


__all__ = [
    "EXT_PIPELINE_VERSION",
    "SLOT_RECORD",
    "SNAPSHOT_SHA256",
    "CorpusIdentity",
    "GraphCache",
    "GraphCacheMismatchError",
    "GraphCacheMissError",
    "cache_key",
    "dataset_corpus_identity",
    "dedup_embedder_for",
    "default_workspace",
    "extraction_recipe_for",
    "extraction_stage_thinking",
    "memo_file_sha256",
    "pack_cache_key",
    "pack_corpus_identity",
    "pack_extraction_recipe",
    "pack_slot",
    "pack_slot_record",
    "resolution_for",
]
