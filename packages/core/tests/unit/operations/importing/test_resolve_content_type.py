# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for _resolve_content_type — per-doc MIME validation with fallback.

Phase 7 audit-remediation (2026-05-09): a loader-supplied
``metadata["content_type"]`` MIME like ``application/vnd.ms-excel`` was split
on ``/`` to obtain ``vnd.ms-excel``, then passed to
``ContentType.from_extension``.  Unknown post-split keys silently produced
``ContentType.TEXT`` (the map's fallback), potentially misrouting documents to
the wrong cleaner.

``_resolve_content_type`` fixes this by validating the post-split key; when
unrecognized it logs a structured warning and falls back to the
extension-derived type.
"""

from __future__ import annotations

import structlog.testing

from chaoscypher_core.operations.importing.indexing_handler import _resolve_content_type
from chaoscypher_core.services.sources.normalizer import ContentType


# ---------------------------------------------------------------------------
# Happy-path: well-known MIME subtype maps directly
# ---------------------------------------------------------------------------


def test_known_mime_html_resolves_to_html() -> None:
    """``text/html`` → splits to ``html`` → ContentType.HTML."""
    result = _resolve_content_type(
        metadata={"content_type": "text/html"},
        filepath="/some/archive/page.zip",
    )
    assert result == ContentType.HTML


def test_known_mime_pdf_resolves_to_pdf() -> None:
    """``application/pdf`` → splits to ``pdf`` → ContentType.PDF."""
    result = _resolve_content_type(
        metadata={"content_type": "application/pdf"},
        filepath="/some/doc.txt",  # file ext says .txt but MIME wins
    )
    assert result == ContentType.PDF


def test_bare_extension_word_resolves_directly() -> None:
    """A bare ``content_type`` without a slash (e.g. ``html``) still works."""
    result = _resolve_content_type(
        metadata={"content_type": "html"},
        filepath="/archive/page.zip",
    )
    assert result == ContentType.HTML


# ---------------------------------------------------------------------------
# Fallback: unrecognized MIME subtype → extension-derived + warning
# ---------------------------------------------------------------------------


def test_unknown_mime_falls_back_to_extension_derived_type() -> None:
    """An unrecognized MIME like ``application/vnd.ms-excel`` must not
    silently produce ``ContentType.TEXT``; instead it must fall back to the
    extension-derived type (``html`` for ``report.html``).
    """
    with structlog.testing.capture_logs() as logs:
        result = _resolve_content_type(
            metadata={"content_type": "application/vnd.ms-excel"},
            filepath="/data/report.html",  # extension says html
        )

    # Falls back to extension-derived type, not silently TEXT from MIME
    assert result == ContentType.HTML

    # A structured warning must have fired
    assert any(
        rec.get("event") == "content_type_unknown_mime_fallback"
        and rec.get("mime") == "application/vnd.ms-excel"
        for rec in logs
    ), f"Expected 'content_type_unknown_mime_fallback' warning; got: {logs}"


def test_unknown_mime_fallback_includes_filepath_field() -> None:
    """The warning log must include the ``filepath`` field for operator
    diagnostics (so they can see which file triggered the misrouting).
    """
    with structlog.testing.capture_logs() as logs:
        _resolve_content_type(
            metadata={
                "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            },
            filepath="/reports/q1.csv",
        )

    warning = next(
        (r for r in logs if r.get("event") == "content_type_unknown_mime_fallback"),
        None,
    )
    assert warning is not None, "Expected warning log was not emitted"
    assert "filepath" in warning, (
        f"Warning log must include 'filepath' field; got keys: {list(warning.keys())}"
    )


def test_unknown_mime_warns_only_once_per_doc() -> None:
    """Each call to ``_resolve_content_type`` emits exactly one warning when
    the MIME is unrecognized (not zero, not two).
    """
    with structlog.testing.capture_logs() as logs:
        _resolve_content_type(
            metadata={"content_type": "application/vnd.ms-excel"},
            filepath="/data/file.pdf",
        )

    warnings = [r for r in logs if r.get("event") == "content_type_unknown_mime_fallback"]
    assert len(warnings) == 1, f"Expected exactly 1 warning; got {len(warnings)}: {warnings}"


# ---------------------------------------------------------------------------
# No metadata / no content_type: extension fallback, no warning
# ---------------------------------------------------------------------------


def test_no_content_type_in_metadata_uses_extension() -> None:
    """When ``metadata`` has no ``content_type``, fall back to the filepath
    extension without emitting a warning.
    """
    with structlog.testing.capture_logs() as logs:
        result = _resolve_content_type(
            metadata={},
            filepath="/docs/report.pdf",
        )

    assert result == ContentType.PDF
    assert not any(rec.get("event") == "content_type_unknown_mime_fallback" for rec in logs), (
        "No warning should fire when content_type is absent"
    )


def test_empty_metadata_uses_extension() -> None:
    """Empty ``metadata`` dict also falls through to extension-derived type."""
    result = _resolve_content_type(
        metadata={},
        filepath="/logs/app.log",
    )
    assert result == ContentType.TEXT  # .log maps to TEXT


def test_none_metadata_uses_extension() -> None:
    """``None`` metadata gracefully falls back to extension-derived type."""
    result = _resolve_content_type(
        metadata=None,
        filepath="/data/page.html",
    )
    assert result == ContentType.HTML
