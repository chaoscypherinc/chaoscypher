# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""TextLoader detects encoding instead of replacing characters.

Workstream 6 (2026-05-07): the previous loader hardcoded
``encoding="utf-8", errors="replace"``, which replaced every non-utf-8
byte with U+FFFD. cp1252 / Latin-1 files lost every special character.
The new loader uses :func:`chaoscypher_core.utils.encoding.detect_encoding`
and records the encoding in document metadata.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.loaders.text_loader import TextLoader


def test_text_loader_handles_cp1252_without_replacement(tmp_path: Path) -> None:
    p = tmp_path / "blog.md"
    p.write_bytes("café — résumé é\nhello world".encode("cp1252"))

    loader = TextLoader()
    docs = loader.load_document(str(p))

    assert len(docs) == 1
    text = docs[0]["content"]
    assert "café" in text
    assert "résumé" in text
    assert "�" not in text  # No replacement characters.

    # Encoding metadata is recorded for the quality counter.
    metadata = docs[0]["metadata"]
    assert "encoding_used" in metadata
    assert "cp1252" in metadata["encoding_used"].lower()


def test_text_loader_handles_utf8(tmp_path: Path) -> None:
    p = tmp_path / "note.txt"
    p.write_text("Hello — café résumé\n", encoding="utf-8")

    loader = TextLoader()
    docs = loader.load_document(str(p))

    assert len(docs) == 1
    assert "café" in docs[0]["content"]
    assert "résumé" in docs[0]["content"]
    assert docs[0]["metadata"]["encoding_used"] == "utf-8"


def test_text_loader_strips_utf8_bom(tmp_path: Path) -> None:
    p = tmp_path / "bom.txt"
    p.write_bytes(b"\xef\xbb\xbfhello world\n")

    loader = TextLoader()
    docs = loader.load_document(str(p))

    assert docs[0]["content"].startswith("hello")
    assert "bom" in docs[0]["metadata"]["encoding_used"].lower()
