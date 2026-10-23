# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""`benchmark run --replay` / `--replay-pack`: the run executes with refuse-on-miss caches and pinned builds."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from chaoscypher_cli.commands.benchmark.run import run


_BUILD = "ollama:local-model:abc123@0.12.1"


def _write_dataset_and_config(tmp_path: Path) -> tuple[Path, Path]:
    """An extraction dataset and an extractors-only config with one local extractor."""
    datasets, configs = tmp_path / "datasets", tmp_path / "configs"
    (datasets / "p1").mkdir(parents=True)
    (datasets / "p1" / "p1.txt").write_text("x", encoding="utf-8")
    (datasets / "p1" / "manifest.yaml").write_text(
        "id: p1\nkind: extraction\nversion: '1.0'\ndomain: literary\ncorpus_path: p1.txt\n",
        encoding="utf-8",
    )
    configs.mkdir()
    (configs / "extraction.yaml").write_text(
        'name: "extraction"\nseed: 42\ntemperature: 0.0\ndatasets:\n  - p1\n'
        'extractors:\n  - provider: ollama\n    model: local-model\n    label: "Local"\n',
        encoding="utf-8",
    )
    return datasets, configs


def _install_pack(data_dir: Path, name: str, *, model_build: str | None) -> None:
    """An installed pack under ``data_dir`` whose extractor is the config's, with or without a store."""
    from chaoscypher_core.benchmark.reference import LLM_STORE_FILE, reference_root, write_manifest
    from chaoscypher_core.cache.export import file_sha256

    root = reference_root(data_dir) / name
    root.mkdir(parents=True)
    with closing(sqlite3.connect(root / "app.db")) as conn:
        conn.execute("CREATE TABLE sources (id TEXT, database_name TEXT)")
        conn.commit()
    (root / "queries.yaml").write_text('version: "1.0"\nqueries: []\n', encoding="utf-8")
    store: dict[str, str] = {}
    if model_build is not None:
        (root / LLM_STORE_FILE).write_bytes(b"recorded completions")
        store = {
            "llm_store": LLM_STORE_FILE,
            "llm_store_sha256": file_sha256(root / LLM_STORE_FILE),
            "model_build": model_build,
        }
    write_manifest(
        root,
        {
            "name": name,
            "fixture_id": "p1",
            "fixture_version": "1.0",
            "corpus_id": "p1",
            "extractor": "ollama/local-model",
            "extractor_label": "Local",
            "embedder": "ollama/qwen3-embedding:0.6b",
            "dedup_embedder": "exact",
            "graph_cache_key": "0" * 16,
            "created": "2026-10-06T00:00:00+00:00",
            "snapshot_file": "app.db",
            "queries_file": "queries.yaml",
            **store,
        },
    )


def _invoke(tmp_path: Path, *flags: str) -> tuple[Any, list[dict[str, Any]]]:
    """Run the extractors-only config with ``flags``; what the runner saw while it ran."""
    from chaoscypher_cli.benchmark.config import load_config
    from chaoscypher_cli.benchmark.discovery import load_dataset_bundle
    from chaoscypher_core.app_config import get_settings
    from chaoscypher_core.cache.build import pinned_build

    datasets, configs = _write_dataset_and_config(tmp_path)
    seen: list[dict[str, Any]] = []

    async def fake_run_benchmark(**_: Any) -> list[Any]:
        cache = get_settings().cache
        seen.append(
            {
                "completions": cache.completions.require_hit,
                "embeddings": cache.embeddings.require_hit,
                "build": pinned_build("ollama", "local-model"),
            }
        )
        return []

    with (
        patch("chaoscypher_cli.commands.benchmark.run.run_benchmark", fake_run_benchmark),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=lambda did: load_dataset_bundle(
                did, builtin_root=datasets, user_root=tmp_path / "none"
            ),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: load_config(
                name, builtin_root=configs, user_root=tmp_path / "none"
            ),
        ),
        patch("chaoscypher_cli.benchmark.models.assert_registry_coverage", return_value=[]),
    ):
        result = CliRunner().invoke(run, ["extraction", "--out", str(tmp_path / "out"), *flags])
    return result, seen


def test_a_plain_run_is_not_a_replay(tmp_path: Path, isolated_settings: Path) -> None:
    result, seen = _invoke(tmp_path)
    assert result.exit_code == 0, result.output
    assert seen == [{"completions": False, "embeddings": False, "build": None}]


def test_replay_runs_with_refuse_on_miss_caches_and_restores_them(
    tmp_path: Path, isolated_settings: Path
) -> None:
    from chaoscypher_core.app_config import get_settings

    result, seen = _invoke(tmp_path, "--replay")
    assert result.exit_code == 0, result.output
    # only completions are replayed: packs ship no embeddings, the embedder recomputes them
    assert seen == [{"completions": True, "embeddings": False, "build": None}]
    cache = get_settings().cache
    assert not cache.completions.require_hit and not cache.embeddings.require_hit


def test_replay_pack_pins_the_extractor_build_its_manifest_records(
    tmp_path: Path, isolated_settings: Path
) -> None:
    _install_pack(isolated_settings, "book", model_build=_BUILD)
    result, seen = _invoke(tmp_path, "--replay-pack", "book")
    assert result.exit_code == 0, result.output
    assert seen == [{"completions": True, "embeddings": False, "build": _BUILD}]
    assert _BUILD in " ".join(result.output.split())


@pytest.mark.parametrize(
    ("pack", "expected"),
    [("missing", "no reference pack"), ("bare", "ships no LLM store")],
)
def test_replay_pack_refuses_a_pack_it_cannot_replay_before_running(
    tmp_path: Path, isolated_settings: Path, pack: str, expected: str
) -> None:
    _install_pack(isolated_settings, "bare", model_build=None)
    result, seen = _invoke(tmp_path, "--replay-pack", pack)
    assert result.exit_code != 0
    assert expected in " ".join(result.output.split())
    assert seen == []


def test_replay_refuses_when_a_cache_is_off(
    tmp_path: Path, isolated_settings: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chaoscypher_core import app_config

    monkeypatch.setenv("CHAOSCYPHER_CACHE__COMPLETIONS__ENABLED", "false")
    app_config.get_settings.cache_clear()
    app_config._settings = None
    result, seen = _invoke(tmp_path, "--replay")
    assert result.exit_code != 0 and "cache.completions is off" in " ".join(result.output.split())
    assert seen == []
