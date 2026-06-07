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


@pytest.mark.parametrize(
    ("mixin_cls", "protocol", "description"),
    [(m, p, d) for m, p, d in MIXIN_PROTOCOL_BINDINGS],
    ids=[d.split(" satisfies ")[0] + "->" + p.__name__ for _, p, d in MIXIN_PROTOCOL_BINDINGS],
)
def test_mixin_satisfies_declared_protocol(
    mixin_cls: type, protocol: type, description: str
) -> None:
    """Each mixin class must declare the expected Protocol base.

    Uses issubclass() rather than isinstance() so no adapter instance is
    required — the binding is checked purely at the class level.  This
    catches regressions at import time: if a mixin loses a method that its
    Protocol declares, issubclass() returns False and this test fails.

    Args:
        mixin_cls: The mixin class under test.
        protocol: The Protocol it should satisfy.
        description: Human-readable description of the binding.

    """
    assert issubclass(mixin_cls, protocol), (
        f"{mixin_cls.__name__} no longer satisfies {protocol.__name__}. "
        f"A required method was likely removed or renamed. "
        f"Check {protocol.__module__} for the full method list."
    )
