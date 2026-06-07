# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Document Loaders - Extensible Plugin Architecture.

Auto-discovers and registers document loaders for various file formats.
Users can add custom loaders by creating a new *_loader.py file in this directory.

Built-in Loaders:
    - PdfLoader: PDF text extraction via pypdf (.pdf)
    - TextLoader: Plain text files (.txt, .md, .log)
    - CSVLoader: CSV files (.csv)
    - JSONLoader: JSON files (.json, .jsonl)
    - ArchiveLoader: Documentation archives (.zip, .tar.gz, .tgz)
    - ImageLoader: Image OCR via Tesseract (.jpg, .png, .gif, .webp, .tiff, .bmp)
    - AudioLoader: Audio transcription via faster-whisper (.mp3, .wav, .m4a, .flac, .ogg)
    - VideoLoader: Video audio transcription via ffmpeg + faster-whisper (.mp4, .mkv, .avi, .mov)

Usage:
    ```python
    from chaoscypher_core.services.sources.loaders import LoaderRegistry

    # Create registry (auto-discovers all loaders)
    registry = LoaderRegistry(settings)

    # Load a document (raw; chunking is ChunkingService's responsibility)
    docs = registry.load_document('/path/to/file.pdf')

    # List supported extensions
    extensions = registry.list_supported_extensions()
    ```

Adding Custom Loaders:
    1. Create a new file in this directory (e.g., excel_loader.py)
    2. Define a loader class with:
       - supported_extensions property (list of extensions)
       - __init__(self, settings=None) method
       - load_document(self, filepath) method (per-loader protocol)
    3. The loader will be automatically discovered and registered
"""

# Infrastructure
# Built-in loaders
from chaoscypher_core.services.sources.loaders.archive_loader import ArchiveLoader
from chaoscypher_core.services.sources.loaders.audio_loader import AudioLoader
from chaoscypher_core.services.sources.loaders.base import BaseLoader
from chaoscypher_core.services.sources.loaders.csv_loader import CSVLoader
from chaoscypher_core.services.sources.loaders.factory import get_loader_registry
from chaoscypher_core.services.sources.loaders.image_loader import ImageLoader
from chaoscypher_core.services.sources.loaders.json_loader import JSONLoader
from chaoscypher_core.services.sources.loaders.pdf_loader import PdfLoader
from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
from chaoscypher_core.services.sources.loaders.text_loader import TextLoader
from chaoscypher_core.services.sources.loaders.video_loader import VideoLoader


__all__ = [
    "ArchiveLoader",
    "AudioLoader",
    "BaseLoader",
    "CSVLoader",
    "ImageLoader",
    "JSONLoader",
    "LoaderRegistry",
    "PdfLoader",
    "TextLoader",
    "VideoLoader",
    "get_loader_registry",
]
