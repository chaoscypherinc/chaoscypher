# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Diagnostics Models.

Pydantic DTOs for diagnostic export responses.
"""

from pydantic import BaseModel, ConfigDict


class DiagnosticExportResponse(BaseModel):
    """Response metadata for diagnostic export."""

    filename: str
    size_bytes: int

    model_config = ConfigDict(extra="forbid")
