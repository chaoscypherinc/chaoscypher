# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage for config parsing error branches + root resolvers + self-judge env."""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_cli.benchmark.config import (
    builtin_config_root,
    list_configs,
    load_config,
    user_config_root,
)


def _write(root: Path, name: str, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{name}.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_builtin_config_root_is_package_relative() -> None:
    root = builtin_config_root()
    assert root.name == "config"
    assert "benchmark" in root.parts


def test_user_config_root_under_benchmark() -> None:
    root = user_config_root()
    assert root.name == "config"
    assert root.parent.name == "benchmark"


def test_config_not_mapping_raises_type_error(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    _write(cfg_dir, "bad", "- a\n- b\n")
    with pytest.raises(TypeError, match="must be a YAML mapping"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_model_list_not_a_list_raises_type_error(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    _write(
        cfg_dir,
        "bad",
        "name: x\ndatasets: [d1]\nextractors: not-a-list\n",
    )
    with pytest.raises(TypeError, match="'extractors' must be a list"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_model_entry_not_mapping_raises_type_error(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    _write(
        cfg_dir,
        "bad",
        "name: x\ndatasets: [d1]\nextractors:\n  - just-a-string\n",
    )
    with pytest.raises(TypeError, match=r"extractors\[0\] is not a mapping"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_model_entry_missing_required_field(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    _write(
        cfg_dir,
        "bad",
        "name: x\ndatasets: [d1]\nextractors:\n  - {provider: ollama, model: m}\n",
    )
    with pytest.raises(ValueError, match="missing required field 'label'"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_model_kinds_not_a_list(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    _write(
        cfg_dir,
        "bad",
        "name: x\n"
        "datasets: [d1]\n"
        "extractors:\n"
        "  - {provider: ollama, model: m, label: M, kinds: chat}\n",
    )
    with pytest.raises(ValueError, match=r"extractors\[0\] 'kinds' must be a list"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_judge_not_a_mapping_raises_type_error(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    _write(
        cfg_dir,
        "bad",
        "name: x\n"
        "datasets: [d1]\n"
        "extractors:\n"
        "  - {provider: ollama, model: m, label: M}\n"
        "chats:\n"
        "  - {provider: openai, model: gpt-4o, label: G}\n"
        "judge: not-a-mapping\n",
    )
    with pytest.raises(TypeError, match="'judge' must be a mapping"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_judge_missing_required_field(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    _write(
        cfg_dir,
        "bad",
        "name: x\n"
        "datasets: [d1]\n"
        "extractors:\n"
        "  - {provider: ollama, model: m, label: M}\n"
        "chats:\n"
        "  - {provider: openai, model: gpt-4o, label: G}\n"
        "judge:\n"
        "  provider: anthropic\n",
    )
    with pytest.raises(ValueError, match="judge missing required field 'model'"):
        load_config("bad", builtin_root=tmp_path / "_none", user_root=cfg_dir)


def test_judge_label_defaults_when_absent(tmp_path: Path) -> None:
    cfg_dir = tmp_path / "config"
    _write(
        cfg_dir,
        "full",
        "name: x\n"
        "datasets: [d1]\n"
        "extractors:\n"
        "  - {provider: ollama, model: a, label: A}\n"
        "embedders:\n"
        "  - {provider: ollama, model: b, label: B}\n"
        "chats:\n"
        "  - {provider: openai, model: gpt-4o, label: C}\n"
        "judge:\n"
        "  provider: anthropic\n"
        "  model: claude-opus-4-7\n",
    )
    cfg = load_config("full", builtin_root=tmp_path / "_none", user_root=cfg_dir)
    assert cfg.judge is not None
    assert cfg.judge.label == "anthropic/claude-opus-4-7 (judge)"


def test_self_judge_allowed_via_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CHAOSCYPHER_BENCHMARK_ALLOW_SELF_JUDGE=1 permits an otherwise-rejected config."""
    monkeypatch.setenv("CHAOSCYPHER_BENCHMARK_ALLOW_SELF_JUDGE", "1")
    cfg_dir = tmp_path / "config"
    _write(
        cfg_dir,
        "selfj",
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
    )
    cfg = load_config("selfj", builtin_root=tmp_path / "_none", user_root=cfg_dir)
    assert cfg.judge is not None
    assert cfg.judge.model == "gpt-4o"


def test_list_configs_empty_when_roots_missing(tmp_path: Path) -> None:
    out = list_configs(builtin_root=tmp_path / "none1", user_root=tmp_path / "none2")
    assert out == []


def test_list_configs_skips_unreadable_description(tmp_path: Path) -> None:
    """A config whose YAML body is a bare list yields an empty description."""
    builtin = tmp_path / "builtin"
    _write(builtin, "weird", "- a\n- b\n")  # not a mapping -> _peek_description -> ''
    out = list_configs(builtin_root=builtin, user_root=tmp_path / "_none")
    by_name = {n: (s, d) for n, s, d in out}
    assert by_name["weird"] == ("builtin", "")


def test_peek_description_swallows_parse_error(tmp_path: Path) -> None:
    """Malformed YAML in a listed config falls back to '' (no crash)."""
    builtin = tmp_path / "builtin"
    builtin.mkdir(parents=True)
    (builtin / "broken.yaml").write_text("key: [unterminated\n", encoding="utf-8")
    out = list_configs(builtin_root=builtin, user_root=tmp_path / "_none")
    by_name = {n: d for n, _s, d in out}
    assert by_name["broken"] == ""
