# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 4 Task G: chats repository + service split.

AST-based contract tests covering the three handlers that used to
contain inline scope-resolution / source-title / session-management
logic. After the refactor:

- ``ChatScopeRepository`` owns scope resolution + source title lookup.
- ``ChatFeatureService`` owns the create / update-scope / clear-scope
  orchestration that previously lived inside the route handlers.
- The handlers delegate via a ``get_chat_feature_service`` factory.
"""

from __future__ import annotations

import ast
from pathlib import Path


CHATS_DIR = (
    Path(__file__).resolve().parents[4] / "src" / "chaoscypher_cortex" / "features" / "chats"
)
CHATS_API = CHATS_DIR / "api.py"
CHATS_SERVICE = CHATS_DIR / "service.py"
CHATS_REPOSITORY = CHATS_DIR / "repository.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _class_methods(tree: ast.Module, class_name: str) -> set[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return set()


def _module_functions(tree: ast.Module) -> set[str]:
    return {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _handler_body_dump(tree: ast.Module, handler_name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == handler_name:
            return ast.unparse(node)
    msg = f"handler {handler_name!r} not found"
    raise AssertionError(msg)


def test_chat_scope_repository_defines_expected_methods() -> None:
    """``ChatScopeRepository`` must expose ``resolve_scope`` + ``get_source_titles``."""
    methods = _class_methods(_tree(CHATS_REPOSITORY), "ChatScopeRepository")
    assert "resolve_scope" in methods
    assert "get_source_titles" in methods


def test_chat_feature_service_defines_expected_methods() -> None:
    """``ChatFeatureService`` must expose the three orchestration methods."""
    methods = _class_methods(_tree(CHATS_SERVICE), "ChatFeatureService")
    assert "create_chat_with_scope" in methods
    assert "update_scope_with_message" in methods
    assert "clear_scope_with_message" in methods


def test_factory_function_exists_and_follows_cc001() -> None:
    """``get_chat_feature_service`` must exist in api.py and match CC001 naming."""
    funcs = _module_functions(_tree(CHATS_API))
    assert "get_chat_feature_service" in funcs
    assert "get_chat_feature_service".startswith("get_")
    assert "get_chat_feature_service".endswith("_service")


def test_handlers_no_longer_reference_moved_helpers() -> None:
    """The three refactored handlers must not contain the moved helpers.

    - ``get_source_ids_by_tag_ids``: moved to ``ChatScopeRepository.resolve_scope``.
    - ``get_db_session``: moved to ``ChatScopeRepository.get_source_titles``
      (which itself uses the adapter, not a raw session).
    - ``load_only``: same migration as ``get_db_session``.
    - ``generate_id``: moved to ``ChatFeatureService.create_chat_with_scope``.
    """
    tree = _tree(CHATS_API)
    forbidden = (
        "get_source_ids_by_tag_ids",
        "get_db_session",
        "load_only",
        "generate_id",
    )
    for handler_name in ("create_chat", "update_chat_scope", "clear_chat_scope"):
        dump = _handler_body_dump(tree, handler_name)
        for token in forbidden:
            assert token not in dump, (
                f"{handler_name!r} handler still references {token!r}; "
                "move it into ChatFeatureService / ChatScopeRepository."
            )


def test_repository_has_no_top_level_session_import() -> None:
    """``chats/repository.py`` must not import ``get_db_session`` at runtime.

    Scope resolution must go through the adapter API, not through raw
    ``get_db_session`` contexts.
    """
    tree = _tree(CHATS_REPOSITORY)
    for node in tree.body:
        # Skip TYPE_CHECKING-gated imports (module-body ``if TYPE_CHECKING:`` blocks)
        if isinstance(node, ast.If):
            continue
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "shared.database.session" in module:
                names = {alias.name for alias in node.names}
                assert "get_db_session" not in names, (
                    "chats/repository.py must not import get_db_session at runtime"
                )
