# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Pydantic DTOs for the pause/resume feature slice."""

from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

from chaoscypher_core.app_config import get_settings
from chaoscypher_core.utils.settings_validators import max_length_from_settings
from chaoscypher_cortex.shared.models.summaries import SystemPauseStatusResponse


class PauseSourceRequest(BaseModel):
    """Body for POST /api/v1/sources/{source_id}/pause."""

    reason: Annotated[
        str | None,
        Field(default=None, description="Operator reason for the pause"),
        max_length_from_settings("pause.reason_max_chars"),
    ] = None


class BulkPauseRequest(BaseModel):
    """Body for POST /api/v1/sources/pause (bulk)."""

    source_ids: list[str] = Field(min_length=1)
    reason: Annotated[
        str | None,
        Field(default=None, description="Operator reason for the pause"),
        max_length_from_settings("pause.reason_max_chars"),
    ] = None

    @model_validator(mode="after")
    def _validate_source_count(self) -> BulkPauseRequest:
        """Reject requests whose source_ids exceed the configured per-request cap."""
        cap = get_settings().pause.sources_per_request_max
        if len(self.source_ids) > cap:
            msg = (
                f"source_ids count {len(self.source_ids)} exceeds configured "
                f"maximum {cap} (setting: pause.sources_per_request_max)"
            )
            raise ValueError(msg)
        return self


class BulkResumeRequest(BaseModel):
    """Body for POST /api/v1/sources/resume (bulk)."""

    source_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_source_count(self) -> BulkResumeRequest:
        """Reject requests whose source_ids exceed the configured per-request cap."""
        cap = get_settings().pause.sources_per_request_max
        if len(self.source_ids) > cap:
            msg = (
                f"source_ids count {len(self.source_ids)} exceeds configured "
                f"maximum {cap} (setting: pause.sources_per_request_max)"
            )
            raise ValueError(msg)
        return self


class PauseSystemRequest(BaseModel):
    """Body for POST /api/v1/system/processing/pause."""

    reason: Annotated[
        str | None,
        Field(default=None, description="Operator reason for the pause"),
        max_length_from_settings("pause.reason_max_chars"),
    ] = None


class SourcePauseActionResponse(BaseModel):
    """Response from per-source pause/resume actions.

    Returned by the single-source endpoints:

    * POST /api/v1/sources/{source_id}/pause  → ``paused=True``
    * POST /api/v1/sources/{source_id}/resume → ``paused=False``
    """

    source_id: str = Field(description="Identifier of the source that was toggled.")
    paused: bool = Field(
        description="New pause state — True after pause, False after resume.",
    )


class BulkPauseActionResponse(BaseModel):
    """Response from bulk pause/resume actions.

    Returned by:

    * POST /api/v1/sources/pause  (bulk)
    * POST /api/v1/sources/resume (bulk)

    ``count`` is the number of source rows the repository actually
    updated — not the length of ``source_ids`` in the request (rows
    already in the requested state are skipped by the repo).
    """

    count: int = Field(description="Number of source rows updated by the bulk action.")


class SystemPauseActionResponse(BaseModel):
    """Response from system-wide pause/resume.

    Returned by:

    * POST /api/v1/system/processing/pause  → ``paused=True``
    * POST /api/v1/system/processing/resume → ``paused=False``
    """

    paused: bool = Field(
        description="New system-wide pause state — True after pause, False after resume.",
    )


class SystemEventResponse(BaseModel):
    """Single audit-trail row from the system_events table.

    Shape comes from ``SqliteAdapter.list_system_events`` — every field
    is nullable in the database except ``id``, so we mirror that here.
    ``timestamp`` is always serialised as an ISO-8601 string with a ``Z``
    suffix when naive.
    """

    id: int = Field(description="Auto-incrementing primary key.")
    timestamp: str | None = Field(
        default=None,
        description="ISO-8601 timestamp when the event was recorded.",
    )
    type: str | None = Field(
        default=None,
        description="Event type category: pause | resume | health_change | task_failed | recovery.",
    )
    action: str | None = Field(
        default=None,
        description="Specific action label within the type (e.g. 'source_paused').",
    )
    source: str | None = Field(
        default=None,
        description="Originating source_id, or a scope tag like 'system' for system-wide events.",
    )
    reason: str | None = Field(
        default=None,
        description="Human-readable reason captured at event time, if any.",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Arbitrary structured payload; schema depends on event type.",
    )
    database_name: str | None = Field(
        default=None,
        description="Database the event originated from (events are not cross-database).",
    )


class SystemEventsClearResponse(BaseModel):
    """Response from DELETE /api/v1/system/processing/events."""

    deleted: int = Field(description="Number of rows deleted from the system_events table.")


__all__ = [
    "BulkPauseActionResponse",
    "BulkPauseRequest",
    "BulkResumeRequest",
    "PauseSourceRequest",
    "PauseSystemRequest",
    "SourcePauseActionResponse",
    "SystemEventResponse",
    "SystemEventsClearResponse",
    "SystemPauseActionResponse",
    "SystemPauseStatusResponse",
]
