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

2026-08-12: this test used to grep
``inspect.getsourcelines(CLISourceProcessingService.commit_to_graph)``
for ``"GraphRepository("`` and ``"storage_adapter.session"``, which the
span satisfies without the wiring being correct. ``storage_adapter.
session`` occurs six times in that span (four comments plus an unrelated
``.rollback()``), so it survives the construction being deleted outright.
And the truer form of the Bug 9 regression — keeping the construction but
binding it to the Engine-managed session, ``GraphRepository(self.ctx.
graph_repository.session, ...)`` — satisfies BOTH greps while
reintroducing the SQLITE_BUSY cascade, contradicting the old test's claim
that "both checks fail simultaneously". (Verified by replay: assigning
``self.ctx.graph_repository`` outright *does* trip the first grep, since
the paren form only ever appeared at the construction site — so that one
mutation was caught, and only that one.) The test now inspects the
repository object the CLI actually hands to ``SourceCommitService``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from chaoscypher_cli.sources.service import CLISourceProcessingService
from chaoscypher_core.adapters.sqlite.repos import GraphRepository


if TYPE_CHECKING:
    from typing import Any


def _seed_extracted_source(ctx: MagicMock, source_id: str = "src-commit-1") -> str:
    """Put one committable source on the fake adapter and stub the commit reads."""
    ctx.storage_adapter._files[source_id] = {
        "id": source_id,
        "filename": "doc.txt",
        "status": "extracted",
    }
    ctx.storage_adapter.get_source_commit_payload = MagicMock(
        return_value={"entities": [], "relationships": []}
    )
    return source_id


def _capture_commit_service() -> tuple[Any, Any]:
    """Patch SourceCommitService and hand back the class mock + its instance."""
    commit_service = MagicMock()
    commit_service.commit = AsyncMock(
        return_value={"created_nodes": [], "created_edges": [], "created_templates": []}
    )
    return (
        patch(
            "chaoscypher_core.services.sources.engine.commit.service.SourceCommitService",
            return_value=commit_service,
        ),
        commit_service,
    )


def test_commit_to_graph_uses_storage_adapter_session_for_graph_repository(
    mock_cli_context: MagicMock,
) -> None:
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
    CLI hands to ``SourceCommitService`` is a fresh one bound to the
    storage adapter's own session. If a future refactor restores the
    default Engine-managed _graph_session (by passing
    ``ctx.graph_repository`` straight through), the assertions below fail
    before anyone tries to run a real commit.
    """
    adapter_session = MagicMock()
    mock_cli_context.storage_adapter.session = adapter_session
    file_id = _seed_extracted_source(mock_cli_context)

    service = CLISourceProcessingService(mock_cli_context)
    patch_commit_service, _ = _capture_commit_service()
    with patch_commit_service as commit_service_cls:
        try:
            service.commit_to_graph(file_id)
        finally:
            service.close()

    graph_repository = commit_service_cls.call_args.kwargs["graph_repository"]

    # Not the Engine-managed repo off the context — a transient one.
    assert graph_repository is not mock_cli_context.graph_repository, (
        "commit_to_graph must instantiate a session-shared GraphRepository "
        "rather than reuse ctx.graph_repository — see header docstring."
    )
    assert isinstance(graph_repository, GraphRepository)
    # ...bound to the storage adapter's session, so both writers land in
    # the one transaction ``adapter.transaction()`` manages.
    # ``_fallback_session`` is the constructor arg itself. The public
    # ``.session`` property can adopt a per-task ContextVar session when one
    # is active, so it would not prove WHICH session this repo was built
    # with — which is exactly the wiring under test.
    assert graph_repository._fallback_session is adapter_session, (
        "commit_to_graph's transient GraphRepository must bind to "
        "``storage_adapter.session`` so both writers share one transaction."
    )
    assert graph_repository.database_name == mock_cli_context.database_name


def test_commit_to_graph_passes_the_storage_adapter_as_the_source_repositories(
    mock_cli_context: MagicMock,
) -> None:
    """The other half of the one-transaction contract.

    Sharing a session only helps if the commit service's source /
    indexing writers are the same adapter that opened the transaction.
    Pinning this alongside the GraphRepository binding means a refactor
    that splits either side is caught here rather than at SQLITE_BUSY
    time in a real commit.
    """
    mock_cli_context.storage_adapter.session = MagicMock()
    file_id = _seed_extracted_source(mock_cli_context, "src-commit-2")

    service = CLISourceProcessingService(mock_cli_context)
    patch_commit_service, _ = _capture_commit_service()
    with patch_commit_service as commit_service_cls:
        try:
            service.commit_to_graph(file_id)
        finally:
            service.close()

    kwargs = commit_service_cls.call_args.kwargs
    for role in ("source_repository", "sources_repository", "indexing_repository"):
        assert kwargs[role] is mock_cli_context.storage_adapter, f"{role} is not the CLI adapter"
    assert kwargs["search_repository"] is mock_cli_context.search_repository
