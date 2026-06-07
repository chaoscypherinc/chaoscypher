# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Atomic force-re-extract — graph delete + source-row reset in one transaction.

Audit fix #C3. Previously these were two separate commits on separate
sessions; a crash between them left a COMMITTED source with no graph
artifacts.

Both writes now share a single SafeSession (``storage_adapter.session``)
inside ``adapter.transaction()``.  ``delete_source_artifacts`` accepts an
optional ``session=`` parameter; when provided it routes all three SQL
deletes and the ``maybe_commit`` through that session instead of the
repository's own.  Because ``adapter.transaction()`` uses
``_transaction_depth > 0`` to defer the actual COMMIT until the context
manager exits cleanly, a crash between the two writes rolls back both —
the graph deletes and the source-row reset are fully atomic.
"""

from __future__ import annotations

from typing import Any, cast

import structlog


logger = structlog.get_logger(__name__)


def force_re_extract(
    *,
    source_id: str,
    database_name: str,
    storage_adapter: Any,
    graph_repository: Any,
) -> dict[str, int]:
    """Reset a committed source to INDEXED + drop its graph artifacts atomically.

    Both writes share ``storage_adapter.session`` inside
    ``storage_adapter.transaction()``.  Passing the adapter's session to
    ``delete_source_artifacts`` routes all graph deletes through the same
    SafeSession, so ``_transaction_depth > 0`` defers the COMMIT until the
    context manager exits cleanly.  A crash between the two writes rolls
    back both — the graph deletes and the source-row reset are fully atomic.

    Args:
        source_id: The source to re-extract.
        database_name: The database the source lives in.
        storage_adapter: Storage adapter (must expose ``transaction()``,
            ``session``, and ``reset_for_re_extraction``).
        graph_repository: Graph repository (must expose
            ``delete_source_artifacts``).

    Returns:
        Dict with counts of removed graph artifacts from delete_source_artifacts
        (keys: nodes_deleted, edges_deleted, templates_deleted).

    Raises:
        RuntimeError: If ``storage_adapter.session`` is ``None`` after entering
            the transaction context (disconnected adapter).
        Whatever the underlying adapter / repository raise. The transaction
            context manager rolls back both writes on any exception.
    """
    with storage_adapter.transaction():
        if storage_adapter.session is None:
            msg = (
                "force_re_extract requires a connected adapter; "
                "storage_adapter.session is None inside transaction()."
            )
            # nosemgrep: cc-045-bare-stdlib-raise-in-core - programmer-error guard; session is always set by transaction() in correct usage
            raise RuntimeError(msg)
        removed = cast(
            "dict[str, int]",
            graph_repository.delete_source_artifacts(
                source_id,
                session=storage_adapter.session,
            ),
        )
        storage_adapter.reset_for_re_extraction(
            source_id=source_id,
            database_name=database_name,
        )
        logger.info(
            "force_re_extract_committed",
            source_id=source_id,
            database_name=database_name,
        )
    return removed


__all__ = ["force_re_extract"]
