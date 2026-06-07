# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests for the CLI commit step's session-sharing wiring.

Historical context (May 2026): this file previously pinned the
``adapter.get_extraction_results(...)`` accessor and the
``extraction_results`` JSON column on the sources row. Migration 0042
replaced that JSON blob with the relational ``source_entities`` /
``source_relationships`` tables, and the dedicated accessor was
removed. The column-shape contract is now enforced by the migration
itself + the repo-method type signatures + the per-table repository
test files (``test_source_entities_repository.py`` and friends), so
the plumbing-test layer that asserted the old accessor is gone.

What remains here is the dual-session lock-conflict regression
(Bug 9), which is orthogonal to the column-shape redesign and still
relevant: the CLI's commit_to_graph must build a transient
``GraphRepository`` bound to ``storage_adapter.session`` so that
``start_commit`` (storage_adapter session) and
``template_handler.create_suggested_templates`` (graph_repository
session) participate in the SAME SQLite transaction. Without that
wiring, the second writer raises SQLITE_BUSY and the commit cascades
with PendingRollbackError.
"""

from __future__ import annotations


def test_commit_to_graph_uses_storage_adapter_session_for_graph_repository() -> None:
    """Bug 9 regression: dual-session lock conflict during commit.

    ``Engine`` creates ``storage_adapter.session`` AND a separate
    ``_graph_session`` on the same SQLite file. Inside
    ``adapter.transaction()``, the commit service does:

    1. ``start_commit(file_id)`` — writes via storage_adapter.session
       (flushes; acquires SQLite writer lock).
    2. ``template_handler.create_suggested_templates(...)`` — writes via
       graph_repository, which is bound to ``_graph_session``. Its
       INSERT INTO graph_templates begins a NEW transaction on
       _graph_session and races the open one on storage_adapter.session.
       SQLite returns SQLITE_BUSY and the whole commit cascades with
       PendingRollbackError.

    The fix in ``CLISourceProcessingService.commit_to_graph`` builds a
    transient ``GraphRepository`` bound to ``storage_adapter.session``
    so both writers participate in the SAME transaction.

    The test makes sure that wiring is intact: the GraphRepository the
    CLI hands to ``SourceCommitService`` shares a session with the
    storage adapter. If a future refactor restores the default
    Engine-managed _graph_session, the assertion below fails before
    anyone tries to run a real commit.
    """
    import inspect

    from chaoscypher_cli.sources.service import CLISourceProcessingService

    src_lines, _ = inspect.getsourcelines(CLISourceProcessingService.commit_to_graph)
    body = "".join(src_lines)

    # Two halves of the wiring contract. If a future cleanup tries to
    # "simplify" by using ctx.graph_repository directly, both checks
    # fail simultaneously and point at the right docstring.
    assert "GraphRepository(" in body, (
        "commit_to_graph must instantiate a session-shared GraphRepository "
        "rather than reuse ctx.graph_repository — see header docstring."
    )
    assert "storage_adapter.session" in body, (
        "commit_to_graph's transient GraphRepository must bind to "
        "``storage_adapter.session`` so both writers share one transaction."
    )
