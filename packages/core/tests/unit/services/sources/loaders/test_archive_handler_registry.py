# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ArchiveHandlerRegistry discovery and selection."""

from __future__ import annotations

from pathlib import Path

from chaoscypher_core.services.sources.loaders.archive.handlers.registry import (
    ArchiveHandlerRegistry,
)
from chaoscypher_core.settings import EngineSettings, PathSettings


def _make_settings(data_dir: Path) -> EngineSettings:
    """Build an EngineSettings whose ``paths.data_dir`` points at ``data_dir``."""
    return EngineSettings(paths=PathSettings(data_dir=str(data_dir)))


def test_registry_discovers_built_in_handlers(tmp_path: Path) -> None:
    """Registry discovers all four built-in handlers by name."""
    registry = ArchiveHandlerRegistry(settings=_make_settings(tmp_path))
    ids = set(registry.list_all().keys())
    assert {"sphinx_html", "markdown_docs", "openapi", "generic"} <= ids


def test_find_handler_returns_none_on_empty_registry() -> None:
    """If no handlers are registered, find_handler returns None."""

    class _EmptyRegistry(ArchiveHandlerRegistry):
        def _discover(self) -> None:  # skip built-in scan
            return

    registry = _EmptyRegistry(settings=None)
    assert registry.find_handler(Path("/tmp/anywhere")) is None


def test_find_handler_picks_highest_specificity(tmp_path: Path) -> None:
    """find_handler selects the handler with the highest can_handle() score.

    Builds a fixture directory with strong Sphinx indicators (``_static/``,
    ``genindex.html``, ``searchindex.js``) so that
    :class:`SphinxHTMLHandler` returns a specificity score well above
    :class:`GenericHandler`'s constant fallback score of ``10``.
    """
    sphinx_dir = tmp_path / "sphinx_fixture"
    sphinx_dir.mkdir()

    # Trigger SphinxHTMLHandler indicators: _static/ dir + genindex.html +
    # searchindex.js yields confidence 0.8 (score 80) > generic's 10.
    (sphinx_dir / "_static").mkdir()
    (sphinx_dir / "genindex.html").write_text("<html><body>Index</body></html>")
    (sphinx_dir / "searchindex.js").write_text("Search.setIndex({})")
    (sphinx_dir / "index.html").write_text("<html><body><div class='body'>Docs</div></body></html>")

    registry = ArchiveHandlerRegistry(settings=_make_settings(tmp_path / "dummy_data"))
    handler = registry.find_handler(sphinx_dir)

    assert handler is not None
    assert handler.metadata.name == "sphinx_html"


def test_find_handler_falls_back_to_generic(tmp_path: Path) -> None:
    """An archive with no format-specific indicators falls back to generic."""
    mixed_dir = tmp_path / "mixed"
    mixed_dir.mkdir()
    (mixed_dir / "random.txt").write_text("nothing special")

    registry = ArchiveHandlerRegistry(settings=_make_settings(tmp_path / "dummy_data"))
    handler = registry.find_handler(mixed_dir)

    # Generic is the only handler that claims any random directory (score 10).
    assert handler is not None
    assert handler.metadata.name == "generic"


def test_find_handler_detects_nested_sphinx(tmp_path: Path) -> None:
    """Nested Sphinx archive still selects the Sphinx handler.

    Regression: a Sphinx archive with docs buried in
    ``docs/_build/html/`` still selects the Sphinx handler (not Generic).

    Before P1-16.5 the retired :class:`DocumentationDetector` walked
    subdirectories to locate the Sphinx root. That walking was lost when
    detection moved into each handler's :meth:`can_handle`, which only
    checked the top level. This test pins the restored behaviour: when
    markers such as ``_static/`` and ``searchindex.js`` live in a nested
    directory, :class:`SphinxHTMLHandler` must still claim the archive.
    """
    archive_root = tmp_path / "nested_sphinx_fixture"
    sphinx_root = archive_root / "docs" / "_build" / "html"
    sphinx_root.mkdir(parents=True)

    # Place the same indicators exercised by the non-nested test, but two
    # directory levels below the archive root. A bare top-level scan would
    # miss them and fall through to GenericHandler.
    (sphinx_root / "_static").mkdir()
    (sphinx_root / "genindex.html").write_text("<html><body>Index</body></html>")
    (sphinx_root / "searchindex.js").write_text("Search.setIndex({})")
    (sphinx_root / "index.html").write_text(
        "<html><body><div class='body'>Docs</div></body></html>"
    )

    registry = ArchiveHandlerRegistry(settings=_make_settings(tmp_path / "dummy_data"))
    handler = registry.find_handler(archive_root)

    assert handler is not None
    assert handler.metadata.name == "sphinx_html"


def test_sphinx_find_root_returns_nested_docs_root(tmp_path: Path) -> None:
    """``find_root`` narrows a nested Sphinx archive to the real docs root.

    This mirrors the old :class:`DocumentationDetector`'s ``root_path``
    behaviour: ``handler.process()`` must receive the deepest directory
    holding Sphinx markers so relative paths, TOC scanning, and static
    asset resolution all key off the real docs root.
    """
    from chaoscypher_core.services.sources.loaders.archive.handlers.sphinx_handler import (
        SphinxHTMLHandler,
    )

    archive_root = tmp_path / "nested_archive"
    sphinx_root = archive_root / "docs" / "_build" / "html"
    sphinx_root.mkdir(parents=True)
    (sphinx_root / "_static").mkdir()
    (sphinx_root / "genindex.html").write_text("x")
    (sphinx_root / "searchindex.js").write_text("x")

    handler = SphinxHTMLHandler()
    resolved = handler.find_root(archive_root)

    assert resolved == sphinx_root


def test_markdown_find_root_returns_docs_subdir(tmp_path: Path) -> None:
    """``find_root`` returns the ``docs/`` subdirectory for MkDocs layouts.

    When the bulk of the Markdown content lives under ``docs/`` (MkDocs /
    Docusaurus layout), the narrower root is the one that gets processed.
    """
    from chaoscypher_core.services.sources.loaders.archive.handlers.markdown_handler import (
        MarkdownHandler,
    )

    archive_root = tmp_path / "md_archive"
    docs_dir = archive_root / "docs"
    docs_dir.mkdir(parents=True)
    (archive_root / "mkdocs.yml").write_text("site_name: x")
    # Enough files to satisfy the Markdown heuristic without ambiguity.
    for i in range(12):
        (docs_dir / f"page-{i}.md").write_text(f"# Page {i}\nhello")

    handler = MarkdownHandler()
    resolved = handler.find_root(archive_root)

    assert resolved == docs_dir


def test_generic_find_root_is_identity(tmp_path: Path) -> None:
    """``GenericHandler.find_root`` is a no-op (safety default)."""
    from chaoscypher_core.services.sources.loaders.archive.handlers.generic_handler import (
        GenericHandler,
    )

    handler = GenericHandler()
    assert handler.find_root(tmp_path) == tmp_path


def test_openapi_find_root_is_identity(tmp_path: Path) -> None:
    """``OpenAPIHandler.find_root`` is a no-op (specs usually sit at the top)."""
    from chaoscypher_core.services.sources.loaders.archive.handlers.openapi_handler import (
        OpenAPIHandler,
    )

    handler = OpenAPIHandler()
    assert handler.find_root(tmp_path) == tmp_path
