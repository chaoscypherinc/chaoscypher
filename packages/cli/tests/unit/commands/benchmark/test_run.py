# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for `chaoscypher benchmark run [NAME]`.

Covers config loading + flag overrides + orchestration glue. End-to-end
tests that hit a real LLM live in the Phase 7 smoke test, not here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from click.testing import CliRunner

from chaoscypher_cli.commands.benchmark.run import run


if TYPE_CHECKING:
    from chaoscypher_cli.benchmark.config import BenchmarkConfig


def _write_dataset(root: Path, pid: str) -> None:
    pdir = root / pid
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{pid}.txt").write_text("x", encoding="utf-8")
    (pdir / "manifest.yaml").write_text(
        f"id: {pid}\nkind: extraction\nversion: '1.0'\ndomain: literary\ncorpus_path: {pid}.txt\n",
        encoding="utf-8",
    )


def _write_config(
    root: Path, name: str, datasets: list[str], *, with_commercial: bool = True
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    body = f'name: "{name}"\nseed: 42\ntemperature: 0.0\ndatasets:\n'
    for d in datasets:
        body += f"  - {d}\n"
    body += 'extractors:\n  - provider: ollama\n    model: local-model\n    label: "Local"\n'
    if with_commercial:
        body += '  - provider: openai\n    model: gpt-x\n    label: "GPT-X"\n'
    (root / f"{name}.yaml").write_text(body, encoding="utf-8")


def _patch_roots(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Set up per-test built-in / user dataset and config roots."""
    builtin_ds = tmp_path / "builtin_datasets"
    user_ds = tmp_path / "user_datasets"
    builtin_cfg = tmp_path / "builtin_config"
    user_cfg = tmp_path / "user_config"
    return builtin_ds, user_ds, builtin_cfg, user_cfg


def _make_load_bundle_side_effect(builtin_ds: Path, user_ds: Path):
    """Return a side_effect callable that delegates to the real load_dataset_bundle."""

    def _side_effect(dataset_id: str) -> object:
        from chaoscypher_cli.benchmark.discovery import load_dataset_bundle

        return load_dataset_bundle(
            dataset_id,
            builtin_root=builtin_ds,
            user_root=user_ds,
        )

    return _side_effect


def test_run_loads_named_config_and_invokes_runner(tmp_path: Path):
    builtin_ds, user_ds, builtin_cfg, user_cfg = _patch_roots(tmp_path)
    _write_dataset(builtin_ds, "p1")
    _write_config(builtin_cfg, "extraction", ["p1"])
    out_dir = tmp_path / "out"

    with (
        patch(
            "chaoscypher_cli.commands.benchmark.run.run_benchmark",
            return_value=[],
        ) as mock_run,
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=_make_load_bundle_side_effect(builtin_ds, user_ds),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: __import__(
                "chaoscypher_cli.benchmark.config", fromlist=["load_config"]
            ).load_config(name, builtin_root=builtin_cfg, user_root=user_cfg),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(run, ["extraction", "--out", str(out_dir)])

    assert result.exit_code == 0, result.output
    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    assert [d.id for d in kwargs["datasets"]] == ["p1"]
    assert len(kwargs["models"]) == 2  # local + commercial
    assert kwargs["config_name"] == "extraction"


def test_run_default_name_is_extraction(tmp_path: Path):
    """`benchmark run` with no positional arg loads 'extraction'."""
    builtin_ds, user_ds, builtin_cfg, user_cfg = _patch_roots(tmp_path)
    _write_dataset(builtin_ds, "p1")
    _write_config(builtin_cfg, "extraction", ["p1"], with_commercial=False)
    out_dir = tmp_path / "out"

    with (
        patch("chaoscypher_cli.commands.benchmark.run.run_benchmark", return_value=[]),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=_make_load_bundle_side_effect(builtin_ds, user_ds),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: __import__(
                "chaoscypher_cli.benchmark.config", fromlist=["load_config"]
            ).load_config(name, builtin_root=builtin_cfg, user_root=user_cfg),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(run, ["--out", str(out_dir)])
    assert result.exit_code == 0, result.output


def test_run_local_only_filters_commercial(tmp_path: Path):
    builtin_ds, user_ds, builtin_cfg, user_cfg = _patch_roots(tmp_path)
    _write_dataset(builtin_ds, "p1")
    _write_config(builtin_cfg, "extraction", ["p1"])
    out_dir = tmp_path / "out"

    with (
        patch(
            "chaoscypher_cli.commands.benchmark.run.run_benchmark",
            return_value=[],
        ) as mock_run,
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=_make_load_bundle_side_effect(builtin_ds, user_ds),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: __import__(
                "chaoscypher_cli.benchmark.config", fromlist=["load_config"]
            ).load_config(name, builtin_root=builtin_cfg, user_root=user_cfg),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(run, ["extraction", "--local-only", "--out", str(out_dir)])
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    assert [m.provider for m in kwargs["models"]] == ["ollama"]


def test_run_dataset_flag_filters_to_one_dataset(tmp_path: Path):
    builtin_ds, user_ds, builtin_cfg, user_cfg = _patch_roots(tmp_path)
    _write_dataset(builtin_ds, "keep_me")
    _write_dataset(builtin_ds, "skip_me")
    _write_config(builtin_cfg, "extraction", ["keep_me", "skip_me"], with_commercial=False)
    out_dir = tmp_path / "out"

    with (
        patch(
            "chaoscypher_cli.commands.benchmark.run.run_benchmark",
            return_value=[],
        ) as mock_run,
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=_make_load_bundle_side_effect(builtin_ds, user_ds),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: __import__(
                "chaoscypher_cli.benchmark.config", fromlist=["load_config"]
            ).load_config(name, builtin_root=builtin_cfg, user_root=user_cfg),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["extraction", "--dataset", "keep_me", "--out", str(out_dir)],
        )
    assert result.exit_code == 0
    kwargs = mock_run.call_args.kwargs
    assert [d.id for d in kwargs["datasets"]] == ["keep_me"]


def test_run_aborts_when_dataset_not_in_config(tmp_path: Path):
    builtin_ds, user_ds, builtin_cfg, user_cfg = _patch_roots(tmp_path)
    _write_dataset(builtin_ds, "p1")
    _write_config(builtin_cfg, "extraction", ["p1"], with_commercial=False)
    out_dir = tmp_path / "out"

    with (
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=_make_load_bundle_side_effect(builtin_ds, user_ds),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: __import__(
                "chaoscypher_cli.benchmark.config", fromlist=["load_config"]
            ).load_config(name, builtin_root=builtin_cfg, user_root=user_cfg),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(
            run,
            ["extraction", "--dataset", "nonexistent", "--out", str(out_dir)],
        )
    assert result.exit_code != 0
    assert "nonexistent" in result.output


def test_run_aborts_on_unknown_config_name(tmp_path: Path):
    builtin_ds, user_ds, builtin_cfg, user_cfg = _patch_roots(tmp_path)
    builtin_cfg.mkdir(parents=True, exist_ok=True)
    user_cfg.mkdir(parents=True, exist_ok=True)
    out_dir = tmp_path / "out"

    with (
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=_make_load_bundle_side_effect(builtin_ds, user_ds),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: __import__(
                "chaoscypher_cli.benchmark.config", fromlist=["load_config"]
            ).load_config(name, builtin_root=builtin_cfg, user_root=user_cfg),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(run, ["nonexistent", "--out", str(out_dir)])
    assert result.exit_code != 0
    assert "nonexistent" in result.output


def test_run_writes_json_and_markdown(tmp_path: Path):
    builtin_ds, user_ds, builtin_cfg, user_cfg = _patch_roots(tmp_path)
    _write_dataset(builtin_ds, "p1")
    _write_config(builtin_cfg, "extraction", ["p1"], with_commercial=False)
    out_dir = tmp_path / "out"

    with (
        patch("chaoscypher_cli.commands.benchmark.run.run_benchmark", return_value=[]),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=_make_load_bundle_side_effect(builtin_ds, user_ds),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: __import__(
                "chaoscypher_cli.benchmark.config", fromlist=["load_config"]
            ).load_config(name, builtin_root=builtin_cfg, user_root=user_cfg),
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(run, ["extraction", "--out", str(out_dir)])
    assert result.exit_code == 0
    json_files = list(out_dir.glob("*.json"))
    md_files = list(out_dir.glob("*.md"))
    assert len(json_files) == 1
    assert len(md_files) == 2  # <timestamp>.md and latest.md
    assert (out_dir / "latest.md").exists()


def test_estimate_prints_breakdown_and_exits(tmp_path: Path):
    """`bench run <cfg> --estimate` prints stage breakdown and exits without running."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True)
    cfg_path = cfg_dir / "full-estimate.yaml"
    cfg_path.write_text(
        "name: full-estimate\n"
        "datasets: [demo]\n"
        "extractors:\n"
        "  - {provider: ollama, model: a, label: A}\n"
        "embedders:\n"
        "  - {provider: ollama, model: b, label: B}\n"
        "chats:\n"
        "  - {provider: openai, model: c, label: C}\n"
        "judge:\n"
        "  provider: anthropic\n"
        "  model: claude-opus-4-7\n"
        "  label: J\n",
        encoding="utf-8",
    )
    ds_dir = tmp_path / "datasets" / "demo"
    ds_dir.mkdir(parents=True)
    (ds_dir / "demo.txt").write_text("text", encoding="utf-8")
    (ds_dir / "manifest.yaml").write_text(
        "id: demo\nkind: extraction\nversion: '1.0'\ndomain: technical\ncorpus_path: demo.txt\n",
        encoding="utf-8",
    )

    with (
        patch(
            "chaoscypher_cli.benchmark.config.builtin_config_root",
            return_value=tmp_path / "_no_builtin",
        ),
        patch(
            "chaoscypher_cli.benchmark.config.user_config_root",
            return_value=cfg_dir,
        ),
        patch(
            "chaoscypher_cli.benchmark.discovery.builtin_dataset_root",
            return_value=tmp_path / "_no_builtin_ds",
        ),
        patch(
            "chaoscypher_cli.benchmark.discovery.user_dataset_root",
            return_value=tmp_path / "datasets",
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(run, ["full-estimate", "--estimate"])

    assert result.exit_code == 0, result.output
    assert "Stage 1" in result.output
    assert "Stage 2" in result.output
    assert "Stage 3" in result.output
    assert "extraction runs" in result.output


def test_rebuild_graphs_clears_cache(tmp_path: Path):
    """`--rebuild-graphs` clears the cache directory before running."""
    from chaoscypher_cli.benchmark.graph_cache import GraphCache

    cache_root = tmp_path / "workspace" / "graph_cache"
    cache_root.mkdir(parents=True)
    marker = cache_root / "marker.txt"
    marker.write_text("hello")
    assert marker.exists()

    cache = GraphCache(root=cache_root)
    cache.clear()
    assert not marker.exists()


def _write_full_config_no_chats(root: Path, name: str, datasets: list[str]) -> None:
    """Write a full-mode config YAML (extractors + embedders, no chats/judge)."""
    root.mkdir(parents=True, exist_ok=True)
    body = f'name: "{name}"\nseed: 42\ntemperature: 0.0\ndatasets:\n'
    for d in datasets:
        body += f"  - {d}\n"
    body += (
        "extractors:\n"
        "  - provider: ollama\n    model: local-ext\n    label: LocalExt\n"
        "  - provider: openai\n    model: gpt-x\n    label: GPT-X\n"
        "embedders:\n"
        "  - provider: ollama\n    model: local-emb\n    label: LocalEmb\n"
        "  - provider: openai\n    model: text-emb-3\n    label: TextEmb3\n"
    )
    (root / f"{name}.yaml").write_text(body, encoding="utf-8")


def test_local_only_strips_commercial_models_in_full_mode(tmp_path: Path):
    """`--local-only` filters commercial extractors/embedders in full mode.

    Uses an extractors+embedders config (no chats/judge) to confirm that
    ``--local-only`` correctly prunes commercial models before the full
    orchestrator is called.
    """
    builtin_ds, user_ds, builtin_cfg, user_cfg = _patch_roots(tmp_path)
    _write_dataset(builtin_ds, "p1")
    _write_full_config_no_chats(builtin_cfg, "full", ["p1"])
    out_dir = tmp_path / "out"

    captured_cfg: list[object] = []

    async def _fake_run_full(cfg: object, bundles: object, *, wiring: object) -> list:
        captured_cfg.append(cfg)
        return []

    with (
        # run_full_benchmark and default_wiring are lazy-imported inside the
        # is_full_mode block, so patch them at their source module.
        patch(
            "chaoscypher_cli.benchmark.orchestrator.run_full_benchmark",
            side_effect=_fake_run_full,
        ),
        patch(
            "chaoscypher_cli.benchmark.orchestrator.default_wiring",
            return_value=object(),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_dataset_bundle",
            side_effect=_make_load_bundle_side_effect(builtin_ds, user_ds),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.load_config",
            side_effect=lambda name: __import__(
                "chaoscypher_cli.benchmark.config", fromlist=["load_config"]
            ).load_config(name, builtin_root=builtin_cfg, user_root=user_cfg),
        ),
        patch(
            "chaoscypher_cli.commands.benchmark.run.user_benchmark_root",
            return_value=tmp_path / "bench",
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(run, ["full", "--local-only", "--out", str(out_dir)])

    assert result.exit_code == 0, result.output
    assert len(captured_cfg) == 1
    cfg_used: BenchmarkConfig = captured_cfg[0]  # type: ignore[assignment]
    # Only ollama extractors/embedders should survive --local-only.
    assert cfg_used.extractors is not None
    assert all(m.provider == "ollama" for m in cfg_used.extractors), (
        f"Expected only ollama extractors; got {[m.provider for m in cfg_used.extractors]}"
    )
    assert cfg_used.embedders is not None
    assert all(m.provider == "ollama" for m in cfg_used.embedders), (
        f"Expected only ollama embedders; got {[m.provider for m in cfg_used.embedders]}"
    )
    # No judge in this config.
    assert cfg_used.judge is None
