# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for BenchmarkConfig loading and built-in/user overlay."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_cli.benchmark.config import list_configs, load_config


def _write_config(root: Path, name: str, *, description: str = "", seed: int = 42) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.yaml").write_text(
        f'name: "{name}"\n'
        f'description: "{description}"\n'
        f"seed: {seed}\n"
        f"temperature: 0.0\n"
        f"datasets:\n"
        f"  - war_and_peace_tiny\n"
        f"extractors:\n"
        f"  - provider: ollama\n"
        f"    model: llama3.1:8b\n"
        f'    label: "Llama 3.1 8B"\n',
        encoding="utf-8",
    )


def test_load_config_builtin(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_config(builtin, "extraction", description="canonical")
    cfg = load_config("extraction", builtin_root=builtin, user_root=user)
    assert cfg.name == "extraction"
    assert cfg.description == "canonical"
    assert cfg.seed == 42
    assert cfg.temperature == 0.0
    assert cfg.dataset_ids == ["war_and_peace_tiny"]
    assert len(cfg.extractors) == 1
    assert cfg.config_name == "extraction"
    assert cfg.source == "builtin"


def test_load_config_user_wins_over_builtin(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_config(builtin, "extraction", description="builtin")
    _write_config(user, "extraction", description="user")
    cfg = load_config("extraction", builtin_root=builtin, user_root=user)
    assert cfg.description == "user"
    assert cfg.source == "user"


def test_load_config_unknown_name_raises(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir(parents=True)
    user.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="no benchmark config"):
        load_config("nonexistent", builtin_root=builtin, user_root=user)


def test_load_config_rejects_missing_required_fields(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir(parents=True)
    (builtin / "bad.yaml").write_text("name: bad\n", encoding="utf-8")
    with pytest.raises(ValueError, match="datasets"):
        load_config("bad", builtin_root=builtin, user_root=user)


def test_load_config_rejects_empty_datasets(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir(parents=True)
    (builtin / "empty.yaml").write_text(
        "name: empty\ndatasets: []\nextractors:\n  - {provider: ollama, model: x, label: X}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="non-empty"):
        load_config("empty", builtin_root=builtin, user_root=user)


def test_list_configs_merges_builtin_and_user(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_config(builtin, "extraction", description="canonical")
    _write_config(builtin, "quick", description="smoke")
    _write_config(user, "my-bench", description="my custom one")
    out = list_configs(builtin_root=builtin, user_root=user)
    by_name = {n: (s, d) for n, s, d in out}
    assert by_name["extraction"] == ("builtin", "canonical")
    assert by_name["quick"] == ("builtin", "smoke")
    assert by_name["my-bench"] == ("user", "my custom one")


def test_list_configs_user_overrides_builtin_in_listing(tmp_path: Path):
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    _write_config(builtin, "extraction", description="from builtin")
    _write_config(user, "extraction", description="from user")
    out = list_configs(builtin_root=builtin, user_root=user)
    by_name = {n: (s, d) for n, s, d in out}
    assert by_name["extraction"] == ("user", "from user")


def test_extractors_only_loads(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "extract_only.yaml").write_text(
        "name: x\ndatasets: [d1]\nextractors:\n  - {provider: ollama, model: llama, label: L}\n",
        encoding="utf-8",
    )
    cfg = load_config("extract_only", builtin_root=tmp_path / "_none", user_root=cfg_dir)
    assert len(cfg.extractors) == 1
    assert cfg.embedders is None
    assert cfg.chats is None
    assert cfg.judge is None


def test_chats_requires_judge(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "bad.yaml").write_text(
        "name: x\n"
        "datasets: [d1]\n"
        "extractors:\n"
        "  - {provider: ollama, model: llama, label: L}\n"
        "chats:\n"
        "  - {provider: openai, model: gpt-4o-mini, label: M}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="judge.*required"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_embedders_requires_extractors(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "bad.yaml").write_text(
        "name: x\ndatasets: [d1]\nembedders:\n  - {provider: ollama, model: nomic, label: N}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="extractors.*required"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_self_judge_rejected(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "selfj.yaml").write_text(
        "name: x\n"
        "datasets: [d1]\n"
        "extractors:\n"
        "  - {provider: ollama, model: llama, label: L}\n"
        "chats:\n"
        "  - {provider: openai, model: gpt-4o, label: G}\n"
        "judge:\n"
        "  provider: openai\n"
        "  model: gpt-4o\n"
        "  label: judge\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="self-judge"):
        load_config("selfj", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_at_least_one_role_list_required(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "empty.yaml").write_text(
        "name: x\ndatasets: [d1]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="at least one"):
        load_config("empty", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_empty_extractors_list_rejected(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "empty_ext.yaml").write_text(
        "name: x\ndatasets: [d1]\nextractors: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="extractors.*non-empty"):
        load_config("empty_ext", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_config_parses_defaults_and_weights(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "t.yaml").write_text(
        "name: t\n"
        "datasets: [d]\n"
        "extractors: [{provider: ollama, model: e, label: E}]\n"
        "embedders: [{provider: ollama, model: emb, label: Emb}]\n"
        "chats: [{provider: ollama, model: c, label: C}]\n"
        "judge: {provider: ollama, model: j, label: J}\n"
        "defaults:\n"
        "  embedder: ollama/emb\n"
        "  chat: ollama/c\n"
        "weights:\n"
        "  extraction: 0.5\n"
        "  retrieval: 0.2\n"
        "  chat: 0.2\n"
        "  speed: 0.05\n"
        "  cost: 0.05\n",
        encoding="utf-8",
    )
    cfg = load_config("t", builtin_root=tmp_path / "_none", user_root=cfg_dir)
    assert cfg.default_embedder == "ollama/emb"
    assert cfg.default_chat == "ollama/c"
    assert cfg.weights is not None
    assert cfg.weights.extraction == 0.5
    assert cfg.weights.retrieval == 0.2
    assert cfg.weights.speed == 0.05
    assert cfg.weights.cost == 0.05


def test_config_without_defaults_or_weights_has_none(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "plain.yaml").write_text(
        "name: plain\ndatasets: [d]\nextractors: [{provider: ollama, model: e, label: E}]\n",
        encoding="utf-8",
    )
    cfg = load_config("plain", builtin_root=tmp_path / "_none", user_root=cfg_dir)
    assert cfg.default_embedder is None
    assert cfg.default_chat is None
    assert cfg.weights is None


def test_full_config_loads(tmp_path):
    from chaoscypher_cli.benchmark.config import load_config

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "full.yaml").write_text(
        "name: full\n"
        "datasets: [d1]\n"
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
    cfg = load_config("full", builtin_root=tmp_path / "_none", user_root=cfg_dir)
    assert len(cfg.extractors) == 1
    assert len(cfg.embedders) == 1
    assert len(cfg.chats) == 1
    assert cfg.judge.model == "claude-opus-4-7"
