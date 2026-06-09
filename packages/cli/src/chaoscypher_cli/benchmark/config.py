# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Named benchmark configs - the user-facing recipe for one runnable benchmark.

A config bundles run params, the list of dataset ids to evaluate, and the
list of models to run. ``chaoscypher benchmark run [NAME]`` loads the named
config, resolves dataset ids against discovered datasets, and dispatches
the runner.

Config discovery mirrors dataset discovery: built-in configs ship in the
package, user configs live in ``<data_dir>/benchmark/config/``, and user wins
on name collision.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Literal

import yaml

from chaoscypher_cli.benchmark.composite import CompositeWeights
from chaoscypher_cli.benchmark.discovery import user_benchmark_root
from chaoscypher_cli.benchmark.models import ModelConfig


# The name resolved when ``bench run`` is called with no positional argument.
DEFAULT_CONFIG_NAME = "extraction"


ConfigSource = Literal["builtin", "user"]


@dataclass(frozen=True)
class BenchmarkConfig:
    """One runnable benchmark recipe with role-list semantics.

    At least one of ``extractors``, ``embedders``, ``chats`` must be
    non-empty. ``embedders`` and ``chats`` require ``extractors`` to be
    set as well (graphs are built from extractors). ``chats`` requires
    ``judge``.

    Attributes:
        name: Display name shown in the leaderboard header.
        description: One-line description for ``bench list``.
        seed: Pinned seed; overridable by ``--seed``.
        temperature: Pinned temperature; overridable by ``--temperature``.
        dataset_ids: Datasets to evaluate, by id.
        extractors: Models that build graphs (stage 1). Also evaluated
            by V7 in the extraction leaderboard.
        embedders: Models indexed against each extractor's graph (stage 2).
        chats: Chat models evaluated end-to-end (stage 3).
        judge: Judge LLM for chat scoring; required iff ``chats`` is set.
        config_name: The slug used to load this config.
        source: Where the config was discovered.
        default_embedder: ``<provider>/<model>`` held fixed when attributing
            retrieval scores to an extractor in the composite. None when no
            ``defaults:`` block is present.
        default_chat: ``<provider>/<model>`` held fixed when attributing chat
            scores to an extractor in the composite. None when absent.
        weights: Composite Overall weights; None falls back to the defaults
            baked into :class:`CompositeWeights`.
    """

    name: str
    description: str
    seed: int
    temperature: float
    dataset_ids: list[str]
    extractors: list[ModelConfig] | None
    embedders: list[ModelConfig] | None
    chats: list[ModelConfig] | None
    judge: ModelConfig | None
    config_name: str
    source: ConfigSource
    default_embedder: str | None = None
    default_chat: str | None = None
    weights: CompositeWeights | None = None


def builtin_config_root() -> Path:
    """Path to the package-bundled config directory."""
    return Path(str(resources.files("chaoscypher_cli.benchmark").joinpath("data", "config")))


def user_config_root() -> Path:
    """Path to the user overlay config directory."""
    return user_benchmark_root() / "config"


def list_configs(
    *,
    builtin_root: Path | None = None,
    user_root: Path | None = None,
) -> list[tuple[str, ConfigSource, str]]:
    """List available config names with source and description.

    Returns tuples of ``(name, source, description)`` sorted by name. User
    configs override built-in ones with the same name (user wins).
    """
    builtin = builtin_root if builtin_root is not None else builtin_config_root()
    user = user_root if user_root is not None else user_config_root()

    by_name: dict[str, tuple[ConfigSource, str]] = {}
    for path in _yaml_files(builtin):
        by_name[path.stem] = ("builtin", _peek_description(path))
    for path in _yaml_files(user):
        by_name[path.stem] = ("user", _peek_description(path))

    return sorted(
        ((name, src, desc) for name, (src, desc) in by_name.items()),
        key=lambda t: t[0],
    )


def load_config(
    name: str,
    *,
    builtin_root: Path | None = None,
    user_root: Path | None = None,
) -> BenchmarkConfig:
    """Load a config by name. User overlay wins on collision.

    Raises:
        FileNotFoundError: If no config with ``name`` exists in either root.
        ValueError: If the config is malformed.
    """
    builtin = builtin_root if builtin_root is not None else builtin_config_root()
    user = user_root if user_root is not None else user_config_root()

    user_path = user / f"{name}.yaml"
    builtin_path = builtin / f"{name}.yaml"

    if user_path.exists():
        return _parse_config(user_path, source="user")
    if builtin_path.exists():
        return _parse_config(builtin_path, source="builtin")

    msg = (
        f"no benchmark config named '{name}' (looked in {user} and {builtin}). "
        "Run `chaoscypher benchmark list` to see available configs."
    )
    raise FileNotFoundError(msg)


def _parse_model_list(raw: object, *, path: Path, field_name: str) -> list[ModelConfig] | None:
    """Parse an optional list of model entries into ModelConfig instances.

    Returns None when the YAML key is absent. Raises ValueError when the key
    is present but the list is empty (matches the strict behavior of the
    pre-migration models: parser).
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        msg = f"{path}: '{field_name}' must be a list"
        raise TypeError(msg)
    if not raw:
        msg = f"{path}: '{field_name}' must be a non-empty list when present"
        raise ValueError(msg)
    out: list[ModelConfig] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            msg = f"{path}: {field_name}[{i}] is not a mapping"
            raise TypeError(msg)
        for required in ("provider", "model", "label"):
            if required not in entry:
                msg = f"{path}: {field_name}[{i}] missing required field '{required}'"
                raise ValueError(msg)
        kinds = entry.get("kinds")
        if kinds is not None and not isinstance(kinds, list):
            msg = f"{path}: {field_name}[{i}] 'kinds' must be a list"
            raise ValueError(msg)
        out.append(
            ModelConfig(
                provider=str(entry["provider"]),
                model=str(entry["model"]),
                label=str(entry["label"]),
                kinds=[str(k) for k in kinds] if kinds is not None else None,
            )
        )
    return out


def _parse_judge(raw: object, *, path: Path) -> ModelConfig | None:
    """Parse the optional 'judge' mapping into a ModelConfig.

    Returns None when the YAML key is absent. The judge is the LLM
    used to score chat answers; it never appears in any candidate role list.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        msg = f"{path}: 'judge' must be a mapping"
        raise TypeError(msg)
    for required in ("provider", "model"):
        if required not in raw:
            msg = f"{path}: judge missing required field '{required}'"
            raise ValueError(msg)
    return ModelConfig(
        provider=str(raw["provider"]),
        model=str(raw["model"]),
        label=str(raw.get("label", f"{raw['provider']}/{raw['model']} (judge)")),
        kinds=None,
    )


def _validate_role_lists(
    *,
    path: Path,
    extractors: list[ModelConfig] | None,
    embedders: list[ModelConfig] | None,
    chats: list[ModelConfig] | None,
    judge: ModelConfig | None,
    allow_self_judge: bool = False,
) -> None:
    """Enforce cross-role config invariants (required roles, no self-judge)."""
    if not extractors and not embedders and not chats:
        msg = f"{path}: config must set at least one of extractors/embedders/chats"
        raise ValueError(msg)
    if (embedders or chats) and not extractors:
        msg = f"{path}: 'extractors' is required when embedders or chats is set"
        raise ValueError(msg)
    if chats and judge is None:
        msg = f"{path}: 'judge' is required when 'chats' is non-empty"
        raise ValueError(msg)
    allow_via_env = os.environ.get("CHAOSCYPHER_BENCHMARK_ALLOW_SELF_JUDGE") == "1"
    if judge is not None and chats and not (allow_self_judge or allow_via_env):
        for c in chats:
            if c.provider == judge.provider and c.model == judge.model:
                msg = (
                    f"{path}: self-judge rejected — judge {judge.provider}/{judge.model} "
                    "matches a chat candidate; "
                    "set CHAOSCYPHER_BENCHMARK_ALLOW_SELF_JUDGE=1 for ablations"
                )
                raise ValueError(msg)


def _parse_config(path: Path, *, source: ConfigSource) -> BenchmarkConfig:
    """Load and validate a single benchmark config YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"{path}: config must be a YAML mapping"
        raise TypeError(msg)

    if "name" not in raw or "datasets" not in raw:
        msg = f"{path}: missing required field 'name' or 'datasets'"
        raise ValueError(msg)

    raw_datasets = raw["datasets"]
    if not isinstance(raw_datasets, list) or not raw_datasets:
        msg = f"{path}: 'datasets' must be a non-empty list of ids"
        raise ValueError(msg)

    extractors = _parse_model_list(raw.get("extractors"), path=path, field_name="extractors")
    embedders = _parse_model_list(raw.get("embedders"), path=path, field_name="embedders")
    chats = _parse_model_list(raw.get("chats"), path=path, field_name="chats")
    judge = _parse_judge(raw.get("judge"), path=path)

    _validate_role_lists(
        path=path,
        extractors=extractors,
        embedders=embedders,
        chats=chats,
        judge=judge,
    )

    defaults = raw.get("defaults") or {}
    weights_raw = raw.get("weights") or {}
    weights = (
        CompositeWeights(
            extraction=float(weights_raw.get("extraction", 0.40)),
            retrieval=float(weights_raw.get("retrieval", 0.20)),
            chat=float(weights_raw.get("chat", 0.20)),
            speed=float(weights_raw.get("speed", 0.10)),
            cost=float(weights_raw.get("cost", 0.10)),
        )
        if weights_raw
        else None
    )

    return BenchmarkConfig(
        name=str(raw["name"]),
        description=str(raw.get("description", "")),
        seed=int(raw.get("seed", 42)),
        temperature=float(raw.get("temperature", 0.0)),
        dataset_ids=[str(d) for d in raw_datasets],
        extractors=extractors,
        embedders=embedders,
        chats=chats,
        judge=judge,
        config_name=path.stem,
        source=source,
        default_embedder=defaults.get("embedder"),
        default_chat=defaults.get("chat"),
        weights=weights,
    )


def _yaml_files(root: Path) -> list[Path]:
    """Return sorted top-level .yaml files in root (empty if missing)."""
    if not root.exists():
        return []
    return sorted(p for p in root.glob("*.yaml") if p.is_file())


def _peek_description(path: Path) -> str:
    """Read just the description field for `bench list`. Failures return ''."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return str(data.get("description", ""))
    except Exception:
        return ""
    return ""


__all__ = [
    "DEFAULT_CONFIG_NAME",
    "BenchmarkConfig",
    "ConfigSource",
    "builtin_config_root",
    "list_configs",
    "load_config",
    "user_config_root",
]
