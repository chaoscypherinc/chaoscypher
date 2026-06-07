# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for Core-exception raises in source loader modules.

Covers the 9 sites replaced in Task 9 of the core-exception-hygiene sweep:
  - facade.py:64 — empty load result raises ValidationError(field="content")
  - audio_loader.py:125 — ffmpeg timeout raises ExternalServiceError (with cause)
  - audio_loader.py:128 — ffmpeg non-zero exit raises ExternalServiceError
  - video_loader.py:124 — ffmpeg timeout raises ExternalServiceError (with cause)
  - video_loader.py:127 — ffmpeg non-zero exit raises ExternalServiceError
  - archive_loader.py:137 — no handler claimed raises OperationError
  - registry.py:102 — missing settings raises ValidationError
  - registry.py:420 — unsupported extension raises ValidationError(field="extension")
  - archive/handlers/openapi_handler.py:241 — missing PyYAML raises OperationError

Each test asserts:
  - The correct ChaosCypherException subclass is raised (not bare stdlib).
  - The exception carries meaningful attributes (message, code, field/operation/service).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chaoscypher_core.exceptions import (
    ExternalServiceError,
    NotFoundError,
    OperationError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# facade.Loaders.load_text
# ---------------------------------------------------------------------------


class TestLoadersFacadeExceptions:
    """facade.py:64 — empty document list raises ValidationError."""

    def test_empty_documents_raises_validation_error(self, tmp_path: Path) -> None:
        """Empty loader result → ValidationError (not ValueError)."""
        from chaoscypher_core.services.sources.loaders.facade import Loaders

        # Create a real file so FileNotFoundError is not raised first
        dummy = tmp_path / "test.pdf"
        dummy.write_bytes(b"%PDF-1.4 stub")

        fake_registry = MagicMock()
        fake_registry.load_document.return_value = []  # empty result

        with (
            patch(
                "chaoscypher_core.services.sources.loaders.facade.get_loader_registry",
                return_value=fake_registry,
            ),
            pytest.raises(ValidationError) as exc_info,
        ):
            Loaders.load_text(str(dummy))

        err = exc_info.value
        assert err.code == "VALIDATION_ERROR"
        assert str(dummy) in err.message or "No content loaded" in err.message
        assert err.details.get("field") == "content"


# ---------------------------------------------------------------------------
# AudioLoader._convert_to_wav
# ---------------------------------------------------------------------------


class TestAudioLoaderExceptions:
    """audio_loader.py — ffmpeg failures raise ExternalServiceError."""

    def _make_loader(self) -> Any:
        from chaoscypher_core.services.sources.loaders.audio_loader import AudioLoader

        return AudioLoader(settings=None)

    def test_ffmpeg_timeout_raises_external_service_error(self) -> None:
        """audio_loader.py:125 — TimeoutExpired → ExternalServiceError with cause."""
        loader = self._make_loader()

        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 600)),
            pytest.raises(ExternalServiceError) as exc_info,
        ):
            loader._convert_to_wav("/fake/input.mp3", "/fake/output.wav")

        err = exc_info.value
        assert err.code == "EXTERNAL_SERVICE_ERROR"
        assert err.details.get("service") == "ffmpeg"
        assert "timed out" in err.message
        assert exc_info.value.__cause__ is not None

    def test_ffmpeg_nonzero_exit_raises_external_service_error(self) -> None:
        """audio_loader.py:128 — non-zero returncode → ExternalServiceError."""
        loader = self._make_loader()

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = "invalid codec"

        with (
            patch("subprocess.run", return_value=fake_result),
            pytest.raises(ExternalServiceError) as exc_info,
        ):
            loader._convert_to_wav("/fake/input.mp3", "/fake/output.wav")

        err = exc_info.value
        assert err.code == "EXTERNAL_SERVICE_ERROR"
        assert err.details.get("service") == "ffmpeg"
        assert "failed" in err.message


# ---------------------------------------------------------------------------
# VideoLoader._extract_audio
# ---------------------------------------------------------------------------


class TestVideoLoaderExceptions:
    """video_loader.py — ffmpeg failures raise ExternalServiceError."""

    def _make_loader(self) -> Any:
        from chaoscypher_core.services.sources.loaders.video_loader import VideoLoader

        return VideoLoader(settings=None)

    def test_ffmpeg_timeout_raises_external_service_error(self) -> None:
        """video_loader.py:124 — TimeoutExpired → ExternalServiceError with cause."""
        loader = self._make_loader()

        with (
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 600)),
            pytest.raises(ExternalServiceError) as exc_info,
        ):
            loader._extract_audio("/fake/video.mp4", "/fake/audio.wav")

        err = exc_info.value
        assert err.code == "EXTERNAL_SERVICE_ERROR"
        assert err.details.get("service") == "ffmpeg"
        assert "timed out" in err.message
        assert exc_info.value.__cause__ is not None

    def test_ffmpeg_nonzero_exit_raises_external_service_error(self) -> None:
        """video_loader.py:127 — non-zero returncode → ExternalServiceError."""
        loader = self._make_loader()

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stderr = "no audio stream"

        with (
            patch("subprocess.run", return_value=fake_result),
            pytest.raises(ExternalServiceError) as exc_info,
        ):
            loader._extract_audio("/fake/video.mp4", "/fake/audio.wav")

        err = exc_info.value
        assert err.code == "EXTERNAL_SERVICE_ERROR"
        assert err.details.get("service") == "ffmpeg"
        assert "failed" in err.message


# ---------------------------------------------------------------------------
# ArchiveLoader.load_document — no handler claimed
# ---------------------------------------------------------------------------


class TestArchiveLoaderExceptions:
    """archive_loader.py:137 — empty handler registry raises OperationError."""

    def test_no_handler_raises_operation_error(self, tmp_path: Path) -> None:
        """When find_handler returns None, OperationError is raised."""
        from chaoscypher_core.services.sources.loaders.archive_loader import ArchiveLoader
        from chaoscypher_core.settings import EngineSettings, PathSettings

        settings = EngineSettings(paths=PathSettings(data_dir=str(tmp_path)))

        # Create a real zip file so extraction can succeed
        import zipfile

        archive_path = tmp_path / "test.zip"
        with zipfile.ZipFile(archive_path, "w") as zf:
            zf.writestr("readme.txt", "hello")

        loader = ArchiveLoader(settings=settings)

        # Patch find_handler to return None (empty registry scenario)
        with (
            patch.object(loader._handler_registry, "find_handler", return_value=None),
            pytest.raises(OperationError) as exc_info,
        ):
            loader.load_document(str(archive_path))

        err = exc_info.value
        assert err.code == "OPERATION_ERROR"
        assert err.operation == "archive_load"
        assert "No archive handler" in err.message


# ---------------------------------------------------------------------------
# LoaderRegistry — constructor and load_document
# ---------------------------------------------------------------------------


class TestLoaderRegistryExceptions:
    """registry.py — missing settings and unsupported extension raise ValidationError."""

    def test_missing_settings_raises_validation_error(self) -> None:
        """registry.py:102 — None settings → ValidationError(field='settings')."""
        from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry

        with pytest.raises(ValidationError) as exc_info:
            LoaderRegistry(settings=None)

        err = exc_info.value
        assert err.code == "VALIDATION_ERROR"
        assert err.field == "settings"
        assert "settings" in err.message.lower()

    def test_unsupported_extension_raises_validation_error(self, tmp_path: Path) -> None:
        """registry.py:420 — no loader for extension → ValidationError."""
        from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
        from chaoscypher_core.settings import EngineSettings, PathSettings

        settings = EngineSettings(paths=PathSettings(data_dir=str(tmp_path)))

        # Create a real file with an unsupported extension
        weird_file = tmp_path / "document.xyzzy_unsupported"
        weird_file.write_text("data")

        registry = LoaderRegistry(settings=settings)

        with pytest.raises(ValidationError) as exc_info:
            registry.load_document(str(weird_file))

        err = exc_info.value
        assert err.code == "VALIDATION_ERROR"
        assert ".xyzzy_unsupported" in err.message
        assert err.details.get("field") == "extension"


# ---------------------------------------------------------------------------
# OpenAPIHandler._parse_spec — missing PyYAML
# ---------------------------------------------------------------------------


class TestOpenAPIHandlerExceptions:
    """openapi_handler.py:241 — missing PyYAML raises OperationError."""

    def test_missing_pyyaml_raises_operation_error(self, tmp_path: Path) -> None:
        """ImportError for yaml on a YAML spec → OperationError."""
        from chaoscypher_core.services.sources.loaders.archive.handlers.openapi_handler import (
            OpenAPIHandler,
        )

        handler = OpenAPIHandler(settings=None)

        # Write a minimal YAML spec file
        spec_file = tmp_path / "openapi.yaml"
        spec_file.write_text("openapi: '3.0.0'\ninfo:\n  title: Test\n  version: '1.0'\n")

        # Make yaml import fail inside _parse_spec
        import builtins

        original_import = builtins.__import__

        def _block_yaml(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=_block_yaml),
            pytest.raises(OperationError) as exc_info,
        ):
            handler._parse_spec(spec_file)

        err = exc_info.value
        assert err.code == "OPERATION_ERROR"
        assert err.operation == "archive_load"
        assert "PyYAML" in err.message


# ---------------------------------------------------------------------------
# NotFoundError sites (FileNotFoundError sweep)
# ---------------------------------------------------------------------------


class TestLoaderRegistryFileNotFoundExceptions:
    """registry.py — missing file raises NotFoundError."""

    def test_missing_file_raises_not_found_error(self, tmp_path: Path) -> None:
        """registry.py load_document — file absent → NotFoundError."""
        from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
        from chaoscypher_core.settings import EngineSettings, PathSettings

        settings = EngineSettings(paths=PathSettings(data_dir=str(tmp_path)))
        registry = LoaderRegistry(settings=settings)

        with pytest.raises(NotFoundError) as exc_info:
            registry.load_document(str(tmp_path / "ghost.pdf"))

        err = exc_info.value
        assert err.code == "NOT_FOUND"
        assert err.resource_type == "File"
        assert "ghost.pdf" in err.identifier


class TestArchiveLoaderFileNotFoundExceptions:
    """archive_loader.py — missing archive raises NotFoundError."""

    def test_missing_archive_raises_not_found_error(self, tmp_path: Path) -> None:
        """archive_loader.py load_document — archive absent → NotFoundError."""
        from chaoscypher_core.services.sources.loaders.archive_loader import ArchiveLoader
        from chaoscypher_core.settings import EngineSettings, PathSettings

        settings = EngineSettings(paths=PathSettings(data_dir=str(tmp_path)))
        loader = ArchiveLoader(settings=settings)

        with pytest.raises(NotFoundError) as exc_info:
            loader.load_document(str(tmp_path / "ghost.zip"))

        err = exc_info.value
        assert err.code == "NOT_FOUND"
        assert err.resource_type == "Archive"
        assert "ghost.zip" in err.identifier


class TestArchiveExtractorFileNotFoundExceptions:
    """extractor.py — missing archive raises NotFoundError."""

    def test_missing_archive_raises_not_found_error(self, tmp_path: Path) -> None:
        """extractor.py extract — archive absent → NotFoundError."""
        from chaoscypher_core.services.sources.loaders.archive.extractor import ArchiveExtractor
        from chaoscypher_core.settings import EngineSettings, PathSettings

        settings = EngineSettings(paths=PathSettings(data_dir=str(tmp_path)))
        extractor = ArchiveExtractor(settings=settings)
        missing = tmp_path / "ghost.zip"
        dest = tmp_path / "out"

        with pytest.raises(NotFoundError) as exc_info:
            extractor.extract(missing, dest)

        err = exc_info.value
        assert err.code == "NOT_FOUND"
        assert err.resource_type == "Archive"
        assert str(missing) in err.identifier
