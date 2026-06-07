# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Video Loader using ffmpeg + faster-whisper.

Extracts audio from video files using ffmpeg, then transcribes
to text using faster-whisper. Runs on CPU, no GPU required.

Implements BaseLoader protocol for auto-discovery by LoaderRegistry.

Requires system package: ffmpeg
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from chaoscypher_core.exceptions import ExternalServiceError


if TYPE_CHECKING:
    from chaoscypher_core.settings import EngineSettings


logger = structlog.get_logger(__name__)


class VideoLoader:
    """Video loader using ffmpeg audio extraction + faster-whisper transcription.

    Extracts the audio track from video files via ffmpeg, then transcribes
    using the Whisper 'base' model via faster-whisper. Automatically detects
    language and concatenates segments into full transcript text.

    Requires: ffmpeg system package, faster-whisper
    """

    _model: Any = None

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [
            ".mp4",
            ".MP4",
            ".mkv",
            ".MKV",
            ".avi",
            ".AVI",
            ".mov",
            ".MOV",
            ".webm",
            ".WEBM",
            ".wmv",
            ".WMV",
            ".flv",
            ".FLV",
        ]

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize video loader.

        Args:
            settings: Settings instance (controls Whisper model/device/timeout).

        """
        self.settings = settings
        # Whisper model size and device are configurable via
        # ``settings.loader.whisper_model_size`` / ``settings.loader.whisper_device``.
        # Defaults match the previous hardcoded values so behaviour is unchanged
        # when no settings are supplied.
        self._whisper_model_size: str = (
            settings.loader.whisper_model_size if settings is not None else "base"
        )
        self._whisper_device: str = (
            settings.loader.whisper_device if settings is not None else "cpu"
        )
        self._whisper_timeout: int = (
            settings.loader.whisper_timeout_seconds if settings is not None else 600
        )

    def _get_model(self) -> Any:
        """Get or lazily initialize the Whisper model.

        The model is cached at the class level after first load; the
        model size and device come from constructor-resolved settings so
        the cache is populated with whatever the caller configured.

        Returns:
            WhisperModel instance (cached after first call).

        """
        if VideoLoader._model is None:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]

            logger.info(
                "whisper_model_loading",
                model_size=self._whisper_model_size,
                device=self._whisper_device,
            )
            VideoLoader._model = WhisperModel(self._whisper_model_size, device=self._whisper_device)
            logger.info("whisper_model_loaded")
        return VideoLoader._model

    def _extract_audio(self, video_path: str, audio_path: str) -> None:
        """Extract audio track from video file using ffmpeg.

        Args:
            video_path: Path to input video file.
            audio_path: Path to output WAV audio file.

        Raises:
            ExternalServiceError: If ffmpeg fails to extract audio.

        """
        # Hard timeout prevents a crafted video (adversarial container,
        # infinite-duration stream) from wedging the worker forever. The
        # default (600 s / 10 min) is generous enough for any legitimate
        # multi-GB media file. Configurable via
        # ``settings.loader.whisper_timeout_seconds``.
        try:
            result = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "ffmpeg",
                    "-i",
                    video_path,
                    "-q:a",
                    "0",
                    "-map",
                    "a",
                    "-f",
                    "wav",
                    "-y",
                    audio_path,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._whisper_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"ffmpeg audio extraction timed out ({self._whisper_timeout}s)"
            raise ExternalServiceError(service_name="ffmpeg", reason=msg) from exc
        if result.returncode != 0:
            msg = f"ffmpeg audio extraction failed: {result.stderr}"
            raise ExternalServiceError(service_name="ffmpeg", reason=msg)

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load video file, extract audio, and transcribe to text.

        Extracts audio via ffmpeg to a temp WAV file, transcribes using
        faster-whisper, then cleans up the temp file.

        Args:
            filepath: Path to video file.

        Returns:
            List of document chunks with content and metadata.

        """
        start_time = time.time()
        filepath_obj = Path(filepath)

        try:
            logger.info("video_transcription_started", filepath=filepath)

            # Extract audio to temporary WAV file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                temp_audio_path = tmp.name

            try:
                self._extract_audio(filepath, temp_audio_path)

                # Transcribe extracted audio
                model = self._get_model()
                segments, info = model.transcribe(temp_audio_path)

                # Concatenate all segments into full transcript
                segment_texts = [segment.text.strip() for segment in segments]
                text = " ".join(segment_texts)
            finally:
                # Clean up temp audio file
                Path(temp_audio_path).unlink(missing_ok=True)

            extraction_time = time.time() - start_time

            logger.info(
                "video_transcription_complete",
                character_count=len(text),
                segment_count=len(segment_texts),
                language=info.language,
                duration_seconds=round(info.duration, 1),
                extraction_time_seconds=round(extraction_time, 2),
            )

            metadata: dict[str, Any] = {
                "source": str(filepath_obj.absolute()),
                "filename": filepath_obj.name,
                "duration": round(info.duration, 1),
                "language": info.language,
                "segment_count": len(segment_texts),
                "total_characters": len(text),
                "extraction_method": "ffmpeg_faster_whisper",
                "extraction_time_seconds": round(extraction_time, 3),
            }

            return [{"content": text, "metadata": metadata}]

        except Exception as e:
            logger.exception(
                "video_transcription_failed",
                filepath=filepath,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def supports_ocr(self) -> bool:
        """Check if this loader supports OCR.

        Returns:
            False - this loader performs video audio transcription, not OCR.

        """
        return False


__all__ = ["VideoLoader"]
