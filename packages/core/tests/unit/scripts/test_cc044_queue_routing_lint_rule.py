# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for CC044 — queue-routing mismatch between register_handlers()
calls and the canonical OPERATION_QUEUE_ROUTING mapping.

Part of Workstream D / Decision 6 of the 2026-04-23 architecture audit.
Pins: (a) correct mapping passes, (b) wrong queue caught, (c) unregistered
op caught, (d) self.attr indirection is resolved, (e) OP_* constant keys
are resolved, (f) test files are out of scope.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


def _load_linter():
    """Import scripts/lint_claude_rules.py as a module."""
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "lint_claude_rules.py"
    spec = importlib.util.spec_from_file_location("lint_claude_rules", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LINTER = _load_linter()

_CORRECT_LITERAL = """
from chaoscypher_core.constants import QUEUE_LLM, QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client


def register():
    queue_client.register_handlers(QUEUE_LLM, {"chat_completion": _fn})
    queue_client.register_handlers(QUEUE_OPERATIONS, {"bulk_nodes": _fn})
"""

_WRONG_QUEUE_LITERAL = """
from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client


def register():
    # chat_completion is an LLM op; routing it to QUEUE_OPERATIONS is a violation.
    queue_client.register_handlers(QUEUE_OPERATIONS, {"chat_completion": _fn})
"""

_UNREGISTERED_OP_LITERAL = """
from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client


def register():
    queue_client.register_handlers(QUEUE_OPERATIONS, {"not_in_the_dict": _fn})
"""

_SELF_ATTR_CORRECT = """
from chaoscypher_core.constants import QUEUE_LLM
from chaoscypher_core.queue import queue_client


class ChatService:
    def __init__(self):
        self.operation_handlers = {
            "chat_completion": self._handler,
            "tool_execution": self._handler,
        }

    def register_handlers(self):
        queue_client.register_handlers(QUEUE_LLM, self.operation_handlers)
"""

_SELF_ATTR_WRONG = """
from chaoscypher_core.constants import QUEUE_OPERATIONS
from chaoscypher_core.queue import queue_client


class ChatService:
    def __init__(self):
        self.operation_handlers = {
            "chat_completion": self._handler,
        }

    def register_handlers(self):
        # chat_completion should be on QUEUE_LLM, not QUEUE_OPERATIONS.
        queue_client.register_handlers(QUEUE_OPERATIONS, self.operation_handlers)
"""

_OP_CONSTANT_KEY = """
from chaoscypher_core.constants import OP_EXTRACT_CHUNK, QUEUE_LLM
from chaoscypher_core.queue import queue_client


def register():
    queue_client.register_handlers(QUEUE_LLM, {OP_EXTRACT_CHUNK: _fn})
"""


def _write(tmp_path: Path, relpath: str, source: str) -> Path:
    target = tmp_path / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return target


def test_cc044_passes_when_literal_dict_matches_canonical(tmp_path: Path) -> None:
    """register_handlers(QUEUE_X, {correct_op: fn}) is silent when mapping matches."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/llm_queue/queue_service.py",
        _CORRECT_LITERAL,
    )
    tree = ast.parse(_CORRECT_LITERAL)
    violations = _LINTER.check_queue_handler_registration_mismatch(target, tree)
    assert violations == [], f"Correct mapping should not trigger CC044; got {violations}"


def test_cc044_catches_wrong_queue_for_op(tmp_path: Path) -> None:
    """chat_completion on QUEUE_OPERATIONS is a mismatch — CC044 fires."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/llm_queue/queue_service.py",
        _WRONG_QUEUE_LITERAL,
    )
    tree = ast.parse(_WRONG_QUEUE_LITERAL)
    violations = _LINTER.check_queue_handler_registration_mismatch(target, tree)
    assert len(violations) == 1, (
        f"Wrong queue for registered op should trigger 1 CC044 violation; got {violations}"
    )
    assert violations[0].rule == "CC044"
    assert "chat_completion" in violations[0].message
    # Error message should name both the wrong and correct queues.
    assert "operations" in violations[0].message
    assert "llm" in violations[0].message


def test_cc044_catches_op_missing_from_canonical_mapping(tmp_path: Path) -> None:
    """An op-name not in OPERATION_QUEUE_ROUTING is an error — every op must be registered."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/operations/custom.py",
        _UNREGISTERED_OP_LITERAL,
    )
    tree = ast.parse(_UNREGISTERED_OP_LITERAL)
    violations = _LINTER.check_queue_handler_registration_mismatch(target, tree)
    assert len(violations) == 1, (
        f"Unregistered op should trigger 1 CC044 violation; got {violations}"
    )
    assert violations[0].rule == "CC044"
    assert "not_in_the_dict" in violations[0].message


def test_cc044_resolves_self_attr_indirection_correct(tmp_path: Path) -> None:
    """register_handlers(QUEUE_LLM, self.operation_handlers) — correct mapping passes."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/llm_queue/queue_service.py",
        _SELF_ATTR_CORRECT,
    )
    tree = ast.parse(_SELF_ATTR_CORRECT)
    violations = _LINTER.check_queue_handler_registration_mismatch(target, tree)
    assert violations == [], (
        f"self.operation_handlers = {{correct ops}} should pass; got {violations}"
    )


def test_cc044_resolves_self_attr_indirection_wrong(tmp_path: Path) -> None:
    """register_handlers(QUEUE_OPERATIONS, self.operation_handlers) — wrong queue for LLM op is caught."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/llm_queue/queue_service.py",
        _SELF_ATTR_WRONG,
    )
    tree = ast.parse(_SELF_ATTR_WRONG)
    violations = _LINTER.check_queue_handler_registration_mismatch(target, tree)
    assert len(violations) == 1, (
        f"self.operation_handlers wrong-queue should trigger 1 violation; got {violations}"
    )
    assert violations[0].rule == "CC044"
    assert "chat_completion" in violations[0].message


def test_cc044_resolves_op_constant_key(tmp_path: Path) -> None:
    """Keys given as OP_* constant Names (not string literals) resolve to their values."""
    target = _write(
        tmp_path,
        "packages/core/src/chaoscypher_core/operations/extraction/chunk_extraction_service.py",
        _OP_CONSTANT_KEY,
    )
    tree = ast.parse(_OP_CONSTANT_KEY)
    violations = _LINTER.check_queue_handler_registration_mismatch(target, tree)
    assert violations == [], (
        f"OP_EXTRACT_CHUNK on QUEUE_LLM is the canonical mapping; got {violations}"
    )


def test_cc044_ignores_test_files(tmp_path: Path) -> None:
    """CC044 is production-only; test files (which mock register_handlers) are skipped."""
    target = _write(
        tmp_path,
        "packages/core/tests/unit/some_test.py",
        _WRONG_QUEUE_LITERAL,
    )
    tree = ast.parse(_WRONG_QUEUE_LITERAL)
    violations = _LINTER.check_queue_handler_registration_mismatch(target, tree)
    assert violations == [], f"Test files must be out of scope for CC044; got {violations}"
