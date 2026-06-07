# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 7 contract test: LLM provider plugin system.

Asserts the structural guarantees of the Phase 7 remediation:

* ``ProviderRegistry`` extends ``BaseRegistry[BaseLLMProvider]``.
* The bespoke ``_PROVIDER_MODULES`` dict is gone.
* ``BaseLLMProvider.metadata`` is an abstract property.
* Each built-in provider defines a ``_METADATA`` ``ClassVar``.
* The dataclass ``PluginMetadata`` at ``ports/plugin_metadata.py`` is deleted.
* The four built-in providers appear under the ``chaoscypher.providers``
  entry-point group declared in ``packages/core/pyproject.toml``.
* ``plugins/base.PluginMetadata`` defines ``priority``, ``applies_to``,
  and ``origin`` fields.

Pure-AST / file-inspection — no runtime imports that would trip the
sibling-worktree editable-install leak documented in Phase 6's
retrospective.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[6]
CORE_SRC = PROJECT_ROOT / "packages" / "core" / "src" / "chaoscypher_core"
PROVIDERS_INIT = CORE_SRC / "adapters" / "llm" / "providers" / "__init__.py"
PROVIDERS_BASE = CORE_SRC / "adapters" / "llm" / "providers" / "base.py"
PLUGIN_METADATA_LEGACY = CORE_SRC / "ports" / "plugin_metadata.py"
PLUGIN_METADATA_CANONICAL = CORE_SRC / "plugins" / "base.py"
CORE_PYPROJECT = PROJECT_ROOT / "packages" / "core" / "pyproject.toml"


def _load_module_ast(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_provider_registry_subclasses_baseregistry() -> None:
    """``ProviderRegistry`` extends ``BaseRegistry[...]`` in providers/__init__.py."""
    tree = _load_module_ast(PROVIDERS_INIT)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ProviderRegistry":
            base_text = {ast.unparse(b) for b in node.bases}
            assert any("BaseRegistry" in b for b in base_text), (
                f"ProviderRegistry must extend BaseRegistry; bases were {base_text}"
            )
            return
    msg = "ProviderRegistry class not declared in providers/__init__.py"
    raise AssertionError(msg)


def test_provider_modules_dict_is_gone() -> None:
    """Phase 7 deletes ``_PROVIDER_MODULES`` — the seed list lives on the registry now."""
    text = PROVIDERS_INIT.read_text(encoding="utf-8")
    assert "_PROVIDER_MODULES" not in text, (
        "_PROVIDER_MODULES dict still present — Phase 7 Task C must remove it. "
        "Seed the built-in providers via ProviderRegistry._BUILTIN_PROVIDERS instead."
    )


def test_baseLLMProvider_metadata_is_abstractmethod() -> None:
    """``BaseLLMProvider.metadata`` is an ``@property`` + ``@abstractmethod``."""
    tree = _load_module_ast(PROVIDERS_BASE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "BaseLLMProvider":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "metadata":
                    decorators = {ast.unparse(d) for d in sub.decorator_list}
                    assert "property" in decorators, (
                        f"BaseLLMProvider.metadata must be @property; decorators: {decorators}"
                    )
                    assert "abstractmethod" in decorators, (
                        f"BaseLLMProvider.metadata must be @abstractmethod; decorators: {decorators}"
                    )
                    return
    msg = "BaseLLMProvider.metadata method not found"
    raise AssertionError(msg)


def test_each_builtin_provider_declares_METADATA_classvar() -> None:
    """Ollama / OpenAI / Anthropic / Gemini each declare a ``_METADATA`` ClassVar."""
    providers = {
        "ollama": CORE_SRC / "adapters" / "llm" / "providers" / "ollama_provider.py",
        "openai": CORE_SRC / "adapters" / "llm" / "providers" / "openai_provider.py",
        "anthropic": CORE_SRC / "adapters" / "llm" / "providers" / "anthropic_provider.py",
        "gemini": CORE_SRC / "adapters" / "llm" / "providers" / "gemini_provider.py",
    }
    for name, path in providers.items():
        tree = _load_module_ast(path)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Provider"):
                for sub in node.body:
                    if (
                        isinstance(sub, ast.AnnAssign)
                        and isinstance(sub.target, ast.Name)
                        and sub.target.id == "_METADATA"
                    ):
                        found = True
                        break
        assert found, f"{name} provider must declare _METADATA: ClassVar[PluginMetadata]"


def test_legacy_plugin_metadata_dataclass_deleted() -> None:
    """The dataclass at ``ports/plugin_metadata.py`` is gone."""
    assert not PLUGIN_METADATA_LEGACY.exists(), (
        f"legacy {PLUGIN_METADATA_LEGACY} still present — Phase 7 Task A must remove it."
    )


def test_plugin_metadata_has_priority_applies_to_origin_fields() -> None:
    """Pydantic ``PluginMetadata`` carries priority / applies_to / origin."""
    tree = _load_module_ast(PLUGIN_METADATA_CANONICAL)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PluginMetadata":
            fields: set[str] = set()
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                    fields.add(sub.target.id)
            required = {"priority", "applies_to", "origin"}
            missing = required - fields
            assert not missing, (
                f"PluginMetadata missing Phase 7 fields: {missing}. Declared fields: {fields}"
            )
            return
    msg = "PluginMetadata class not declared in plugins/base.py"
    raise AssertionError(msg)


def test_providers_entry_point_group_declared_with_four_builtins() -> None:
    """``packages/core/pyproject.toml`` declares the four built-ins under the group."""
    data = tomllib.loads(CORE_PYPROJECT.read_text(encoding="utf-8"))
    ep_table = data.get("project", {}).get("entry-points", {})
    group = ep_table.get("chaoscypher.providers")
    assert group is not None, (
        'pyproject.toml must declare [project.entry-points."chaoscypher.providers"]'
    )
    for name in ("ollama", "openai", "anthropic", "gemini"):
        assert name in group, (
            f'chaoscypher.providers entry-point group missing "{name}"; got {sorted(group)}'
        )
        target = group[name]
        assert ":" in target, (
            f"entry-point target for {name!r} must be 'module:attr'; got {target!r}"
        )


def test_base_registry_declares_plugin_origins_map() -> None:
    """``BaseRegistry`` stores per-plugin origin (Phase 7 Task F)."""
    registry_path = CORE_SRC / "plugins" / "registry.py"
    text = registry_path.read_text(encoding="utf-8")
    assert "_plugin_origins" in text, (
        "_plugin_origins parallel provenance map missing from BaseRegistry"
    )
    assert "PluginOrigin" in text, "PluginOrigin alias missing from plugins/registry.py"


def test_cleaner_registry_uses_importlib_import_module_for_builtins() -> None:
    """Cleaner registry's built-in discovery calls importlib.import_module (Task G)."""
    registry_path = CORE_SRC / "services" / "sources" / "normalizer" / "cleaners" / "registry.py"
    text = registry_path.read_text(encoding="utf-8")
    assert "_BUILTIN_MODULES" in text, (
        "_BUILTIN_MODULES class-level tuple missing from CleanerRegistry"
    )
    assert "importlib.import_module" in text, (
        "CleanerRegistry must load built-ins via importlib.import_module"
    )


def test_archive_handler_registry_uses_importlib_import_module_for_builtins() -> None:
    """Archive-handler registry's built-in discovery calls importlib.import_module (Task G)."""
    registry_path = (
        CORE_SRC / "services" / "sources" / "loaders" / "archive" / "handlers" / "registry.py"
    )
    text = registry_path.read_text(encoding="utf-8")
    assert "_BUILTIN_MODULES" in text, (
        "_BUILTIN_MODULES class-level tuple missing from ArchiveHandlerRegistry"
    )
    assert "importlib.import_module" in text, (
        "ArchiveHandlerRegistry must load built-ins via importlib.import_module"
    )
