# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 3 Task H: ``source_files.py`` mixin returns dicts, not SQLModel entities.

Prior to Phase 3 the mixin exposed ``get_source_entity`` and
``list_file_entities`` — both returned SQLModel objects that service
code then mapped to dicts via
``chaoscypher_core/services/sources/mappers/file.py``. The adapter
boundary is supposed to emit DTOs, so the mapping logic moved into the
mixin as module-level helpers and the public surface now has
``get_source_detail`` / ``list_source_summaries`` returning dicts
directly. The services-side mappers module has been deleted.
"""

from __future__ import annotations

import ast
from pathlib import Path


PHASE1_PACKAGES = Path(__file__).resolve().parents[5].parent
MIXIN_FILE = (
    PHASE1_PACKAGES
    / "core"
    / "src"
    / "chaoscypher_core"
    / "adapters"
    / "sqlite"
    / "mixins"
    / "source_files.py"
)
DELETED_MAPPER_DIR = (
    PHASE1_PACKAGES / "core" / "src" / "chaoscypher_core" / "services" / "sources" / "mappers"
)


def _class_method_return_annotations() -> dict[str, str | None]:
    """Return `method_name -> annotation source` for public methods on SourceLifecycleMixin."""
    tree = ast.parse(MIXIN_FILE.read_text(encoding="utf-8"))
    annotations: dict[str, str | None] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SourceLifecycleMixin":
            for body_node in node.body:
                if isinstance(body_node, ast.FunctionDef) and not body_node.name.startswith("_"):
                    returns = body_node.returns
                    annotations[body_node.name] = ast.unparse(returns) if returns else None
    return annotations


def test_public_methods_return_dicts_or_bools() -> None:
    """No public method on SourceLifecycleMixin returns SourceRow / list[SourceRow]."""
    annotations = _class_method_return_annotations()
    assert annotations, "expected to find public methods on SourceLifecycleMixin"
    bad: list[str] = []
    for name, ann in annotations.items():
        if ann is None:
            continue
        # Allow dict[...], list[dict[...]], list[dict[..]] | None, bool, None, str, etc.
        # Flag anything that names SourceRow directly.
        if "SourceRow" in ann:
            bad.append(f"{name} -> {ann}")
    assert not bad, "Public methods still return SourceRow:\n  " + "\n  ".join(bad)


def test_mixin_defines_new_public_methods() -> None:
    annotations = _class_method_return_annotations()
    assert "get_source_detail" in annotations
    assert "list_source_summaries" in annotations


def test_mixin_no_longer_defines_entity_methods() -> None:
    """The old SQLModel-returning public methods are renamed to private loaders."""
    annotations = _class_method_return_annotations()
    assert "get_source_entity" not in annotations, (
        "get_source_entity should be renamed to the private _load_source_entity"
    )
    assert "list_file_entities" not in annotations, (
        "list_file_entities should be renamed/replaced by list_source_summaries"
    )


def test_services_mappers_module_is_gone() -> None:
    """The services-side mappers package was deleted; adapter owns the projection."""
    assert not DELETED_MAPPER_DIR.exists(), (
        f"{DELETED_MAPPER_DIR} should have been deleted in Phase 3 Task H"
    )

    import importlib

    try:
        importlib.import_module("chaoscypher_core.services.sources.mappers")
    except ModuleNotFoundError:
        pass
    else:  # pragma: no cover
        raise AssertionError("chaoscypher_core.services.sources.mappers is still importable")


def test_no_stale_imports_of_deleted_mapper() -> None:
    """AST scan: nothing under packages/*/src or packages/*/tests imports the deleted module."""
    repo_root = Path(__file__).resolve().parents[5].parent.parent
    targets = [
        repo_root / "packages" / "core" / "src",
        repo_root / "packages" / "core" / "tests",
        repo_root / "packages" / "cortex" / "src",
        repo_root / "packages" / "cortex" / "tests",
        repo_root / "packages" / "cli" / "src",
        repo_root / "packages" / "neuron" / "src",
    ]
    bad: list[str] = []
    for root in targets:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # fmt: skip
                continue
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module is not None
                    and node.module.startswith("chaoscypher_core.services.sources.mappers")
                ):
                    bad.append(f"{path}:{node.lineno}")
    assert not bad, "Stale imports of deleted mappers module:\n" + "\n".join(bad)
