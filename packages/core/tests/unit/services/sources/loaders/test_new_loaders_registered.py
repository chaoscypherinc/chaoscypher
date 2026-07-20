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


def test_tgz_archive_routes_to_archive_loader(loader_registry: LoaderRegistry) -> None:
    from chaoscypher_core.services.sources.loaders.archive_loader import ArchiveLoader

    loader = loader_registry.get_loader("/tmp/bundle.tgz")
    assert isinstance(loader, ArchiveLoader)


def test_targz_compound_extension_routes_to_archive_loader(
    loader_registry: LoaderRegistry,
) -> None:
    """A ``.tar.gz`` upload resolves to the archive loader.

    Regression: ``get_loader`` matched on ``Path(...).suffix`` which is only
    ``.gz`` for ``bundle.tar.gz`` — never a registered key — so ``.tar.gz``
    archives 404'd with "No loader available for file type: .gz" even though
    ArchiveLoader advertises ``.tar.gz``. The registry now matches the
    compound suffix.
    """
    from chaoscypher_core.services.sources.loaders.archive_loader import ArchiveLoader

    loader = loader_registry.get_loader("/tmp/bundle.tar.gz")
    assert isinstance(loader, ArchiveLoader)
    # Resolves to the same loader class the ``.tgz`` alias does.
    assert type(loader) is type(loader_registry.get_loader("/tmp/other.tgz"))


def test_plain_gz_without_tar_is_unsupported(loader_registry: LoaderRegistry) -> None:
    """A bare ``.gz`` (not ``.tar.gz``) has no registered loader.

    Documents the scope of the compound-suffix fix: only the advertised
    ``.tar.gz`` / ``.tgz`` archive extensions resolve, not arbitrary gzip.
    """
    assert loader_registry.get_loader("/tmp/single.gz") is None
