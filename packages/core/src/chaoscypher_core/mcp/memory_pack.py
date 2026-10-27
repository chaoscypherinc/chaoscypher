# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Memory packs: one scope's memories as a ``.ccx`` package (spec 2026-10-20, slice 7).

A pack is the package exporter run over a scope's memory sources: their chunks, the facts
they attest, the citations of those facts and the closures the memories made, each memory
carrying its ``by``, scope and ``valid_from``. ``package load`` imports it like any package;
the importer verifies its signatures and brings each memory back as a memory, so it can be
recalled, forgotten and superseded where it lands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.mcp.memory_seed import memory_rows
from chaoscypher_core.services.export import CcxExporter


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine


def memory_source_ids(engine: Engine, scope: str | None) -> list[str]:
    """The memory sources of ``scope`` (every memory when None), in the order they were remembered.

    Oldest first: the importer creates sources in the package's order, so the imported
    memories keep the order a tie on ``valid_from`` is broken by (``created_at``).
    """
    rows = sorted(
        memory_rows(engine, scope), key=lambda r: (str(r.get("created_at") or ""), r["id"])
    )
    return [str(r["id"]) for r in rows]


def export_memory_pack(engine: Engine, *, scope: str | None, title: str | None = None) -> bytes:
    """The ``.ccx`` bytes of the memories of ``scope`` (every memory when None).

    The package holds the memory sources and what they attest, with no vectors (the
    importing engine embeds with its own model) and no lenses or workflows. Its title is
    ``title``, else ``memory:SCOPE`` (``memory:all`` without a scope).

    Raises:
        ValidationError: When the scope holds no memory.
    """
    source_ids = memory_source_ids(engine, scope)
    if not source_ids:
        message = f"No memories in scope {scope}." if scope else "No memories yet."
        raise ValidationError(message, field="scope")
    exporter = CcxExporter(
        graph_repository=engine.graph_repository,
        sources_repository=engine.storage_adapter,
        settings=engine.settings,
        workflow_db=None,
    )
    return exporter.export(
        source_ids=source_ids,
        include_sources=True,
        include_embeddings=False,
        include_lenses=False,
        include_workflows=False,
        title=title or f"memory:{scope or 'all'}",
    )


__all__ = ["export_memory_pack", "memory_source_ids"]
