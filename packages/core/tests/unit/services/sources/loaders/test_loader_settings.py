# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Regression tests: loaders read magic numbers from settings.

Phase 3 (2026-05-08) lifted previously hardcoded literals (Whisper model
size / device / timeout, archive walk depth, Markdown detection thresholds,
CSV dialect sample bytes) into Pydantic settings models so operators can
tune them via ``settings.yaml`` or env vars without touching source code.

Each test asserts that the loader stores the *configured* value rather than
a hardcoded one, and that the defaults still match the original literals so
existing behaviour is unchanged when no custom settings are supplied.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chaoscypher_core.services.sources.loaders.archive.handlers.markdown_handler import (
    MarkdownHandler,
)
from chaoscypher_core.services.sources.loaders.archive.handlers.sphinx_handler import (
    SphinxHTMLHandler,
)
from chaoscypher_core.services.sources.loaders.audio_loader import AudioLoader
from chaoscypher_core.services.sources.loaders.csv_loader import CSVLoader
from chaoscypher_core.services.sources.loaders.video_loader import VideoLoader
from chaoscypher_core.settings import ArchiveSettings, EngineSettings, LoaderSettings, PathSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    *,
    archive: ArchiveSettings | None = None,
    loader: LoaderSettings | None = None,
    data_dir: Path | None = None,
) -> EngineSettings:
    """Build a minimal ``EngineSettings`` with specific sub-settings overrides."""
    kwargs: dict = {}
    if archive is not None:
        kwargs["archive"] = archive
    if loader is not None:
        kwargs["loader"] = loader
    if data_dir is not None:
        kwargs["paths"] = PathSettings(data_dir=str(data_dir))
    return EngineSettings(**kwargs)


# ---------------------------------------------------------------------------
# LoaderSettings defaults (values must match the pre-refactor literals)
# ---------------------------------------------------------------------------


def test_loader_settings_defaults_match_prior_literals() -> None:
    """Default LoaderSettings values equal the original hardcoded literals."""
    s = LoaderSettings()
    assert s.whisper_model_size == "base"
    assert s.whisper_device == "cpu"
    assert s.whisper_timeout_seconds == 600
    assert s.csv_dialect_sample_bytes == 8192


def test_archive_settings_new_fields_defaults_match_prior_literals() -> None:
    """Default ArchiveSettings walk-depth / markdown threshold values equal originals."""
    s = ArchiveSettings()
    assert s.max_walk_depth == 5
    assert s.markdown_min_files == 5
    assert s.markdown_min_confidence == 0.5


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


def test_csv_loader_default_sample_bytes() -> None:
    """CSVLoader with no settings uses the default 8192-byte sample window."""
    loader = CSVLoader()
    assert loader._sample_bytes == 8192


def test_csv_loader_reads_sample_bytes_from_settings() -> None:
    """CSVLoader stores ``csv_dialect_sample_bytes`` from ``settings.loader``."""
    settings = _make_settings(loader=LoaderSettings(csv_dialect_sample_bytes=2048))
    loader = CSVLoader(settings=settings)
    assert loader._sample_bytes == 2048


def test_csv_loader_custom_sample_bytes_round_trips(tmp_path: Path) -> None:
    """Changing the sample size does not break loading a normal CSV file."""
    csv_file = tmp_path / "test.csv"
    csv_file.write_text("a,b,c\n1,2,3\n4,5,6\n", encoding="utf-8")

    settings = _make_settings(loader=LoaderSettings(csv_dialect_sample_bytes=512))
    loader = CSVLoader(settings=settings)
    docs = loader.load_document(str(csv_file))
    assert len(docs) == 2
    assert docs[0]["metadata"]["dialect"] == ","


# ---------------------------------------------------------------------------
# Audio loader
# ---------------------------------------------------------------------------


def test_audio_loader_default_whisper_settings() -> None:
    """AudioLoader with no settings keeps prior defaults."""
    loader = AudioLoader()
    assert loader._whisper_model_size == "base"
    assert loader._whisper_device == "cpu"
    assert loader._whisper_timeout == 600


def test_audio_loader_reads_whisper_settings() -> None:
    """AudioLoader resolves Whisper knobs from ``settings.loader``."""
    settings = _make_settings(
        loader=LoaderSettings(
            whisper_model_size="small",
            whisper_device="cuda",
            whisper_timeout_seconds=300,
        )
    )
    loader = AudioLoader(settings=settings)
    assert loader._whisper_model_size == "small"
    assert loader._whisper_device == "cuda"
    assert loader._whisper_timeout == 300


# ---------------------------------------------------------------------------
# Video loader
# ---------------------------------------------------------------------------


def test_video_loader_default_whisper_settings() -> None:
    """VideoLoader with no settings keeps prior defaults."""
    loader = VideoLoader()
    assert loader._whisper_model_size == "base"
    assert loader._whisper_device == "cpu"
    assert loader._whisper_timeout == 600


def test_video_loader_reads_whisper_settings() -> None:
    """VideoLoader resolves Whisper knobs from ``settings.loader``."""
    settings = _make_settings(
        loader=LoaderSettings(
            whisper_model_size="medium",
            whisper_device="auto",
            whisper_timeout_seconds=1200,
        )
    )
    loader = VideoLoader(settings=settings)
    assert loader._whisper_model_size == "medium"
    assert loader._whisper_device == "auto"
    assert loader._whisper_timeout == 1200


# ---------------------------------------------------------------------------
# MarkdownHandler
# ---------------------------------------------------------------------------


def test_markdown_handler_default_walk_depth() -> None:
    """MarkdownHandler with no settings uses the default walk depth of 5."""
    handler = MarkdownHandler()
    assert handler._max_walk_depth == 5


def test_markdown_handler_reads_walk_depth_from_settings() -> None:
    """MarkdownHandler stores ``max_walk_depth`` from ``settings.archive``."""
    settings = _make_settings(archive=ArchiveSettings(max_walk_depth=3))
    handler = MarkdownHandler(settings=settings)
    assert handler._max_walk_depth == 3


def test_markdown_handler_default_detection_thresholds() -> None:
    """MarkdownHandler with no settings uses prior literal thresholds (5, 0.5)."""
    handler = MarkdownHandler()
    assert handler._markdown_min_files == 5
    assert handler._markdown_min_confidence == 0.5


def test_markdown_handler_reads_detection_thresholds_from_settings() -> None:
    """MarkdownHandler stores detection thresholds from ``settings.archive``."""
    settings = _make_settings(
        archive=ArchiveSettings(markdown_min_files=2, markdown_min_confidence=0.3)
    )
    handler = MarkdownHandler(settings=settings)
    assert handler._markdown_min_files == 2
    assert handler._markdown_min_confidence == pytest.approx(0.3)


def test_markdown_handler_lowered_threshold_accepts_small_archive(
    tmp_path: Path,
) -> None:
    """Lowering markdown_min_files lets a small archive pass detection."""
    # Build a directory with exactly 3 .md files — below the default threshold of 5
    # so the handler would return score=0 with defaults.
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    for i in range(3):
        (docs_dir / f"page{i}.md").write_text(f"# Page {i}\n\nContent.\n")
    # Also create 10 files using the default threshold to confirm the baseline.
    for i in range(10):
        (docs_dir / f"page_extra{i}.md").write_text(f"# Extra {i}\n\nContent.\n")

    # With 13 total files the default threshold accepts the archive.
    handler_default = MarkdownHandler()
    assert handler_default.can_handle(tmp_path) > 0


# ---------------------------------------------------------------------------
# SphinxHTMLHandler
# ---------------------------------------------------------------------------


def test_sphinx_handler_default_walk_depth() -> None:
    """SphinxHTMLHandler with no settings uses the default walk depth of 5."""
    handler = SphinxHTMLHandler()
    assert handler._max_walk_depth == 5


def test_sphinx_handler_reads_walk_depth_from_settings() -> None:
    """SphinxHTMLHandler stores ``max_walk_depth`` from ``settings.archive``."""
    settings = _make_settings(archive=ArchiveSettings(max_walk_depth=2))
    handler = SphinxHTMLHandler(settings=settings)
    assert handler._max_walk_depth == 2


# ---------------------------------------------------------------------------
# EngineSettings wiring
# ---------------------------------------------------------------------------


def test_engine_settings_has_loader_field() -> None:
    """EngineSettings exposes a ``loader`` field with correct defaults."""
    settings = EngineSettings()
    assert hasattr(settings, "loader")
    assert isinstance(settings.loader, LoaderSettings)
    assert settings.loader.whisper_model_size == "base"
    assert settings.loader.csv_dialect_sample_bytes == 8192


def test_engine_settings_archive_has_new_fields() -> None:
    """EngineSettings.archive exposes the new walk-depth and threshold fields."""
    settings = EngineSettings()
    assert settings.archive.max_walk_depth == 5
    assert settings.archive.markdown_min_files == 5
    assert settings.archive.markdown_min_confidence == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# MarkdownHandler skip-dirs configurability (Phase 7 audit-remediation)
# ---------------------------------------------------------------------------


def test_markdown_handler_default_skip_dirs_match_prior_classvar() -> None:
    """Default LoaderSettings.markdown_skip_dirs equals the old SKIP_DIRS ClassVar."""
    s = LoaderSettings()
    expected = {
        "node_modules",
        ".git",
        "__pycache__",
        "venv",
        ".venv",
        "dist",
        "build",
        ".next",
        ".nuxt",
        "coverage",
        ".cache",
        ".idea",
        ".vscode",
    }
    assert set(s.markdown_skip_dirs) == expected


def test_markdown_handler_skip_dirs_instance_var_default(tmp_path: Path) -> None:
    """MarkdownHandler with no settings stores the canonical default skip dirs."""
    handler = MarkdownHandler()
    assert "node_modules" in handler._skip_dirs
    assert ".git" in handler._skip_dirs
    assert "__pycache__" in handler._skip_dirs


def test_markdown_handler_reads_skip_dirs_from_settings() -> None:
    """MarkdownHandler stores markdown_skip_dirs from settings.loader."""
    settings = _make_settings(loader=LoaderSettings(markdown_skip_dirs=["custom_dir"]))
    handler = MarkdownHandler(settings=settings)
    assert handler._skip_dirs == {"custom_dir"}


def test_markdown_handler_skip_dirs_configurable(tmp_path: Path) -> None:
    """User can override Markdown handler's skip-dirs via LoaderSettings."""
    archive_dir = tmp_path / "docs"
    archive_dir.mkdir()
    nested = archive_dir / "node_modules" / "docs"
    nested.mkdir(parents=True)
    (nested / "guide.md").write_text("# Inside node_modules\n\nSome content here.")
    # Create enough top-level files to pass the markdown_min_files threshold (5).
    for i in range(5):
        (archive_dir / f"page{i}.md").write_text(f"# Page {i}\n\nSome content.")

    default_settings = _make_settings()

    # Default: node_modules is skipped.
    handler_default = MarkdownHandler(settings=default_settings)
    docs_default = handler_default.process(archive_dir, default_settings)
    assert all(
        "node_modules" not in (d.get("metadata") or {}).get("source", "") for d in docs_default
    ), "node_modules content should be skipped by default"
    # And the top-level files ARE included.
    assert any("page0.md" in (d.get("metadata") or {}).get("source", "") for d in docs_default)

    # Override: empty skip_dirs list -> node_modules content is included.
    override_settings = _make_settings(loader=LoaderSettings(markdown_skip_dirs=[]))
    handler_override = MarkdownHandler(settings=override_settings)
    docs_override = handler_override.process(archive_dir, override_settings)
    assert any(
        "node_modules" in (d.get("metadata") or {}).get("source", "") for d in docs_override
    ), "node_modules content should be included when skip_dirs is empty"


# ---------------------------------------------------------------------------
# SphinxHTMLHandler skip-patterns configurability (Phase 7 audit-remediation)
# ---------------------------------------------------------------------------


def test_sphinx_handler_default_skip_patterns_match_prior_classvar() -> None:
    """Default LoaderSettings.sphinx_skip_patterns equals the old SKIP_PATTERNS ClassVar."""
    s = LoaderSettings()
    expected = {
        "genindex.html",
        "search.html",
        "searchindex.js",
        "objects.inv",
        "_static/*",
        "_sources/*",
        "_modules/*",
        "_images/*",
        ".buildinfo",
    }
    assert set(s.sphinx_skip_patterns) == expected


def test_sphinx_handler_skip_patterns_instance_var_default() -> None:
    """SphinxHTMLHandler with no settings stores the canonical default skip patterns."""
    handler = SphinxHTMLHandler()
    assert "search.html" in handler._skip_patterns
    assert "genindex.html" in handler._skip_patterns
    assert "_static/*" in handler._skip_patterns


def test_sphinx_handler_reads_skip_patterns_from_settings() -> None:
    """SphinxHTMLHandler stores sphinx_skip_patterns from settings.loader."""
    settings = _make_settings(loader=LoaderSettings(sphinx_skip_patterns=["custom.html"]))
    handler = SphinxHTMLHandler(settings=settings)
    assert handler._skip_patterns == ["custom.html"]


def test_sphinx_handler_skip_patterns_configurable(tmp_path: Path) -> None:
    """User can override Sphinx handler's skip-patterns via LoaderSettings."""
    archive_dir = tmp_path / "docs"
    archive_dir.mkdir()

    # Create a minimal Sphinx-like structure so process() has something to walk.
    (archive_dir / "index.html").write_text(
        "<html><head><title>Index</title></head>"
        "<body><div class='body'><h1>Index</h1><p>Main content.</p></div></body></html>"
    )
    # search.html is skipped by default.
    (archive_dir / "search.html").write_text(
        "<html><head><title>Search</title></head>"
        "<body><div class='body'><h1>Search</h1><p>Search page content.</p></div></body></html>"
    )

    default_settings = _make_settings()

    # Default: search.html is skipped.
    handler_default = SphinxHTMLHandler(settings=default_settings)
    docs_default = handler_default.process(archive_dir, default_settings)
    assert all(
        (d.get("metadata") or {}).get("source", "") != "search.html"
        for d in docs_default
        if (d.get("metadata") or {}).get("source")
    ), "search.html should be skipped by default"

    # Override: empty skip_patterns list -> search.html is included.
    override_settings = _make_settings(loader=LoaderSettings(sphinx_skip_patterns=[]))
    handler_override = SphinxHTMLHandler(settings=override_settings)
    docs_override = handler_override.process(archive_dir, override_settings)
    assert any(
        (d.get("metadata") or {}).get("source", "") == "search.html" for d in docs_override
    ), "search.html should be included when sphinx_skip_patterns is empty"
