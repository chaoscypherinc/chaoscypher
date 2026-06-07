# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for dataset discovery: built-in + user overlay."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_cli.benchmark.discovery import discover_datasets
from chaoscypher_cli.benchmark.extraction_dataset import ExtractionDataset


def _write_dataset(
    root: Path,
    pid: str,
    *,
    kind: str = "extraction",
    domain: str = "literary",
    version: str = "1.0",
) -> None:
    """Write a dataset directory with sibling-relative corpus_path."""
    pdir = root / pid
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{pid}.txt").write_text("Pierre visited Moscow in 1812.", encoding="utf-8")
    (pdir / "manifest.yaml").write_text(
        f"id: {pid}\n"
        f"kind: {kind}\n"
        f"version: '{version}'\n"
        f"domain: {domain}\n"
        f"corpus_path: {pid}.txt\n",
        encoding="utf-8",
    )


def test_discover_finds_builtin_datasets(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_dataset(builtin, "p1")
    _write_dataset(builtin, "p2")
    out = discover_datasets(builtin_root=builtin, user_root=user)
    assert sorted(d.id for d in out) == ["p1", "p2"]
    assert all(d.source == "builtin" for d in out)


def test_discover_finds_user_datasets(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_dataset(user, "u1")
    out = discover_datasets(builtin_root=builtin, user_root=user)
    assert [d.id for d in out] == ["u1"]
    assert out[0].source == "user"


def test_user_overrides_builtin_on_id_collision(tmp_path: Path):
    """When a user dataset has the same id as a built-in, user wins."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_dataset(builtin, "shared", domain="literary")
    _write_dataset(user, "shared", domain="technical")
    out = discover_datasets(builtin_root=builtin, user_root=user)
    assert len(out) == 1
    assert out[0].id == "shared"
    assert out[0].source == "user"
    assert isinstance(out[0], ExtractionDataset)
    assert out[0].domain == "technical"  # user version's domain


def test_discover_corpus_path_resolves_sibling_relative(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_dataset(builtin, "p1")
    out = discover_datasets(builtin_root=builtin, user_root=user)
    assert out[0].corpus_path == (builtin / "p1" / "p1.txt").resolve()


def test_discover_rejects_unknown_kind(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_dataset(builtin, "chat_p", kind="chat")
    with pytest.raises(ValueError, match="unknown dataset kind"):
        discover_datasets(builtin_root=builtin, user_root=user)


def test_discover_rejects_missing_required_fields(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    pdir = builtin / "bad"
    pdir.mkdir(parents=True)
    (pdir / "manifest.yaml").write_text("id: bad\nversion: '1.0'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="kind"):
        discover_datasets(builtin_root=builtin, user_root=user)


def test_discover_returns_empty_for_empty_roots(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    assert discover_datasets(builtin_root=builtin, user_root=user) == []


def test_discover_id_must_match_directory_name(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    pdir = builtin / "actual_dir"
    pdir.mkdir(parents=True)
    (pdir / "x.txt").write_text("x", encoding="utf-8")
    (pdir / "manifest.yaml").write_text(
        "id: declared_id\nkind: extraction\nversion: '1.0'\ndomain: literary\ncorpus_path: x.txt\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="must match"):
        discover_datasets(builtin_root=builtin, user_root=user)


def test_discover_rejects_missing_corpus_file(tmp_path: Path):
    """Manifest is well-formed but the corpus file does not exist."""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    pdir = builtin / "p1"
    pdir.mkdir(parents=True)
    (pdir / "manifest.yaml").write_text(
        "id: p1\nkind: extraction\nversion: '1.0'\ndomain: literary\ncorpus_path: missing.txt\n",
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError):
        discover_datasets(builtin_root=builtin, user_root=user)


def test_manifest_with_queries_path(tmp_path):
    from chaoscypher_cli.benchmark.discovery import (
        load_dataset_bundle,
    )

    ds_root = tmp_path / "datasets"
    pack_dir = ds_root / "demo_set"
    pack_dir.mkdir(parents=True)
    (pack_dir / "demo_set.txt").write_text("hello world", encoding="utf-8")
    (pack_dir / "manifest.yaml").write_text(
        "id: demo_set\n"
        "kind: extraction\n"
        "version: '1.0'\n"
        "domain: technical\n"
        "corpus_path: demo_set.txt\n"
        "queries_path: queries.yaml\n",
        encoding="utf-8",
    )
    (pack_dir / "queries.yaml").write_text(
        "version: '1.0'\n"
        "queries:\n"
        "  - {id: q1, band: factual_single_hop, question: 'q?', "
        "     gold_entities: [E], gold_answer: 'a'}\n",
        encoding="utf-8",
    )

    bundle = load_dataset_bundle("demo_set", user_root=ds_root, builtin_root=tmp_path / "_none")
    assert bundle.id == "demo_set"
    assert bundle.queries is not None
    assert bundle.queries.queries[0].id == "q1"
    assert bundle.extraction_dataset.id == "demo_set"


def test_manifest_without_queries_path(tmp_path):
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    ds_root = tmp_path / "datasets"
    pack_dir = ds_root / "extraction_only"
    pack_dir.mkdir(parents=True)
    (pack_dir / "extraction_only.txt").write_text("text", encoding="utf-8")
    (pack_dir / "manifest.yaml").write_text(
        "id: extraction_only\n"
        "kind: extraction\n"
        "version: '1.0'\n"
        "domain: technical\n"
        "corpus_path: extraction_only.txt\n",
        encoding="utf-8",
    )

    bundle = load_dataset_bundle(
        "extraction_only", user_root=ds_root, builtin_root=tmp_path / "_none"
    )
    assert bundle.queries is None
    assert bundle.extraction_dataset.id == "extraction_only"


def test_existing_discover_datasets_unchanged(tmp_path):
    """Backward-compat: discover_datasets still returns BenchmarkDataset list."""
    from chaoscypher_cli.benchmark.discovery import discover_datasets

    ds_root = tmp_path / "datasets"
    pack_dir = ds_root / "ds1"
    pack_dir.mkdir(parents=True)
    (pack_dir / "ds1.txt").write_text("t", encoding="utf-8")
    (pack_dir / "manifest.yaml").write_text(
        "id: ds1\nkind: extraction\nversion: '1.0'\ndomain: technical\ncorpus_path: ds1.txt\n",
        encoding="utf-8",
    )
    out = discover_datasets(builtin_root=tmp_path / "_none", user_root=ds_root)
    assert len(out) == 1
    assert out[0].id == "ds1"
