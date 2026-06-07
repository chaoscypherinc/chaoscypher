# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Import Loaders - Content-type specific loaders for CCX import.

Each loader handles importing a specific type of content from the CCX package:
- TemplateLoader: Imports templates from templates.jsonld
- KnowledgeLoader: Imports knowledge nodes and edges from knowledge.jsonld
- WorkflowLoader: Imports workflow nodes, edges, and triggers from workflows.jsonld
- SourceLoader: Imports sources, chunks, citations, and tags from sources.jsonl

Example:
    from chaoscypher_core.services.package.importer.loaders import (
        TemplateLoader,
        KnowledgeLoader,
        SourceLoader,
    )

    template_loader = TemplateLoader(graph_repository)
    knowledge_loader = KnowledgeLoader(graph_repository)
"""

from chaoscypher_core.services.package.importer.loaders.base import PackageLoaderBase
from chaoscypher_core.services.package.importer.loaders.knowledge import KnowledgeLoader
from chaoscypher_core.services.package.importer.loaders.sources import SourceLoader
from chaoscypher_core.services.package.importer.loaders.templates import TemplateLoader
from chaoscypher_core.services.package.importer.loaders.workflows import WorkflowLoader


__all__ = [
    "KnowledgeLoader",
    "PackageLoaderBase",
    "SourceLoader",
    "TemplateLoader",
    "WorkflowLoader",
]
