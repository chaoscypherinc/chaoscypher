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
    "graph_cache_key",
    "created",
    "snapshot_file",
    "queries_file",
)
"""Every field a pack manifest must carry (all strings)."""

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


def _default_engine(data_dir: Path, settings: EngineSettings) -> Any:
    """Open a core engine over ``data_dir`` (a ``databases/<name>`` directory)."""
    from chaoscypher_core.bootstrap import Engine

    return Engine(data_dir=data_dir, settings=settings)


async def _default_reindex(engine: Any) -> None:
    """Re-embed the engine's nodes and chunks with its own embedding service."""
    from chaoscypher_core.benchmark.reindex import reindex_graph

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
        graph_cache_key: The local graph cache slot the snapshot came from.
        created: When the pack was exported (ISO 8601).
        snapshot_file: The snapshot's file name inside the pack.
        queries_file: The fixture's file name inside the pack.
        manifest: The manifest as read.
    """

    root: Path
    name: str
    fixture_id: str
    fixture_version: str
    corpus_id: str
    extractor: str
    extractor_label: str
    embedder: str
    graph_cache_key: str
    created: str
    snapshot_file: str
    queries_file: str
    manifest: dict[str, Any] = field(default_factory=dict, compare=False)

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

    def _ready_marker(self) -> dict[str, str]:
        """What READY records: the embedder and the graph the copy was indexed from."""
        return {"embedder": self.embedder, "graph_cache_key": self.graph_cache_key}

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
        embedder (so query embeddings at retrieval time come from it too),
        re-embeds every node and chunk, and writes ``index/READY``. Later
        uses find READY naming the same embedder and graph and skip straight
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
            if self.index_dir.exists():
                shutil.rmtree(self.index_dir)
            db_dir.mkdir(parents=True)
            shutil.copyfile(self.snapshot_path, db_dir / "app.db")
            logger.info("reference_pack_indexing", pack=self.name, embedder=self.embedder)

        provider, model = split_model_id(self.embedder)
        engine_settings = settings.model_copy(deep=True)
        engine_settings.embedding.provider = provider
        engine_settings.embedding.model = model
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
    for key in ("snapshot_file", "queries_file"):
        if not _FILE_NAME_RE.match(raw[key]):
            msg = f"{path}: {key} must be a plain file name, got {raw[key]!r}"
            raise ReferencePackError(msg)
        if not (root / raw[key]).is_file():
            msg = f"{path}: {key} {raw[key]!r} is missing from the pack"
            raise ReferencePackError(msg)
    return ReferencePack(root=root, manifest=dict(raw), **{f: str(raw[f]) for f in MANIFEST_FIELDS})


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
    path = root / MANIFEST_FILE
    path.write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True), encoding="utf-8")
    _pack_from_manifest(root)
    return path


__all__ = [
    "INDEX_DIR",
    "MANIFEST_FIELDS",
    "MANIFEST_FILE",
    "PACK_NAME_RE",
    "READY_FILE",
    "ReferencePack",
    "ReferencePackError",
    "list_packs",
    "load_pack",
    "reference_root",
    "split_model_id",
    "write_manifest",
]
