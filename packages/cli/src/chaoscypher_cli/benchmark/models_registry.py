# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Typed loader for the benchmark model registry (single source of truth)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml


@dataclass(frozen=True)
class RegistryEntry:
    """One model's metadata + optional pricing."""

    provider: str
    model: str
    label: str
    tier: str | None = None
    released: str | None = None
    context: int | None = None
    open_weight: bool = False
    license: str | None = None
    price: dict[str, float] | None = None
    price_dated: str | None = None
    vram_gb: float | None = None
    why: str | None = None
    notes: str | None = None

    @property
    def model_id(self) -> str:
        """``<provider>/<model>`` key."""
        return f"{self.provider}/{self.model}"


def registry_path() -> Path:
    """Path to the package-bundled registry yaml."""
    return Path(
        str(resources.files("chaoscypher_cli.benchmark").joinpath("data", "models_registry.yaml"))
    )


def load_registry(*, path: Path | None = None) -> dict[str, RegistryEntry]:
    """Load and validate the registry, keyed by ``<provider>/<model>``.

    Raises:
        ValueError: if an entry is missing required fields or its key does not
            match its ``provider/model``.
    """
    if path is None:
        return _load_cached()
    return _parse(path)


@lru_cache(maxsize=1)
def _load_cached() -> dict[str, RegistryEntry]:
    """Load and cache the registry from its default path for the process lifetime."""
    return _parse(registry_path())


def _parse(path: Path) -> dict[str, RegistryEntry]:
    """Parse and validate the model registry YAML mapping at ``path``."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        msg = f"{path}: registry must be a YAML mapping"
        raise TypeError(msg)
    out: dict[str, RegistryEntry] = {}
    for key, body in raw.items():
        if not isinstance(body, dict):
            msg = f"{path}: entry '{key}' must be a mapping"
            raise TypeError(msg)
        for required in ("provider", "model", "label"):
            if required not in body:
                msg = f"{path}: entry '{key}' missing required field '{required}'"
                raise ValueError(msg)
        entry = RegistryEntry(
            provider=str(body["provider"]),
            model=str(body["model"]),
            label=str(body["label"]),
            tier=body.get("tier"),
            released=body.get("released"),
            context=body.get("context"),
            open_weight=bool(body.get("open_weight", False)),
            license=body.get("license"),
            price=dict(body["price"]) if body.get("price") is not None else None,
            price_dated=body.get("price_dated"),
            vram_gb=body.get("vram_gb"),
            why=body.get("why"),
            notes=body.get("notes"),
        )
        if entry.price is not None:
            for k in ("input", "output"):
                if k not in entry.price:
                    msg = f"{path}: entry '{key}' price block missing key '{k}'"
                    raise ValueError(msg)
        if entry.model_id != str(key):
            msg = f"{path}: key '{key}' must equal '{entry.model_id}'"
            raise ValueError(msg)
        out[entry.model_id] = entry
    return out


__all__ = ["RegistryEntry", "load_registry", "registry_path"]
