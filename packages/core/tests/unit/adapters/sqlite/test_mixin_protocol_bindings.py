# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Per-mixin Protocol-binding conformance tests.

Task 12 verified the composed SqliteAdapter satisfies all 6 new Protocols.
Task 13 adds Protocol bases to each source-family mixin's class signature so
that EACH mixin individually satisfies its stated Protocol(s). This test
verifies those bindings hold — catches regressions where a mixin loses a
method that its Protocol declares.

Binding map (Task 13):

    SourcesMixin         -> SourceStorageProtocol
    SourceIndexingMixin  -> ExtractionQueueStorageProtocol, EntityEmbeddingStorageProtocol
    SourceChunksMixin    -> ChunkStorageProtocol
    SourceCitationsMixin -> CitationStorageProtocol
    SourceTagsMixin      -> SourceTagStorageProtocol
    StageProgressMixin   -> StageProgressStorageProtocol

Note on SourceIndexingMixin: this is a god-mixin that satisfies the lifecycle
half of SourceStorageProtocol but does NOT satisfy the full protocol (it is
missing the CRUD half which lives in SourcesMixin and SourceLifecycleMixin).
It is therefore bound only to the two protocols it fully satisfies; splitting
the mixin into focused pieces is deferred to Phase 3.

Note on SourceLifecycleMixin: provides only upload_source (1 of 22 protocol
methods) so it cannot be bound to SourceStorageProtocol alone.  It contributes
to the composed SqliteAdapter which does satisfy the full protocol.
"""

import pytest

from chaoscypher_core.adapters.sqlite.mixins.source_files_indexing import (
    SourceIndexingMixin,
)
from chaoscypher_core.adapters.sqlite.mixins.sources import SourcesMixin
from chaoscypher_core.adapters.sqlite.mixins.sources_chunks import SourceChunksMixin
from chaoscypher_core.adapters.sqlite.mixins.sources_citations import SourceCitationsMixin
from chaoscypher_core.adapters.sqlite.mixins.sources_tags import SourceTagsMixin
from chaoscypher_core.adapters.sqlite.mixins.stage_progress import (
    StageProgressMixin,
)
from chaoscypher_core.ports.stage_progress import StageProgressStorageProtocol
from chaoscypher_core.ports.storage_chunks import ChunkStorageProtocol
from chaoscypher_core.ports.storage_citations import CitationStorageProtocol
from chaoscypher_core.ports.storage_embeddings import EntityEmbeddingStorageProtocol
from chaoscypher_core.ports.storage_extraction_queue import ExtractionQueueStorageProtocol
from chaoscypher_core.ports.storage_source_tags import SourceTagStorageProtocol
from chaoscypher_core.ports.storage_sources import SourceStorageProtocol


# Each row: (mixin_class, protocol_class, description)
MIXIN_PROTOCOL_BINDINGS: list[tuple[type, type, str]] = [
    (
        SourcesMixin,
        SourceStorageProtocol,
        "SourcesMixin satisfies SourceStorageProtocol (source CRUD + lifecycle + stats)",
    ),
    (
        SourceIndexingMixin,
        ExtractionQueueStorageProtocol,
        "SourceIndexingMixin satisfies ExtractionQueueStorageProtocol (queue gating)",
    ),
    (
        SourceIndexingMixin,
        EntityEmbeddingStorageProtocol,
        "SourceIndexingMixin satisfies EntityEmbeddingStorageProtocol (entity embeddings)",
    ),
    (
        SourceChunksMixin,
        ChunkStorageProtocol,
        "SourceChunksMixin satisfies ChunkStorageProtocol (document chunk CRUD)",
    ),
    (
        SourceCitationsMixin,
        CitationStorageProtocol,
        "SourceCitationsMixin satisfies CitationStorageProtocol (citation + orphan ops)",
    ),
    (
        SourceTagsMixin,
        SourceTagStorageProtocol,
        "SourceTagsMixin satisfies SourceTagStorageProtocol (tag CRUD + assignments)",
    ),
    (
        StageProgressMixin,
        StageProgressStorageProtocol,
        "StageProgressMixin satisfies StageProgressStorageProtocol (stage lifecycle + extras)",
    ),
]


# Protocol members a bound mixin does NOT implement itself because a SIBLING
# mixin on the composed SqliteAdapter provides them.  The nominal Protocol
# base hid these gaps (issubclass() is unconditionally True for a nominal
# subclass); the structural check below surfaces them, so they are recorded
# explicitly.  Anything missing BEYOND these sets is a lost method.
# (test_source_protocols_split.py pins that the composed adapter has no gaps.)
SIBLING_PROVIDED: dict[tuple[str, str], frozenset[str]] = {
    # Lifecycle/stats half lives in SourceIndexingMixin, SourceLifecycleMixin,
    # SourceDeletionMixin and SourceCitationsMixin — see the module docstring.
    ("SourcesMixin", "SourceStorageProtocol"): frozenset(
        {
            "complete_commit",
            "complete_extraction",
            "complete_indexing",
            "delete_source_db",
            "fail_commit",
            "fail_extraction",
            "fail_indexing",
            "get_entity_uris_grouped_by_source",
            "get_stats",
            "increment_source_counter",
            "start_commit",
            "start_extraction",
            "start_indexing",
            "update_source_columns",
            "update_step_progress",
            "upload_source",
        }
    ),
    # Bulk job/task maintenance lives in SourceExtractionJobsMixin.
    ("SourceIndexingMixin", "ExtractionQueueStorageProtocol"): frozenset(
        {
            "clear_all_extraction_jobs",
            "clear_all_extraction_tasks",
            "count_extraction_jobs",
            "count_extraction_tasks",
            "delete_extraction_jobs",
            "delete_extraction_tasks",
        }
    ),
}


def protocol_members(protocol: type) -> set[str]:
    """Names the Protocol itself declares (``__protocol_attrs__``)."""
    return set(protocol.__protocol_attrs__)  # type: ignore[attr-defined]


def unimplemented_members(cls: type, protocol: type) -> list[str]:
    """Protocol members ``cls`` does not really implement.

    Every mixin lists its Protocol as an explicit base, so ``issubclass`` /
    ``isinstance`` short-circuit through the nominal MRO and can never fail —
    and because the Protocol's method bodies are ``...``, a deleted
    implementation is silently inherited as a ``None``-returning stub rather
    than raising AttributeError.  So resolve each member through the MRO and
    reject any that is still owned by a Protocol class: that member has no
    real implementation.
    """
    missing: list[str] = []
    for member in sorted(protocol_members(protocol)):
        owner = next((base for base in cls.__mro__ if member in vars(base)), None)
        if owner is None or getattr(owner, "_is_protocol", False):
            missing.append(member)
    return missing


@pytest.mark.parametrize(
    ("mixin_cls", "protocol", "description"),
    [(m, p, d) for m, p, d in MIXIN_PROTOCOL_BINDINGS],
    ids=[d.split(" satisfies ")[0] + "->" + p.__name__ for _, p, d in MIXIN_PROTOCOL_BINDINGS],
)
def test_mixin_satisfies_declared_protocol(
    mixin_cls: type, protocol: type, description: str
) -> None:
    """Each mixin must declare the Protocol base AND implement every member.

    The ``issubclass`` half pins the declared base.  It cannot pin the
    methods — a nominal subclass satisfies ``issubclass`` unconditionally —
    so the structural half compares the Protocol's declared member set
    against the mixin's real implementations, excluding members that resolve
    to the Protocol's own ``...``-bodied stubs.  That is the half that fails
    when a mixin loses or renames a method its Protocol declares.

    Args:
        mixin_cls: The mixin class under test.
        protocol: The Protocol it should satisfy.
        description: Human-readable description of the binding.

    """
    assert issubclass(mixin_cls, protocol), (
        f"{mixin_cls.__name__} no longer declares {protocol.__name__} as a base. "
        f"Check {protocol.__module__} for the full method list."
    )

    expected_gap = SIBLING_PROVIDED.get((mixin_cls.__name__, protocol.__name__), frozenset())
    lost = sorted(set(unimplemented_members(mixin_cls, protocol)) - expected_gap)
    assert not lost, (
        f"{mixin_cls.__name__} no longer implements {protocol.__name__}: {lost} "
        f"resolve to the Protocol's '...' stubs (which silently return None) "
        f"instead of a real implementation — a required method was removed or "
        f"renamed. Check {protocol.__module__} for the full method list; if the "
        f"method genuinely moved to a sibling mixin, record it in SIBLING_PROVIDED."
    )
