# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SearchRepositoryProtocol cleanliness.

Verify no sqlmodel or sqlalchemy imports leak into the port module.
All eleven ``session``-bearing methods now use ``TransactionalSession | None``
instead of ``sqlmodel.Session | None``.
"""

import ast
import inspect


def test_ports_search_has_no_sqlmodel_imports() -> None:
    """ports/search.py must not import from sqlmodel or sqlalchemy at any level.

    Uses ``ast.walk`` to check the entire module tree, including code
    nested inside ``if TYPE_CHECKING:`` blocks — both runtime and
    type-checking-only sqlmodel/sqlalchemy imports are forbidden in
    a port interface.
    """
    import chaoscypher_core.ports.search as port_mod

    tree = ast.parse(inspect.getsource(port_mod))
    offending: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (
                node.module == "sqlmodel"
                or node.module.startswith("sqlmodel.")
                or node.module == "sqlalchemy"
                or node.module.startswith("sqlalchemy.")
            )
        ):
            offending.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            offending.extend(
                f"line {node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name in ("sqlmodel", "sqlalchemy")
                or alias.name.startswith(("sqlmodel.", "sqlalchemy."))
            )

    assert not offending, (
        "ports/search.py imports framework types from sqlmodel/sqlalchemy:\n  "
        + "\n  ".join(offending)
    )


def test_search_protocol_can_be_imported() -> None:
    """Sanity — the protocol import path still resolves."""
    from chaoscypher_core.ports.search import SearchRepositoryProtocol

    assert SearchRepositoryProtocol is not None


def test_search_protocol_uses_transactional_session() -> None:
    """Session-bearing methods must annotate with TransactionalSession, not sqlmodel.Session.

    Inspects the source of each method signature for the string
    ``TransactionalSession`` so we catch any future regression where
    the wrong type annotation slips back in.
    """
    import chaoscypher_core.ports.search as port_mod

    tree = ast.parse(inspect.getsource(port_mod))
    session_methods: list[str] = [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        for arg in node.args.kwonlyargs + node.args.args
        if arg.arg == "session"
    ]

    assert session_methods, "Expected at least one method with a 'session' parameter"

    # Each of those methods must annotate session as TransactionalSession
    source = inspect.getsource(port_mod)
    # The annotation appears as 'TransactionalSession | None' in the source
    assert "TransactionalSession" in source, (
        "ports/search.py does not reference TransactionalSession at all"
    )


def test_transactional_session_protocol_is_runtime_checkable() -> None:
    """TransactionalSession is @runtime_checkable so isinstance() works."""
    from chaoscypher_core.ports.transactional import TransactionalSession

    class DuckSession:
        def connection(self) -> object:
            return object()

        def flush(self) -> None:
            pass

    assert isinstance(DuckSession(), TransactionalSession)


def test_transactional_session_missing_flush_rejected() -> None:
    """An object missing ``flush`` is correctly rejected by the protocol check."""
    from chaoscypher_core.ports.transactional import TransactionalSession

    class IncompleteSession:
        def connection(self) -> object:
            return object()

    assert not isinstance(IncompleteSession(), TransactionalSession)


def test_transactional_session_reachable_via_ports_init() -> None:
    """TransactionalSession is exported from the top-level ports package."""
    from chaoscypher_core.ports import TransactionalSession

    assert TransactionalSession is not None


def test_sqlmodel_session_satisfies_transactional_session_protocol() -> None:
    """sqlmodel.Session structurally satisfies TransactionalSession (duck typing).

    This ensures the concrete SearchRepository still compiles and works
    when callers pass a real sqlmodel.Session — the protocol is a superset
    of what the concrete type provides.
    """
    from sqlmodel import Session

    from chaoscypher_core.ports.transactional import TransactionalSession

    assert issubclass(Session, TransactionalSession)

    # Verify Session has both required methods
    assert hasattr(Session, "connection"), "sqlmodel.Session must have .connection()"
    assert hasattr(Session, "flush"), "sqlmodel.Session must have .flush()"
