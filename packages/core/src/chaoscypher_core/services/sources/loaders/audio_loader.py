# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Audio Loader using ffmpeg + faster-whisper.

Converts audio files to WAV via ffmpeg, then transcribes to text
using faster-whisper (CTranslate2-based Whisper). Runs on CPU,
no GPU required.

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


class AudioLoader:
    """Audio loader using ffmpeg normalization + faster-whisper transcription.

    Converts audio to WAV via ffmpeg (handles all codec edge cases),
    then transcribes using the Whisper 'base' model via faster-whisper.
    Automatically detects language and concatenates segments into full
    transcript text.

    Requires: ffmpeg system package, faster-whisper
    """

    _model: Any = None

    @property
    def supported_extensions(self) -> list[str]:
        """File extensions this loader supports."""
        return [
            ".mp3",
            ".MP3",
            ".wav",
            ".WAV",
            ".m4a",
            ".M4A",
            ".flac",
            ".FLAC",
            ".ogg",
            ".OGG",
            ".wma",
            ".WMA",
            ".aac",
            ".AAC",
        ]

    def __init__(self, settings: EngineSettings | None = None) -> None:
        """Initialize audio loader.

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
        if AudioLoader._model is None:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]

            logger.info(
                "whisper_model_loading",
                model_size=self._whisper_model_size,
                device=self._whisper_device,
            )
            AudioLoader._model = WhisperModel(self._whisper_model_size, device=self._whisper_device)
            logger.info("whisper_model_loaded")
        return AudioLoader._model

    def _convert_to_wav(self, input_path: str, output_path: str) -> None:
        """Convert audio file to WAV format using ffmpeg.

        Args:
            input_path: Path to input audio file.
            output_path: Path to output WAV file.

        Raises:
            ExternalServiceError: If ffmpeg fails to convert audio.

        """
        # Hard timeout prevents a crafted audio file from wedging the worker
        # forever. The default (600 s / 10 min) covers any legitimate
        # multi-GB audio file. Configurable via
        # ``settings.loader.whisper_timeout_seconds``.
        try:
            result = subprocess.run(  # noqa: S603
                [  # noqa: S607
                    "ffmpeg",
                    "-i",
                    input_path,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-f",
                    "wav",
                    "-y",
                    output_path,
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=self._whisper_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            msg = f"ffmpeg audio conversion timed out ({self._whisper_timeout}s)"
            raise ExternalServiceError(service_name="ffmpeg", reason=msg) from exc
        if result.returncode != 0:
            msg = f"ffmpeg audio conversion failed: {result.stderr}"
            raise ExternalServiceError(service_name="ffmpeg", reason=msg)

    def load_document(self, filepath: str) -> list[dict[str, Any]]:
        """Load audio file, convert to WAV, and transcribe to text.

        Converts audio to WAV via ffmpeg to handle all codec variations,
        then transcribes using faster-whisper.

        Args:
            filepath: Path to audio file.

        Returns:
            List of document chunks with content and metadata.

        """
        start_time = time.time()
        filepath_obj = Path(filepath)

        try:
            logger.info("audio_transcription_started", filepath=filepath)

            # Convert to WAV via ffmpeg to normalize codec
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                temp_wav_path = tmp.name

            try:
                self._convert_to_wav(filepath, temp_wav_path)

                model = self._get_model()
                segments, info = model.transcribe(temp_wav_path)

                # Concatenate all segments into full transcript
                segment_texts = [segment.text.strip() for segment in segments]
                text = " ".join(segment_texts)
            finally:
                Path(temp_wav_path).unlink(missing_ok=True)

            extraction_time = time.time() - start_time

            logger.info(
                "audio_transcription_complete",
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
                "audio_transcription_failed",
                filepath=filepath,
                error_type=type(e).__name__,
                error_message=str(e),
            )
            raise

    def supports_ocr(self) -> bool:
        """Check if this loader supports OCR.

        Returns:
            False - this loader performs audio transcription, not OCR.

        """
        return False


__all__ = ["AudioLoader"]
