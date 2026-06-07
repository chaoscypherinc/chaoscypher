#!/usr/bin/env python3
# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Custom linter for ChaosCypher CLAUDE.md rules.

Enforces architectural rules that standard linters don't catch:
- Data type boundaries (dict vs entity attribute access)
- SQLAlchemy query performance (load_only in list methods)
- Barrel pattern compliance (__all__ in __init__.py)
- Factory function naming conventions
- Session management patterns
- Direct session.commit() in repository layers (use maybe_commit() or _maybe_commit())

Usage:
    python scripts/lint_claude_rules.py [--fix] [path...]

Exit codes:
    0 = All checks passed
    1 = Violations found
"""

import argparse
import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    """A CLAUDE.md rule violation."""

    file: Path
    line: int
    rule: str
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        """Format violation for output."""
        return f"{self.file}:{self.line}: [{self.rule}] {self.message}"


class ClaudeRulesChecker(ast.NodeVisitor):
    """AST visitor that checks for CLAUDE.md rule violations."""

    def __init__(self, file_path: Path) -> None:
        """Initialize the checker."""
        self.file_path = file_path
        self.violations: list[Violation] = []
        self.current_function: str | None = None
        self.current_class: str | None = None
        # Track storage protocol calls for data type boundary checking
        self.storage_call_vars: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Track current class context."""
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check function-level rules.

        CC001 (factory naming in api.py) was migrated to semgrep in
        Stage 4 — see tools/semgrep/rules/cc-001-factory-naming.yml.
        This visitor is retained because CC002 (entity attr access on
        storage dict) needs storage_call_vars cleared per function
        scope.
        """
        old_function = self.current_function
        self.current_function = node.name
        self.storage_call_vars.clear()
        self.generic_visit(node)
        self.current_function = old_function

    visit_AsyncFunctionDef = visit_FunctionDef  # noqa: N815 - ast.NodeVisitor dispatch name

    def visit_Assign(self, node: ast.Assign) -> None:
        """Track variables assigned from storage protocol calls."""
        if isinstance(node.value, ast.Call):
            call_name = self._get_call_name(node.value)
            # Check if this is a storage protocol call
            if call_name and (
                call_name.startswith("self.storage.get_")
                or call_name.startswith("self.storage.list_")
                or call_name.startswith("self.storage.create_")
                or call_name.startswith("self.storage.update_")
            ):
                # Track the variable names that receive storage data
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self.storage_call_vars.add(target.id)

        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check for attribute access on storage-returned data (should use dict access)."""
        # Fires on attribute access on a variable that came from storage,
        # excluding safe dict methods like .get(), and skipping test files.
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in self.storage_call_vars
            and node.attr not in ("get", "keys", "values", "items", "update", "pop")
            and "tests" not in str(self.file_path)
        ):
            self.violations.append(
                Violation(
                    file=self.file_path,
                    line=node.lineno,
                    rule="CC002",
                    message=f"Possible entity attribute access on storage-returned "
                    f"dict: '{node.value.id}.{node.attr}'. Use dict access: "
                    f"{node.value.id}['{node.attr}'] or {node.value.id}.get('{node.attr}')",
                    severity="warning",
                )
            )

        self.generic_visit(node)

    def _is_repository_file(self) -> bool:
        """True if current file is a repository layer where session.commit() is forbidden.

        Covers:
        - Adapter mixins: any .py under adapters/sqlite/mixins/
        - Core domain repositories: any .py under chaoscypher_core/repos/
        - Cortex VSA feature repositories: files named repository.py under features/
        """
        parts = self.file_path.resolve().parts
        if "adapters" in parts and "sqlite" in parts and "mixins" in parts:
            return True
        if "chaoscypher_core" in parts and "repos" in parts:
            return True
        return "features" in parts and self.file_path.name == "repository.py"

    def _has_noqa(self, node: ast.AST, rule: str) -> bool:
        """Return true when the node's source line suppresses ``rule``."""
        lineno = getattr(node, "lineno", 0)
        if lineno < 1:
            return False
        try:
            line = self.file_path.read_text(encoding="utf-8").splitlines()[lineno - 1]
        except (OSError, IndexError):  # fmt: skip
            return False
        return f"noqa: {rule}" in line

    def visit_Call(self, node: ast.Call) -> None:
        """Check for forbidden self.session.commit() in repository layers (CC011)."""
        if (
            self._is_repository_file()
            and not self._has_noqa(node, "CC011")
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "session"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "self"
        ):
            self.violations.append(
                Violation(
                    file=self.file_path,
                    line=node.lineno,
                    rule="CC011",
                    message=(
                        "self.session.commit() in a repository layer — use "
                        "self.session.maybe_commit() (or self._maybe_commit() "
                        "in adapter mixins) so writes participate in enclosing "
                        "adapter.transaction() contexts"
                    ),
                )
            )
        self.generic_visit(node)

    def _get_call_name(self, node: ast.Call) -> str | None:
        """Get the full dotted name of a call."""
        parts = []
        current = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
        return None


def check_barrel_pattern(file_path: Path) -> list[Violation]:
    """Check __init__.py files for barrel pattern compliance."""
    violations = []

    if file_path.name != "__init__.py":
        return violations

    content = file_path.read_text(encoding="utf-8")

    # Skip empty or minimal __init__.py files
    lines = [line for line in content.split("\n") if line.strip() and not line.startswith("#")]
    if len(lines) < 3:
        return violations

    # Check for __all__ definition — only warn for files that have actual exports
    if "__all__" not in content and ("from " in content or "import " in content):
        violations.append(
            Violation(
                file=file_path,
                line=1,
                rule="CC004",
                message="__init__.py with exports should define __all__ for barrel pattern",
                severity="error",
            )
        )

    return violations


def _annotation_is_plain_str(node: ast.expr | None) -> bool:
    """True if the annotation is ``str`` or ``str | None`` (or ``Optional[str]``).

    False for ``SecretStr``, ``SecretStr | None``, or anything else.
    """
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id == "str"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return (_annotation_is_plain_str(node.left) and _is_none_annotation(node.right)) or (
            _annotation_is_plain_str(node.right) and _is_none_annotation(node.left)
        )
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Optional"
    ):
        return _annotation_is_plain_str(node.slice)
    return False


def _is_none_annotation(node: ast.expr) -> bool:
    """True if the annotation is ``None``."""
    return isinstance(node, ast.Constant) and node.value is None


# Field-name fragments that mark a Pydantic model field as secret-carrying.
# Matched as substrings against the field name (case-insensitive).
_SECRET_FIELD_FRAGMENTS: tuple[str, ...] = (
    "password",
    "secret",
    "api_key",
    "access_key",
    "private_key",
    "auth_token",
    "bearer_token",
    "refresh_token",
)

# Pydantic base classes whose fields are candidates for SecretStr enforcement.
_PYDANTIC_BASE_NAMES: frozenset[str] = frozenset(
    {
        "BaseModel",
        "BaseSettings",
        "Settings",
        "SQLModel",
    }
)


def check_plain_str_secret_fields(  # noqa: C901 - one branch per annotation/opt-out shape
    file_path: Path, tree: ast.AST
) -> list[Violation]:
    """CC018: secret-carrying Pydantic fields must be ``SecretStr``.

    CLAUDE.md § Secrets: new secret fields use ``pydantic.SecretStr`` so
    ``__repr__`` redacts the value and the secret is only reachable via
    ``.get_secret_value()``. Plain ``str`` fields for passwords / tokens /
    API keys leak through default logging.

    Scope:
    - Only Pydantic-style classes (bases: BaseModel, BaseSettings, Settings, SQLModel).
    - Only annotated assignments (``foo: str = Field(...)``); function
      parameters and plain variables are skipped.
    - Field names whose lowercase form contains any of the fragments in
      ``_SECRET_FIELD_FRAGMENTS``.
    - Annotation is ``str`` or ``str | None`` / ``Optional[str]``.

    A field can opt out of this rule with a trailing ``# noqa: CC018`` comment
    (with optional reason). Use sparingly — only for fields that legitimately
    cannot be SecretStr (e.g. round-trip JSON serialization to disk where the
    raw value must be preserved).
    """
    violations: list[Violation] = []

    # Skip test files — fixtures commonly hold throwaway plaintext.
    if "tests" in str(file_path).replace("\\", "/").split("/"):
        return violations

    try:
        source_lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):  # fmt: skip
        source_lines = []

    def _has_noqa(line_no: int) -> bool:
        """True if the field's line carries a trailing ``# noqa: CC018`` marker."""
        idx = line_no - 1
        if 0 <= idx < len(source_lines):
            return "# noqa: CC018" in source_lines[idx]
        return False

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Only flag classes inheriting from a Pydantic-style base.
        base_names: set[str] = set()
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_names.add(base.id)
            elif isinstance(base, ast.Attribute):
                base_names.add(base.attr)
        if not (base_names & _PYDANTIC_BASE_NAMES):
            continue

        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            if not isinstance(stmt.target, ast.Name):
                continue
            field_name = stmt.target.id
            lower = field_name.lower()
            if not any(frag in lower for frag in _SECRET_FIELD_FRAGMENTS):
                continue
            if not _annotation_is_plain_str(stmt.annotation):
                continue
            # Allow opt-out via an inline CC018 noqa comment on the annotation
            # line OR on the closing line of a multi-line Field(...) declaration.
            if _has_noqa(stmt.lineno) or _has_noqa(stmt.end_lineno or stmt.lineno):
                continue
            violations.append(
                Violation(
                    file=file_path,
                    line=stmt.lineno,
                    rule="CC018",
                    message=(
                        f"Pydantic field '{node.name}.{field_name}: str' holds a "
                        "secret - use 'pydantic.SecretStr' so the value is "
                        "redacted in logs/reprs."
                    ),
                    severity="warning",
                )
            )

    return violations


def _route_decorators(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Return FastAPI ``@router.<method>(...)`` decorators on a function."""
    routes: list[ast.Call] = []
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        if not isinstance(dec.func, ast.Attribute):
            continue
        if dec.func.attr not in {"get", "post", "put", "patch", "delete"}:
            continue
        routes.append(dec)
    return routes


def _first_path_string(dec: ast.Call) -> str | None:
    """Return the literal path from a route decorator (``router.get("/foo")``)."""
    if not dec.args:
        return None
    first = dec.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def check_session_construction_outside_standalone_repo(
    file_path: Path, tree: ast.AST
) -> list[Violation]:
    """Flag ``Session(...)`` constructed outside the standalone-repo carve-out (CC011).

    Applies to adapter/mixin code; the engine-based standalone repos are permitted.

    Permitted:
    - ``Session(self._engine)`` inside ``packages/core/src/chaoscypher_core/adapters/sqlite/repos/*.py``
      — the engine-based standalone repo pattern (GraphSnapshotRepository, SearchRepository).
    - Any line annotated with ``# noqa: CC011`` — used by the SafeSession
      fallback in ``adapters/sqlite/mixin_base.py`` (the only such line in the repo).

    Banned:
    - ``Session(anything)`` anywhere under ``packages/core/src/chaoscypher_core/adapters/sqlite/``
      that isn't covered by the two carve-outs above.

    Scope: only fires on ``.py`` files under ``adapters/sqlite/``. Test files are
    skipped. Non-SQLAlchemy/SQLModel ``Session`` classes in unrelated packages
    are out of scope because the path check rules them out.
    """
    violations: list[Violation] = []

    parts = file_path.resolve().parts
    if "adapters" not in parts or "sqlite" not in parts:
        return violations
    if "tests" in parts:
        return violations
    if file_path.suffix != ".py":
        return violations

    # File must be a direct child of adapters/sqlite/repos/ — the engine-based
    # standalone-repo carve-out. Any deeper nesting (e.g. adapters/sqlite/other/repos/)
    # is intentionally not matched.
    in_standalone_repo = (
        len(parts) >= 4
        and parts[-2] == "repos"
        and parts[-3] == "sqlite"
        and parts[-4] == "adapters"
    )

    # Read source lines once so we can check for a CC011 noqa on the call line.
    try:
        source_lines = file_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):  # fmt: skip
        source_lines = []

    def _has_noqa(lineno: int) -> bool:
        if not (1 <= lineno <= len(source_lines)):
            return False
        return "noqa: CC011" in source_lines[lineno - 1]

    def _is_self_engine(arg: ast.expr) -> bool:
        return (
            isinstance(arg, ast.Attribute)
            and arg.attr == "_engine"
            and isinstance(arg.value, ast.Name)
            and arg.value.id == "self"
        )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "Session"):
            continue
        if _has_noqa(node.lineno):
            continue
        if in_standalone_repo and len(node.args) == 1 and _is_self_engine(node.args[0]):
            continue
        violations.append(
            Violation(
                file=file_path,
                line=node.lineno,
                rule="CC011",
                message=(
                    "Session(...) construction outside the engine-based "
                    "standalone-repo carve-out. Use adapter.transaction() / "
                    "self.session, or — for denormalized caches backed by "
                    "adapters/sqlite/repos/ — Session(self._engine). "
                    "See the public contributor guide and surrounding code patterns."
                ),
            )
        )
    return violations


_CANONICAL_QUEUE_ROUTING_CACHE: dict[str, str] | None = None
_CANONICAL_CONSTANTS_CACHE: dict[str, str] | None = None


def _load_canonical_queue_routing() -> (  # noqa: C901, PLR0912 - AST walk over constants.py shapes
    tuple[dict[str, str], dict[str, str]]
):
    """Parse the canonical queue routing out of chaoscypher_core/constants.py.

    Extracts OPERATION_QUEUE_ROUTING and the ``OP_*`` / ``QUEUE_*`` string
    constants. Cached on first call.

    Returns ``(op_to_queue, name_to_string)`` where:
    - ``op_to_queue``: maps op-name strings (e.g. "extract_chunk") to queue
      strings ("llm" / "operations").
    - ``name_to_string``: maps Python names (e.g. "OP_EXTRACT_CHUNK",
      "QUEUE_LLM") to their literal string values. Used by the check to
      resolve ``Name`` nodes inside `register_handlers(...)` calls.
    """
    global _CANONICAL_QUEUE_ROUTING_CACHE, _CANONICAL_CONSTANTS_CACHE
    if _CANONICAL_QUEUE_ROUTING_CACHE is not None and _CANONICAL_CONSTANTS_CACHE is not None:
        return _CANONICAL_QUEUE_ROUTING_CACHE, _CANONICAL_CONSTANTS_CACHE

    # Locate constants.py relative to this script. The script lives at
    # scripts/lint_claude_rules.py, so the repo root is one level up.
    repo_root = Path(__file__).resolve().parent.parent
    constants_path = repo_root / "packages" / "core" / "src" / "chaoscypher_core" / "constants.py"
    if not constants_path.is_file():
        _CANONICAL_QUEUE_ROUTING_CACHE = {}
        _CANONICAL_CONSTANTS_CACHE = {}
        return {}, {}

    source = constants_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    name_to_string: dict[str, str] = {}
    # First pass: pick up every top-level ``NAME = "literal"`` assignment.
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            name_to_string[node.targets[0].id] = node.value.value

    op_to_queue: dict[str, str] = {}
    # Second pass: find OPERATION_QUEUE_ROUTING = {...} and resolve each entry.
    for node in tree.body:
        target_name = None
        value_node = None
        if isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            value_node = node.value
        if target_name != "OPERATION_QUEUE_ROUTING" or not isinstance(value_node, ast.Dict):
            continue
        for key, value in zip(value_node.keys, value_node.values, strict=False):
            # Key is either str literal (Constant) or OP_* (Name).
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                op_name = key.value
            elif isinstance(key, ast.Name) and key.id in name_to_string:
                op_name = name_to_string[key.id]
            else:
                continue
            # Value is expected to be QUEUE_LLM / QUEUE_OPERATIONS (Name).
            if isinstance(value, ast.Name) and value.id in name_to_string:
                op_to_queue[op_name] = name_to_string[value.id]
            elif isinstance(value, ast.Constant) and isinstance(value.value, str):
                op_to_queue[op_name] = value.value
        break

    _CANONICAL_QUEUE_ROUTING_CACHE = op_to_queue
    _CANONICAL_CONSTANTS_CACHE = name_to_string
    return op_to_queue, name_to_string


def check_queue_handler_registration_mismatch(  # noqa: C901, PLR0912, PLR0915 - resolves every registration shape (literal/Name/self.attr) by hand
    file_path: Path, tree: ast.AST
) -> list[Violation]:
    """Flag ``register_handlers(queue, handlers)`` calls that contradict CC044.

    The op-name → queue mapping must agree with
    ``chaoscypher_core.constants.OPERATION_QUEUE_ROUTING``.

    Resolves:
    - The queue arg (string literal or ``QUEUE_LLM`` / ``QUEUE_OPERATIONS`` Name).
    - The handlers dict (literal or ``self.attr`` bound in the same class).
    - Each handler dict key (string literal or ``OP_*`` Name).

    Out of scope:
    - Dynamically constructed handler dicts (helpers, comprehensions, update()).
    - Files under ``tests/`` — test code mocks registration.
    """
    violations: list[Violation] = []

    parts = file_path.resolve().parts
    if "tests" in parts:
        return violations
    if file_path.suffix != ".py":
        return violations
    # Production scope: anything under packages/core/src or packages/neuron/src.
    if not ("core" in parts or "neuron" in parts):
        return violations

    op_to_queue, name_to_string = _load_canonical_queue_routing()
    if not op_to_queue:
        return violations

    # Build a file-local map of Name → string constant for imported symbols.
    # This lets us resolve `QUEUE_LLM` inside the call when it's imported from
    # chaoscypher_core.constants.
    local_names: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "chaoscypher_core.constants":
            for alias in node.names:
                canonical = alias.asname or alias.name
                if alias.name in name_to_string:
                    local_names[canonical] = name_to_string[alias.name]
    # Also honor top-level `QUEUE_X = "..."` aliases in the current file.
    for node in getattr(tree, "body", []):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            local_names[node.targets[0].id] = node.value.value

    # Build a map of ClassName → {attr_name: ast.Dict} for self.attr indirection.
    class_attr_dicts: dict[str, dict[str, ast.Dict]] = {}
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        attr_map: dict[str, ast.Dict] = {}
        for stmt in ast.walk(cls):
            if not isinstance(stmt, ast.Assign):
                continue
            for target in stmt.targets:
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    continue
                if isinstance(stmt.value, ast.Dict):
                    attr_map[target.attr] = stmt.value
        class_attr_dicts[cls.name] = attr_map

    def _resolve_string(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return local_names.get(node.id)
        return None

    def _find_enclosing_class(target_node: ast.AST) -> str | None:
        # Cheap: AST doesn't expose parent pointers, so we re-walk and check
        # whether target_node is inside any class body.
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            for descendant in ast.walk(cls):
                if descendant is target_node:
                    return cls.name
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # We're looking for <anything>.register_handlers(queue, handlers).
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "register_handlers"):
            continue
        if len(node.args) < 2:
            continue
        queue_arg, handlers_arg = node.args[0], node.args[1]

        declared_queue = _resolve_string(queue_arg)
        if declared_queue is None:
            continue  # Can't resolve — skip silently; not our job to flag dynamic queues.

        # Resolve the handlers dict.
        handlers_dict: ast.Dict | None = None
        if isinstance(handlers_arg, ast.Dict):
            handlers_dict = handlers_arg
        elif (
            isinstance(handlers_arg, ast.Attribute)
            and isinstance(handlers_arg.value, ast.Name)
            and handlers_arg.value.id == "self"
        ):
            cls_name = _find_enclosing_class(node)
            if cls_name is not None:
                handlers_dict = class_attr_dicts.get(cls_name, {}).get(handlers_arg.attr)
        if handlers_dict is None:
            continue  # Unresolvable indirection — out of scope per QUEUE_ROUTING.md.

        for key_node in handlers_dict.keys:
            if key_node is None:
                continue
            op_name = _resolve_string(key_node)
            if op_name is None:
                continue
            expected = op_to_queue.get(op_name)
            if expected is None:
                violations.append(
                    Violation(
                        file=file_path,
                        line=key_node.lineno,
                        rule="CC044",
                        message=(
                            f"Operation '{op_name}' is not registered in "
                            f"chaoscypher_core.constants.OPERATION_QUEUE_ROUTING "
                            f"(packages/core/src/chaoscypher_core/constants.py). "
                            f"Add it to the canonical mapping — see "
                            f"the queue routing constants and related tests."
                        ),
                    )
                )
                continue
            if expected != declared_queue:
                violations.append(
                    Violation(
                        file=file_path,
                        line=key_node.lineno,
                        rule="CC044",
                        message=(
                            f"Operation '{op_name}' is registered on queue "
                            f"'{declared_queue}' but OPERATION_QUEUE_ROUTING "
                            f"maps it to '{expected}'. Either fix the "
                            f"register_handlers() call or update the "
                            f"canonical mapping (and QUEUE_ROUTING.md)."
                        ),
                    )
                )
    return violations


def _is_session_routed_write_call(call: ast.Call) -> bool:
    """True if ``call`` passes ``session=<session-like>`` as a keyword arg.

    Recognises:
        foo(..., session=session)
        foo(..., session=self.session)
        foo(..., session=self.adapter.session)
        foo(..., session=adapter.session)

    The convention is "I'm writing through the caller's session and the
    caller owns commit lifecycle." Inside SearchRepository this means the
    write rides the session's already-open SQLAlchemy autobegin
    transaction without committing — so the writer lock is held until
    something else commits.
    """
    for kw in call.keywords:
        if kw.arg != "session":
            continue
        value = kw.value
        if isinstance(value, ast.Name) and value.id == "session":
            return True
        # Walk attribute chain: anything ending in ".session" counts.
        if isinstance(value, ast.Attribute) and value.attr == "session":
            return True
    return False


def _is_session_commit_or_maybe_commit_call(call: ast.Call) -> bool:
    """True if ``call`` is a session-side commit that releases the writer lock.

    Recognises:
        session.commit() / session.maybe_commit()
        self.session.commit() / self.session.maybe_commit()
        self.adapter.session.commit() / .maybe_commit()
        adapter.session.commit() / .maybe_commit()
        self._maybe_commit() / adapter._maybe_commit() / self.adapter._maybe_commit()
    """
    if not isinstance(call.func, ast.Attribute):
        return False
    attr = call.func.attr
    if attr == "_maybe_commit":
        return True
    if attr in ("commit", "maybe_commit"):
        # Receiver chain must end with ``session``.
        current: ast.AST = call.func.value
        while isinstance(current, ast.Attribute):
            if current.attr == "session":
                return True
            current = current.value
        if isinstance(current, ast.Name) and current.id == "session":
            return True
    return False


def check_session_held_across_await(file_path: Path, tree: ast.AST) -> list[Violation]:
    """CC051: a session-routed write must commit before the next ``await``.

    Pattern this catches (the 2026-05-21 in-vivo regression that the
    first-iteration writer-lock-contention fix missed):

        for template_id in template_ids:
            self.search_repository.index_template(  # writes via session
                template_id, embedding, session=session,
            )
            embedding = await template_service.generate_embedding(...)
            # ^ writer lock held across the LLM HTTP call —
            # ``index_template`` only flushed; nothing committed.

    The ``session=session`` convention says "the caller owns commit
    lifecycle." When the caller is async and awaits external I/O before
    committing, SQLAlchemy's autobegin transaction (with the SQLite
    writer lock acquired by the prior write) stays open across the
    await. Sibling handlers on their own connections hit
    ``OperationalError("database is locked")`` after ``busy_timeout``.

    Fix shape: call ``session.commit()`` (or ``adapter._maybe_commit()``
    when depth-zero is guaranteed) between the session-routed write and
    the next ``await``.

    Scope: Core handlers + services that run on the neuron queue and
    therefore share writer-lock contention. Cortex API requests use
    per-request short-lived adapters with no concurrent peers and are
    out of scope (low risk; few false positives saved by skipping).

    Opt out with a trailing ``# noqa: CC051`` on either the session-write
    line OR the await line — used for cases the static checker cannot
    prove safe (e.g. the inner call commits internally).
    """
    parts = file_path.resolve().parts
    if "tests" in parts:
        return []
    if "chaoscypher_core" not in parts:
        return []
    # Restrict to handler / service / operations paths where the
    # writer-lock contention bug class is reachable from the queue.
    in_scope = (
        "operations" in parts
        or ("services" in parts and "sources" in parts)
        or ("services" in parts and "graph" in parts)
    )
    if not in_scope:
        return []

    try:
        source_lines = file_path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError):  # fmt: skip
        source_lines = []

    def has_noqa(lineno: int) -> bool:
        idx = lineno - 1
        if 0 <= idx < len(source_lines):
            return "noqa: CC051" in source_lines[idx]
        return False

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        violations.extend(_scan_async_for_held_session(file_path, node, has_noqa))
    return violations


def _scan_async_for_held_session(
    file_path: Path,
    func: ast.AsyncFunctionDef,
    has_noqa,
) -> list[Violation]:
    """Walk ``func`` body in source order; flag awaits after uncommitted writes."""
    events: list[tuple[int, str, ast.AST]] = []
    for child in ast.walk(func):
        if isinstance(child, ast.Call):
            if _is_session_routed_write_call(child):
                events.append((child.lineno, "write", child))
            elif _is_session_commit_or_maybe_commit_call(child):
                events.append((child.lineno, "commit", child))
        elif isinstance(child, ast.Await):
            events.append((child.lineno, "await", child))
    # Source order — ast.walk is not guaranteed sorted across branches; sort
    # by (lineno, col_offset) so we evaluate in document order.
    events.sort(key=lambda e: (e[0], getattr(e[2], "col_offset", 0)))

    violations: list[Violation] = []
    pending_write_line: int | None = None
    for lineno, kind, _node in events:
        if kind == "write":
            pending_write_line = lineno
        elif kind == "commit":
            pending_write_line = None
        elif kind == "await" and pending_write_line is not None:
            if has_noqa(lineno) or has_noqa(pending_write_line):
                continue
            violations.append(
                Violation(
                    file=file_path,
                    line=lineno,
                    rule="CC051",
                    message=(
                        f"await at line {lineno} follows a session-routed write at "
                        f"line {pending_write_line} with no intervening "
                        "session.commit() / adapter._maybe_commit() — the SQLite "
                        "writer lock is held across the await, starving sibling "
                        "handlers (2026-05-21 writer-lock-contention regression "
                        "class). Commit before the await, or hoist the write "
                        "after. Suppress with a trailing `# noqa: CC051` only "
                        "after verifying the callee commits internally."
                    ),
                )
            )
    return violations


def check_file(file_path: Path) -> list[Violation]:
    """Run all CLAUDE.md rule checks on a file."""
    violations = []

    # Barrel pattern check (doesn't need AST)
    violations.extend(check_barrel_pattern(file_path))

    # Skip non-Python files for AST checks
    if file_path.suffix != ".py":
        return violations

    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
    except SyntaxError, UnicodeDecodeError:
        return violations

    # AST-based checks
    checker = ClaudeRulesChecker(file_path)
    checker.visit(tree)
    violations.extend(checker.violations)

    # CC010, CC012, CC013, CC014, CC042, CC043 — module-boundary rules —
    # migrated to import-linter (Stage 1 of the 2026 tooling migration).
    # See pyproject.toml [tool.importlinter] and run via `uv run lint-imports`
    # or `make lint-claude`.

    # Secret-carrying Pydantic fields must use SecretStr
    violations.extend(check_plain_str_secret_fields(file_path, tree))

    # Session(...) construction outside the engine-based standalone repo carve-out
    violations.extend(check_session_construction_outside_standalone_repo(file_path, tree))

    # Queue-routing mismatch against the canonical OPERATION_QUEUE_ROUTING mapping
    violations.extend(check_queue_handler_registration_mismatch(file_path, tree))

    # CC051 — session-routed write followed by await without commit
    violations.extend(check_session_held_across_await(file_path, tree))

    return violations


def find_python_files(paths: list[Path]) -> Iterator[Path]:
    """Find all Python files in the given paths."""
    for path in paths:
        if path.is_file() and path.suffix == ".py":
            yield path
        elif path.is_dir():
            # Skip common excluded directories
            excluded = {".git", ".venv", "venv", "__pycache__", "node_modules", ".ruff_cache"}
            for py_file in path.rglob("*.py"):
                if not any(ex in py_file.parts for ex in excluded):
                    yield py_file


def main() -> int:
    """Run the CLAUDE.md rules linter."""
    parser = argparse.ArgumentParser(
        description="Check for CLAUDE.md rule violations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Rules:
  CC002  Data type boundary violation (entity attribute access on dict)
  CC004  Barrel pattern missing __all__
  CC011  Direct self.session.commit() in repository layers, or Session(...) construction outside adapters/sqlite/repos/ — permitted pattern is Session(self._engine) in standalone repos (GraphSnapshotRepository, SearchRepository); see CANONICAL_PATTERNS.md
  CC018  Secret-carrying Pydantic field typed as 'str' (use 'SecretStr')
  CC044  queue_client.register_handlers(QUEUE_X, {op: fn}) op-name -> queue mapping disagrees with OPERATION_QUEUE_ROUTING in chaoscypher_core.constants; see QUEUE_ROUTING.md
  CC051  session-routed write (`something(..., session=session)`) followed by `await` with no intervening `session.commit()` / `_maybe_commit()` inside an async function on the queue path — the SQLite writer lock is held across the await, starving sibling handlers (2026-05-21 writer-lock-contention regression class)

Rules now enforced by import-linter (see pyproject.toml [tool.importlinter];
run via `uv run lint-imports` or `make lint-claude`):
  CC010  Framework import in core (fastapi/uvicorn/starlette) + sqlmodel
         restricted to storage / runtime subpackages
  CC012  Runtime adapter import in core services/ layer (use a port)
  CC013  neuron / cli must not import chaoscypher_cortex
  CC014  core must not import chaoscypher_cortex / chaoscypher_neuron / chaoscypher_cli
  CC042  cortex production must not import chaoscypher_neuron / chaoscypher_cli
  CC043  neuron production must not import chaoscypher_cli

Rules now enforced by semgrep (see tools/semgrep/rules/; run via
`uv run --with semgrep --no-project semgrep --config tools/semgrep/rules/`
or `make lint-claude`):
  CC001  Factory function naming (should be get_{feature}_service())
  CC003  list_* methods without load_only()
  CC005  Manual Session(...) creation outside session-factory files
  CC006  Hyphens in API paths
  CC007  session.exec() in Cortex feature service.py
  CC008  defer() in repository (use load_only())
  CC009  sqlmodel import in Cortex feature service.py
  CC015  Bare '# type: ignore' without a rule code
  CC019  Inline uuid.uuid4() (use generate_id())
  CC022  f-string in logger event name (use literal + structured kwargs)
  CC023  logger.bind() (use bind_contextvars())
  CC026  session.{execute,scalar,scalars}() in Cortex feature service
  CC027  Action verb in route path
  CC028  Resource ID in query string
  CC029  ?skip= / ?offset= pagination
  CC031  HTTPException raised in Core or service layer
  CC033  Sync route handler in Cortex feature api.py
  CC036  get_settings from internal chaoscypher_core.settings outside Core
  CC038  CheckConstraint in models (new columns use StrEnum + String)
  CC040  ':memory:' SQLite URL in tests
  CC041  asyncio.run() in tests
  CC045  Bare stdlib raise in core/services or core/operations
        """,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        default=[Path("packages")],
        help="Paths to check (default: packages/)",
    )
    parser.add_argument(
        "--severity",
        choices=["error", "warning", "all"],
        default="all",
        help="Only show violations of this severity",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only output violation count",
    )

    args = parser.parse_args()

    all_violations: list[Violation] = []

    for file_path in find_python_files(args.paths):
        violations = check_file(file_path)
        if args.severity != "all":
            violations = [v for v in violations if v.severity == args.severity]
        all_violations.extend(violations)

    # Sort by file and line
    all_violations.sort(key=lambda v: (str(v.file), v.line))

    if not args.quiet:
        for violation in all_violations:
            print(violation)

    error_count = sum(1 for v in all_violations if v.severity == "error")
    warning_count = sum(1 for v in all_violations if v.severity == "warning")

    if all_violations:
        print(f"\nFound {error_count} error(s), {warning_count} warning(s)")
        return 1 if error_count > 0 else 0

    print("All CLAUDE.md rules passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
