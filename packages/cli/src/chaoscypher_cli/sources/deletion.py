# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Delete a source the way the API does: one cascade for ``source delete`` and ``source site delete``."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from chaoscypher_cli.context import CLIContext


def delete_source_cascade(
    ctx: CLIContext, source_id: str, *, leave_out_of_site: bool = True
) -> bool:
    """Delete a source with the API's cascade.

    The graph release and the SQL cascade in one transaction, then the
    post-release refresh, the vector rows, the staged file and any rendered
    vision images.

    Args:
        ctx: The connected CLI context.
        source_id: The source to delete.
        leave_out_of_site: A page of a docs site joins the pages the site
            leaves out (False when the site is deleted with its pages).

    Returns:
        Whether a source was deleted.
    """
    from chaoscypher_cli.context import cli_release_embedder
    from chaoscypher_core.services.graph.management.release import refresh_after_release_sync
    from chaoscypher_core.services.graph.management.source import SourceService

    service = SourceService(
        repository=ctx.storage_adapter,
        database_name=ctx.database_name,
        settings=ctx.settings,
    )
    deleted = service.delete_source(
        source_id, search_repo=ctx.search_repository, leave_out_of_site=leave_out_of_site
    )
    refresh_after_release_sync(
        ctx.storage_adapter,
        ctx.database_name,
        deleted.released,
        embedder=cli_release_embedder(ctx),
        search=ctx.search_repository,
    )
    return bool(deleted)


__all__ = ["delete_source_cascade"]
