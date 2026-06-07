# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Importing sub-package for import operations.

Provides the ``ImportOperationsService`` orchestrator and its supporting
handler modules for format-specific imports and document indexing.

Components:
- ImportOperationsService: Main orchestrator for all import operations
- format_handler: CCX and Lexicon package import handlers
- indexing_handler: Document indexing pipeline (chunking + embeddings)

Example:
    from chaoscypher_core.operations.importing import ImportOperationsService

"""

from chaoscypher_core.operations.importing.confirmation_gate import (
    confirm_extraction,
    gate_decision,
    park_for_confirmation,
)
from chaoscypher_core.operations.importing.import_service import (
    ImportOperationsService,
)


__all__ = [
    "ImportOperationsService",
    "confirm_extraction",
    "gate_decision",
    "park_for_confirmation",
]
