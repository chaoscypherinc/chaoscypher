# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""XLSXLoader walks each sheet and flattens rows to text.

Workstream 7 (2026-05-07): standalone .xlsx uploads had no loader.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.loaders.xlsx_loader import XLSXLoader


FIXTURE = Path(__file__).parent.parent.parent.parent.parent / "fixtures" / "loaders" / "sample.xlsx"


def test_xlsx_loader_extracts_rows() -> None:
    assert FIXTURE.exists(), f"fixture missing: {FIXTURE}"
    loader = XLSXLoader()
    docs = loader.load_document(str(FIXTURE))
    text = docs[0]["content"]
    # Header row.
    assert "Name | Age | City" in text
    # Body rows (cells are stringified).
    assert "Alice | 30 | NYC" in text
    assert "Bob | 25 | LA" in text
    assert "Carol | 28 | SF" in text


def test_xlsx_loader_includes_sheet_names() -> None:
    loader = XLSXLoader()
    docs = loader.load_document(str(FIXTURE))
    text = docs[0]["content"]
    assert "[Sheet: Sheet1]" in text
    assert "[Sheet: Notes]" in text
    assert "Top-line note" in text


def test_xlsx_loader_skips_blank_rows() -> None:
    """All-None rows must not appear as a stray ' |  | ' line."""
    loader = XLSXLoader()
    docs = loader.load_document(str(FIXTURE))
    # Sheet1 has 4 non-empty rows (header + 3 names).
    sheets = docs[0]["metadata"]["sheets"]
    sheet1 = next(s for s in sheets if s["name"] == "Sheet1")
    assert sheet1["rows"] == 4


def test_xlsx_loader_supported_extensions() -> None:
    loader = XLSXLoader()
    assert ".xlsx" in loader.supported_extensions
    assert loader.metadata.plugin_id == "xlsx"


def test_xlsx_loader_wraps_invalid_file_in_validation_error(tmp_path: Path) -> None:
    """A file with .xlsx extension but wrong magic bytes raises ValidationError.

    Without the wrapper, openpyxl surfaces ``InvalidFileException`` or
    ``zipfile.BadZipFile`` — both bypass the API exception envelope.
    """
    bogus = tmp_path / "fake.xlsx"
    bogus.write_bytes(b"not a real xlsx")

    loader = XLSXLoader()
    with pytest.raises(ValidationError) as exc_info:
        loader.load_document(str(bogus))

    err = exc_info.value
    assert err.code == "VALIDATION_ERROR"
    assert err.field == "content"
    assert "fake.xlsx" in err.message
    assert "not a valid" in err.message
    assert exc_info.value.__cause__ is not None
