# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
# ruff: noqa: D301  -- docstrings include literal "\r" / "\n" examples.

"""Pipeline-parity regression tests for the CLI's index_file path.

Bugs 11 + 12 (May 2026): the CLI's source-add pipeline passed
``enable_normalization=None`` down to ``CLISourceProcessingService.index_file``,
whose signature accepted only ``bool``. Python's truthiness coerced
``None`` → ``False``, the ``if enable_normalization:`` branch in
``index_file`` was silently skipped, and ContentNormalizerService never
ran for ANY CLI upload of a prose file. Combined with a second bug in
``ChunkingService._sanitize_text`` that left ``\\r`` characters intact,
the LLM ended up reading chunks full of ``\\r `` (CR + space)
substitutions. Same input file produced different chunks via CLI vs
Cortex queue vs MCP — three pipelines, three behaviours.

The fixes:

* ``CLISourceProcessingService.index_file`` accepts ``bool | None`` and
  resolves ``None`` via ``resolve_normalization_default(filename)`` —
  the same helper Cortex's indexing_handler uses. Prose files
  (.txt / .md / .html / .pdf / ...) default to ``True``; structured
  formats (.csv / .tsv / .json / .jsonl / .ndjson / .xml) default to
  ``False``. An explicit user override (``True`` or ``False``) wins.

* ``ChunkingService._sanitize_text`` strips CR/CRLF unconditionally —
  defense in depth, so even paths that skip ContentNormalizerService
  cannot leak raw ``\\r`` into stored chunks. ftfy in
  ContentNormalizerService does the same when normalization is on; this
  is the universal fallback.

These tests pin both halves of the contract so any future "simplify"
that drops the tri-state or the CR scrub fails loudly.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def cli_context_real(tmp_path: Path) -> Generator[Any]:
    """A real ``CLIContext`` pointed at a tmp_path SQLite DB.

    Boots the full Engine wiring (adapter, repositories, ChunkingService)
    so the index_file path runs end-to-end without the Cortex queue.
    """
    from chaoscypher_cli.context import CLIContext

    ctx = CLIContext(database_name="default", data_dir=tmp_path)
    ctx.connect()
    try:
        yield ctx
    finally:
        ctx.disconnect()


# ---------------------------------------------------------------------------
# Bug 11: tri-state resolution
# ---------------------------------------------------------------------------


def test_index_file_signature_accepts_none_for_normalization() -> None:
    """The default for ``enable_normalization`` must be ``None`` (tri-state),
    not ``True`` — that's what makes the CLI honour the file-type
    default the same way Cortex's indexing_handler does.
    """
    import inspect

    from chaoscypher_cli.sources.service import CLISourceProcessingService

    sig = inspect.signature(CLISourceProcessingService.index_file)
    param = sig.parameters["enable_normalization"]

    assert param.default is None, (
        f"index_file.enable_normalization default is {param.default!r}; "
        "must be None for tri-state parity with indexing_handler. If "
        "hardcoded True, CLI defaults would override stored row preferences "
        "and structured files (CSV/JSON/XML) would get prose-normalized."
    )


def test_index_file_resolves_none_to_filetype_default_for_prose() -> None:
    """``enable_normalization=None`` for a .txt source must resolve to
    True via ``resolve_normalization_default`` — the same code path
    Cortex's indexing_handler.py:301-303 walks.
    """
    from chaoscypher_core.utils.normalization_default import (
        resolve_normalization_default,
    )

    # .txt is a prose extension → default True
    assert resolve_normalization_default(filename="document.txt") is True
    # .md, .html, .pdf are all prose
    assert resolve_normalization_default(filename="notes.md") is True
    assert resolve_normalization_default(filename="page.html") is True
    assert resolve_normalization_default(filename="book.pdf") is True


def test_index_file_resolves_none_to_filetype_default_for_structured() -> None:
    """Structured formats (CSV/JSON/XML) must resolve to ``False`` — these
    files have meaningful whitespace that the OCR/text cleaners would
    damage. Parity matters here because a user uploading a CSV via the
    CLI without --no-normalize should get the same "skip normalization"
    behaviour they'd get via Cortex.
    """
    from chaoscypher_core.utils.normalization_default import (
        resolve_normalization_default,
    )

    assert resolve_normalization_default(filename="data.csv") is False
    assert resolve_normalization_default(filename="data.tsv") is False
    assert resolve_normalization_default(filename="payload.json") is False
    assert resolve_normalization_default(filename="stream.jsonl") is False
    assert resolve_normalization_default(filename="stream.ndjson") is False
    assert resolve_normalization_default(filename="config.xml") is False


# ---------------------------------------------------------------------------
# Bug 12: CR / CRLF strip in stored chunks — end-to-end via CLI index_file
# ---------------------------------------------------------------------------


def test_crlf_input_produces_chunks_with_no_carriage_returns(
    cli_context_real: Any, tmp_path: Path
) -> None:
    """A CRLF-encoded prose file run through ``index_file`` must produce
    chunks whose ``content`` column contains no ``\\r`` characters.

    Pre-fix this assertion would have failed: a war_and_peace_tiny.txt
    upload via the CLI persisted chunks like
    ``"WAR AND PEACE\\r \\r \\r By Leo Tolstoy\\r \\r ..."`` because
    normalization was skipped (Bug 11) and the chunker's
    _sanitize_text didn't handle CR (Bug 12). Either bug alone is
    enough to leak ``\\r`` — the test is a single observation that
    catches both.
    """
    from chaoscypher_cli.sources.service import CLISourceProcessingService

    # Build a CRLF-encoded prose file. Matches the byte shape of the
    # canonical war_and_peace_tiny.txt benchmark file.
    crlf_text = (
        "WAR AND PEACE\r\n\r\nBy Leo Tolstoy\r\n\r\n"
        "Chapter One: It was the best of times, it was the worst of times. "
        "Pierre and Natasha attended the ball where Anna Pavlovna held "
        "court. The Empress was in attendance.\r\n\r\n"
        "Chapter Two: Pierre Bezukhov, a man of considerable means, found "
        "himself unable to forget Natasha Rostova's beauty.\r\n"
    )
    src_file = tmp_path / "crlf_prose.txt"
    src_file.write_bytes(crlf_text.encode("utf-8"))

    service = CLISourceProcessingService(cli_context_real)
    source_id = service.upload_file(src_file)
    # ``upload_file`` returns either a str id or a dict on duplicate. We
    # passed a fresh file so it should be a str.
    assert isinstance(source_id, str), source_id

    # Run indexing with default enable_normalization (None → resolves to
    # True for .txt). This is the production code path users invoke via
    # ``chaoscypher source add file.txt``.
    service.index_file(source_id, skip_embeddings=True, enable_vision=False)

    # Direct raw-SQL check on the chunks table — assert ZERO ``\r`` chars
    # appear in any chunk's content. CR-free is the contract.
    import sqlite3

    adapter = cli_context_real.storage_adapter
    con = sqlite3.connect(adapter.db_path)
    rows = con.execute(
        "SELECT chunk_index, content FROM document_chunks WHERE source_id=? ORDER BY chunk_index",
        (source_id,),
    ).fetchall()
    con.close()

    assert rows, "expected at least one chunk to be persisted"
    for chunk_index, content in rows:
        assert "\r" not in content, (
            f"chunk {chunk_index} contains a literal CR character — "
            f"either normalization was skipped (Bug 11) or "
            f"_sanitize_text regressed (Bug 12). "
            f"chunk_content (first 200 chars): {content[:200]!r}"
        )
