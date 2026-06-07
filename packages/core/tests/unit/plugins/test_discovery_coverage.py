# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Coverage tests for the plugin discovery utilities.

Target: chaoscypher_core.plugins.discovery

Covers both discovery patterns plus their private helpers:
- discover_python_plugins: imports plugin modules from a tmp package on
  sys.path, exercising required-attr filtering, instantiation, ID extraction,
  exclude-file skipping, and per-file error tolerance.
- discover_config_plugins: scans a tmp directory tree for JSON-LD configs,
  exercising primary/alternative filenames, name derivation, hidden-dir
  skipping, recursive vs non-recursive, missing-dir, and bad-JSON tolerance.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from chaoscypher_core.plugins.discovery import (
    _extract_plugin_id,
    _find_config_file,
    _has_required_attrs,
    _load_config_file,
    discover_config_plugins,
    discover_python_plugins,
)


# ----------------------------------------------------------------------
# _has_required_attrs
# ----------------------------------------------------------------------
class TestHasRequiredAttrs:
    def test_all_present(self) -> None:
        class Obj:
            a = 1
            b = 2

        assert _has_required_attrs(Obj, ["a", "b"]) is True

    def test_missing_returns_false(self) -> None:
        class Obj:
            a = 1

        assert _has_required_attrs(Obj, ["a", "missing"]) is False

    def test_empty_required_always_true(self) -> None:
        assert _has_required_attrs(object(), []) is True


# ----------------------------------------------------------------------
# _extract_plugin_id
# ----------------------------------------------------------------------
class TestExtractPluginId:
    def test_prefers_plugin_id(self) -> None:
        inst = type("X", (), {"plugin_id": "pid", "tool_id": "tid", "name": "nm"})()
        assert _extract_plugin_id(inst, []) == "pid"

    def test_falls_back_to_tool_id(self) -> None:
        inst = type("X", (), {"tool_id": "tid", "name": "nm"})()
        assert _extract_plugin_id(inst, []) == "tid"

    def test_falls_back_to_name(self) -> None:
        inst = type("X", (), {"name": "nm"})()
        assert _extract_plugin_id(inst, []) == "nm"

    def test_first_string_required_attr(self) -> None:
        # No common ID attrs; uses first required attr that is a string.
        inst = type("X", (), {"custom_id": "cid"})()
        assert _extract_plugin_id(inst, ["custom_id"]) == "cid"

    def test_non_string_common_attr_skipped(self) -> None:
        # plugin_id is non-string -> skipped; falls back to required attr.
        inst = type("X", (), {"plugin_id": 123, "label": "lbl"})()
        assert _extract_plugin_id(inst, ["label"]) == "lbl"

    def test_last_resort_class_name(self) -> None:
        class NoIdHere:
            pass

        assert _extract_plugin_id(NoIdHere(), []) == "NoIdHere"


# ----------------------------------------------------------------------
# _find_config_file / _load_config_file
# ----------------------------------------------------------------------
class TestFindConfigFile:
    def test_primary_found(self, tmp_path: Path) -> None:
        (tmp_path / "plugin.jsonld").write_text("{}", encoding="utf-8")
        result = _find_config_file(tmp_path, "plugin.jsonld", [])
        assert result == tmp_path / "plugin.jsonld"

    def test_alternative_found(self, tmp_path: Path) -> None:
        (tmp_path / "domain.json").write_text("{}", encoding="utf-8")
        result = _find_config_file(tmp_path, "domain.jsonld", ["domain.json"])
        assert result == tmp_path / "domain.json"

    def test_none_when_absent(self, tmp_path: Path) -> None:
        assert _find_config_file(tmp_path, "plugin.jsonld", ["other.json"]) is None


class TestLoadConfigFile:
    def test_loads_json(self, tmp_path: Path) -> None:
        path = tmp_path / "c.jsonld"
        path.write_text(json.dumps({"name": "x", "version": "2.0"}), encoding="utf-8")
        cfg = _load_config_file(path)
        assert cfg == {"name": "x", "version": "2.0"}

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonld"
        path.write_text("{nope", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            _load_config_file(path)


# ----------------------------------------------------------------------
# discover_config_plugins
# ----------------------------------------------------------------------
class TestDiscoverConfigPlugins:
    def test_missing_directory_returns_empty(self, tmp_path: Path) -> None:
        assert discover_config_plugins(tmp_path / "nope") == {}

    def test_discovers_subdir_configs_with_name(self, tmp_path: Path) -> None:
        tech = tmp_path / "technical"
        tech.mkdir()
        (tech / "domain.jsonld").write_text(
            json.dumps({"name": "technical", "version": "1.0"}), encoding="utf-8"
        )
        generic = tmp_path / "generic"
        generic.mkdir()
        (generic / "domain.jsonld").write_text(json.dumps({"name": "generic"}), encoding="utf-8")

        configs = discover_config_plugins(tmp_path, config_filename="domain.jsonld")
        assert set(configs) == {"technical", "generic"}
        assert configs["technical"]["version"] == "1.0"

    def test_name_falls_back_to_folder(self, tmp_path: Path) -> None:
        sub = tmp_path / "myfolder"
        sub.mkdir()
        # Config without a "name" key -> plugin keyed by folder name.
        (sub / "plugin.jsonld").write_text(json.dumps({"foo": "bar"}), encoding="utf-8")
        configs = discover_config_plugins(tmp_path)
        assert "myfolder" in configs

    def test_alternative_filename(self, tmp_path: Path) -> None:
        sub = tmp_path / "alt"
        sub.mkdir()
        (sub / "domain.json").write_text(json.dumps({"name": "alt"}), encoding="utf-8")
        configs = discover_config_plugins(
            tmp_path,
            config_filename="domain.jsonld",
            alternative_filenames=["domain.json"],
        )
        assert "alt" in configs

    def test_hidden_and_underscore_dirs_skipped(self, tmp_path: Path) -> None:
        for skip in ("_private", ".hidden"):
            d = tmp_path / skip
            d.mkdir()
            (d / "plugin.jsonld").write_text(json.dumps({"name": skip}), encoding="utf-8")
        ok = tmp_path / "ok"
        ok.mkdir()
        (ok / "plugin.jsonld").write_text(json.dumps({"name": "ok"}), encoding="utf-8")

        configs = discover_config_plugins(tmp_path)
        assert set(configs) == {"ok"}

    def test_subdir_without_config_skipped(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()  # no config file inside
        assert discover_config_plugins(tmp_path) == {}

    def test_bad_json_tolerated(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad"
        bad.mkdir()
        (bad / "plugin.jsonld").write_text("{not json", encoding="utf-8")
        good = tmp_path / "good"
        good.mkdir()
        (good / "plugin.jsonld").write_text(json.dumps({"name": "good"}), encoding="utf-8")
        configs = discover_config_plugins(tmp_path)
        # Bad one skipped, good one kept.
        assert set(configs) == {"good"}

    def test_non_recursive_scans_directory_itself(self, tmp_path: Path) -> None:
        (tmp_path / "plugin.jsonld").write_text(
            json.dumps({"name": "root_plugin"}), encoding="utf-8"
        )
        configs = discover_config_plugins(tmp_path, recursive=False)
        assert "root_plugin" in configs


# ----------------------------------------------------------------------
# discover_python_plugins
# ----------------------------------------------------------------------
_PLUGIN_SOURCE = """
class WidgetPlugin:
    plugin_id = "widget"
    name = "Widget"

    def execute(self):
        return "ran"


class NotAPlugin:
    # Missing the required "execute" attr.
    name = "incomplete"
"""

_IMPORTED_CLASS_SOURCE = """
from collections import OrderedDict  # imported class must be skipped


class GadgetPlugin:
    plugin_id = "gadget"

    def execute(self):
        return "gadget"
"""


@pytest.fixture
def plugin_package(tmp_path: Path) -> Any:
    """Create a tmp package directory on sys.path with plugin modules."""
    pkg_dir = tmp_path / "tmp_plugin_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "widget_plugin.py").write_text(_PLUGIN_SOURCE, encoding="utf-8")
    (pkg_dir / "gadget_plugin.py").write_text(_IMPORTED_CLASS_SOURCE, encoding="utf-8")
    # A file that should be excluded by default.
    (pkg_dir / "base.py").write_text("class Base: pass", encoding="utf-8")

    sys.path.insert(0, str(tmp_path))
    try:
        yield pkg_dir, "tmp_plugin_pkg"
    finally:
        sys.path.remove(str(tmp_path))
        for mod in list(sys.modules):
            if mod == "tmp_plugin_pkg" or mod.startswith("tmp_plugin_pkg."):
                del sys.modules[mod]


class TestDiscoverPythonPlugins:
    def test_discovers_plugin_with_required_attrs(self, plugin_package: Any) -> None:
        pkg_dir, prefix = plugin_package
        plugins = discover_python_plugins(
            directory=pkg_dir,
            pattern="*_plugin.py",
            required_attrs=["execute"],
            module_prefix=prefix,
        )
        # widget + gadget both expose execute; NotAPlugin is filtered out.
        assert plugins["widget"].execute() == "ran"
        assert plugins["gadget"].execute() == "gadget"

    def test_imported_class_is_skipped(self, plugin_package: Any) -> None:
        """OrderedDict imported into gadget module must not be registered."""
        pkg_dir, prefix = plugin_package
        plugins = discover_python_plugins(
            directory=pkg_dir,
            pattern="*_plugin.py",
            required_attrs=["execute"],
            module_prefix=prefix,
        )
        assert "OrderedDict" not in plugins
        assert not any(type(p).__name__ == "OrderedDict" for p in plugins.values())

    def test_excluded_file_skipped(self, plugin_package: Any) -> None:
        pkg_dir, prefix = plugin_package
        # base.py is in the default exclude list; match it explicitly to prove
        # the exclude branch runs.
        plugins = discover_python_plugins(
            directory=pkg_dir,
            pattern="*.py",
            required_attrs=[],  # everything would match without the exclude
            module_prefix=prefix,
            exclude_files=["base.py", "__init__.py"],
        )
        # Base class lived only in the excluded file.
        assert not any(type(p).__name__ == "Base" for p in plugins.values())

    def test_empty_directory_returns_empty(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert discover_python_plugins(directory=empty, pattern="*_plugin.py") == {}

    def test_import_error_is_tolerated(self, plugin_package: Any) -> None:
        """A module that fails to import logs a warning but doesn't crash."""
        pkg_dir, prefix = plugin_package
        broken = pkg_dir / "broken_plugin.py"
        broken.write_text("import nonexistent_module_xyz", encoding="utf-8")
        plugins = discover_python_plugins(
            directory=pkg_dir,
            pattern="*_plugin.py",
            required_attrs=["execute"],
            module_prefix=prefix,
        )
        # broken_plugin failed but the good ones still loaded.
        assert "widget" in plugins
        assert "gadget" in plugins

    def test_settings_passed_to_constructor(self, tmp_path: Path) -> None:
        """When settings is supplied, plugins are built with it."""
        pkg_dir = tmp_path / "settings_pkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (pkg_dir / "cfg_plugin.py").write_text(
            "class CfgPlugin:\n"
            "    name = 'cfg'\n"
            "    def __init__(self, settings=None):\n"
            "        self.settings = settings\n"
            "    def execute(self):\n"
            "        return self.settings\n",
            encoding="utf-8",
        )
        sys.path.insert(0, str(tmp_path))
        try:
            sentinel = object()
            plugins = discover_python_plugins(
                directory=pkg_dir,
                pattern="*_plugin.py",
                required_attrs=["execute"],
                module_prefix="settings_pkg",
                settings=sentinel,  # type: ignore[arg-type]
            )
            assert plugins["cfg"].settings is sentinel
        finally:
            sys.path.remove(str(tmp_path))
            for mod in list(sys.modules):
                if mod == "settings_pkg" or mod.startswith("settings_pkg."):
                    del sys.modules[mod]

    def test_instantiation_failure_tolerated(self, tmp_path: Path) -> None:
        """A class whose constructor always raises is skipped, not fatal.

        With ``settings`` supplied, discovery first tries ``obj(settings)``;
        a no-arg constructor raises ``TypeError`` so it retries ``obj()``,
        which raises ``RuntimeError`` -> the inner instantiation-failure
        branch logs a warning and continues to the next class.
        """
        pkg_dir = tmp_path / "fail_pkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (pkg_dir / "bad_plugin.py").write_text(
            "class BadPlugin:\n"
            "    name = 'bad'\n"
            "    def __init__(self):\n"  # no-arg -> obj(settings) raises TypeError
            "        raise RuntimeError('cannot build')\n"
            "    def execute(self):\n"
            "        return 1\n"
            "class OkPlugin:\n"
            "    name = 'ok'\n"
            "    def __init__(self, settings=None):\n"
            "        self.settings = settings\n"
            "    def execute(self):\n"
            "        return 2\n",
            encoding="utf-8",
        )
        sys.path.insert(0, str(tmp_path))
        try:
            plugins = discover_python_plugins(
                directory=pkg_dir,
                pattern="*_plugin.py",
                required_attrs=["execute"],
                module_prefix="fail_pkg",
                settings=object(),  # type: ignore[arg-type]
            )
            # OkPlugin still loads despite BadPlugin's constructor blowing up.
            assert "ok" in plugins
            assert "bad" not in plugins
        finally:
            sys.path.remove(str(tmp_path))
            for mod in list(sys.modules):
                if mod == "fail_pkg" or mod.startswith("fail_pkg."):
                    del sys.modules[mod]

    def test_default_exclude_files_used_when_none(self, tmp_path: Path) -> None:
        """Passing exclude_files=None applies the built-in default exclude set."""
        pkg_dir = tmp_path / "def_excl_pkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (pkg_dir / "registry.py").write_text(
            "class RegistryThing:\n    name='r'\n    def execute(self):\n        return 1\n",
            encoding="utf-8",
        )
        sys.path.insert(0, str(tmp_path))
        try:
            # registry.py is in the DEFAULT exclude list -> nothing discovered.
            plugins = discover_python_plugins(
                directory=pkg_dir,
                pattern="*.py",
                required_attrs=["execute"],
                module_prefix="def_excl_pkg",
            )
            assert "r" not in plugins
        finally:
            sys.path.remove(str(tmp_path))
            for mod in list(sys.modules):
                if mod == "def_excl_pkg" or mod.startswith("def_excl_pkg."):
                    del sys.modules[mod]
