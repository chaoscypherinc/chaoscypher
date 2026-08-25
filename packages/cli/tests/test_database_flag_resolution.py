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

``source list`` is pinned behaviourally: the command is invoked with a
mocked context whose resolved ``database_name`` differs from the Click
default, and the adapter calls are inspected.

2026-08-12: the ``source list`` test used to grep
``inspect.getsourcelines(list_files.callback)`` for
``"database_name=ctx.database_name"``. That token occurs three times in
the callback (the load-bearing ``list_files`` call plus two
``count_sources*`` calls), so regressing the load-bearing one back to the
raw ``database`` arg left the assertion green. Its paired negative
(``"list_files(database_name=database," not in body``) was dead by
construction — the real source breaks the line after ``list_files(``, so
that substring is absent whether or not the bug is present.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock, patch

from click.testing import CliRunner


# The name ``db switch`` made active. Deliberately not "default", so a
# command that queries the raw Click arg lands on the wrong database.
_ACTIVE_DB = "cli_smoke_warpeace"


def _ctx(files: list[dict[str, Any]] | None = None, total: int = 0) -> MagicMock:
    """A CLI context whose resolved database differs from the Click default."""
    ctx = MagicMock()
    ctx.database_name = _ACTIVE_DB
    ctx.storage_adapter.list_files.return_value = files or []
    ctx.storage_adapter.count_sources.return_value = total
    ctx.storage_adapter.count_sources_by_statuses.return_value = total
    return ctx


def _database_names(adapter: MagicMock) -> set[str]:
    """Every ``database_name`` kwarg the command sent to the adapter."""
    names: set[str] = set()
    for method in ("list_files", "count_sources", "count_sources_by_statuses"):
        for call in getattr(adapter, method).call_args_list:
            if "database_name" in call.kwargs:
                names.add(call.kwargs["database_name"])
    return names


def test_source_list_queries_the_resolved_database_not_the_raw_flag() -> None:
    """``chaoscypher source list`` must query ``ctx.database_name``.

    With ``--database`` omitted, Click substitutes the literal "default".
    ``get_context`` treats that as "no override" and resolves the active
    workspace, so the adapter query must use the RESOLVED name — passing
    the raw arg is Bug 10 and shows "No ingested files found" on a
    populated database.
    """
    from chaoscypher_cli.commands.source.list import list_files

    runner = CliRunner()
    ctx = _ctx(files=[{"id": "s1", "filename": "f.txt", "status": "indexed", "file_size": 1}])

    with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx) as get_context:
        result = runner.invoke(list_files, [])

    assert result.exit_code == 0, result.output
    # get_context receives the raw Click default and does the resolving...
    assert get_context.call_args.kwargs["database_name"] == "default"
    # ...so the adapter query must carry the resolved name, not "default".
    kwargs = ctx.storage_adapter.list_files.call_args.kwargs
    assert kwargs["database_name"] == _ACTIVE_DB, (
        "source list passes the unresolved Click ``database`` arg to "
        "list_files. Use ``ctx.database_name`` (resolved via get_context) "
        "so the query honours ``db switch``."
    )


def test_source_list_count_queries_use_the_resolved_database_too() -> None:
    """The truncation-footer counts must target the same resolved database.

    A count taken against ``default`` while the rows come from the active
    workspace produces a nonsensical "Showing first N of M" footer, so all
    three adapter calls in the callback are covered here rather than only
    the one the original regression named.
    """
    from chaoscypher_cli.commands.source.list import list_files

    runner = CliRunner()
    rows = [
        {"id": f"s{i}", "filename": f"f{i}.txt", "status": "indexed", "file_size": 1}
        for i in range(3)
    ]

    for argv in ([], ["--status", "indexed"]):
        ctx = _ctx(files=rows, total=99)  # total > fetched → truncation branch
        with patch("chaoscypher_cli.commands.source.list.get_context", return_value=ctx):
            result = runner.invoke(list_files, argv)

        assert result.exit_code == 0, result.output
        assert "Showing first 3 of 99" in result.output, f"argv={argv}: no count query ran"
        assert _database_names(ctx.storage_adapter) == {_ACTIVE_DB}, (
            f"argv={argv}: an adapter query used an unresolved database name"
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
