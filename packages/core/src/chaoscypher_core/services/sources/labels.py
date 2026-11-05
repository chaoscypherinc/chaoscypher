# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A document's labels, applied in one step: its tags and a caller's summary.

Every way a document arrives (MCP ``add_document``, ``Engine.process_document``,
the upload service behind REST and URL/site imports, the CLI's ``source add``)
calls :func:`apply_source_labels` right after the source row exists, and on a
``skip_duplicates`` hit for the existing row. The tag edit itself is
:func:`~chaoscypher_core.services.sources.tags.edit_source_tags`, the one
copy of "append or replace, then cap on the result, under the writer lock".

A path that creates the row first calls :func:`validate_source_labels` before
it does, so a refused tag or summary never leaves a half-created document.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

import structlog

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.models import SourceErrorStage, SourceStatus
from chaoscypher_core.services.sources.tags import (
    edit_source_tags,
    require_taggable_source,
    source_tag_names,
    validate_tags,
)


if TYPE_CHECKING:
    from chaoscypher_core.services.sources.tags import SourceAndTagStorage


__all__ = ["apply_source_labels", "clean_summary", "label_new_source", "validate_source_labels"]


logger = structlog.get_logger(__name__)

_WHITESPACE_RUN = re.compile(r"\s+")
# A straight pair, or a typographic one (written as escapes so no lookalike hides in the source).
_QUOTE_PAIRS = (('"', '"'), ("'", "'"), ("\u201c", "\u201d"), ("\u2018", "\u2019"))


def _unwrap_quotes(text: str) -> str:
    """``text`` without one pair of quotes that wraps all of it, else ``text`` unchanged.

    The pair comes off only when it belongs together: neither quote character
    appears again inside, so ``"A" and "B"`` (two quoted parts) is kept whole.
    """
    for opening, closing in _QUOTE_PAIRS:
        if len(text) >= 2 and text.startswith(opening) and text.endswith(closing):
            inner = text[1:-1]
            if opening in inner or closing in inner:
                return text
            return inner.strip()
    return text


def clean_summary(raw: str, *, max_chars: int) -> str | None:
    """Return the summary as stored: whitespace folded, trimmed, one pair of wrapping quotes off.

    Args:
        raw: The summary as given (typed by a caller, or written by a model).
        max_chars: The longest summary kept (``source_processing.summary_max_chars``).

    Returns:
        The cleaned summary, or ``None`` when nothing is left (``""`` clears).

    Raises:
        ValidationError: ``raw`` is not text, or the cleaned summary is longer
            than ``max_chars``.

    """
    given: object = raw  # a JSON caller can send anything
    if not isinstance(given, str):
        msg = f"a summary is text, not {type(given).__name__}"
        raise ValidationError(msg, field="summary")
    text = _WHITESPACE_RUN.sub(" ", raw).strip()
    text = _unwrap_quotes(text)
    if not text:
        return None
    if len(text) > max_chars:
        msg = f"summary is too long: at most {max_chars} characters"
        raise ValidationError(msg, field="summary")
    return text


def validate_source_labels(
    *, tags: Sequence[str] | None, summary: str | None, max_summary_chars: int
) -> None:
    """Refuse bad tags or a bad summary before a creation path writes anything.

    The tags pass the tag rule and the cap on their own (a new document has
    no other tags); a re-add's cap on the result is checked by
    :func:`apply_source_labels`.

    Raises:
        TagRuleError: a tag breaks the rule, or there are more than the cap.
        ValidationError: the summary is not text or is too long.

    """
    if tags is not None:
        validate_tags(tags)
    if summary is not None:
        clean_summary(summary, max_chars=max_summary_chars)


def apply_source_labels(
    storage: SourceAndTagStorage,
    database_name: str,
    source_id: str,
    *,
    tags: Sequence[str] | None = None,
    replace_tags: bool = False,
    summary: str | None = None,
    max_summary_chars: int,
) -> list[str]:
    """Give the document the caller's tags and summary, in one writer-locked transaction.

    Args:
        storage: The source and tag storage (the SQLite adapter).
        database_name: The database holding the document.
        source_id: The document's id.
        tags: ``None`` leaves the tags alone; otherwise they are added to the
            document's tags, or with ``replace_tags`` become the whole set
            (``[]`` clears). The 32-tag cap applies to the result.
        replace_tags: Make ``tags`` the whole set instead of adding them.
        summary: ``None`` keeps the summary; otherwise it replaces it, cleaned
            by :func:`clean_summary` (so ``""`` clears it).
        max_summary_chars: The longest summary kept.

    Returns:
        The document's tags after, sorted.

    Raises:
        TagRuleError: a tag breaks the rule, or the result is over the cap.
        ValidationError: the summary is too long, or the document is a memory.
        NotFoundError: no document has ``source_id``.

    """
    if tags is None and summary is None:
        return source_tag_names(storage, source_id)
    cleaned = clean_summary(summary, max_chars=max_summary_chars) if summary is not None else None
    with storage.write_locked():
        if tags is not None:
            if replace_tags:
                edit_source_tags(storage, database_name, source_id, replace=tags)
            else:
                edit_source_tags(storage, database_name, source_id, add=tags)
        if tags is None:
            # The tag edit checks the document; a summary alone checks it here.
            require_taggable_source(storage, database_name, source_id)
        if summary is not None:
            storage.update_source(source_id, {"summary": cleaned})
    return source_tag_names(storage, source_id)


def label_new_source(
    storage: SourceAndTagStorage,
    database_name: str,
    source_id: str,
    *,
    tags: Sequence[str] | None = None,
    replace_tags: bool = False,
    summary: str | None = None,
    max_summary_chars: int,
) -> list[str]:
    """:func:`apply_source_labels` on a row the caller has just created, before its indexing.

    The labels were checked before the row was written (:func:`validate_source_labels`);
    what can still fail (a lost tag-create race) marks the row failed at the
    indexing stage, so it is never left pending with nothing queued, and re-raises.

    Returns:
        The document's tags after, sorted.

    """
    try:
        return apply_source_labels(
            storage,
            database_name,
            source_id,
            tags=tags,
            replace_tags=replace_tags,
            summary=summary,
            max_summary_chars=max_summary_chars,
        )
    except Exception as exc:
        logger.warning("source_labels_failed", source_id=source_id, error_type=type(exc).__name__)
        storage.update_source(
            source_id,
            {
                "status": SourceStatus.ERROR,
                "error_message": f"Failed to apply tags or summary: {exc!s}",
                "error_stage": SourceErrorStage.INDEXING,
            },
        )
        raise
