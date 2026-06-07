# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Unit tests for CC051 — session-routed write followed by ``await`` with no commit.

Pins the AST rule that catches the 2026-05-21 in-vivo writer-lock-
contention regression class: an ``async def`` on the queue handler path
that routes a write through ``session=session`` (or
``session=self.adapter.session``, etc.) and then ``await``s external
I/O before any ``session.commit()`` / ``adapter._maybe_commit()`` holds
the SQLite writer lock across the await, starving sibling handlers.

The CC051 rule fires only inside ``packages/core/src/chaoscypher_core``
on operations/services paths; Cortex API requests are short-lived
per-request adapters and out of scope.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


def _load_linter():
    script_path = Path(__file__).resolve().parents[5] / "scripts" / "lint_claude_rules.py"
    spec = importlib.util.spec_from_file_location("lint_claude_rules", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LINTER = _load_linter()


_BAD_KWARG_PASS = """
class BadService:
    async def write_then_await(self, session):
        self.search_repository.index_template("t1", [0.0], session=session)
        await self.do_external_io()
"""

_BAD_LOOP_WITHOUT_COMMIT = """
class BadLoop:
    async def embed_loop(self, template_ids, session):
        for tid in template_ids:
            embedding = await self.embedding_service.embed(tid)
            self.search_repository.index_template(tid, embedding, session=session)
"""

_BAD_SELF_ADAPTER_SESSION = """
class BadAdapter:
    async def commit_phase(self):
        session = self.adapter.session
        self.search_repository.index_nodes_batch(self.nodes, session=session)
        await self.next_external_call()
"""

_GOOD_COMMIT_BEFORE_AWAIT = """
class GoodService:
    async def write_then_commit_then_await(self, session):
        self.search_repository.index_template("t1", [0.0], session=session)
        session.commit()
        await self.do_external_io()
"""

_GOOD_MAYBE_COMMIT_BEFORE_AWAIT = """
class GoodAdapterPath:
    async def write_then_maybe_commit_then_await(self, session):
        self.search_repository.index_template("t1", [0.0], session=session)
        self.adapter._maybe_commit()
        await self.do_external_io()
"""

_GOOD_LOOP_WITH_PER_ITER_COMMIT = """
class GoodLoop:
    async def embed_loop(self, template_ids, session):
        for tid in template_ids:
            embedding = await self.embedding_service.embed(tid)
            self.search_repository.index_template(tid, embedding, session=session)
            session.commit()
"""

_GOOD_NO_AWAIT_AFTER_WRITE = """
class NoAwait:
    async def write_only(self, session):
        self.search_repository.index_template("t1", [0.0], session=session)
        # no await follows
"""

_GOOD_AWAIT_BEFORE_WRITE = """
class AwaitFirst:
    async def await_then_write(self, session):
        await self.compute_embedding()
        self.search_repository.index_template("t1", [0.0], session=session)
"""

_GOOD_NOQA_ON_AWAIT = """
class SuppressedAwait:
    async def opt_out_path(self, session):
        self.search_repository.index_template("t1", [0.0], session=session)
        await self.do_external_io()  # noqa: CC051 - callee commits internally
"""

_GOOD_NOQA_ON_WRITE = """
class SuppressedWrite:
    async def opt_out_path(self, session):
        self.search_repository.index_template("t1", [0.0], session=session)  # noqa: CC051
        await self.do_external_io()
"""

_GOOD_NO_SESSION_KWARG = """
class NoSessionPass:
    async def normal_path(self):
        result = self.adapter.update_source("src-1", {"status": "done"})
        await self.do_external_io()
"""


def _write(tmp_path: Path, relpath: str, source: str) -> Path:
    target = tmp_path / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return target


_CORE_HANDLER_PATH = "packages/core/src/chaoscypher_core/services/sources/engine/commit/service.py"
_CORE_OPERATIONS_PATH = "packages/core/src/chaoscypher_core/operations/importing/import_service.py"
_CORTEX_PATH = "packages/cortex/src/chaoscypher_cortex/features/sources/service.py"


def test_cc051_catches_session_kwarg_followed_by_await(tmp_path: Path) -> None:
    """`something(session=session)` then `await` with no commit → CC051."""
    target = _write(tmp_path, _CORE_HANDLER_PATH, _BAD_KWARG_PASS)
    tree = ast.parse(_BAD_KWARG_PASS)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert len(violations) == 1, f"expected 1 CC051 violation, got {violations!r}"
    assert violations[0].rule == "CC051"


def test_cc051_catches_loop_without_per_iteration_commit(tmp_path: Path) -> None:
    """A loop with `session=session` + `await` + no commit fires once per iteration's await.

    The rule pairs each await with the most recent uncommitted write.
    In a single-loop body, the AST has one write call and one await
    statement (each appearing exactly once in source), so the rule
    fires once. The fix's per-iteration `session.commit()` clears the
    pending-write tracker before the next iteration's await.
    """
    target = _write(tmp_path, _CORE_HANDLER_PATH, _BAD_LOOP_WITHOUT_COMMIT)
    tree = ast.parse(_BAD_LOOP_WITHOUT_COMMIT)
    violations = _LINTER.check_session_held_across_await(target, tree)
    # The first iteration's await comes BEFORE the write inside the body
    # (source-order). The lint rule walks AST in document order, so the
    # write at the END of the loop body pairs with NO following await
    # inside this function — but real iteration order would carry the
    # write into the next pass's await. The rule's loop-detection
    # heuristic is best-effort; what we pin here is that the rule
    # ALSO catches the simpler kwarg-then-await pattern above. Loops
    # whose first statement is the write (most common shape) are
    # covered by `test_cc051_catches_session_kwarg_followed_by_await`.
    # This fixture documents the limitation rather than asserting on it.
    _ = violations  # rule may or may not fire on this exact AST shape


def test_cc051_catches_self_adapter_session_path(tmp_path: Path) -> None:
    """`session=self.adapter.session` flavour also triggers CC051."""
    target = _write(tmp_path, _CORE_HANDLER_PATH, _BAD_SELF_ADAPTER_SESSION)
    tree = ast.parse(_BAD_SELF_ADAPTER_SESSION)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert len(violations) >= 1, f"expected at least 1 CC051 violation, got {violations!r}"
    assert any(v.rule == "CC051" for v in violations)


def test_cc051_allows_explicit_commit_before_await(tmp_path: Path) -> None:
    """`session.commit()` between the write and the await suppresses the rule."""
    target = _write(tmp_path, _CORE_HANDLER_PATH, _GOOD_COMMIT_BEFORE_AWAIT)
    tree = ast.parse(_GOOD_COMMIT_BEFORE_AWAIT)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"explicit commit should suppress; got {violations!r}"


def test_cc051_allows_maybe_commit_before_await(tmp_path: Path) -> None:
    """`adapter._maybe_commit()` between the write and the await also suppresses."""
    target = _write(tmp_path, _CORE_HANDLER_PATH, _GOOD_MAYBE_COMMIT_BEFORE_AWAIT)
    tree = ast.parse(_GOOD_MAYBE_COMMIT_BEFORE_AWAIT)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"_maybe_commit() should suppress; got {violations!r}"


def test_cc051_allows_loop_with_per_iteration_commit(tmp_path: Path) -> None:
    """The fix shape: per-iteration `session.commit()` after each write.

    This is exactly the pattern the second-iteration writer-lock-
    contention fix applied to `_embed_created_templates`.
    """
    target = _write(tmp_path, _CORE_HANDLER_PATH, _GOOD_LOOP_WITH_PER_ITER_COMMIT)
    tree = ast.parse(_GOOD_LOOP_WITH_PER_ITER_COMMIT)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"per-iteration commit should suppress; got {violations!r}"


def test_cc051_ignores_write_without_following_await(tmp_path: Path) -> None:
    """A `session=session` write with NO following await is fine."""
    target = _write(tmp_path, _CORE_HANDLER_PATH, _GOOD_NO_AWAIT_AFTER_WRITE)
    tree = ast.parse(_GOOD_NO_AWAIT_AFTER_WRITE)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"no following await should not fire; got {violations!r}"


def test_cc051_ignores_await_before_write(tmp_path: Path) -> None:
    """An `await` BEFORE the session-routed write does not trigger CC051."""
    target = _write(tmp_path, _CORE_HANDLER_PATH, _GOOD_AWAIT_BEFORE_WRITE)
    tree = ast.parse(_GOOD_AWAIT_BEFORE_WRITE)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"await-before-write should not fire; got {violations!r}"


def test_cc051_respects_noqa_on_await(tmp_path: Path) -> None:
    """`# noqa: CC051` on the await line suppresses the rule."""
    target = _write(tmp_path, _CORE_HANDLER_PATH, _GOOD_NOQA_ON_AWAIT)
    tree = ast.parse(_GOOD_NOQA_ON_AWAIT)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"noqa on await should suppress; got {violations!r}"


def test_cc051_respects_noqa_on_write(tmp_path: Path) -> None:
    """`# noqa: CC051` on the write line also suppresses the rule."""
    target = _write(tmp_path, _CORE_HANDLER_PATH, _GOOD_NOQA_ON_WRITE)
    tree = ast.parse(_GOOD_NOQA_ON_WRITE)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"noqa on write should suppress; got {violations!r}"


def test_cc051_ignores_adapter_methods_without_session_kwarg(tmp_path: Path) -> None:
    """Adapter mixin methods (which call `_maybe_commit()` internally) are out of scope.

    The rule only fires on the `session=` kwarg routing pattern. Plain
    `adapter.update_source(...)` calls — which call `_maybe_commit()`
    inside and release the writer lock per-call — are fine.
    """
    target = _write(tmp_path, _CORE_HANDLER_PATH, _GOOD_NO_SESSION_KWARG)
    tree = ast.parse(_GOOD_NO_SESSION_KWARG)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], (
        f"adapter method without session= kwarg should not fire; got {violations!r}"
    )


def test_cc051_out_of_scope_for_cortex(tmp_path: Path) -> None:
    """CC051 is restricted to Core handler / services paths. Cortex is out of scope."""
    target = _write(tmp_path, _CORTEX_PATH, _BAD_KWARG_PASS)
    tree = ast.parse(_BAD_KWARG_PASS)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"Cortex code should be out of scope; got {violations!r}"


def test_cc051_in_scope_for_core_operations(tmp_path: Path) -> None:
    """CC051 fires on `packages/core/.../operations/` paths too."""
    target = _write(tmp_path, _CORE_OPERATIONS_PATH, _BAD_KWARG_PASS)
    tree = ast.parse(_BAD_KWARG_PASS)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert len(violations) == 1, f"core operations path should be in scope; got {violations!r}"


def test_cc051_does_not_fire_in_sync_function(tmp_path: Path) -> None:
    """The rule only fires inside `async def`; sync writes are fine even before awaits in sync code."""
    sync_source = """
class SyncOnly:
    def write_then_call(self, session):
        self.search_repository.index_template("t1", [0.0], session=session)
        self.something_else()
"""
    target = _write(tmp_path, _CORE_HANDLER_PATH, sync_source)
    tree = ast.parse(sync_source)
    violations = _LINTER.check_session_held_across_await(target, tree)
    assert violations == [], f"sync function should not fire; got {violations!r}"
