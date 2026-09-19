# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Loader-slice fixes from the 2026-09-14 ingest section-audit.

1. Text-shaped loaders and archive handlers pass their ``LoaderSettings`` to
   ``detect_encoding`` — every call site used to call it bare, so the
   operator's ``max_disk_bytes`` / chardet tunables were silently the model
   defaults (and the size error told the operator to raise the very setting
   they had already raised).
2. ``GenericHandler`` asks the registry whether a member is loadable instead
   of checking ``Path.suffix`` — ``inner.tar.gz`` was skipped while
   ``inner.tgz`` recursed.
3. ``ImageLoader`` closes the Pillow handle.
4. ``MarkdownHandler._find_markdown_files`` dedupes (case-insensitive
   filesystems return the same file for ``*.md`` and ``*.MD``).
5. The handlers' can_handle/find_root memo is released by ``process()`` —
   the old dict was keyed by a fresh ``mkdtemp`` dir per load and grew for
   the life of the process-cached handler.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from chaoscypher_core.services.sources.loaders.archive.handlers.generic_handler import (
    GenericHandler,
)
from chaoscypher_core.services.sources.loaders.archive.handlers.markdown_handler import (
    MarkdownHandler,
)
from chaoscypher_core.services.sources.loaders.archive.handlers.sphinx_handler import (
    SphinxHTMLHandler,
)
from chaoscypher_core.services.sources.loaders.csv_loader import CSVLoader
from chaoscypher_core.services.sources.loaders.html_loader import HTMLLoader
from chaoscypher_core.services.sources.loaders.image_loader import ImageLoader
from chaoscypher_core.services.sources.loaders.json_loader import JSONLoader
from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
from chaoscypher_core.services.sources.loaders.rst_loader import RSTLoader
from chaoscypher_core.services.sources.loaders.text_loader import TextLoader
from chaoscypher_core.settings import EngineSettings, LoaderSettings, PathSettings
from chaoscypher_core.utils import encoding as encoding_module


_DETECT = "chaoscypher_core.utils.encoding.detect_encoding"


def _settings(tmp_path: Path) -> EngineSettings:
    return EngineSettings(
        paths=PathSettings(data_dir=str(tmp_path)),
        loader=LoaderSettings(encoding_chardet_min_input_size=7),
    )


def _recording_detect(seen: list[Any]):
    real = encoding_module.detect_encoding

    def _wrapped(path: Path, *, settings: LoaderSettings | None = None):
        seen.append(settings)
        return real(path, settings=settings)

    return _wrapped


# ---------------------------------------------------------------------------
# 1. detect_encoding receives the loader settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("loader_cls", "filename", "body"),
    [
        (TextLoader, "a.txt", "plain text body\n"),
        (CSVLoader, "a.csv", "name,age\nAda,36\n"),
        (JSONLoader, "a.json", '{"title": "t", "body": "json body"}\n'),
        (HTMLLoader, "a.html", "<html><body><p>html body</p></body></html>\n"),
        (RSTLoader, "a.rst", "Title\n=====\n\nrst body\n"),
    ],
)
def test_text_shaped_loaders_pass_loader_settings_to_detect_encoding(
    tmp_path: Path, loader_cls: type, filename: str, body: str
) -> None:
    settings = _settings(tmp_path)
    sample = tmp_path / filename
    sample.write_text(body, encoding="utf-8")
    seen: list[Any] = []

    with patch(_DETECT, side_effect=_recording_detect(seen)):
        loader_cls(settings=settings).load_document(str(sample))

    assert seen, f"{loader_cls.__name__} never called detect_encoding"
    assert all(s is settings.loader for s in seen), (
        f"{loader_cls.__name__} called detect_encoding without its LoaderSettings: {seen}"
    )


def test_loader_without_settings_passes_none(tmp_path: Path) -> None:
    """No settings on the loader → ``detect_encoding`` gets ``None`` (its own defaults)."""
    sample = tmp_path / "a.txt"
    sample.write_text("body\n", encoding="utf-8")
    seen: list[Any] = []

    with patch(_DETECT, side_effect=_recording_detect(seen)):
        TextLoader().load_document(str(sample))

    assert seen == [None]


def test_markdown_handler_passes_loader_settings_to_detect_encoding(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "README.md").write_text("# Title\n\nbody\n", encoding="utf-8")
    seen: list[Any] = []

    with patch(_DETECT, side_effect=_recording_detect(seen)):
        docs = MarkdownHandler(settings=settings).process(docs_dir, settings)

    assert docs
    assert seen and all(s is settings.loader for s in seen)


# ---------------------------------------------------------------------------
# 2. compound extensions: registry candidates + GenericHandler deferral
# ---------------------------------------------------------------------------


def test_candidate_extensions_longest_first_then_bare_suffix() -> None:
    assert LoaderRegistry._candidate_extensions(Path("x/inner.tar.gz")) == [".tar.gz", ".gz"]
    assert LoaderRegistry._candidate_extensions(Path("x/report.PDF")) == [".pdf"]
    assert LoaderRegistry._candidate_extensions(Path("x/noext")) == [""]


def test_generic_handler_defers_member_support_to_registry(tmp_path: Path) -> None:
    """A member the registry can load by compound extension is processed, not skipped."""
    archive_dir = tmp_path / "bundle"
    archive_dir.mkdir()
    (archive_dir / "inner.tar.gz").write_bytes(b"not really a tarball")
    (archive_dir / "notes.unknownext").write_bytes(b"x")

    class _FakeRegistry:
        def get_loader(self, filepath: str) -> object | None:
            return object() if filepath.endswith(".tar.gz") else None

        def load_document(self, filepath: str) -> list[dict[str, Any]]:
            return [{"content": "nested", "metadata": {"source": filepath}}]

    # Imported lazily inside ``process()``, so patch it at its defining module.
    with patch(
        "chaoscypher_core.services.sources.loaders.factory.get_loader_registry",
        return_value=_FakeRegistry(),
    ):
        docs = GenericHandler().process(archive_dir, _settings(tmp_path))

    real_docs = [d for d in docs if d["content"]]
    assert len(real_docs) == 1
    assert real_docs[0]["metadata"]["archive_source"] == "inner.tar.gz"
    # Only the genuinely unsupported member counts as skipped.
    assert all(d["metadata"].get("loader_files_skipped", 1) == 1 for d in docs)


# ---------------------------------------------------------------------------
# 3. ImageLoader closes its Pillow handle
# ---------------------------------------------------------------------------


def test_image_loader_closes_the_image_handle(tmp_path: Path) -> None:
    from PIL import Image

    png = tmp_path / "probe.png"
    Image.new("RGB", (4, 3), color=(1, 2, 3)).save(png)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        docs = ImageLoader().load_document(str(png))
        import gc

        gc.collect()

    assert docs[0]["metadata"]["width"] == 4
    assert docs[0]["metadata"]["height"] == 3
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)], (
        "ImageLoader leaked the file handle"
    )


# ---------------------------------------------------------------------------
# 4. Markdown file discovery dedupes case-insensitive double matches
# ---------------------------------------------------------------------------


def test_find_markdown_files_dedupes_case_insensitive_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    only = docs_dir / "guide.md"
    only.write_text("# g\n", encoding="utf-8")

    real_rglob = Path.rglob

    def _case_insensitive_rglob(self: Path, pattern: str):
        # Emulate a case-insensitive filesystem: ``*.MD`` also matches ``guide.md``.
        return real_rglob(self, pattern.lower())

    monkeypatch.setattr(Path, "rglob", _case_insensitive_rglob)

    assert MarkdownHandler()._find_markdown_files(docs_dir) == [only]


# ---------------------------------------------------------------------------
# 5. The can_handle/find_root memo does not outlive process()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("handler_cls", [MarkdownHandler, SphinxHTMLHandler])
def test_scan_memo_is_shared_then_released_by_process(tmp_path: Path, handler_cls: type) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.md").write_text("# i\n", encoding="utf-8")
    (docs_dir / "index.html").write_text(
        "<html><body><div class='document'>x</div></body></html>", encoding="utf-8"
    )
    settings = _settings(tmp_path)
    handler = handler_cls(settings=settings)

    handler.can_handle(docs_dir)
    assert handler._scan_memo is not None
    assert handler._scan_memo[0] == docs_dir.resolve()
    handler.find_root(docs_dir)  # served from the memo, no second scan needed

    handler.process(docs_dir, settings)

    assert handler._scan_memo is None, "process() must release the memo"
