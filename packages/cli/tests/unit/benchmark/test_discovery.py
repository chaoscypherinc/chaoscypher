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


# --- Packs (spec 2026-10-07 § 6): a manifest lists its packs, or names one domain ---


def _write_manifest_lines(root: Path, pid: str, packs_line: str, *, kind: str = "extraction"):
    pdir = root / pid
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{pid}.txt").write_text("Pierre visited Moscow in 1812.", encoding="utf-8")
    extra = "sections: [s.yaml]\n" if kind == "probes" else f"corpus_path: {pid}.txt\n"
    (pdir / "manifest.yaml").write_text(
        f"id: {pid}\nkind: {kind}\nversion: '1.0'\n{packs_line}{extra}", encoding="utf-8"
    )


def test_a_manifest_with_a_list_of_packs_loads(tmp_path: Path) -> None:
    """``packs: [literary, historical]``: both, sorted; the label records them joined."""
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    _write_manifest_lines(tmp_path, "two", "packs: [literary, historical]\n")
    (ds,) = discover_datasets(builtin_root=tmp_path, user_root=tmp_path / "none")
    assert isinstance(ds, ExtractionDataset)
    assert ds.packs == ("historical", "literary")
    bundle = load_dataset_bundle("two", builtin_root=tmp_path, user_root=tmp_path / "none")
    assert bundle.packs == ("historical", "literary")
    assert bundle.extraction_domain == "historical+literary"
    assert bundle.extraction_dataset.packs == bundle.packs


@pytest.mark.parametrize(
    ("line", "packs", "label"),
    [
        ("domain: literary\n", ("literary",), "literary"),
        ("domain: generic\n", (), "generic"),
        ("packs: []\n", (), "generic"),
    ],
)
def test_a_domain_is_one_pack_and_generic_is_the_floor_alone(
    tmp_path: Path, line: str, packs: tuple[str, ...], label: str
) -> None:
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    _write_manifest_lines(tmp_path, "one", line)
    bundle = load_dataset_bundle("one", builtin_root=tmp_path, user_root=tmp_path / "none")
    assert bundle.packs == packs
    assert bundle.extraction_domain == label
    # the one-domain shim callers read the first pack, or the floor
    assert bundle.domain == (packs[0] if packs else "generic")
    assert bundle.extraction_dataset.domain == bundle.domain


def test_a_user_overlay_with_no_packs_runs_the_floor_alone(tmp_path: Path) -> None:
    """The v5 floor arm: the overlay's ``packs: []`` wins over the built-in ``domain: literary``."""
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    _write_manifest_lines(tmp_path / "builtin", "book", "domain: literary\n")
    _write_manifest_lines(tmp_path / "user", "book", "packs: []\n")
    bundle = load_dataset_bundle(
        "book", builtin_root=tmp_path / "builtin", user_root=tmp_path / "user"
    )
    assert bundle.source == "user"
    assert bundle.packs == ()
    assert bundle.extraction_domain == "generic"


def test_a_manifest_naming_packs_twice_is_refused(tmp_path: Path) -> None:
    _write_manifest_lines(tmp_path, "both", "packs: [literary]\ndomain: literary\n")
    with pytest.raises(ValueError, match="not both"):
        discover_datasets(builtin_root=tmp_path, user_root=tmp_path / "none")


def test_the_probe_pack_reads_its_packs_with_the_literary_default() -> None:
    """The shipped probes manifest names ``domain: literary``; one that names none reads literary."""
    from chaoscypher_core.benchmark.probes import PROBES_DEFAULT_PACKS

    probes = [d for d in discover_datasets(user_root=Path("/nonexistent")) if d.kind == "probes"]
    assert [p.packs for p in probes] == [("literary",)]
    assert PROBES_DEFAULT_PACKS == ("literary",)


def test_a_manifest_label_names_the_corpus_and_book_one_has_one(tmp_path: Path) -> None:
    """``label``: how the leaderboard names a corpus; optional, None when a manifest has none."""
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

    ds_root = tmp_path / "datasets"
    for name, extra in (("labelled", 'label: "the Demo Manual"\n'), ("bare", "")):
        pack_dir = ds_root / name
        pack_dir.mkdir(parents=True)
        (pack_dir / f"{name}.txt").write_text("text", encoding="utf-8")
        (pack_dir / "manifest.yaml").write_text(
            f"id: {name}\nkind: extraction\nversion: '1.0'\ndomain: technical\n"
            f"corpus_path: {name}.txt\n{extra}",
            encoding="utf-8",
        )
    none = tmp_path / "_none"
    assert load_dataset_bundle("labelled", user_root=ds_root, builtin_root=none).label == (
        "the Demo Manual"
    )
    assert load_dataset_bundle("bare", user_root=ds_root, builtin_root=none).label is None
    book = load_dataset_bundle("war_and_peace_book1", user_root=none)
    assert book.label == "War and Peace, Book One"
