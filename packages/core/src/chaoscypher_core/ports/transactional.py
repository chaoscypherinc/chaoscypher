# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Protocol for adapters that expose a transaction boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from contextlib import AbstractContextManager


@runtime_checkable
class TransactionalSession(Protocol):
    """Minimal session surface required by port-layer write methods.

    Implemented structurally by ``sqlmodel.Session`` and
    ``sqlalchemy.orm.Session``. Ports that accept an optional caller
    session (to participate in an outer transaction) depend on this
    protocol rather than on the concrete framework type.

    Only the two methods actually called by port implementations are
    declared here (YAGNI).  ``connection()`` returns the underlying
    ``sqlalchemy.engine.Connection`` and is used to route raw SQL
    writes through the caller's transaction.  ``flush()`` synchronises
    pending ORM state before the caller commits.
    """

    def connection(self) -> Any:
        """Return the underlying database connection for this session."""
        ...

    def flush(self) -> None:
        """Flush pending ORM writes without committing."""
        ...


class TransactionalAdapterProtocol(Protocol):
    """Adapter supporting ``transaction()`` context manager for atomic writes.

    Implemented by ``SqliteAdapter`` (and any future adapter that supports
    multi-method atomic writes). Services that need to group operations
    from multiple repository protocols into one transaction depend on
    this protocol in addition to the narrower repository protocols.
    """

    def transaction(self) -> AbstractContextManager[None]:
        """Context manager that commits on clean exit, rolls back on exception."""
        ...
