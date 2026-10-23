# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source Sites Mixin for SqliteAdapter: imported docs sites and their pages (migration 0021).

A site is a ``source_sites`` row; its pages are sources whose ``site_id``
names it. List reads project their columns with ``load_only`` (CC003). Writes
go through ``_maybe_commit`` so they join an enclosing ``adapter.transaction()``.

Implements ``chaoscypher_core.ports.storage_sites.SiteStorageProtocol``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import func
from sqlalchemy import update as sqla_update
from sqlalchemy.orm import load_only
from sqlmodel import col, select

from chaoscypher_core.adapters.sqlite.mixin_base import SqliteMixinBase
from chaoscypher_core.adapters.sqlite.models import SourceRow, SourceSiteRow
from chaoscypher_core.models import SiteStatus, SourceStatus


if TYPE_CHECKING:
    from chaoscypher_core.ports.storage_sites import (
        SitePageCount,
        SitePageRecord,
        SourceSiteRecord,
    )


logger = structlog.get_logger(__name__)

#: Columns a site row's caller may write (everything but its identity).
_SITE_WRITABLE = frozenset(
    {
        "url",
        "title",
        "discovery_method",
        "status",
        "page_count",
        "options",
        "error_message",
        "last_checked_at",
        "run_started_at",
        "run_cursor",
        "excluded_pages",
    }
)

#: Every column of a site row, for the list reads' ``load_only`` (CC003).
_SITE_COLUMNS = (
    SourceSiteRow.id,
    SourceSiteRow.database_name,
    SourceSiteRow.url,
    SourceSiteRow.title,
    SourceSiteRow.discovery_method,
    SourceSiteRow.status,
    SourceSiteRow.page_count,
    SourceSiteRow.options,
    SourceSiteRow.error_message,
    SourceSiteRow.created_at,
    SourceSiteRow.last_checked_at,
    SourceSiteRow.run_started_at,
    SourceSiteRow.run_cursor,
    SourceSiteRow.excluded_pages,
)

#: The statuses a run is queued or under way in (``SiteStatus.in_progress``).
_RUNNING = [str(status) for status in SiteStatus if status.in_progress]


def _stamped(updates: Mapping[str, Any]) -> dict[str, Any]:
    """*updates*, with ``run_started_at`` set to now when they start a run.

    Every write of an in-progress status (queued, importing, refreshing)
    starts or continues a run; the time it did is what makes a killed run go
    stale (``operations.sources.site_runs.site_run_live``), so no caller can
    forget to write it.
    """
    values = dict(updates)
    status = values.get("status")
    if status is not None and str(status) in _RUNNING and "run_started_at" not in values:
        values["run_started_at"] = datetime.now(UTC)
    return values


def _site_record(row: SourceSiteRow) -> SourceSiteRecord:
    """The row as the port's ``SourceSiteRecord`` dict."""
    return {
        "id": row.id,
        "database_name": row.database_name,
        "url": row.url,
        "title": row.title,
        "discovery_method": row.discovery_method,
        "status": row.status,
        "page_count": row.page_count,
        "options": dict(row.options) if row.options is not None else None,
        "error_message": row.error_message,
        "created_at": row.created_at,
        "last_checked_at": row.last_checked_at,
        "run_started_at": row.run_started_at,
        "run_cursor": row.run_cursor,
        "excluded_pages": list(row.excluded_pages or []),
    }


class SourceSitesMixin(SqliteMixinBase):
    """Site rows and the page rows that name them."""

    def create_site(
        self,
        *,
        site_id: str,
        database_name: str,
        url: str,
        title: str,
        status: str,
        options: Mapping[str, Any] | None,
        excluded_pages: Sequence[str] = (),
    ) -> SourceSiteRecord:
        """Insert a site row and return it (an in-progress status stamps ``run_started_at``)."""
        self._ensure_connected()
        now = datetime.now(UTC)
        row = SourceSiteRow(
            id=site_id,
            database_name=database_name,
            url=url,
            title=title,
            status=str(status),
            page_count=0,
            options=dict(options) if options is not None else None,
            created_at=now,
            run_started_at=now if str(status) in _RUNNING else None,
            excluded_pages=list(excluded_pages),
        )
        self.session.add(row)
        self._maybe_commit()
        return _site_record(row)

    def get_site(self, site_id: str, database_name: str) -> SourceSiteRecord | None:
        """Return the site, or ``None`` when it is not in ``database_name``."""
        self._ensure_connected()
        row = self.session.exec(
            select(SourceSiteRow).where(
                SourceSiteRow.id == site_id, SourceSiteRow.database_name == database_name
            )
        ).first()
        return _site_record(row) if row is not None else None

    def list_sites(
        self, database_name: str, *, page: int, page_size: int
    ) -> tuple[list[SourceSiteRecord], int]:
        """Return one page of sites, newest first, and the total count."""
        self._ensure_connected()
        where = SourceSiteRow.database_name == database_name
        total = int(
            self.session.exec(select(func.count()).select_from(SourceSiteRow).where(where)).one()
        )
        statement = (
            select(SourceSiteRow)
            .options(load_only(*_SITE_COLUMNS))
            .where(where)
            .order_by(col(SourceSiteRow.created_at).desc(), col(SourceSiteRow.id).desc())
            .offset((max(page, 1) - 1) * page_size)
            .limit(page_size)
        )
        return [_site_record(row) for row in self.session.exec(statement)], total

    def update_site(self, site_id: str, database_name: str, updates: Mapping[str, Any]) -> None:
        """Write the given columns of a site row (a missing row is a no-op).

        An in-progress ``status`` also writes ``run_started_at`` (now) unless
        the update names it.

        Raises:
            ValueError: An update names a column that is not writable.
        """
        unknown = set(updates) - _SITE_WRITABLE
        if unknown:
            msg = f"source_sites columns not writable: {sorted(unknown)}"
            raise ValueError(msg)
        if not updates:
            return
        self._ensure_connected()
        values = {k: (str(v) if k == "status" else v) for k, v in _stamped(updates).items()}
        self.session.execute(
            sqla_update(SourceSiteRow)
            .where(
                col(SourceSiteRow.id) == site_id,
                col(SourceSiteRow.database_name) == database_name,
            )
            .values(**values)
        )
        self._maybe_commit()

    def list_running_sites(self, database_name: str) -> list[SourceSiteRecord]:
        """Every site whose run is queued or under way (by its status), oldest first."""
        self._ensure_connected()
        statement = (
            select(SourceSiteRow)
            .options(load_only(*_SITE_COLUMNS))
            .where(
                SourceSiteRow.database_name == database_name,
                col(SourceSiteRow.status).in_(_RUNNING),
            )
            .order_by(col(SourceSiteRow.created_at), col(SourceSiteRow.id))
        )
        return [_site_record(row) for row in self.session.exec(statement)]

    def delete_site(self, site_id: str, database_name: str) -> bool:
        """Delete the site row; its pages stay, unlinked (``site_id`` set to NULL)."""
        self._ensure_connected()
        row = self.session.exec(
            select(SourceSiteRow).where(
                SourceSiteRow.id == site_id, SourceSiteRow.database_name == database_name
            )
        ).first()
        if row is None:
            return False
        # The foreign key's ON DELETE SET NULL does this too; done explicitly
        # so the unlink holds on a connection with foreign keys off.
        self.session.execute(
            sqla_update(SourceRow)
            .where(
                col(SourceRow.site_id) == site_id,
                col(SourceRow.database_name) == database_name,
            )
            .values(site_id=None, site_gone_at=None)
        )
        self.session.delete(row)
        self._maybe_commit()
        return True

    def list_site_pages(self, site_id: str, database_name: str) -> list[SitePageRecord]:
        """Return every page of the site, gone ones included, oldest first."""
        self._ensure_connected()
        statement = (
            select(SourceRow)
            .options(
                load_only(
                    SourceRow.id,
                    SourceRow.origin_url,
                    SourceRow.title,
                    SourceRow.status,
                    SourceRow.error_stage,
                    SourceRow.auto_analyze,
                    SourceRow.content_hash,
                    SourceRow.filepath,
                    SourceRow.site_gone_at,
                )
            )
            .where(col(SourceRow.site_id) == site_id, SourceRow.database_name == database_name)
            .order_by(col(SourceRow.created_at), col(SourceRow.id))
        )
        return [
            {
                "id": row.id,
                "origin_url": row.origin_url,
                "title": row.title,
                "status": row.status,
                "error_stage": row.error_stage,
                "auto_analyze": bool(row.auto_analyze),
                "content_hash": row.content_hash,
                "filepath": row.filepath,
                "site_gone_at": row.site_gone_at,
            }
            for row in self.session.exec(statement)
        ]

    def count_site_pages(self, site_ids: Sequence[str], database_name: str) -> list[SitePageCount]:
        """Count the sites' pages grouped by site, status, ``auto_analyze`` and gone mark."""
        if not site_ids:
            return []
        self._ensure_connected()
        gone = col(SourceRow.site_gone_at).is_not(None)
        statement = (
            select(
                SourceRow.site_id,
                SourceRow.status,
                SourceRow.auto_analyze,
                gone,
                func.count(),
            )
            .where(
                col(SourceRow.site_id).in_(list(site_ids)),
                SourceRow.database_name == database_name,
            )
            .group_by(
                col(SourceRow.site_id), col(SourceRow.status), col(SourceRow.auto_analyze), gone
            )
        )
        return [
            {
                "site_id": str(site_id),
                "status": str(status),
                "auto_analyze": bool(auto_analyze),
                "gone": bool(is_gone),
                "count": int(count),
            }
            for site_id, status, auto_analyze, is_gone, count in self.session.exec(statement)
        ]

    def set_site_pages_gone(
        self, source_ids: Sequence[str], database_name: str, gone_at: datetime | None
    ) -> int:
        """Mark pages gone at ``gone_at`` (``None`` clears the mark); the rows changed."""
        if not source_ids:
            return 0
        self._ensure_connected()
        result = self.session.execute(
            sqla_update(SourceRow)
            .where(
                col(SourceRow.id).in_(list(source_ids)),
                SourceRow.database_name == database_name,
            )
            .values(site_gone_at=gone_at)
        )
        self._maybe_commit()
        return int(getattr(result, "rowcount", 0) or 0)

    def restage_site_page(
        self,
        source_id: str,
        database_name: str,
        *,
        payload: bytes,
        content_hash: str,
        title: str,
    ) -> None:
        """Write a page's new content over its staged file and send the row back to PENDING.

        The caller has already released the page's graph rows and reset its
        extraction (``force_re_extract``); this re-stages the bytes the
        indexing handler reads (``filepath``) and clears the indexing state so
        the queued ``index_document`` run re-chunks the new text.

        Raises:
            NotFoundError: The page is not in ``database_name`` or has no
                staged file path.
        """
        from chaoscypher_core.exceptions import NotFoundError

        self._ensure_connected()
        row = self.session.exec(
            select(SourceRow)
            .options(load_only(SourceRow.id, SourceRow.database_name, SourceRow.filepath))
            .where(SourceRow.id == source_id, SourceRow.database_name == database_name)
        ).first()
        if row is None or not row.filepath:
            raise NotFoundError("Source", source_id)
        filepath = Path(row.filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        self._write_to_staging(filepath, payload, None)  # type: ignore[attr-defined]  # SourceLifecycleMixin sibling on SqliteAdapter
        self.session.execute(
            sqla_update(SourceRow)
            .where(col(SourceRow.id) == source_id, SourceRow.database_name == database_name)
            .values(
                content_hash=content_hash,
                file_size=len(payload),
                title=title,
                status=SourceStatus.PENDING,
                indexing_complete=False,
                extraction_complete=False,
                commit_complete=False,
                error_message=None,
                error_stage=None,
                recovery_attempts=0,
                site_gone_at=None,
                updated_at=datetime.now(UTC),
            )
        )
        self._maybe_commit()
        logger.info("site_page_restaged", source_id=source_id, size=len(payload))


__all__ = ["SourceSitesMixin"]
