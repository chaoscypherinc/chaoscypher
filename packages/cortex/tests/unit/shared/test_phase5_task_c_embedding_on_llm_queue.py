# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 5 Task C: embedding stage split off onto QUEUE_LLM.

Contract tests asserting that the LLM-bound chunk-embedding stage has
been extracted from ``OP_INDEX_DOCUMENT`` (on ``QUEUE_OPERATIONS``) into
a dedicated ``OP_EMBED_CHUNKS`` handler registered on ``QUEUE_LLM``.

These tests are AST-based so they do not need to import the Cortex
runtime package — the sibling-worktree editable install in this
environment intercepts ``chaoscypher_core`` imports and breaks runtime
collection, as documented in Phase 3/4 retrospectives and in the pattern
established by ``test_phase5_task_b_priority_zpopmax.py``.

PR 2 (2026-05-13, Task 12) closed the carve-out: vision processing has
moved off the ops-queue indexing path entirely. ``OP_INDEX_DOCUMENT``
no longer issues vision LLM calls — the handler creates vision_jobs +
N pending vision_page_descriptions rows, flips the source to
``vision_pending``, and enqueues ``OP_VISION_PAGE`` x N on
``QUEUE_LLM``. The contract test that previously allow-listed
``vision_service.describe_images(`` now asserts that the indexing
handler does NOT issue any vision LLM call (the rewire is final).
"""

from __future__ import annotations

import ast
from pathlib import Path


# Phase 5 Task C landed by relocating constants + import operations from
# Cortex into Core. The QUEUE_LLM split is unchanged; only the import paths
# shifted.
REPO_ROOT = Path(__file__).resolve().parents[4]
CORE_SRC = REPO_ROOT / "core" / "src" / "chaoscypher_core"
CONSTANTS_MODULE = CORE_SRC / "constants.py"
QUEUE_UTILS_MODULE = CORE_SRC / "operations" / "queue_utils.py"
IMPORTING_DIR = CORE_SRC / "operations" / "importing"
INDEXING_HANDLER_MODULE = IMPORTING_DIR / "indexing_handler.py"
EMBEDDING_HANDLER_MODULE = IMPORTING_DIR / "embedding_handler.py"
IMPORT_SERVICE_MODULE = IMPORTING_DIR / "import_service.py"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse(path: Path) -> ast.Module:
    return ast.parse(_source(path))


def _find_toplevel(tree: ast.Module, name: str) -> ast.AST | None:
    """Return the toplevel def/assign named ``name`` (or None)."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return node
    return None


def _assign_str_constant(tree: ast.Module, name: str) -> str | None:
    """Return the string value of ``NAME = "..."`` at module top level."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if node.targets[0].id != name:
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return node.value.value
    return None


def test_op_embed_chunks_constant_defined() -> None:
    """OP_EMBED_CHUNKS = "embed_chunks" exists in shared/constants.py."""
    tree = _parse(CONSTANTS_MODULE)
    value = _assign_str_constant(tree, "OP_EMBED_CHUNKS")
    assert value == "embed_chunks", (
        'shared/constants.py must define OP_EMBED_CHUNKS = "embed_chunks" '
        "so the embedding handler and its enqueue helper agree on the "
        "operation name used by the queue."
    )


def test_queue_embed_chunks_helper_exists_and_targets_llm_queue() -> None:
    """queue_embed_chunks(...) in queue_utils.py enqueues on QUEUE_LLM."""
    src = _source(QUEUE_UTILS_MODULE)
    tree = _parse(QUEUE_UTILS_MODULE)

    helper = _find_toplevel(tree, "queue_embed_chunks")
    assert isinstance(helper, ast.AsyncFunctionDef), (
        "queue_utils.py must define an async def queue_embed_chunks(...) helper — "
        "call sites rely on it to hand embedding work to the LLM queue."
    )

    # The body must enqueue on QUEUE_LLM with OP_EMBED_CHUNKS. We look
    # for the literal keyword arguments rather than matching the full
    # call node so the assertion survives formatting changes.
    assert "queue=QUEUE_LLM" in src, (
        "queue_embed_chunks must target QUEUE_LLM (not QUEUE_OPERATIONS). "
        "The embedding stage is the LLM-bound tail of the indexing pipeline."
    )
    assert "operation=OP_EMBED_CHUNKS" in src, (
        "queue_embed_chunks must name the OP_EMBED_CHUNKS operation."
    )


def test_handle_embed_chunks_handler_exists() -> None:
    """embedding_handler.py defines async handle_embed_chunks(...)."""
    assert EMBEDDING_HANDLER_MODULE.exists(), (
        f"Expected embedding handler module at {EMBEDDING_HANDLER_MODULE} — "
        "Phase 5 Task C splits the LLM-bound embedding stage into its own module."
    )
    tree = _parse(EMBEDDING_HANDLER_MODULE)
    handler = _find_toplevel(tree, "handle_embed_chunks")
    assert isinstance(handler, ast.AsyncFunctionDef), (
        "embedding_handler.py must define async def handle_embed_chunks(...). "
        "ImportOperationsService._embed_chunks_handler delegates to it."
    )


def test_embed_unembedded_chunks_moved_to_embedding_handler() -> None:
    """_embed_unembedded_chunks lives in embedding_handler.py, not indexing_handler.py."""
    embed_tree = _parse(EMBEDDING_HANDLER_MODULE)
    assert isinstance(
        _find_toplevel(embed_tree, "_embed_unembedded_chunks"),
        ast.AsyncFunctionDef,
    ), (
        "_embed_unembedded_chunks must live in embedding_handler.py so the "
        "resume helper travels with the queue handler that invokes it."
    )

    index_tree = _parse(INDEXING_HANDLER_MODULE)
    assert _find_toplevel(index_tree, "_embed_unembedded_chunks") is None, (
        "_embed_unembedded_chunks must be removed from indexing_handler.py "
        "after the Task C split — keeping a stale copy would silently "
        "double the truth."
    )


def test_indexing_handler_no_longer_calls_embed_chunks() -> None:
    """indexing_handler.py must not call indexing_service.embed_chunks(...)."""
    src = _source(INDEXING_HANDLER_MODULE)
    assert "indexing_service.embed_chunks(" not in src, (
        "indexing_handler.py must not invoke indexing_service.embed_chunks — "
        "the LLM-bound embedding stage moved to OP_EMBED_CHUNKS on QUEUE_LLM. "
        "See embedding_handler.handle_embed_chunks."
    )
    # Also assert the helper isn't called via its bare name in this module.
    assert "_embed_unembedded_chunks(" not in src, (
        "indexing_handler.py must not call _embed_unembedded_chunks(...) "
        "after the split — the only caller should be handle_embed_chunks "
        "in embedding_handler.py."
    )


def test_indexing_handler_enqueues_embed_chunks() -> None:
    """indexing_handler.py enqueues OP_EMBED_CHUNKS via queue_embed_chunks."""
    src = _source(INDEXING_HANDLER_MODULE)
    assert "queue_embed_chunks(" in src, (
        "indexing_handler.py must enqueue the embedding stage via "
        "queue_embed_chunks(...) after persisting chunks — that's the "
        "hand-off from QUEUE_OPERATIONS to QUEUE_LLM."
    )


def test_vision_llm_call_is_absent_from_indexing_handler() -> None:
    """Indexing handler issues no vision LLM calls.

    PR 2 (2026-05-13, Task 12) closed the carve-out — vision moved to a
    dedicated per-page handler on QUEUE_LLM via OP_VISION_PAGE plus a
    finalizer on QUEUE_OPERATIONS (OP_VISION_FINALIZE). The legacy
    ``describe_images`` batch helper is gone. The indexing handler's
    ``_apply_vision_processing`` now only enqueues per-page tasks and
    returns; the LLM call lives in ``vision_page_handler.py``.
    """
    src = _source(INDEXING_HANDLER_MODULE)
    # The batched helper must be gone.
    assert "describe_images(" not in src, (
        "vision_service.describe_images(...) must not appear in "
        "indexing_handler.py — Task 12 moved vision LLM calls to the "
        "per-page handler on QUEUE_LLM."
    )
    # Sanity: even the single-page helper should not be called from
    # indexing_handler — the per-page handler is the only call site.
    assert ".describe_image(" not in src, (
        "vision_service.describe_image(...) must not appear in "
        "indexing_handler.py — only vision_page_handler.py issues the "
        "per-page LLM call."
    )
    # The module docstring must document the move so future readers
    # understand where vision lives now.
    module_doc = ast.get_docstring(_parse(INDEXING_HANDLER_MODULE)) or ""
    assert "vision" in module_doc.lower(), (
        "indexing_handler.py module docstring must explain how vision "
        "is now driven by the per-page handler + finalizer."
    )


def test_embed_chunks_registered_on_llm_queue() -> None:
    """import_service.py registers the embed-chunks handler on QUEUE_LLM.

    Looks for a ``queue_client.register_handlers(QUEUE_LLM, ...)`` call in
    the AST of ``import_service.py``. This is stricter than a substring
    search because it forces the registration to be a real call rather
    than e.g. a comment or a docstring mention.
    """
    tree = _parse(IMPORT_SERVICE_MODULE)

    found_llm_registration = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match `queue_client.register_handlers(...)`
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "register_handlers"
            and isinstance(func.value, ast.Name)
            and func.value.id == "queue_client"
        ):
            continue
        if not node.args:
            continue
        first = node.args[0]
        # Accept either the bare name QUEUE_LLM or the attribute form.
        if isinstance(first, ast.Name) and first.id == "QUEUE_LLM":
            found_llm_registration = True
            break

    assert found_llm_registration, (
        "import_service.py must call queue_client.register_handlers(QUEUE_LLM, ...) — "
        "without it, OP_EMBED_CHUNKS has no registered handler and enqueued "
        "tasks will fail with 'no handler' on the LLM queue."
    )


def test_llm_operation_handlers_contains_embed_chunks_spec() -> None:
    """The llm_operation_handlers dict maps OP_EMBED_CHUNKS to a HandlerSpec."""
    src = _source(IMPORT_SERVICE_MODULE)
    # These three substrings together assert the shape of the registration
    # without having to reconstruct the full dict via AST.
    assert "self.llm_operation_handlers" in src, (
        "ImportOperationsService must expose an llm_operation_handlers dict "
        "(separate from operation_handlers) so the LLM-queue registration "
        "is visible at the composition root."
    )
    assert "OP_EMBED_CHUNKS" in src, "OP_EMBED_CHUNKS must be imported and used"
    assert "_embed_chunks_handler" in src, (
        "A dedicated _embed_chunks_handler dispatcher must exist on "
        "ImportOperationsService — symmetric to _index_document_handler."
    )
    # Retry-on-crash is part of the contract: OP_EMBED_CHUNKS is
    # idempotent via the embedded_at checkpoint, so the reconciler can
    # safely re-dispatch abandoned tasks rather than failing them.
    assert "retry_on_crash=True" in src, (
        "The OP_EMBED_CHUNKS HandlerSpec must set retry_on_crash=True — "
        "the operation is idempotent (embedded_at is the resume checkpoint) "
        "so the reconciler should requeue abandoned tasks."
    )
