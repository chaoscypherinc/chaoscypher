# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for discovery root resolvers + bundle-loading error branches."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_cli.benchmark.discovery import (
    builtin_dataset_root,
    discover_datasets,
    load_dataset_bundle,
    user_benchmark_root,
    user_dataset_root,
)


def _write_manifest(pack_dir: Path, body: str, *, corpus: str | None = "x.txt") -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    if corpus is not None:
        (pack_dir / corpus).write_text("Pierre visited Moscow.", encoding="utf-8")
    (pack_dir / "manifest.yaml").write_text(body, encoding="utf-8")


def test_builtin_dataset_root_is_package_relative() -> None:
    root = builtin_dataset_root()
    assert root.name == "datasets"
    assert "benchmark" in root.parts


def test_user_dataset_root_under_benchmark() -> None:
    assert user_dataset_root().name == "datasets"
    assert user_dataset_root().parent.name == "benchmark"


def test_user_benchmark_root_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path / "mydata"))
    root = user_benchmark_root()
    assert root == tmp_path / "mydata" / "benchmark"


def test_user_benchmark_root_falls_back_to_platformdirs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHAOSCYPHER_DATA_DIR", raising=False)
    root = user_benchmark_root()
    # No env var -> platformdirs default; still ends in /benchmark.
    assert root.name == "benchmark"


def test_discover_manifest_not_mapping_raises_type_error(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    pdir = builtin / "p1"
    _write_manifest(pdir, "- a\n- b\n")
    with pytest.raises(TypeError, match="must be a YAML mapping"):
        discover_datasets(builtin_root=builtin, user_root=tmp_path / "_none")


def test_discover_extraction_missing_domain_field(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    pdir = builtin / "p1"
    _write_manifest(
        pdir,
        "id: p1\nkind: extraction\nversion: '1.0'\ncorpus_path: x.txt\n",
    )
    with pytest.raises(ValueError, match="extraction dataset missing 'domain'"):
        discover_datasets(builtin_root=builtin, user_root=tmp_path / "_none")


def test_load_dataset_bundle_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="no dataset bundle with id 'ghost'"):
        load_dataset_bundle("ghost", builtin_root=tmp_path / "b", user_root=tmp_path / "u")


def test_bundle_manifest_not_mapping_raises_type_error(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    _write_manifest(root / "demo", "- a\n- b\n")
    with pytest.raises(TypeError, match="must be a YAML mapping"):
        load_dataset_bundle("demo", user_root=root, builtin_root=tmp_path / "_none")


def test_bundle_missing_required_field(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    _write_manifest(root / "demo", "id: demo\nkind: extraction\n")  # no version
    with pytest.raises(ValueError, match="missing required field 'version'"):
        load_dataset_bundle("demo", user_root=root, builtin_root=tmp_path / "_none")


def test_bundle_id_must_match_dir(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    _write_manifest(
        root / "demo",
        "id: other\nkind: extraction\nversion: '1.0'\ndomain: technical\ncorpus_path: x.txt\n",
    )
    with pytest.raises(ValueError, match="must match dir 'demo'"):
        load_dataset_bundle("demo", user_root=root, builtin_root=tmp_path / "_none")


def test_bundle_non_extraction_primary_kind_rejected(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    _write_manifest(
        root / "demo",
        "id: demo\nkind: chat\nversion: '1.0'\ndomain: technical\ncorpus_path: x.txt\n",
    )
    with pytest.raises(ValueError, match="only kind='extraction' supported"):
        load_dataset_bundle("demo", user_root=root, builtin_root=tmp_path / "_none")


def test_bundle_missing_extraction_field(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    _write_manifest(
        root / "demo",
        "id: demo\nkind: extraction\nversion: '1.0'\ncorpus_path: x.txt\n",  # no domain
    )
    with pytest.raises(ValueError, match="extraction dataset missing 'domain'"):
        load_dataset_bundle("demo", user_root=root, builtin_root=tmp_path / "_none")


def test_bundle_missing_corpus_file(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    _write_manifest(
        root / "demo",
        "id: demo\nkind: extraction\nversion: '1.0'\ndomain: technical\ncorpus_path: gone.txt\n",
        corpus=None,
    )
    with pytest.raises(FileNotFoundError, match="corpus_path resolves to missing file"):
        load_dataset_bundle("demo", user_root=root, builtin_root=tmp_path / "_none")


def test_bundle_missing_queries_file(tmp_path: Path) -> None:
    root = tmp_path / "datasets"
    _write_manifest(
        root / "demo",
        "id: demo\n"
        "kind: extraction\n"
        "version: '1.0'\n"
        "domain: technical\n"
        "corpus_path: x.txt\n"
        "queries_path: gone.yaml\n",
    )
    with pytest.raises(FileNotFoundError, match="queries_path resolves to missing file"):
        load_dataset_bundle("demo", user_root=root, builtin_root=tmp_path / "_none")


def test_bundle_builtin_used_when_no_user_override(tmp_path: Path) -> None:
    """When only the builtin root has the id, the builtin bundle is loaded."""
    builtin = tmp_path / "builtin"
    _write_manifest(
        builtin / "demo",
        "id: demo\nkind: extraction\nversion: '1.0'\ndomain: technical\ncorpus_path: x.txt\n",
    )
    bundle = load_dataset_bundle("demo", user_root=tmp_path / "empty_user", builtin_root=builtin)
    assert bundle.source == "builtin"
    assert bundle.queries is None
    assert bundle.domain == "technical"
