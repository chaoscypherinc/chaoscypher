# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Archive Handlers - Documentation Format Processors.

Provides specialized handlers for different documentation formats found in archives:
- SphinxHTMLHandler: Sphinx/ReadTheDocs HTML documentation
- MarkdownHandler: Markdown documentation (MkDocs, Docusaurus, etc.)
- OpenAPIHandler: OpenAPI/Swagger API specifications
- GenericHandler: Fallback for mixed archives (uses LoaderRegistry)

Each handler implements the ArchiveHandler protocol and can detect whether
it's appropriate for a given extracted archive based on file structure.

Example:
    from chaoscypher_core.services.sources.loaders.archive.handlers import (
        SphinxHTMLHandler,
        MarkdownHandler,
    )

    handler = SphinxHTMLHandler(settings)
    score = handler.can_handle(extracted_dir)
    if score > 0:
        documents = handler.process(extracted_dir, settings)
"""

# Infrastructure
from chaoscypher_core.services.sources.loaders.archive.handlers.base import (
    ArchiveHandler,
)

# Built-in handlers
from chaoscypher_core.services.sources.loaders.archive.handlers.generic_handler import (
    GenericHandler,
)
from chaoscypher_core.services.sources.loaders.archive.handlers.markdown_handler import (
    MarkdownHandler,
)
from chaoscypher_core.services.sources.loaders.archive.handlers.openapi_handler import (
    OpenAPIHandler,
)
from chaoscypher_core.services.sources.loaders.archive.handlers.sphinx_handler import (
    SphinxHTMLHandler,
)


__all__ = [
    # Infrastructure
    "ArchiveHandler",
    # Built-in handlers
    "GenericHandler",
    "MarkdownHandler",
    "OpenAPIHandler",
    "SphinxHTMLHandler",
]
