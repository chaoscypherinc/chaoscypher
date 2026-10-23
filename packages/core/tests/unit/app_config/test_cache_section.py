# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The cache section: defaults, env override, resolved directory under the data dir."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_defaults_are_on_two_gigabytes_under_the_data_dir(tmp_path: Path) -> None:
    from chaoscypher_core.settings import CacheSettings, EngineSettings, PathSettings

    s = EngineSettings(paths=PathSettings(data_dir=str(tmp_path)))
    assert isinstance(s.cache, CacheSettings)
    assert s.cache.completions.enabled is True and s.cache.embeddings.enabled is True
    assert s.cache.completions.max_bytes == 2 * 1024**3
    assert s.cache.embeddings.max_bytes == 2 * 1024**3
    assert s.cache.completions.require_hit is False
    assert s.cache.resolved_dir(s.paths.data_dir) == tmp_path / "cache" / "llm"


def test_an_explicit_dir_wins_over_the_data_dir(tmp_path: Path) -> None:
    from chaoscypher_core.settings import CacheSettings

    assert CacheSettings(dir=str(tmp_path / "elsewhere")).resolved_dir("/data") == (
        tmp_path / "elsewhere"
    )


def test_the_env_overrides_a_flag_and_the_app_settings_carry_the_section(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chaoscypher_core.app_config import Settings
    from chaoscypher_core.app_config.engine_factory import build_engine_settings

    path = tmp_path / "settings.yaml"
    path.write_text("app_name: t\n", encoding="utf-8")
    monkeypatch.setenv("CHAOSCYPHER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHAOSCYPHER_CACHE__COMPLETIONS__ENABLED", "false")
    # the autouse tmp-cache fixture sets CHAOSCYPHER_CACHE__DIR; this test is about the default
    monkeypatch.delenv("CHAOSCYPHER_CACHE__DIR", raising=False)
    app = Settings.load_from_yaml(path)
    assert app.cache.completions.enabled is False and app.cache.embeddings.enabled is True
    engine = build_engine_settings(app)
    assert engine.cache.completions.enabled is False
    assert engine.cache.resolved_dir(engine.paths.data_dir) == tmp_path / "cache" / "llm"
    engine.cache.embeddings.enabled = False
    assert app.cache.embeddings.enabled is True  # an isolated copy, like every group


def test_the_cache_section_loads_from_yaml(tmp_path: Path) -> None:
    from chaoscypher_core.app_config import Settings

    path = tmp_path / "settings.yaml"
    path.write_text(
        "cache:\n  embeddings:\n    enabled: false\n    max_bytes: 33554432\n", encoding="utf-8"
    )
    app = Settings.load_from_yaml(path)
    assert app.cache.embeddings.enabled is False
    assert app.cache.embeddings.max_bytes == 32 * 1024**2


def test_a_cap_below_the_floor_is_refused() -> None:
    from pydantic import ValidationError

    from chaoscypher_core.settings import CacheStoreSettings

    with pytest.raises(ValidationError):
        CacheStoreSettings(max_bytes=1024)


def _nested_flags() -> list[tuple[str, str, str]]:
    """(section, sub-model field, bool field) for every sub-model of every ``Settings`` section."""
    from pydantic import BaseModel

    from chaoscypher_core.app_config import Settings

    out: list[tuple[str, str, str]] = []
    for name, field in Settings.model_fields.items():
        model = field.annotation
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        for sub_name, sub_field in model.model_fields.items():
            sub = sub_field.annotation
            if isinstance(sub, type) and issubclass(sub, BaseModel):
                flag = next((n for n, f in sub.model_fields.items() if f.annotation is bool), None)
                if flag:
                    out.append((name, sub_name, flag))
    return out


def test_the_cache_section_has_nested_flags() -> None:
    assert ("cache", "completions", "enabled") in _nested_flags()


@pytest.mark.parametrize(("section", "sub", "flag"), _nested_flags())
def test_every_nested_sub_section_flag_loads_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, section: str, sub: str, flag: str
) -> None:
    """A two-level env var reaches its field even with no such section in settings.yaml."""
    from chaoscypher_core.app_config import Settings

    path = tmp_path / "settings.yaml"
    path.write_text("app_name: t\n", encoding="utf-8")
    model = type(getattr(getattr(Settings(), section), sub))
    flipped = not model.model_fields[flag].default
    monkeypatch.setenv(
        f"CHAOSCYPHER_{section.upper()}__{sub.upper()}__{flag.upper()}", str(flipped).lower()
    )
    loaded = Settings.load_from_yaml(path)
    assert getattr(getattr(getattr(loaded, section), sub), flag) is flipped
