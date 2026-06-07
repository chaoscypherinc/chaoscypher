# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""LoaderRegistry auto-discovers all six Workstream-7 loaders.

Workstream 7 (2026-05-07): the registry uses ``*_loader.py`` filename
discovery — confirms each new loader resolves through the public
``get_loader(filepath)`` API for a representative extension.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
from chaoscypher_core.settings import EngineSettings, PathSettings


@pytest.fixture
def loader_registry(tmp_path: Path) -> LoaderRegistry:
    """A fresh LoaderRegistry pointed at an empty user-plugins dir.

    Per-test ``tmp_path`` keeps user plugin discovery quiet; built-in
    loaders are still picked up from the source tree.
    """
    settings = EngineSettings(
        paths=PathSettings(
            data_dir=str(tmp_path),
            config_dir=str(tmp_path / "cfg"),
            cache_dir=str(tmp_path / "cache"),
        )
    )
    return LoaderRegistry(settings=settings)


def test_html_loader_registered(loader_registry: LoaderRegistry) -> None:
    loader = loader_registry.get_loader("/tmp/page.html")
    assert loader is not None
    assert loader.metadata.plugin_id == "html"


def test_rst_loader_registered(loader_registry: LoaderRegistry) -> None:
    loader = loader_registry.get_loader("/tmp/doc.rst")
    assert loader is not None
    assert loader.metadata.plugin_id == "rst"


def test_docx_loader_registered(loader_registry: LoaderRegistry) -> None:
    loader = loader_registry.get_loader("/tmp/doc.docx")
    assert loader is not None
    assert loader.metadata.plugin_id == "docx"


def test_xlsx_loader_registered(loader_registry: LoaderRegistry) -> None:
    loader = loader_registry.get_loader("/tmp/sheet.xlsx")
    assert loader is not None
    assert loader.metadata.plugin_id == "xlsx"


def test_pptx_loader_registered(loader_registry: LoaderRegistry) -> None:
    loader = loader_registry.get_loader("/tmp/deck.pptx")
    assert loader is not None
    assert loader.metadata.plugin_id == "pptx"


def test_epub_loader_registered(loader_registry: LoaderRegistry) -> None:
    loader = loader_registry.get_loader("/tmp/book.epub")
    assert loader is not None
    assert loader.metadata.plugin_id == "epub"
