# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared base providing ``get_extraction_job`` to chunk-task mixins.

Several mixins — ``SourceExtractionJobsMixin`` itself, plus the
``ChunkTasksLifecycleMixin`` and ``ChunkTasksRecoveryMixin`` that need
to look up a job while processing its child tasks — all need the same
single-job lookup. Prior to Phase 3 the task mixins relied on Python MRO
to find ``get_extraction_job`` on a sibling, which the architecture
review flagged as implicit cross-mixin coupling (exit criterion: "No
mixin calls a sibling mixin's method unless both inherit a shared base
defining that method").

This module hosts that shared base. Every participating mixin inherits
from it so the call is always statically resolvable — no MRO guessing,
no stub declarations in the task mixins, and no risk of drift if
someone renames the method on one class but not the others.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import ChunkExtractionJob


class ExtractionJobQueryBase(SqliteMixinBase):
    """Shared lookup surface for chunk-extraction jobs.

    All chunk-extraction-related mixins inherit from this class so the
    ``get_extraction_job`` call on any of them resolves to the same
    implementation rather than depending on sibling-mixin MRO.
    """

    def get_extraction_job(self, job_id: str) -> dict[str, Any] | None:
        """Get extraction job by ID.

        Args:
            job_id: Job identifier.

        Returns:
            Job as a dictionary, or ``None`` if not found.
        """
        self._ensure_connected()

        # Expire session cache to see changes from other processes.
        self.session.expire_all()

        statement = select(ChunkExtractionJob).where(ChunkExtractionJob.id == job_id)
        result = self.session.exec(statement)
        job = result.first()
        return self._entity_to_dict(job) if job else None
