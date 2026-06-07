# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Dataset discovery: built-in + user overlay.

Two-layer model:
  - Built-in datasets ship with the package under
    ``chaoscypher_cli/benchmark/data/datasets/`` and are accessible via
    ``importlib.resources``.
  - User datasets live under ``<chaoscypher_data_dir>/benchmark/datasets/``
    and are discovered if the directory exists.

User datasets with the same id as a built-in override the built-in. Each
returned dataset carries a ``source`` attribute so the leaderboard renderer
can mark provenance.

Manifest format is sibling-relative: each dataset is a self-contained
directory with a ``manifest.yaml`` and a corpus file referenced by
``corpus_path`` (relative to the manifest directory).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

import platformdirs
import yaml

from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset
from chaoscypher_cli.benchmark.queries import LabeledQuerySet, load_queries


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.dataset import BenchmarkDataset, DatasetSource


_REQUIRED_FIELDS: tuple[str, ...] = ("id", "kind", "version")
_REQUIRED_EXTRACTION_FIELDS: tuple[str, ...] = ("domain", "corpus_path")


def builtin_dataset_root() -> Path:
    """Path to the package-bundled datasets directory."""
    return Path(str(resources.files("chaoscypher_cli.benchmark").joinpath("data", "datasets")))


def user_dataset_root() -> Path:
    """Path to the user overlay datasets directory under the data dir."""
    return user_benchmark_root() / "datasets"


def user_benchmark_root() -> Path:
    """Resolve the user's bench root.

    Mirrors CLIContext's data-dir resolution: ``CHAOSCYPHER_DATA_DIR`` env
    var first, then platformdirs default.
    """
    base = Path(
        os.getenv(
            "CHAOSCYPHER_DATA_DIR",
            platformdirs.user_data_dir("chaoscypher", appauthor=False),
        )
    )
    return base / "benchmark"


def discover_datasets(
    *,
    builtin_root: Path | None = None,
    user_root: Path | None = None,
) -> list[BenchmarkDataset]:
    """Discover datasets from built-in + user overlay, sorted by id.

    User datasets with the same id as a built-in override the built-in
    (the user version wins). Each dataset carries a ``source`` attribute
    indicating where it was found.

    Args:
        builtin_root: Override the built-in root (test injection only).
            Defaults to the package's bundled data directory.
        user_root: Override the user root (test injection only). Defaults
            to ``<data_dir>/benchmark/datasets/``.

    Returns:
        Merged list of datasets, sorted by id.
    """
    builtin = builtin_root if builtin_root is not None else builtin_dataset_root()
    user = user_root if user_root is not None else user_dataset_root()

    by_id: dict[str, BenchmarkDataset] = {}
    for ds in _discover_in_root(builtin, source="builtin"):
        by_id[ds.id] = ds
    for ds in _discover_in_root(user, source="user"):
        by_id[ds.id] = ds  # user wins on collision
    return sorted(by_id.values(), key=lambda d: d.id)


def _discover_in_root(root: Path, *, source: DatasetSource) -> list[BenchmarkDataset]:
    """Discover datasets under one root directory.

    Raises:
        ValueError: If a manifest is malformed, has an unknown ``kind``,
            or its declared ``id`` does not match its directory name.
        FileNotFoundError: If a manifest's resolved ``corpus_path`` does
            not exist on disk.
    """
    if not root.exists():
        return []
    return [
        _load_dataset(manifest, source=source) for manifest in sorted(root.glob("*/manifest.yaml"))
    ]


def _load_dataset(manifest: Path, *, source: DatasetSource) -> BenchmarkDataset:
    """Parse a single dataset manifest into a typed BenchmarkDataset."""
    pack_dir = manifest.parent
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{manifest}: manifest must be a YAML mapping"
        raise TypeError(msg)
    for field in _REQUIRED_FIELDS:
        if field not in data:
            msg = f"{manifest}: missing required field '{field}'"
            raise ValueError(msg)
    if data["id"] != pack_dir.name:
        msg = f"{manifest}: declared id '{data['id']}' must match directory name '{pack_dir.name}'"
        raise ValueError(msg)
    kind = data["kind"]
    if kind == "extraction":
        for field in _REQUIRED_EXTRACTION_FIELDS:
            if field not in data:
                msg = f"{manifest}: extraction dataset missing '{field}' field"
                raise ValueError(msg)
        # corpus_path is sibling-relative to the manifest.
        corpus_abs = (pack_dir / str(data["corpus_path"])).resolve()
        return ExtractionDataset(
            id=str(data["id"]),
            version=str(data["version"]),
            corpus_path=corpus_abs,
            domain=str(data["domain"]),
            source=source,
        )
    msg = f"{manifest}: unknown dataset kind '{kind}' (v1 supports 'extraction')"
    raise ValueError(msg)


@dataclass(frozen=True)
class DatasetBundle:
    """A discovered dataset with all its kind-spawning artifacts.

    The orchestrator constructs kind-specific BenchmarkDataset instances
    from this bundle (e.g. EmbeddingRetrievalDataset, GraphRAGChatDataset)
    bound to a particular extractor's graph. The bundle is the data
    side; runtime wiring is the orchestrator's job.

    Attributes:
        id: Dataset id.
        version: Dataset version from the manifest.
        domain: Domain label for extraction.
        corpus_path: Absolute path to the corpus file.
        source: builtin / user.
        extraction_dataset: An ExtractionDataset already constructed for
            stage 1 — same instance discover_datasets() would return.
        queries: Loaded labeled queries if manifest set queries_path,
            else None. Presence of this is what unlocks embedding +
            chat benches for the dataset.
    """

    id: str
    version: str
    domain: str
    corpus_path: Path
    source: DatasetSource
    extraction_dataset: ExtractionDataset
    queries: LabeledQuerySet | None


def load_dataset_bundle(
    dataset_id: str,
    *,
    builtin_root: Path | None = None,
    user_root: Path | None = None,
) -> DatasetBundle:
    """Load one dataset bundle by id, applying user-overlay-wins semantics.

    Raises:
        FileNotFoundError: If no manifest with this id exists.
        ValueError: On malformed manifest.
    """
    builtin = builtin_root if builtin_root is not None else builtin_dataset_root()
    user = user_root if user_root is not None else user_dataset_root()

    candidates: tuple[tuple[Path, DatasetSource], ...] = (
        (user, "user"),
        (builtin, "builtin"),
    )
    for root, source in candidates:
        manifest_path = root / dataset_id / "manifest.yaml"
        if manifest_path.exists():
            return _build_bundle(manifest_path, source=source)

    msg = f"no dataset bundle with id '{dataset_id}' (looked in {user} and {builtin})"
    raise FileNotFoundError(msg)


def _build_bundle(manifest: Path, *, source: DatasetSource) -> DatasetBundle:
    """Parse a manifest into a DatasetBundle (extraction + optional queries)."""
    pack_dir = manifest.parent
    data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        msg = f"{manifest}: manifest must be a YAML mapping"
        raise TypeError(msg)
    for required in _REQUIRED_FIELDS:
        if required not in data:
            msg = f"{manifest}: missing required field '{required}'"
            raise ValueError(msg)
    if data["id"] != pack_dir.name:
        msg = f"{manifest}: declared id '{data['id']}' must match dir '{pack_dir.name}'"
        raise ValueError(msg)
    if data["kind"] != "extraction":
        msg = f"{manifest}: only kind='extraction' supported as primary kind"
        raise ValueError(msg)
    for required in _REQUIRED_EXTRACTION_FIELDS:
        if required not in data:
            msg = f"{manifest}: extraction dataset missing '{required}'"
            raise ValueError(msg)

    corpus_abs = (pack_dir / str(data["corpus_path"])).resolve()
    if not corpus_abs.exists():
        msg = f"{manifest}: corpus_path resolves to missing file: {corpus_abs}"
        raise FileNotFoundError(msg)
    extraction_ds = ExtractionDataset(
        id=str(data["id"]),
        version=str(data["version"]),
        corpus_path=corpus_abs,
        domain=str(data["domain"]),
        source=source,
    )

    queries: LabeledQuerySet | None = None
    if "queries_path" in data:
        queries_abs = (pack_dir / str(data["queries_path"])).resolve()
        if not queries_abs.exists():
            msg = f"{manifest}: queries_path resolves to missing file: {queries_abs}"
            raise FileNotFoundError(msg)
        queries = load_queries(queries_abs)

    return DatasetBundle(
        id=str(data["id"]),
        version=str(data["version"]),
        domain=str(data["domain"]),
        corpus_path=corpus_abs,
        source=source,
        extraction_dataset=extraction_ds,
        queries=queries,
    )


__all__ = [
    "DatasetBundle",
    "builtin_dataset_root",
    "discover_datasets",
    "load_dataset_bundle",
    "user_benchmark_root",
    "user_dataset_root",
]
