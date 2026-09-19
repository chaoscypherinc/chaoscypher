# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Document Loaders - Extensible Plugin Architecture.

Auto-discovers and registers document loaders for various file formats.
Users can add custom loaders by creating a new *_loader.py file in this directory.

Built-in Loaders:
    - PdfLoader: PDF text extraction via pypdf (.pdf)
    - TextLoader: Plain text files (.txt, .md, .log)
    - CSVLoader: CSV files (.csv)
    - JSONLoader: JSON files (.json, .jsonl, .ndjson)
    - HTMLLoader: Standalone HTML pages (.html, .htm, .xhtml)
    - RSTLoader: reStructuredText (.rst, .rest)
    - DOCXLoader: Microsoft Word documents (.docx)
    - XLSXLoader: Microsoft Excel workbooks (.xlsx, .xlsm)
    - PPTXLoader: Microsoft PowerPoint decks (.pptx)
    - EPUBLoader: EPUB e-books (.epub)
    - ArchiveLoader: Documentation archives (.zip, .tar.gz, .tgz)
    - ImageLoader: Image metadata via Pillow; description deferred to the
      vision LLM (.jpg, .png, .gif, .webp, .tiff, .bmp)
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
from chaoscypher_core.services.sources.loaders.docx_loader import DOCXLoader
from chaoscypher_core.services.sources.loaders.epub_loader import EPUBLoader
from chaoscypher_core.services.sources.loaders.factory import get_loader_registry
from chaoscypher_core.services.sources.loaders.html_loader import HTMLLoader
from chaoscypher_core.services.sources.loaders.image_loader import ImageLoader
from chaoscypher_core.services.sources.loaders.json_loader import JSONLoader
from chaoscypher_core.services.sources.loaders.pdf_loader import PdfLoader
from chaoscypher_core.services.sources.loaders.pptx_loader import PPTXLoader
from chaoscypher_core.services.sources.loaders.registry import LoaderRegistry
from chaoscypher_core.services.sources.loaders.rst_loader import RSTLoader
from chaoscypher_core.services.sources.loaders.text_loader import TextLoader
from chaoscypher_core.services.sources.loaders.video_loader import VideoLoader
from chaoscypher_core.services.sources.loaders.xlsx_loader import XLSXLoader


__all__ = [
    "ArchiveLoader",
    "AudioLoader",
    "BaseLoader",
    "CSVLoader",
    "DOCXLoader",
    "EPUBLoader",
    "HTMLLoader",
    "ImageLoader",
    "JSONLoader",
    "LoaderRegistry",
    "PPTXLoader",
    "PdfLoader",
    "RSTLoader",
    "TextLoader",
    "VideoLoader",
    "XLSXLoader",
    "get_loader_registry",
]
