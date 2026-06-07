# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Backup feature request and response models."""

from pydantic import BaseModel


class BackupResponse(BaseModel):
    """Response for backup creation."""

    database: str
    filename: str
    size: int
    created_at: str


class BackupSummaryResponse(BaseModel):
    """Summary response for backup list items (no database field)."""

    filename: str
    size: int
    created_at: str


class BackupListResponse(BaseModel):
    """Response for backup listing."""

    backups: list[BackupSummaryResponse]


class RestoreResponse(BaseModel):
    """Response for backup restore."""

    database: str
    restored_from: str
