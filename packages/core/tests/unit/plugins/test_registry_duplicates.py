# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for deterministic scan ordering and duplicate-ID errors."""

from pathlib import Path

import pytest

from chaoscypher_core.plugins import PluginMetadata
from chaoscypher_core.plugins.registry import BaseRegistry, DuplicatePluginError


class _P:
    def __init__(self, plugin_id: str, source: str = "") -> None:
        self._metadata = PluginMetadata(plugin_id=plugin_id, name=plugin_id, description="")
        self.source = source

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata


class _Registry(BaseRegistry[_P]):
    def _discover(self) -> None:
        pass


def test_user_space_duplicate_raises() -> None:
    r = _Registry()
    r._register(_P("dup"), source_path=Path("/data/plugins/a.py"), is_user=True)
    with pytest.raises(DuplicatePluginError) as excinfo:
        r._register(
            _P("dup"),
            source_path=Path("/data/plugins/b.py"),
            is_user=True,
        )
    err = excinfo.value
    assert err.plugin_id == "dup"
    # The exception message renders paths via repr() which escapes backslashes
    # on Windows; check filename segments so the assertion stays cross-platform.
    assert "a.py" in str(err)
    assert "b.py" in str(err)
    assert err.existing_path == str(Path("/data/plugins/a.py"))
    assert err.new_path == str(Path("/data/plugins/b.py"))


def test_user_overrides_builtin_does_not_raise() -> None:
    r = _Registry()
    r._register(_P("x", source="builtin"), source_path=Path("builtin/x.py"), is_user=False)
    # User plugin with same ID is an intentional override.
    r._register(_P("x", source="user"), source_path=Path("user/x.py"), is_user=True)
    assert r.get("x").source == "user"


def test_register_is_backwards_compatible_without_kwargs() -> None:
    # Old callers that don't pass source_path/is_user still work.
    r = _Registry()
    r._register(_P("y"))
    r._register(_P("y"))  # no error -- keeps prior 'overwrite' behavior
    assert r.get("y") is not None


def test_loader_discovery_files_sorted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loader discovery must iterate files in sorted order."""
    from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
    from chaoscypher_core.settings import EngineSettings, PathSettings

    loaders_dir = tmp_path / "plugins" / "loaders"
    loaders_dir.mkdir(parents=True)

    for stem in ("zzz_loader", "aaa_loader"):
        (loaders_dir / f"{stem}.py").write_text(
            f"""
class LoaderFor_{stem}:
    supported_extensions = ['.{stem[:3]}']
    def __init__(self, settings=None):
        self.settings = settings
""",
            encoding="utf-8",
        )

    settings = EngineSettings(
        paths=PathSettings(
            data_dir=str(tmp_path),
            config_dir=str(tmp_path / "c"),
            cache_dir=str(tmp_path / "ch"),
        )
    )

    LoaderRegistry(settings=settings)

    # Both loaders should have registered -- sort order only matters for
    # deterministic duplicate handling, not for registration success.
    # We separately verify sort order by inspecting what the glob site uses.
    import inspect as inspect_mod

    from chaoscypher_core.services.sources.loaders import registry as reg_mod

    src = inspect_mod.getsource(reg_mod.LoaderRegistry._discover)
    assert "sorted(" in src, "LoaderRegistry._discover must sort() its glob"
