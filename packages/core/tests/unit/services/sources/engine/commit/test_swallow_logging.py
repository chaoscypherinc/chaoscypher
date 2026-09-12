# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Stopgap smoke test: every swallow-style 'except Exception:' block in the
commit pipeline emits a unique structured event.

Audit fix #M1 (stopgap). A deeper per-block audit (which swallows are correct
vs which should propagate) is tracked as a follow-up plan.

Approach chosen: a static scan rather than per-block behaviour tests. Wiring
up isolated mocks for the swallow sites across the package would require a
200+ line fixture setup that duplicates the real integration tests. Instead
this module:

  1. Discovers **every** module in the commit package (see ``_FILES``).
  2. Parses each with ``ast`` and finds every ``except Exception`` handler.
  3. Excludes handlers that terminate with a bare ``raise`` (re-raisers, not
     swallowers).
  4. For every remaining (swallow) handler: asserts that the handler's **own**
     body contains a ``logger.exception(...)`` call whose first argument is a
     unique, descriptive literal string (not an f-string — CC022).

This pins the M1 contract without duplicating integration logic.

**Rewritten 2026-09-10** to close two false-pass modes in the previous
text-scanning version:

(a) ``_FILES`` was a hand-typed list of four modules and the docstring froze
    that count into prose as "all four commit files". The package also ships
    ``matcher.py``, which the sweep silently skipped — benign only because
    that module happens to contain no ``except`` clause today, so a swallow
    added there would have gone unchecked. The list is now derived from the
    directory.

(b) The check was a regex ``search`` over the handler plus up to 15 following
    lines at deeper indentation — which swept in any *nested* ``try/except``.
    An outer handler that logged nothing itself passed on an inner handler's
    ``logger.exception``. Attribution is now exact: a nested handler's body
    belongs to the nested handler, and each handler is judged on its own
    statements.
"""

from __future__ import annotations

import ast
from pathlib import Path


_COMMIT_DIR = (
    Path(__file__).parents[6]
    / "src"
    / "chaoscypher_core"
    / "services"
    / "sources"
    / "engine"
    / "commit"
)

# Every module in the commit package, discovered rather than enumerated, so a
# new module is covered the day it lands. ``__init__.py`` is re-exports only.
_FILES = sorted(p for p in _COMMIT_DIR.glob("*.py") if p.name != "__init__.py")


def _is_except_exception(handler: ast.ExceptHandler) -> bool:
    """True for ``except Exception`` / ``except Exception as e`` handlers."""
    node = handler.type
    if node is None:  # bare ``except:``
        return False
    names = node.elts if isinstance(node, ast.Tuple) else [node]
    return any(isinstance(n, ast.Name) and n.id == "Exception" for n in names)


def _ends_with_bare_raise(handler: ast.ExceptHandler) -> bool:
    """True when the handler's last statement is a bare ``raise`` (re-raiser)."""
    last = handler.body[-1] if handler.body else None
    return isinstance(last, ast.Raise) and last.exc is None


def _own_statements(handler: ast.ExceptHandler) -> list[ast.AST]:
    """Every node in the handler body EXCEPT those inside a nested handler.

    This is the attribution rule: an inner ``except``'s logging satisfies the
    inner handler, never the outer one.
    """
    owned: list[ast.AST] = []
    for stmt in handler.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.ExceptHandler):
                continue
            owned.append(node)
    # Drop everything reachable only through a nested handler.
    nested: set[int] = set()
    for stmt in handler.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.ExceptHandler):
                for inner in node.body:
                    nested.update(id(n) for n in ast.walk(inner))
    return [n for n in owned if id(n) not in nested]


def _logger_exception_literals(nodes: list[ast.AST]) -> list[str]:
    """Literal first arguments of ``logger.exception(...)`` calls in *nodes*."""
    found: list[str] = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "exception"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "logger"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            value = node.args[0].value
            if isinstance(value, str):
                found.append(value)
    return found


def _swallow_handlers(source: str) -> list[ast.ExceptHandler]:
    """Every ``except Exception`` handler that swallows rather than re-raises."""
    return [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ExceptHandler)
        and _is_except_exception(node)
        and not _ends_with_bare_raise(node)
    ]


def test_commit_package_scan_covers_every_module():
    """The sweep is derived from the directory, not a hand-typed snapshot.

    Pins the fix for (a) above: ``matcher.py`` was omitted from the old
    literal list, so any swallow added to it was unchecked.
    """
    discovered = {p.name for p in _FILES}
    assert discovered, f"No commit modules discovered under {_COMMIT_DIR}"
    assert "matcher.py" in discovered, (
        "matcher.py missing from the sweep — the module list has regressed to a hand-typed snapshot"
    )
    on_disk = {p.name for p in _COMMIT_DIR.glob("*.py")} - {"__init__.py"}
    assert discovered == on_disk


def test_every_swallow_has_logger_exception_event():
    """Every swallow ``except Exception:`` in the commit package logs, itself.

    "Itself" is load-bearing: a nested handler's ``logger.exception`` does not
    satisfy its parent.
    """
    failures: list[str] = []

    for filepath in _FILES:
        assert filepath.exists(), f"Commit file missing: {filepath}"
        source = filepath.read_text(encoding="utf-8")

        for handler in _swallow_handlers(source):
            if not _logger_exception_literals(_own_statements(handler)):
                failures.append(
                    f"{filepath.name}:{handler.lineno} — swallow 'except Exception:' "
                    f"has no logger.exception(<literal>) of its own"
                )

    assert not failures, "\n".join(failures)


def test_no_duplicate_event_names_across_commit_files():
    """Every logger.exception event name in the commit package is unique.

    Duplicate names defeat the per-swallow monitoring contract: operators
    cannot distinguish which block fired if two swallows share a name.
    """
    event_names: list[str] = []

    for filepath in _FILES:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
        event_names.extend(_logger_exception_literals(list(ast.walk(tree))))

    duplicates = {name for name in event_names if event_names.count(name) > 1}
    assert not duplicates, f"Duplicate logger.exception event names: {sorted(duplicates)}"
