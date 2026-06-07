# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Phase 3 Task G: shared ``get_extraction_job`` lookup base.

Both chunk-task mixins used to call ``get_extraction_job`` via sibling
MRO, with only a stub declaration locally for type-checking. Phase 3
folds the real implementation into ``ExtractionJobQueryBase`` and has
every participating mixin inherit from it so the call is statically
resolvable rather than implicitly cross-mixin.
"""

from __future__ import annotations

import ast
from pathlib import Path


MIXINS_DIR = (
    Path(__file__).resolve().parents[5]
    / "src"
    / "chaoscypher_core"
    / "adapters"
    / "sqlite"
    / "mixins"
)


def test_extraction_job_query_base_module_exists() -> None:
    assert (MIXINS_DIR / "_extraction_job_query_base.py").is_file()


def test_all_three_mixins_inherit_from_shared_base() -> None:
    """SourceExtractionJobsMixin and both chunk-task mixins inherit from the new base."""
    from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_lifecycle import (
        ChunkTasksLifecycleMixin,
    )
    from chaoscypher_core.adapters.sqlite.mixins._chunk_tasks_recovery import (
        ChunkTasksRecoveryMixin,
    )
    from chaoscypher_core.adapters.sqlite.mixins._extraction_job_query_base import (
        ExtractionJobQueryBase,
    )
    from chaoscypher_core.adapters.sqlite.mixins.source_files_extraction_jobs import (
        SourceExtractionJobsMixin,
    )

    for cls in (
        SourceExtractionJobsMixin,
        ChunkTasksLifecycleMixin,
        ChunkTasksRecoveryMixin,
    ):
        assert issubclass(cls, ExtractionJobQueryBase), (
            f"{cls.__name__} should inherit from ExtractionJobQueryBase"
        )


def test_chunk_task_mixins_no_longer_declare_stub() -> None:
    """The old type-checking stubs for ``get_extraction_job`` are gone.

    AST scan on both files ensures no locally-defined ``get_extraction_job``
    method body exists on either task mixin class — the method must come
    from ``ExtractionJobQueryBase``, full stop.
    """
    targets = [
        MIXINS_DIR / "_chunk_tasks_lifecycle.py",
        MIXINS_DIR / "_chunk_tasks_recovery.py",
    ]
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for body_node in node.body:
                    if (
                        isinstance(body_node, ast.FunctionDef)
                        and body_node.name == "get_extraction_job"
                    ):
                        msg = (
                            f"{path.name}:{body_node.lineno} — "
                            "get_extraction_job stub was not removed; it should "
                            "come from ExtractionJobQueryBase now."
                        )
                        raise AssertionError(msg)


def test_extraction_jobs_mixin_has_no_duplicate_implementation() -> None:
    """``SourceExtractionJobsMixin`` should not redefine ``get_extraction_job``.

    The method now lives on ``ExtractionJobQueryBase``; keeping a second
    copy here would defeat the point.
    """
    source = (MIXINS_DIR / "source_files_extraction_jobs.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SourceExtractionJobsMixin":
            for body_node in node.body:
                if (
                    isinstance(body_node, ast.FunctionDef)
                    and body_node.name == "get_extraction_job"
                ):
                    offenders.append(body_node.lineno)
    assert not offenders, (
        f"SourceExtractionJobsMixin redefines get_extraction_job at lines {offenders}"
    )


def test_sqlite_adapter_resolves_method_from_shared_base() -> None:
    """At runtime, the composed adapter resolves ``get_extraction_job`` to the base's impl."""
    from chaoscypher_core.adapters.sqlite import SqliteAdapter
    from chaoscypher_core.adapters.sqlite.mixins._extraction_job_query_base import (
        ExtractionJobQueryBase,
    )

    assert issubclass(SqliteAdapter, ExtractionJobQueryBase)
    assert SqliteAdapter.get_extraction_job.__qualname__.startswith("ExtractionJobQueryBase"), (
        f"Expected adapter.get_extraction_job to resolve to ExtractionJobQueryBase; "
        f"got {SqliteAdapter.get_extraction_job.__qualname__}"
    )
