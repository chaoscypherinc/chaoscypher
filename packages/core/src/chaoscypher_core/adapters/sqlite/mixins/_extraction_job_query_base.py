# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared base providing ``get_extraction_job`` to chunk-task mixins.

Several mixins — ``SourceExtractionJobsMixin`` itself, plus the
``ChunkTasksLifecycleMixin`` and ``ChunkTasksRecoveryMixin`` that need
to look up a job while processing its child tasks — all need the same
single-job lookup. Previously the task mixins relied on Python MRO
to find ``get_extraction_job`` on a sibling, which an architecture
review flagged as implicit cross-mixin coupling (no mixin should call a
sibling mixin's method unless both inherit a shared base defining that
method).

This module hosts that shared base. Every participating mixin inherits
from it so the call is always statically resolvable — no MRO guessing,
no stub declarations in the task mixins, and no risk of drift if
someone renames the method on one class but not the others.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import load_only
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

    def get_extraction_job_progress(self, job_id: str) -> dict[str, Any] | None:
        """Get the progress-only slice of an extraction job.

        The extraction-status endpoint is polled every few seconds for the
        whole life of an extraction and reads seven scalar columns. The full
        ``get_extraction_job`` read above selects all 29 columns, ten of them
        prompt/template/config TEXT, so this projected sibling exists for the
        poll path the way ``get_running_chunk_task`` does for chunk tasks.

        ``get_extraction_job`` is deliberately left unprojected: other callers
        read ``system_prompt``, ``generate_embeddings``, ``detected_domain``
        and ``forced_domain``, and ``load_only`` would silently drop those
        keys (``_entity_to_dict``/``model_dump`` reads ``__dict__``, so a
        deferred column is missing rather than lazy-loaded). The dict below is
        built by explicit attribute reads for the same reason.

        Args:
            job_id: Job identifier.

        Returns:
            Dict with status, chunk counters, depth and timing, or ``None``
            if the job does not exist.
        """
        self._ensure_connected()

        # Expire session cache to see changes from other processes.
        self.session.expire_all()

        statement = (
            select(ChunkExtractionJob)
            .options(
                load_only(
                    ChunkExtractionJob.status,
                    ChunkExtractionJob.total_chunks,
                    ChunkExtractionJob.completed_chunks,
                    ChunkExtractionJob.failed_chunks,
                    ChunkExtractionJob.extraction_depth,
                    ChunkExtractionJob.started_at,
                    ChunkExtractionJob.completed_at,
                )
            )
            .where(ChunkExtractionJob.id == job_id)
        )
        job = self.session.exec(statement).first()
        if not job:
            return None

        return {
            "status": job.status,
            "total_chunks": job.total_chunks,
            "completed_chunks": job.completed_chunks,
            "failed_chunks": job.failed_chunks,
            "extraction_depth": job.extraction_depth,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }
