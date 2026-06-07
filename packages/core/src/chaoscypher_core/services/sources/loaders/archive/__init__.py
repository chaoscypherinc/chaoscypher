# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Processing Module.

Provides utilities and handlers for extracting and processing documentation
archives (ZIP/TAR.GZ) containing various documentation formats.

Components:
- ArchiveExtractor: Secure extraction of ZIP and TAR.GZ archives
- Handlers: Format-specific processors (Sphinx, Markdown, OpenAPI, Generic)
- ArchiveHandlerRegistry: Plugin-aware handler selection
- Exceptions: Archive-specific error types

Example:
    from chaoscypher_core.services.sources.loaders.archive import (
        ArchiveExtractor,
    )
    from chaoscypher_core.services.sources.loaders.archive.handlers.registry import (
        ArchiveHandlerRegistry,
    )

    # Extract archive
    extractor = ArchiveExtractor(settings=engine_settings)
    extracted_path = extractor.extract(archive_path, temp_dir)

    # Select handler (built-ins + user plugins)
    registry = ArchiveHandlerRegistry(settings=engine_settings)
    handler = registry.find_handler(extracted_path)
    if handler is not None:
        documents = handler.process(extracted_path, engine_settings)
"""

# Core components
from chaoscypher_core.services.sources.loaders.archive.exceptions import (
    ArchiveExtractionError,
    ArchiveLoaderError,
    ArchiveSecurityError,
    FormatDetectionError,
    HandlerError,
    UnsupportedArchiveError,
)
from chaoscypher_core.services.sources.loaders.archive.extractor import (
    ArchiveExtractor,
)

# Handlers
from chaoscypher_core.services.sources.loaders.archive.handlers import (
    ArchiveHandler,
    GenericHandler,
    MarkdownHandler,
    OpenAPIHandler,
    SphinxHTMLHandler,
)


__all__ = [
    "ArchiveExtractionError",
    # Core
    "ArchiveExtractor",
    # Handlers
    "ArchiveHandler",
    # Exceptions
    "ArchiveLoaderError",
    "ArchiveSecurityError",
    "FormatDetectionError",
    "GenericHandler",
    "HandlerError",
    "MarkdownHandler",
    "OpenAPIHandler",
    "SphinxHTMLHandler",
    "UnsupportedArchiveError",
]
