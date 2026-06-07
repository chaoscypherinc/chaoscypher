# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Targeted unit tests for ``resolve_normalization_default``.

Structured formats (CSV / TSV / JSON / JSONL / NDJSON / XML) must default
to *False* so normalization doesn't shred their layout; prose-shaped files
default to *True*. Mutations on the set membership, the negation, or the
``.lower()`` call must be caught here.
"""

from __future__ import annotations

import pytest

from chaoscypher_core.utils.normalization_default import (
    STRUCTURED_EXTENSIONS,
    resolve_normalization_default,
)


@pytest.mark.parametrize(
    "filename",
    [
        "data.csv",
        "tabular.tsv",
        "payload.json",
        "stream.jsonl",
        "ndjson_export.ndjson",
        "sitemap.xml",
        # Path-shaped names hit the same suffix path.
        "/tmp/some/dir/data.csv",
        "C:\\Users\\me\\file.JSON",  # mixed-case → lowercased before lookup
    ],
)
def test_structured_files_default_to_false(filename: str) -> None:
    """Every structured extension defaults to False (no normalization)."""
    assert resolve_normalization_default(filename=filename) is False


@pytest.mark.parametrize(
    "filename",
    [
        "doc.txt",
        "essay.md",
        "report.pdf",
        "page.html",
        "guide.rst",
        "memo.docx",
        "deck.pptx",
        "book.epub",
    ],
)
def test_prose_files_default_to_true(filename: str) -> None:
    """Non-structured extensions default to True (normalize prose)."""
    assert resolve_normalization_default(filename=filename) is True


def test_extensionless_filename_defaults_to_true() -> None:
    """No suffix → prose default (True)."""
    assert resolve_normalization_default(filename="README") is True


def test_empty_filename_defaults_to_true() -> None:
    """Empty string has no suffix → prose default."""
    assert resolve_normalization_default(filename="") is True


def test_extension_case_is_normalised() -> None:
    """Uppercase / mixed-case extensions still match the structured set."""
    assert resolve_normalization_default(filename="data.CSV") is False
    assert resolve_normalization_default(filename="payload.Json") is False


def test_structured_extensions_membership_is_stable() -> None:
    """The structured-extensions set must contain exactly these six."""
    expected = frozenset({".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".xml"})
    assert expected == STRUCTURED_EXTENSIONS


def test_unknown_extension_defaults_to_true() -> None:
    """An unrecognised extension is treated as prose."""
    assert resolve_normalization_default(filename="thing.zzz") is True
