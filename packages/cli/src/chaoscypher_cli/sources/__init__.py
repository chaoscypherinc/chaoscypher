# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Processing Module - CLI document processing pipeline.

Provides offline document source processing capabilities for the CLI, enabling:
- Document loading and chunking
- Embedding generation (with local LLM)
- Entity extraction (with local LLM)
- Commit to knowledge graph

This module works entirely offline using SQLite storage,
without requiring Cortex or Neuron backends.

Example:
    from chaoscypher_cli.sources import CLISourceProcessingService
    from chaoscypher_cli.context import get_context

    ctx = get_context()
    service = CLISourceProcessingService(ctx)

    # Step by step
    file_id = service.upload_file(Path("document.pdf"))
    service.index_file(file_id)
    service.extract_entities(file_id)
    service.commit_to_graph(file_id)
"""

from chaoscypher_cli.sources.pipeline import SourcePipeline
from chaoscypher_cli.sources.service import CLISourceProcessingService


__all__ = [
    "CLISourceProcessingService",
    "SourcePipeline",
]
