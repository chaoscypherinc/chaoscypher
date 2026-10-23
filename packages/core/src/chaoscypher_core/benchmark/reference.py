# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Reference packs: the fixed graph a grounded-chat run retrieves from.

Every chat model on the board answers from one extracted graph (the
reference), so their scores differ by the chat model alone. A pack is a
directory ``<data_dir>/benchmark/reference/<name>/`` holding:

- ``manifest.yaml`` - what built the graph (:data:`MANIFEST_FIELDS`);
- ``app.db`` - a single-file SQLite snapshot of the extracted graph;
- ``queries.yaml`` - the chat fixture the questions come from.

``chaoscypher benchmark reference export`` writes one from a local run's
graph cache. :meth:`ReferencePack.indexed_engine` opens an engine over an
indexed copy of the snapshot, re-embedded once with the pack's embedder, so
retrieval runs exactly as it did for the local chat runs.
"""

from __future__ import annotations

import json
import re
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
import yaml

from chaoscypher_core.benchmark.queries import load_queries
from chaoscypher_core.benchmark.snapshot import snapshot_database_name


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from chaoscypher_core.benchmark.queries import LabeledQuerySet
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)

MANIFEST_FILE = "manifest.yaml"
"""The pack's manifest file name."""

INDEX_DIR = "index"
"""Subdirectory holding the indexed copy of the snapshot (rebuildable, never shipped)."""

READY_FILE = "READY"
"""Marker written into :data:`INDEX_DIR` once the copy is re-embedded."""

MANIFEST_FIELDS = (
    "name",
    "fixture_id",
    "fixture_version",
    "corpus_id",
    "extractor",
    "extractor_label",
    "embedder",
    "dedup_embedder",
    "graph_cache_key",
    "created",
    "snapshot_file",
    "queries_file",
)
"""Every field a pack manifest must carry (all strings)."""

LLM_STORE_FIELDS = ("llm_store", "llm_store_sha256", "model_build")
"""The manifest fields of a pack's LLM store, all three or none.

``llm_store`` is the file (``llm_store.db``, a completion store holding the
answers the extractor's build gave while the graph was built,
:func:`~chaoscypher_core.cache.export.export_store`), ``llm_store_sha256`` its
sha256 (the installer refuses a file that does not match) and
``model_build`` the extractor's build id the entries are keyed on
(:func:`~chaoscypher_core.cache.keys.model_build_id`), which a replay pins
so it needs no server to say the digest."""

OPTIONAL_MANIFEST_FIELDS = (
    "resolution",
    "extraction_recipe",
    "extraction_domain",
    "corpus_sha256",
    *LLM_STORE_FIELDS,
)
"""Fields a manifest may carry: ``resolution``, the identity resolver's recipe
(:func:`~chaoscypher_core.services.graph.resolution.resolution_recipe`) when the
graph was resolved after commit, and ``extraction_recipe``
(:func:`~chaoscypher_core.benchmark.extraction_recipe.extraction_recipe`), the
settings and prompts the graph was extracted under; both are part of the graph
cache key the installer seeds. And the pack's LLM store (:data:`LLM_STORE_FIELDS`)."""

LLM_STORE_FILE = "llm_store.db"
"""The file name ``benchmark reference export`` gives a pack's LLM store."""

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

PACK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
"""A pack name: it is a directory name and appears in tool arguments."""

_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class ReferencePackError(ValueError):
    """A reference pack is missing, malformed or incomplete."""


def reference_root(data_dir: str | Path) -> Path:
    """Directory holding every reference pack under ``data_dir``."""
    return Path(data_dir) / "benchmark" / "reference"


def split_model_id(model_id: str) -> tuple[str, str]:
    """Split ``"provider/model"`` into its parts (the model may itself contain ``/``).

    Raises:
        ReferencePackError: When either part is empty.
    """
    provider, _, model = model_id.partition("/")
    if not provider or not model:
        msg = f"model id {model_id!r} must look like 'provider/model'"
        raise ReferencePackError(msg)
    return provider, model


def embedder_dimensions(embedder: str) -> int:
    """The native vector size of a ``provider/model`` embedder (the curated registry's).

    Every rung indexes and queries at this size: the pack's index
    (:meth:`ReferencePack.indexed_engine`) and the naive rung's. Until
    2026-10-06 the graph rungs indexed at ``search.vector_dimensions`` (1024,
    the first 1024 of qwen3-embedding:4b's 2560, not re-normalised) and the
    naive rung at 2560 (audit R1).

    Raises:
        ReferencePackError: When the model's native size is not in the registry
            (add it to ``adapters.embedding.registry``; guessing would index at
            the wrong size).
    """
    from chaoscypher_core.adapters.embedding.registry import get_curated_dimensions

    _, model = split_model_id(embedder)
    dims = get_curated_dimensions(model)
    if dims is None:
        msg = (
            f"the native vector size of embedder {embedder!r} is unknown: add the model "
            "to chaoscypher_core.adapters.embedding.registry"
        )
        raise ReferencePackError(msg)
    return dims


def _default_engine(data_dir: Path, settings: EngineSettings) -> Any:
    """Open a core engine over ``data_dir`` (a ``databases/<name>`` directory)."""
    from chaoscypher_core.bootstrap import Engine

    return Engine(data_dir=data_dir, settings=settings)


async def _default_reindex(engine: Any) -> None:
    """Re-embed the engine's nodes and chunks with its own embedding service."""
    from chaoscypher_core.benchmark.reindex import reindex_graph
    from chaoscypher_core.cache.call_site import call_site

    with call_site("embeddings.index"):
        await reindex_graph(engine, engine.embedding_service, settings=engine.settings)


@dataclass(frozen=True)
class ReferencePack:
    """One validated reference pack on disk.

    Attributes:
        root: The pack directory.
        name: Pack name (the directory's name).
        fixture_id: Dataset id of the chat fixture, e.g. ``war_and_peace_book1``.
        fixture_version: That dataset's version.
        corpus_id: Corpus the graph was extracted from.
        extractor: ``provider/model`` that extracted the graph.
        extractor_label: Display name of the extractor.
        embedder: ``provider/model`` that indexes the graph for retrieval.
        dedup_embedder: ``provider/model`` whose vectors decided the graph's
            semantic entity merges at extraction, or ``"exact"``: part of the
            graph itself, unlike ``embedder``, which can be swapped by
            re-indexing.
        graph_cache_key: The local graph cache slot the snapshot came from.
        created: When the pack was exported (ISO 8601).
        snapshot_file: The snapshot's file name inside the pack.
        queries_file: The fixture's file name inside the pack.
        manifest: The manifest as read.
        resolution: The identity resolver's recipe, when the graph was resolved.
        extraction_recipe: The extraction recipe the graph was built under,
            when the manifest records it (packs exported before 2026-10-06 do not).
        llm_store: The pack's LLM store file, when it ships one (:data:`LLM_STORE_FIELDS`).
        llm_store_sha256: That file's sha256.
        model_build: The extractor's build id the store's entries are keyed on.
    """

    root: Path
    name: str
    fixture_id: str
    fixture_version: str
    corpus_id: str
    extractor: str
    extractor_label: str
    embedder: str
    dedup_embedder: str
    graph_cache_key: str
    created: str
    snapshot_file: str
    queries_file: str
    manifest: dict[str, Any] = field(default_factory=dict, compare=False)
    resolution: str | None = None
    extraction_recipe: str | None = None
    extraction_domain: str | None = None
    corpus_sha256: str | None = None
    llm_store: str | None = None
    llm_store_sha256: str | None = None
    model_build: str | None = None

    @property
    def llm_store_path(self) -> Path | None:
        """The shipped LLM store, or None when the pack ships none."""
        return self.root / self.llm_store if self.llm_store else None

    def verify_llm_store(self) -> Path | None:
        """The LLM store when its bytes match ``llm_store_sha256`` (None when none ships).

        Raises:
            ReferencePackError: When the file's sha256 is not the manifest's.
        """
        return verified_llm_store(self.root, self.manifest)

    @cached_property
    def domain(self) -> str | None:
        """The domain the pack's graph was extracted under (:func:`pack_domain`; read once)."""
        return pack_domain(self)

    @property
    def snapshot_path(self) -> Path:
        """The shipped snapshot (never opened by an engine: it stays pristine)."""
        return self.root / self.snapshot_file

    @property
    def queries_path(self) -> Path:
        """The chat fixture."""
        return self.root / self.queries_file

    @property
    def index_dir(self) -> Path:
        """Where the indexed copy lives."""
        return self.root / INDEX_DIR

    def load_queries(self) -> LabeledQuerySet:
        """Parse the pack's chat fixture."""
        return load_queries(self.queries_path)

    @property
    def index_db_path(self) -> Path:
        """The indexed copy's database (it exists once the index has been built)."""
        return self._index_db_dir() / "app.db"

    def indexed_chunk_count(self) -> int:
        """How many chunk vectors the indexed copy holds (0 before it is built).

        What :func:`~chaoscypher_core.benchmark.retrieval.require_complete_retrieval`
        compares a graphrag or hybrid retrieval against. Reads sqlite-vec's
        shadow table, so no extension is loaded.
        """
        import sqlite3

        db = self.index_db_path
        if not db.exists():
            return 0
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute('SELECT COUNT(*) FROM "vec_search_chunks_rowids"').fetchone()
        except sqlite3.Error:
            return 0
        finally:
            con.close()
        return int(row[0]) if row else 0

    @property
    def vector_dimensions(self) -> int:
        """The size the pack's index stores and its queries are embedded at: the embedder's own."""
        return embedder_dimensions(self.embedder)

    def _ready_marker(self) -> dict[str, Any]:
        """What READY records: the embedder and its dimensions, the graph, and the reindex recipe.

        ``snapshot_sha256`` names the exact snapshot the copy was made from
        (the cache key names the slot, not the bytes), and ``vector_dimensions``
        the size the copy's vectors were written at: a READY written under
        other dimensions or another snapshot is not ready (audit R8; a changed
        ``vector_dimensions`` used to empty a READY index silently).
        """
        from chaoscypher_core.benchmark.reindex import reindex_recipe
        from chaoscypher_core.cache.export import file_sha256

        return {
            "embedder": self.embedder,
            "vector_dimensions": self.vector_dimensions,
            "graph_cache_key": self.graph_cache_key,
            "snapshot_sha256": file_sha256(self.snapshot_path),
            "reindex_recipe": reindex_recipe(),
        }

    def index_is_ready(self) -> bool:
        """Whether the indexed copy exists and was built for this embedder and graph."""
        ready = self.index_dir / READY_FILE
        try:
            recorded = json.loads(ready.read_text(encoding="utf-8"))
        except OSError, ValueError:
            return False
        return recorded == self._ready_marker() and self._index_db_dir().joinpath("app.db").exists()

    def _index_db_dir(self) -> Path:
        """``index/databases/<dbname>``: the engine names the database after the directory.

        The snapshot's rows are scoped by the name of the run that built it,
        so the copy must sit in a directory of exactly that name.
        """
        return self.index_dir / "databases" / (snapshot_database_name(self.snapshot_path) or "app")

    @asynccontextmanager
    async def indexed_engine(
        self,
        settings: EngineSettings,
        *,
        engine_factory: Callable[[Path, EngineSettings], Any] | None = None,
        reindex: Callable[[Any], Awaitable[None]] | None = None,
    ) -> AsyncIterator[Any]:
        """Yield a connected engine over an indexed copy of the snapshot.

        The first use copies the snapshot to ``index/databases/<dbname>/app.db``,
        opens an engine over it with ``settings.embedding`` set to the pack's
        embedder and ``search.vector_dimensions`` to that embedder's native
        size (:func:`embedder_dimensions`; query embeddings at retrieval time
        come from the same provider at the same size), re-embeds every node
        and chunk, and writes ``index/READY``. Later uses find READY naming
        the same embedder, dimensions, snapshot and recipe and skip straight
        to opening the engine. The engine is closed on exit.

        Args:
            settings: Base settings (LLM/Ollama configuration, search and
                GraphRAG tunables); the embedder is overridden, the object
                is not mutated.
            engine_factory: ``(databases/<name> dir, settings) -> engine``;
                defaults to the core :class:`~chaoscypher_core.bootstrap.Engine`.
            reindex: ``(engine) -> None`` re-embedder; defaults to
                :func:`~chaoscypher_core.benchmark.reindex.reindex_graph`
                with the engine's embedding service.
        """
        make_engine = engine_factory or _default_engine
        do_reindex = reindex or _default_reindex
        ready = self.index_is_ready()
        db_dir = self._index_db_dir()
        if not ready:
            # Only the graph index: the naive rung's index sits beside it
            # (index/naive-<embedder>, its own READY) and was deleted with it
            # until 2026-10-04, so a graph rebuild silently re-embedded naive.
            (self.index_dir / READY_FILE).unlink(missing_ok=True)
            if db_dir.parent.exists():
                shutil.rmtree(db_dir.parent)
            db_dir.mkdir(parents=True)
            shutil.copyfile(self.snapshot_path, db_dir / "app.db")
            logger.info("reference_pack_indexing", pack=self.name, embedder=self.embedder)

        provider, model = split_model_id(self.embedder)
        engine_settings = settings.model_copy(deep=True)
        engine_settings.embedding.provider = provider
        engine_settings.embedding.model = model
        engine_settings.search.vector_dimensions = self.vector_dimensions
        engine = make_engine(db_dir, engine_settings)
        try:
            if not ready:
                await do_reindex(engine)
                (self.index_dir / READY_FILE).write_text(
                    json.dumps(self._ready_marker()) + "\n", encoding="utf-8"
                )
            yield engine
        finally:
            engine.close()


def snapshot_domain(db: Path) -> str | None:
    """The packs the snapshot's source rows were extracted under, as their label, or None.

    The union of every row's packs
    (:func:`~chaoscypher_core.services.sources.engine.extraction.domain_resolver.sources_packs`,
    the reading the resolver and retrieval share) as
    :func:`~chaoscypher_core.benchmark.packs.packs_label` writes it: one
    pack's name, the packs joined with ``+`` for several, ``generic`` for the
    floor alone. None when no row names a domain or the file has no
    ``sources`` table. Until 2026-10-07 rows naming several domains read as
    None too; sources that chose different packs are now read by their union
    (spec 2026-10-07 § 6). Read-only.
    """
    import sqlite3

    from chaoscypher_core.benchmark.packs import packs_label
    from chaoscypher_core.services.sources.engine.extraction.domain_resolver import (
        source_domain_name,
        sources_packs,
    )

    if not db.is_file():
        return None
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute("SELECT forced_domain, extraction_domain FROM sources").fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    named = [dict(row) for row in rows if source_domain_name(dict(row))]
    return packs_label(sources_packs(named)) if named else None


def pack_domain(pack: ReferencePack) -> str | None:
    """The domain a pack's graph was extracted under: the one every stage reads profiles and names by.

    The manifest's ``extraction_domain`` (what ``reference export`` writes),
    else its ``domain``, else the snapshot's source rows
    (:func:`snapshot_domain`), else None; a label naming several packs
    (``historical+literary``, :func:`~chaoscypher_core.benchmark.packs.packs_label`)
    is read with its packs sorted. Until 2026-10-06 the MCP chat suite
    read the manifest's ``domain`` alone (None for every v3 pack, whose
    manifest records neither), the CLI the dataset's ``domain`` and the pin
    tool a copy of its own, so one pack resolved its GraphRAG profile under
    different domains in different callers (audit wave 4). A caller that can
    read the corpus's dataset manifest may fall back to it when this is None.

    Raises:
        ReferencePackError: When the manifest names a domain and the
            snapshot's source rows name another: the graph was extracted
            under one of them, and which is not for this function to guess
            (until 2026-10-06 the manifest won silently, audit wave 5).
    """
    from chaoscypher_core.benchmark.packs import packs_label, packs_of_label

    snapshot = snapshot_domain(pack.snapshot_path)
    for key in ("extraction_domain", "domain"):
        value = pack.manifest.get(key)
        if isinstance(value, str) and value:
            value = packs_label(packs_of_label(value))  # one spelling of a label (packs sorted)
            if snapshot is not None and snapshot != value:
                msg = (
                    f"{pack.name}: the manifest's {key} is {value!r} but the snapshot's "
                    f"source rows were extracted under {snapshot!r}; fix the manifest "
                    "or re-export the pack"
                )
                raise ReferencePackError(msg)
            return value
    return snapshot


def _pack_from_manifest(root: Path) -> ReferencePack:
    """Read and validate ``root/manifest.yaml``.

    Raises:
        ReferencePackError: On a missing manifest, missing or non-string
            fields, a name that does not match the directory, or a missing file.
    """
    path = root / MANIFEST_FILE
    if not path.exists():
        msg = f"{root}: no {MANIFEST_FILE}"
        raise ReferencePackError(msg)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"{path}: not valid YAML ({exc})"
        raise ReferencePackError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"{path}: manifest must be a mapping"
        raise ReferencePackError(msg)
    missing = [f for f in MANIFEST_FIELDS if not isinstance(raw.get(f), str) or not raw.get(f)]
    if missing:
        msg = f"{path}: missing or non-string field(s): {', '.join(missing)}"
        raise ReferencePackError(msg)
    if raw["name"] != root.name:
        msg = f"{path}: name {raw['name']!r} must match the directory name {root.name!r}"
        raise ReferencePackError(msg)
    for key in ("extractor", "embedder"):
        split_model_id(raw[key])
    if raw["dedup_embedder"] != "exact":
        split_model_id(raw["dedup_embedder"])
    for key in ("snapshot_file", "queries_file"):
        if not _FILE_NAME_RE.match(raw[key]):
            msg = f"{path}: {key} must be a plain file name, got {raw[key]!r}"
            raise ReferencePackError(msg)
        if not (root / raw[key]).is_file():
            msg = f"{path}: {key} {raw[key]!r} is missing from the pack"
            raise ReferencePackError(msg)
    optional = {f: raw[f] for f in OPTIONAL_MANIFEST_FIELDS if raw.get(f) is not None}
    bad = [f for f, v in optional.items() if not isinstance(v, str) or not v]
    if bad:
        msg = f"{path}: non-string or empty field(s): {', '.join(bad)}"
        raise ReferencePackError(msg)
    llm_store_file(root, raw)
    return ReferencePack(
        root=root, manifest=dict(raw), **optional, **{f: str(raw[f]) for f in MANIFEST_FIELDS}
    )


def llm_store_file(root: Path, manifest: dict[str, Any]) -> Path | None:
    """The pack's LLM store the manifest names (:data:`LLM_STORE_FIELDS`), or None when it names none.

    Checks the fields' shape and that the file is in the pack, not its bytes
    (:func:`verified_llm_store` does).

    Raises:
        ReferencePackError: When only some of the fields are given, the file
            name is not a plain name or the file is missing, or the sha256 is
            not 64 hex digits.
    """
    given = [f for f in LLM_STORE_FIELDS if manifest.get(f)]
    if not given:
        return None
    where = root / MANIFEST_FILE
    if len(given) != len(LLM_STORE_FIELDS):
        missing = [f for f in LLM_STORE_FIELDS if f not in given]
        msg = f"{where}: an LLM store needs {', '.join(LLM_STORE_FIELDS)}; missing {', '.join(missing)}"
        raise ReferencePackError(msg)
    name, sha = str(manifest["llm_store"]), str(manifest["llm_store_sha256"])
    if not _FILE_NAME_RE.match(name):
        msg = f"{where}: llm_store must be a plain file name, got {name!r}"
        raise ReferencePackError(msg)
    if not _SHA256_RE.match(sha):
        msg = f"{where}: llm_store_sha256 must be 64 lowercase hex digits, got {sha!r}"
        raise ReferencePackError(msg)
    if not (root / name).is_file():
        msg = f"{where}: llm_store {name!r} is missing from the pack"
        raise ReferencePackError(msg)
    return root / name


def verified_llm_store(root: Path, manifest: dict[str, Any]) -> Path | None:
    """:func:`llm_store_file`, once its bytes match the manifest's ``llm_store_sha256``.

    Raises:
        ReferencePackError: As :func:`llm_store_file`, or when the sha256 differs.
    """
    from chaoscypher_core.cache.export import file_sha256

    path = llm_store_file(root, manifest)
    if path is None:
        return None
    actual, expected = file_sha256(path), str(manifest["llm_store_sha256"])
    if actual != expected:
        msg = (
            f"{path}: sha256 {actual} does not match the manifest's llm_store_sha256 "
            f"{expected}; the store is not the one the pack was exported with"
        )
        raise ReferencePackError(msg)
    return path


def load_pack(data_dir: str | Path, name: str) -> ReferencePack:
    """Load and validate the pack called ``name`` under ``data_dir``.

    Raises:
        ReferencePackError: When the name is malformed, the pack does not
            exist, or its manifest does not validate.
    """
    if not isinstance(name, str) or not PACK_NAME_RE.match(name):
        msg = f"reference pack name {name!r} is not valid"
        raise ReferencePackError(msg)
    root = reference_root(data_dir) / name
    if not root.is_dir():
        msg = f"no reference pack {name!r} under {reference_root(data_dir)}"
        raise ReferencePackError(msg)
    return _pack_from_manifest(root)


def list_packs(data_dir: str | Path) -> list[ReferencePack]:
    """Every valid pack under ``data_dir``, sorted by name; invalid ones are logged and skipped."""
    root = reference_root(data_dir)
    if not root.is_dir():
        return []
    packs: list[ReferencePack] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not PACK_NAME_RE.match(child.name):
            continue
        try:
            packs.append(_pack_from_manifest(child))
        except ReferencePackError as exc:
            logger.warning("reference_pack_invalid", pack=child.name, error=str(exc))
    return packs


def write_manifest(root: Path, fields: dict[str, str]) -> Path:
    """Write ``root/manifest.yaml`` with :data:`MANIFEST_FIELDS` in order, then validate it.

    Raises:
        ReferencePackError: When a field is missing or the result does not validate.
    """
    missing = [f for f in MANIFEST_FIELDS if not fields.get(f)]
    if missing:
        msg = f"manifest is missing field(s): {', '.join(missing)}"
        raise ReferencePackError(msg)
    body = {f: str(fields[f]) for f in MANIFEST_FIELDS}
    body.update({f: str(fields[f]) for f in OPTIONAL_MANIFEST_FIELDS if fields.get(f)})
    path = root / MANIFEST_FILE
    path.write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _pack_from_manifest(root)
    return path


__all__ = [
    "INDEX_DIR",
    "LLM_STORE_FIELDS",
    "LLM_STORE_FILE",
    "MANIFEST_FIELDS",
    "MANIFEST_FILE",
    "OPTIONAL_MANIFEST_FIELDS",
    "PACK_NAME_RE",
    "READY_FILE",
    "ReferencePack",
    "ReferencePackError",
    "embedder_dimensions",
    "list_packs",
    "llm_store_file",
    "load_pack",
    "pack_domain",
    "reference_root",
    "snapshot_domain",
    "split_model_id",
    "verified_llm_store",
    "write_manifest",
]
