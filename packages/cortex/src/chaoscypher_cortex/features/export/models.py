# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Export Models.

Pydantic DTOs for export/import operations.
"""

from pydantic import BaseModel


class ExportResponse(BaseModel):
    """Export operation response."""

    task_id: str
    status: str
    message: str


class ImportResponse(BaseModel):
    """Import operation response."""

    task_id: str
    status: str
    message: str
