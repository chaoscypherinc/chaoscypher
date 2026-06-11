# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""JSON / JSONL / NDJSON Document Loader.

Replaces the LangChain-backed loader (which silently failed on
``.jsonl`` files because it tried ``json.load`` on a multi-document
file and swallowed the exception). This loader:

- Branches on extension: ``.json`` is parsed as a single document;
  ``.jsonl`` / ``.ndjson`` is parsed line-by-line with per-line error
  isolation.
- Uses :func:`chaoscypher_core.utils.encoding.detect_encoding` so cp1252
  / Latin-1 JSONL exports decode correctly and the encoding is recorded
  in document metadata.
- Raises :class:`ValidationError` when every JSONL line fails to parse;
  otherwise attaches a ``loader_warnings`` metadata key to the first
  surviving document so the indexing handler can surface partial
  failures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ValidationError


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class JSONLoader:
    """JSON / JSONL / NDJSON file loader.

    Each ``.jsonl`` / ``.ndjson`` line becomes its own document; ``.json``
    is loaded as a single document.
    """

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [
            ".json",
            ".JSON",
            ".jsonl",
            ".JSONL",
            ".ndjson",
            ".NDJSON",
        ]

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize JSON loader.

        Args:
            settings: Settings instance (not currently used)

        """
        self.settings = settings

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load JSON / JSONL / NDJSON file.

        Args:
            filepath: Path to JSON file.

        Returns:
            List of documents. ``.json`` produces a single document; JSONL
            / NDJSON produce one document per non-blank line.

        Raises:
            ValidationError: When the file is JSON and unparseable, or
                when every JSONL line fails to parse.
        """
        from chaoscypher_core.utils.encoding import detect_encoding

        path = Path(filepath)
        encoding_used, text, replacement_chars_count = detect_encoding(path)

        if path.suffix.lower() in {".jsonl", ".ndjson"}:
            return self._load_jsonl(text, path, encoding_used, replacement_chars_count)
        return self._load_json(text, path, encoding_used, replacement_chars_count)

    def _load_jsonl(
        self,
        text: str,
        path: Path,
        encoding_used: str,
        replacement_chars_count: int = 0,
    ) -> list[dict[str, Any]]:
        """Decode a JSONL / NDJSON file line-by-line.

        Per-line parse errors are isolated and recorded as
        ``loader_warnings`` on the first surviving document. If every
        line fails, a :class:`ValidationError` is raised so the user
        sees the failure instead of getting a silent empty source.
        """
        logger.info("jsonl_loading_started", filepath=str(path))

        docs: list[dict[str, Any]] = []
        warnings: list[str] = []
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                warnings.append(f"line {line_no}: {exc.msg} (col {exc.colno})")
                continue
            # replacement_chars_count is a file-level metric; attach it only
            # to the first surviving document so the indexing handler rollup
            # doesn't multiply the count by the number of parsed lines.
            doc_replacement_count = replacement_chars_count if not docs else 0
            docs.append(
                {
                    "content": json.dumps(obj, indent=2, ensure_ascii=False),
                    "metadata": {
                        "source": str(path),
                        "extraction_method": "jsonl_line",
                        "line_number": line_no,
                        "encoding_used": encoding_used,
                        "replacement_chars_count": doc_replacement_count,
                    },
                }
            )

        if warnings:
            if not docs:
                # Every line malformed: surface a hard failure rather
                # than silently returning [].
                msg = f"JSONL parse failure: every line failed to parse. First error: {warnings[0]}"
                logger.warning(
                    "jsonl_all_lines_invalid",
                    filepath=str(path),
                    line_count=len(warnings),
                    first_error=warnings[0],
                )
                raise ValidationError(msg, field="content")
            # Attach warnings to the first surviving doc so the indexing
            # handler / quality counter has somewhere to record them.
            docs[0]["metadata"]["loader_warnings"] = warnings
            logger.warning(
                "jsonl_partial_parse",
                filepath=str(path),
                lines_loaded=len(docs),
                lines_skipped=len(warnings),
            )

        logger.info(
            "jsonl_loaded",
            filepath=str(path),
            line_count=len(docs),
            encoding_used=encoding_used,
        )
        return docs

    def _load_json(
        self,
        text: str,
        path: Path,
        encoding_used: str,
        replacement_chars_count: int = 0,
    ) -> list[dict[str, Any]]:
        """Decode a single-document JSON file."""
        logger.info("json_loading_started", filepath=str(path))
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            msg = f"JSON parse error: {exc.msg} at line {exc.lineno}, column {exc.colno}"
            logger.warning(
                "json_loading_failed",
                filepath=str(path),
                error_message=exc.msg,
                line=exc.lineno,
                column=exc.colno,
            )
            raise ValidationError(msg, field="content") from exc

        rendered = json.dumps(obj, indent=2, ensure_ascii=False)
        logger.info(
            "json_loaded",
            filepath=str(path),
            character_count=len(rendered),
            encoding_used=encoding_used,
        )
        return [
            {
                "content": rendered,
                "metadata": {
                    "source": str(path),
                    "extraction_method": "json",
                    "encoding_used": encoding_used,
                    "replacement_chars_count": replacement_chars_count,
                },
            }
        ]

    def supports_ocr(self) -> bool:
        """JSON files don't need OCR."""
        return False


__all__ = ["JSONLoader"]
