# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The MCP server's ``tag_document`` and ``list_documents``: a document's tags, and finding by them.

``tag_document`` (write mode) adds, removes or replaces a document's tags and
optionally sets its summary, in one writer-locked transaction; it answers
``{"success": true, "source_id": ..., "tags": [...sorted]}``.
``list_documents`` (read mode too) lists the documents, memories excluded,
newest first, optionally by tag; it answers ``{"success": true, "documents":
[{"source_id", "title", "summary", "tags", "added"}], "total": n}`` from the
stored fields alone, never a model call.

The tag rule, the edit and the listing are Core's
(``services.sources.tags``, ``services.sources.labels``, ``SourceService``);
this module only reads the arguments and shapes the answers. A refusal is
``{"success": false, "error": <the rule's words>}`` with ``isError``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chaoscypher_core.exceptions import ConflictError, NotFoundError, ValidationError
from chaoscypher_core.services.graph.management.source import SourceService
from chaoscypher_core.services.sources.labels import apply_source_labels, clean_summary
from chaoscypher_core.services.sources.tags import edit_source_tags, filter_tag_scope
from chaoscypher_core.utils.tool_failure import discard_failed_work


if TYPE_CHECKING:
    from chaoscypher_core.bootstrap import Engine


DOCUMENT_TOOLS = frozenset({"tag_document", "list_documents"})
"""The document-tag tools the server dispatches to :func:`handle_document_tool`."""


def _whole_number(args: dict[str, Any], key: str, *, default: int, least: int) -> int:
    """``args[key]`` as an int of at least ``least`` (``default`` when absent), else refused."""
    # Over MCP the schema (type, minimum) refuses these first; this guards direct callers.
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < least:
        msg = f"{key} is a whole number of at least {least}, not {value!r}"
        raise ValidationError(msg, field=key)
    return int(value)


def _tag_document(engine: Engine, args: dict[str, Any]) -> dict[str, Any]:
    """Add, remove or replace a document's tags; with ``summary`` set it in the same transaction."""
    source_id = str(args.get("source_id") or "").strip()
    if not source_id:
        msg = "tag_document needs the document's source_id"
        raise ValidationError(msg, field="source_id")
    storage = engine.storage_adapter
    database = engine.settings.current_database
    max_chars = engine.settings.source_processing.summary_max_chars
    summary = args.get("summary")
    if summary is not None:
        clean_summary(summary, max_chars=max_chars)  # refused before any tag changes
    with storage.write_locked():
        tags = edit_source_tags(
            storage,
            database,
            source_id,
            add=args.get("add", []),
            remove=args.get("remove", []),
            replace=args.get("replace"),
        )
        if summary is not None:
            tags = apply_source_labels(
                storage, database, source_id, summary=summary, max_summary_chars=max_chars
            )
    return {"success": True, "source_id": source_id, "tags": tags}


def _list_documents(engine: Engine, args: dict[str, Any]) -> dict[str, Any]:
    """One page of the documents, newest first, by tag when ``tags`` is given."""
    settings = engine.settings
    limit = _whole_number(args, "limit", default=50, least=1)
    most = settings.pagination.max_page_size
    if limit > most:
        msg = f"limit is at most {most}, not {limit}"
        raise ValidationError(msg, field="limit")
    offset = _whole_number(args, "offset", default=0, least=0)
    tags, match = filter_tag_scope(args.get("tags", []), str(args.get("match", "any")))
    service = SourceService(
        repository=engine.storage_adapter,
        database_name=settings.current_database,
        settings=settings,
    )
    documents, total = service.list_documents(tags=tags, match=match, limit=limit, offset=offset)
    return {"success": True, "documents": documents, "total": total}


async def handle_document_tool(
    engine: Engine, name: str, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Run ``tag_document`` or ``list_documents``.

    Returns:
        ``(answer, is_error)``: the JSON answer and whether it is a refusal.
    """
    try:
        if name == "tag_document":
            return _tag_document(engine, args), False
        return _list_documents(engine, args), False
    except (ValidationError, NotFoundError, ConflictError) as exc:
        discard_failed_work(engine.storage_adapter.session)
        return {"success": False, "error": exc.message}, True


__all__ = ["DOCUMENT_TOOLS", "handle_document_tool"]
