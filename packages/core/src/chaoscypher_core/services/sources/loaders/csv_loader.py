# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

r"""CSV Document Loader.

Replaces the previous loader, which hardcoded LangChain's ``CSVLoader``
(comma delimiter, platform default encoding): TSV-saved-as-csv and EU
semicolon-delimited files came out as single-cell rows, and cp1252
exports got mojibake. This loader uses :func:`csv.Sniffer.sniff` over
``,;\t|`` and routes through :func:`detect_encoding` so the dialect
and encoding are detected explicitly and recorded on each document's
metadata.
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)

# Most CSV-shaped real-world files use one of these delimiters.
_DIALECT_CANDIDATES = ",;\t|"


class CSVLoader:
    """CSV file loader with dialect detection and explicit encoding."""

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [".csv", ".CSV"]

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize CSV loader.

        Args:
            settings: Settings instance (controls CSV dialect sample size).

        """
        self.settings = settings
        # ``Sniffer`` needs a sample big enough to be statistically meaningful
        # but small enough that we don't pay for parsing the whole file twice
        # on a 1-GB CSV.  The default (8 KiB) is the convention used by
        # csvkit, pandas, etc. Configurable via
        # ``settings.loader.csv_dialect_sample_bytes``.
        self._sample_bytes: int = (
            settings.loader.csv_dialect_sample_bytes if settings is not None else 8192
        )

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load CSV file as a list of one-document-per-row dictionaries.

        The loader sniffs the dialect (handles TSV-saved-as-csv, EU
        semicolons, pipe-delimited exports) and reads the file with the
        encoding chosen by :func:`detect_encoding`.  Each non-header row
        becomes one document whose content is a ``key: value, …`` text
        rendering.

        Args:
            filepath: Path to CSV file.

        Returns:
            One document per body row.  An empty file returns an empty
            list rather than raising.
        """
        from chaoscypher_core.services.sources.loaders.base import check_loader_file_size
        from chaoscypher_core.utils.encoding import detect_encoding

        check_loader_file_size(filepath, self.settings)

        path = Path(filepath)
        encoding_used, text, replacement_chars_count = detect_encoding(path)

        sample = text[: self._sample_bytes]
        sniffer = csv.Sniffer()

        try:
            dialect: type[csv.Dialect] | csv.Dialect = sniffer.sniff(
                sample, delimiters=_DIALECT_CANDIDATES
            )
        except csv.Error:
            # Tiny / column-of-one files can't be sniffed — default to
            # the comma dialect rather than raising.
            dialect = csv.excel

        try:
            has_header = sniffer.has_header(sample)
        except csv.Error:
            has_header = True  # safest assumption for typical exports

        reader = csv.reader(StringIO(text), dialect)
        rows = list(reader)
        if not rows:
            logger.info(
                "csv_loaded_empty",
                filepath=str(path),
                encoding_used=encoding_used,
            )
            return []

        if has_header:
            header = [str(cell) for cell in rows[0]]
            body = rows[1:]
        else:
            header = [f"column_{i}" for i in range(len(rows[0]))]
            body = rows

        delimiter = getattr(dialect, "delimiter", ",")

        docs: list[dict[str, Any]] = []
        rows_truncated = 0
        for row_idx, row in enumerate(body):
            # Count rows whose column count doesn't match the header length.
            # zip(strict=False) silently truncates the shorter side; we track
            # mismatched rows so the operator can see data was left on the floor.
            if len(row) != len(header):
                rows_truncated += 1
            kv = ", ".join(f"{h}: {v}" for h, v in zip(header, row, strict=False))
            # replacement_chars_count and rows_truncated are file-level metrics;
            # attach them only to the first row document so the indexing handler
            # rollup doesn't multiply the count by the number of rows.
            row_replacement_count = replacement_chars_count if row_idx == 0 else 0
            row_truncated_count = rows_truncated if row_idx == len(body) - 1 else 0
            docs.append(
                {
                    "content": kv,
                    "metadata": {
                        "source": str(path),
                        "extraction_method": "csv",
                        "encoding_used": encoding_used,
                        "replacement_chars_count": row_replacement_count,
                        "dialect": delimiter,
                        "row_index": row_idx,
                        "loader_csv_rows_truncated": row_truncated_count,
                    },
                }
            )

        logger.info(
            "csv_loaded",
            filepath=str(path),
            row_count=len(docs),
            encoding_used=encoding_used,
            dialect=delimiter,
            has_header=has_header,
        )

        return docs

    def supports_ocr(self) -> bool:
        """CSV files don't need OCR."""
        return False


__all__ = ["CSVLoader"]
