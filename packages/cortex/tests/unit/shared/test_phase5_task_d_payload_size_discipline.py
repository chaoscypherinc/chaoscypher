# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 5 Task D: OP_EXTRACT_CHUNK + OP_IMPORT_COMMIT payload discipline.

Contract tests asserting that the two largest queue payloads have been
shrunk to IDs-only:

1. ``queue_extract_chunk`` no longer packs ``chunk_content`` into the
   queue message (handler rehydrates from DB via
   ``adapter.get_chunks_by_ids``).
2. ``queue_import_commit`` no longer packs ``commit_data`` into the
   queue message (handler rehydrates from the ``SourceRow.commit_payload``
   column).

These tests are AST-based so they do not need to import the Cortex
runtime package — the sibling-worktree editable install in this
environment intercepts ``chaoscypher_core`` imports and breaks runtime
collection, as documented in Phase 3/4 retrospectives and in the pattern
established by ``test_phase5_task_b_priority_zpopmax.py`` and
``test_phase5_task_c_embedding_on_llm_queue.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path


# Phase 5 Task D landed by relocating the OP_EXTRACT_CHUNK and
# OP_IMPORT_COMMIT operation services from Cortex into Core. The payload
# discipline (IDs-only, DB-backed rehydrate) is unchanged; only the import
# paths shifted.
REPO_ROOT = Path(__file__).resolve().parents[5]
CORE_SRC = REPO_ROOT / "packages" / "core" / "src" / "chaoscypher_core"
CHUNK_EXTRACTION_MODULE = CORE_SRC / "operations" / "extraction" / "chunk_extraction_service.py"
QUEUE_UTILS_MODULE = CORE_SRC / "operations" / "queue_utils.py"
IMPORT_SERVICE_MODULE = CORE_SRC / "operations" / "importing" / "import_service.py"
SOURCES_MIXIN_MODULE = CORE_SRC / "adapters" / "sqlite" / "mixins" / "sources.py"
SOURCES_CHUNKS_MIXIN_MODULE = CORE_SRC / "adapters" / "sqlite" / "mixins" / "sources_chunks.py"
MODELS_MODULE = CORE_SRC / "adapters" / "sqlite" / "models.py"


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


def _find_method(tree: ast.Module, class_name: str, method_name: str) -> ast.AST | None:
    """Return the method named ``method_name`` on class ``class_name`` (or None)."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    and item.name == method_name
                ):
                    return item
    return None


def _find_any_method(tree: ast.Module, method_name: str) -> ast.AST | None:
    """Return the first method named ``method_name`` on any class (or None)."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if (
                    isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef)
                    and item.name == method_name
                ):
                    return item
    return None


def _find_class_field(tree: ast.Module, class_name: str, field_name: str) -> ast.AST | None:
    """Return the AnnAssign node for ``class.field`` (or None)."""
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if item.target.id == field_name:
                        return item
    return None


def _enqueue_data_keys_in_function(func: ast.AST) -> set[str]:
    """Collect the set of dict keys passed as the ``data=`` kwarg of any
    ``enqueue_task``/``enqueue_tasks_batch`` call inside ``func``.

    This walks the AST of the function body, finds every Call whose
    function attribute is ``enqueue_task`` (or whose ``data`` kwarg
    literal is a Dict), and unions every string key seen. Batch specs
    that build a dict literal under the key ``"data"`` inside a spec
    dict are also detected — the batch path in
    import_service._queue_extraction_chunks builds specs via
    ``{"operation": ..., "data": {...}}``.
    """
    keys: set[str] = set()
    for node in ast.walk(func):
        # Direct: foo(..., data={"a": 1, ...})
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "data" and isinstance(kw.value, ast.Dict):
                    for k in kw.value.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
        # Batch spec: {"operation": ..., "data": {...}}
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=False):
                if isinstance(k, ast.Constant) and k.value == "data" and isinstance(v, ast.Dict):
                    for inner_k in v.keys:
                        if isinstance(inner_k, ast.Constant) and isinstance(inner_k.value, str):
                            keys.add(inner_k.value)
    return keys


def test_queue_extract_chunk_payload_drops_chunk_content() -> None:
    """queue_extract_chunk's enqueue data dict must not carry chunk_content.

    Phase 5 Task D shrinks OP_EXTRACT_CHUNK to IDs-only; the handler
    rehydrates chunk text from DocumentChunk via get_chunks_by_ids.
    Reintroducing chunk_content here would silently multiply Valkey
    memory at document scale.
    """
    tree = _parse(CHUNK_EXTRACTION_MODULE)
    helper = _find_method(tree, "ChunkExtractionOperationsService", "queue_extract_chunk")
    assert isinstance(helper, ast.AsyncFunctionDef), (
        "ChunkExtractionOperationsService must define queue_extract_chunk — "
        "could not find the method in the AST of chunk_extraction_service.py."
    )

    keys = _enqueue_data_keys_in_function(helper)
    assert "chunk_content" not in keys, (
        "queue_extract_chunk's enqueue payload must NOT contain 'chunk_content' — "
        "the handler rehydrates chunk text from DB via "
        "adapter.get_chunks_by_ids(small_chunk_ids, ...). Re-adding this key "
        "would revert the Phase 5 Task D payload shrink. Current keys: "
        f"{sorted(keys)}"
    )
    # Sanity check: the new ID-only shape must still carry the IDs the
    # handler needs to rehydrate.
    assert "small_chunk_ids" in keys, (
        "queue_extract_chunk must still carry 'small_chunk_ids' in its "
        "payload — it is the lookup key the handler passes to "
        "adapter.get_chunks_by_ids. Missing it would make the handler "
        "unable to fetch chunk text."
    )


def test_batch_extract_chunk_payload_drops_chunk_content() -> None:
    """The batch enqueue spec in _queue_extraction_chunks must not carry chunk_content.

    ImportOperationsService._queue_extraction_chunks builds a list of
    {"operation": OP_EXTRACT_CHUNK, "data": {...}} specs and dispatches
    them via enqueue_tasks_batch. The inner data dict must follow the
    same shrink as queue_extract_chunk.
    """
    tree = _parse(IMPORT_SERVICE_MODULE)

    # The batch logic lives inside a method; find it on the class.
    target = _find_any_method(tree, "_enqueue_chunk_tasks")
    assert target is not None, (
        "ImportOperationsService must expose _enqueue_chunk_tasks — "
        "it is the batch enqueue path for OP_EXTRACT_CHUNK tasks created "
        "during analysis. If renamed, update this test accordingly."
    )

    keys = _enqueue_data_keys_in_function(target)
    assert "chunk_content" not in keys, (
        "_enqueue_chunk_tasks batch-spec 'data' dict must NOT contain "
        "'chunk_content' — Phase 5 Task D requires IDs-only payloads. "
        f"Current keys: {sorted(keys)}"
    )
    assert "small_chunk_ids" in keys, (
        "_enqueue_chunk_tasks batch specs must still carry "
        "'small_chunk_ids' so the handler can rehydrate chunk text."
    )


def test_queue_import_commit_payload_drops_commit_data() -> None:
    """queue_import_commit's enqueue data dict must not carry commit_data.

    Phase 5 Task D persists the large commit_data dict on
    SourceRow.commit_payload before enqueue; the queue message only
    carries {file_id, file_info}. Re-embedding commit_data here would
    put megabyte-scale entity/relationship lists back in Valkey.
    """
    tree = _parse(QUEUE_UTILS_MODULE)
    helper = _find_toplevel(tree, "queue_import_commit")
    assert isinstance(helper, ast.AsyncFunctionDef), (
        "queue_utils.py must define async def queue_import_commit(...) — "
        "it is the shared enqueue helper used by both the finalizer and "
        "the manual retry path."
    )

    keys = _enqueue_data_keys_in_function(helper)
    assert "commit_data" not in keys, (
        "queue_import_commit's enqueue payload must NOT contain 'commit_data' — "
        "the handler rehydrates it from SourceRow.commit_payload via "
        "adapter.get_source_commit_payload(file_id, database_name). "
        f"Current keys: {sorted(keys)}"
    )
    assert "file_id" in keys, (
        "queue_import_commit must still carry 'file_id' — it is the lookup "
        "key for the commit_payload the handler reads from the DB."
    )


def test_queue_import_commit_writes_payload_to_db_before_enqueue() -> None:
    """queue_import_commit must call set_source_commit_payload before enqueue.

    The helper's contract is: persist the commit_data dict on the
    source row first, THEN enqueue a thin payload. If the enqueue
    call came first, a successfully-enqueued task could run before
    the DB write committed, and the handler would fail with
    commit_payload_not_found.
    """
    src = _source(QUEUE_UTILS_MODULE)
    assert "adapter.set_source_commit_payload(" in src, (
        "queue_import_commit must call adapter.set_source_commit_payload(...) "
        "to persist the large commit_data dict before enqueueing the thin "
        "queue message. Without it, the handler has no payload to read."
    )


def test_source_row_has_commit_payload_column() -> None:
    """SourceRow must define a commit_payload field.

    This is the DB column the Task D enqueue helper writes to and the
    commit handler reads from. Auto-migration picks up the new column
    on startup (per CLAUDE.md's auto schema migrations rule), so
    simply defining the field on the SQLModel is enough for the
    contract to hold.
    """
    tree = _parse(MODELS_MODULE)
    field = _find_class_field(tree, "SourceRow", "commit_payload")
    assert field is not None, (
        "SourceRow must define a commit_payload field (JSON-as-TEXT) in "
        "adapters/sqlite/models.py. It is the on-disk home for the pending "
        "OP_IMPORT_COMMIT payload. Auto-migration adds the column at startup."
    )


def test_get_chunks_by_ids_exists_on_chunks_mixin() -> None:
    """SourceChunksMixin must expose get_chunks_by_ids(chunk_ids, database_name).

    The chunk extraction handler calls this to rehydrate hierarchical
    group content from the DB instead of carrying chunk_content on the
    queue.
    """
    tree = _parse(SOURCES_CHUNKS_MIXIN_MODULE)
    method = _find_method(tree, "SourceChunksMixin", "get_chunks_by_ids")
    assert isinstance(method, ast.FunctionDef), (
        "SourceChunksMixin must define get_chunks_by_ids(...) — it is the "
        "batch accessor the OP_EXTRACT_CHUNK handler uses to rehydrate "
        "chunk text from small_chunk_ids. Missing this method breaks the "
        "Task D payload shrink."
    )


def test_commit_payload_accessors_exist_on_sources_mixin() -> None:
    """SourcesMixin must expose the three commit_payload accessors.

    ``set_source_commit_payload`` — written by queue_import_commit
    before enqueue.  ``get_source_commit_payload`` — read by
    _import_commit_handler at dispatch time.
    ``clear_source_commit_payload`` — cleared by the commit handler
    inside the commit transaction on success.
    """
    tree = _parse(SOURCES_MIXIN_MODULE)
    for method_name in (
        "set_source_commit_payload",
        "get_source_commit_payload",
        "clear_source_commit_payload",
    ):
        method = _find_method(tree, "SourcesMixin", method_name)
        assert method is not None, (
            f"SourcesMixin must define {method_name}(...) — "
            "required by the Phase 5 Task D OP_IMPORT_COMMIT payload shrink. "
            "Missing this accessor breaks either the enqueue helper "
            "(set_/get_) or the commit handler's atomic clear on success."
        )


def test_extract_chunk_handler_fetches_chunks_by_ids() -> None:
    """_extract_chunk_handler must call adapter.get_chunks_by_ids(...).

    Without this call, the handler has no chunk_content to pass to the
    AIEntityExtractor (the payload no longer carries it).  A stricter
    AST-level assertion of the call shape keeps future refactors from
    silently dropping the rehydrate step.
    """
    src = _source(CHUNK_EXTRACTION_MODULE)
    assert "adapter.get_chunks_by_ids(" in src, (
        "_extract_chunk_handler must call adapter.get_chunks_by_ids(...) to "
        "rehydrate chunk text from the DB — the queue payload no longer "
        "carries chunk_content (Phase 5 Task D)."
    )


def test_import_commit_handler_reads_commit_payload_from_db() -> None:
    """_import_commit_handler must hydrate commit_data via get_source_commit_payload.

    The handler must not read 'commit_data' from the task data dict
    (payload no longer carries it). A stray ``data["commit_data"]``
    would crash with KeyError on every OP_IMPORT_COMMIT after Task D.
    """
    src = _source(IMPORT_SERVICE_MODULE)
    assert ".get_source_commit_payload(" in src, (
        "_import_commit_handler must call get_source_commit_payload(...) "
        "to rehydrate commit_data from the DB — the queue payload no longer "
        "carries commit_data (Phase 5 Task D)."
    )
    # ``clear_source_commit_payload`` moved into ``SourceCommitService`` as
    # the LAST write inside its inner commit transaction (2026-05-20 writer-
    # lock-contention root fix). The atomicity invariant — "payload clears
    # together with commit writes; payload retained on commit failure" —
    # is preserved by folding the clear into the same SQLite commit that
    # flips status to COMMITTED. Pin the new home via Core's
    # ``services/sources/engine/commit/service.py`` so a future refactor
    # cannot silently drop the call.
    commit_service_src = _source(
        CORE_SRC / "services" / "sources" / "engine" / "commit" / "service.py"
    )
    assert ".clear_source_commit_payload(" in commit_service_src, (
        "SourceCommitService must call clear_source_commit_payload(...) as "
        "the last write inside its inner commit transaction so the payload "
        "is discarded atomically with a successful commit (and preserved "
        "for retry on failure). Dropped from import_service.py with the "
        "2026-05-20 writer-lock-contention root fix; folded into the "
        "commit service so the outer transaction at import_service.py:1230 "
        "can be removed."
    )
    assert 'data["commit_data"]' not in src and "data['commit_data']" not in src, (
        "_import_commit_handler must not read commit_data from the task "
        "data dict — Task D moved commit_data onto SourceRow.commit_payload. "
        'A stray data["commit_data"] will crash with KeyError on every '
        "OP_IMPORT_COMMIT dispatch."
    )
