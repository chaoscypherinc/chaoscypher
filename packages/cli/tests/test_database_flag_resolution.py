# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Bug 10 regression: commands must respect ``db switch`` even when the
user omits ``--database``.

Background: every CLI subcommand declares ``--database`` /  ``-d`` with
``default="default"``. Click's parser substitutes that literal when the
user doesn't supply the flag. Two layers downstream then handle the
resolved value differently:

* ``get_context(database_name="default")`` calls ``get_database_name``,
  which TREATS the literal "default" as "no override" and falls through
  to env var → config (the file ``db switch`` writes) → final fallback.
  So the resolved ``ctx.database_name`` is correct.

* But code that passes the raw Click arg straight to a repository call
  (``adapter.list_files(database_name=database, ...)``) skips that
  resolution chain entirely and queries the ``default`` database — even
  when ``db current`` reports a different active workspace.

The bug surfaced when ``source list`` showed "No ingested files found"
on a freshly-populated ``cli_smoke_warpeace`` DB. The fix is mechanical
— each affected site uses ``ctx.database_name`` (the resolved value)
instead of the raw ``database`` arg.

These tests pin the contract by introspecting the source of both
affected commands. If a future refactor reintroduces the raw-arg
pattern, the assertion fires before the user hits an empty list.
"""

from __future__ import annotations

import inspect


def test_source_list_uses_resolved_database_name() -> None:
    """``chaoscypher source list`` must query ``ctx.database_name``,
    not the raw Click ``database`` arg.
    """
    from chaoscypher_cli.commands.source.list import list_files

    # Click wraps the function in a Command object; the original
    # callable lives on ``.callback``.
    callback = list_files.callback
    src_lines, _ = inspect.getsourcelines(callback)
    body = "".join(src_lines)

    # The right call: list_files(database_name=ctx.database_name, ...)
    assert "database_name=ctx.database_name" in body, (
        "source list passes the unresolved Click ``database`` arg to "
        "list_files. Use ``ctx.database_name`` (resolved via get_context) "
        "so the query honours ``db switch``."
    )
    # The wrong call: list_files(database_name=database, ...) bare token
    assert "list_files(database_name=database," not in body, (
        "source list still passes the raw ``database`` arg to list_files. Bug 10 regression."
    )


def test_package_load_uses_resolved_database_name() -> None:
    """``chaoscypher package load`` builds ImportOptions; its
    ``database_name=...`` must be the resolved ``ctx.database_name``.
    """
    from chaoscypher_cli.commands.package.load import load

    callback = load.callback
    src_lines, _ = inspect.getsourcelines(callback)
    body = "".join(src_lines)

    assert "database_name=ctx.database_name" in body, (
        "package load passes the unresolved Click ``database`` arg to "
        "ImportOptions. Use ``ctx.database_name`` (resolved via get_context) "
        "so the import lands in the active workspace."
    )


def test_get_database_name_returns_override_only_when_not_literal_default() -> None:
    """Pin the semantic that ``get_database_name`` treats the literal
    "default" string as "no override" — this is what makes
    ``ctx.database_name`` resolve correctly even when callers pass the
    Click default verbatim.
    """
    import os
    from unittest.mock import patch

    from chaoscypher_cli.context import get_database_name

    # No override and no env var → should fall through to config / fallback.
    # With env var set to "my_active" and override="default", resolution
    # MUST yield "my_active" (the override is ignored as a non-override).
    with patch.dict(os.environ, {"CHAOSCYPHER_DATABASE": "my_active"}):
        assert get_database_name("default") == "my_active"
        # Explicit non-default override wins over env.
        assert get_database_name("my_explicit") == "my_explicit"

    # No env and no override — falls back through to config / "default".
    # We don't pin the exact return here because it depends on a config
    # file the user may have; the contract under test is just the
    # "default" → "no override" semantics above.
