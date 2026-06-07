# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests that idempotent handlers register with retry_on_crash=True.

The queue reconciler consults HandlerSpec.retry_on_crash when deciding
whether to requeue an abandoned task or fail it with
error_type="worker_crashed". Non-idempotent handlers default to False
(failing loudly is safer than silently duplicating work). The five
source-processing handlers are idempotent, so they set True and
crashed tasks resume instead of dying.
"""

from chaoscypher_core.constants import (
    OP_EXTRACT_CHUNK,
    OP_FINALIZE_EXTRACTION,
    OP_IMPORT_ANALYSIS,
    OP_IMPORT_COMMIT,
    OP_INDEX_DOCUMENT,
)
from chaoscypher_core.queue.handler_spec import HandlerSpec


def test_idempotent_import_handlers_opt_into_retry() -> None:
    """Import handler specs all have retry_on_crash=True.

    Covers OP_INDEX_DOCUMENT, OP_IMPORT_ANALYSIS, and OP_IMPORT_COMMIT.
    """
    from chaoscypher_neuron.setup.ops_handlers import build_import_handler_specs

    specs = build_import_handler_specs()

    for op in (OP_INDEX_DOCUMENT, OP_IMPORT_ANALYSIS, OP_IMPORT_COMMIT):
        spec = specs.get(op)
        assert spec is not None, f"Missing handler spec for {op}"
        assert isinstance(spec, HandlerSpec)
        assert spec.retry_on_crash is True, (
            f"{op} should have retry_on_crash=True (idempotent handler)"
        )


def test_idempotent_llm_handlers_opt_into_retry() -> None:
    """OP_EXTRACT_CHUNK and OP_FINALIZE_EXTRACTION also have retry_on_crash=True."""
    from chaoscypher_neuron.setup.ops_handlers import build_llm_handler_specs

    specs = build_llm_handler_specs()

    for op in (OP_EXTRACT_CHUNK, OP_FINALIZE_EXTRACTION):
        spec = specs.get(op)
        assert spec is not None, f"Missing handler spec for {op}"
        assert isinstance(spec, HandlerSpec)
        assert spec.retry_on_crash is True, (
            f"{op} should have retry_on_crash=True (idempotent handler)"
        )


def test_import_service_registers_handler_specs_directly() -> None:
    """The ImportOperationsService wires HandlerSpec instances into its operation_handlers.

    This ensures register_handlers() propagates the retry_on_crash flag
    to the queue client.
    """
    from unittest.mock import MagicMock

    from chaoscypher_core.operations.importing.import_service import (
        ImportOperationsService,
    )

    service = ImportOperationsService(
        graph_repository=MagicMock(),
        config_manager=MagicMock(),
        source_manager=MagicMock(),
        trigger_service=MagicMock(),
        llm_service=MagicMock(),
        source_repository=MagicMock(),
        chunking_service=MagicMock(),
        indexing_service=MagicMock(),
    )

    for op in (OP_IMPORT_COMMIT, OP_IMPORT_ANALYSIS, OP_INDEX_DOCUMENT):
        entry = service.operation_handlers[op]
        assert isinstance(entry, HandlerSpec)
        assert entry.retry_on_crash is True


def test_chunk_extraction_service_registers_handler_specs_directly() -> None:
    """ChunkExtractionOperationsService also uses HandlerSpec with retry_on_crash=True.

    Both extract_chunk and finalize_extraction land as HandlerSpec instances.
    """
    from chaoscypher_core.operations.extraction.chunk_extraction_service import (
        ChunkExtractionOperationsService,
    )

    service = ChunkExtractionOperationsService()

    for op in (OP_EXTRACT_CHUNK, OP_FINALIZE_EXTRACTION):
        entry = service.operation_handlers[op]
        assert isinstance(entry, HandlerSpec)
        assert entry.retry_on_crash is True
