# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""RSTLoader extracts text from .rst / .rest files.

Workstream 7 (2026-05-07): reStructuredText is the canonical Python
documentation format (Sphinx, README.rst). Standalone .rst uploads had
no loader before this workstream.
"""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.loaders.rst_loader import RSTLoader


def test_rst_loader_extracts_text(tmp_path: Path) -> None:
    p = tmp_path / "doc.rst"
    p.write_text(
        "My Document\n"
        "===========\n"
        "\n"
        "First paragraph.\n"
        "\n"
        "* item 1\n"
        "* item 2\n"
        "\n"
        ".. code-block:: python\n"
        "\n"
        "   x = 1\n",
        encoding="utf-8",
    )
    loader = RSTLoader()
    docs = loader.load_document(str(p))
    text = docs[0]["content"]
    assert "My Document" in text
    assert "First paragraph" in text
    assert "item 1" in text
    assert "item 2" in text


def test_rst_loader_handles_cp1252_encoded(tmp_path: Path) -> None:
    p = tmp_path / "cp.rst"
    p.write_bytes("Café résumé\n========\n".encode("cp1252"))
    loader = RSTLoader()
    docs = loader.load_document(str(p))
    assert "Café" in docs[0]["content"]
    assert "résumé" in docs[0]["content"]


def test_rst_loader_falls_back_on_unparseable_directives(tmp_path: Path) -> None:
    """Custom / unknown RST directives must not crash the loader.

    RST in the wild often references Sphinx-only directives
    (``.. autoclass::`` etc.) that vanilla docutils doesn't recognize.
    The loader should degrade to raw-text content rather than raising.
    """
    p = tmp_path / "weird.rst"
    p.write_text(
        "Title\n=====\n\n.. unknownsphinxdirective:: something\n\n   Some text inside.\n",
        encoding="utf-8",
    )
    loader = RSTLoader()
    docs = loader.load_document(str(p))
    assert "Title" in docs[0]["content"]


def test_rst_loader_supports_extensions() -> None:
    loader = RSTLoader()
    assert ".rst" in loader.supported_extensions
    assert ".rest" in loader.supported_extensions
    assert loader.metadata.plugin_id == "rst"
