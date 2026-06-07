# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Source processing Management - CRUD Operations.

Provides CRUD operations for source processing files and status management.

Components:
- SourceProcessingService: File upload, status tracking, and lifecycle management
- get_original_text_path: Canonical path for per-source raw-upload text (Phase 5a)

Example:
    from chaoscypher_core.services.sources.management import SourceProcessingService

    service = SourceProcessingService(source_mgr, ops_mgr, config_mgr, validators)
    result = await service.upload_file(file_content, filename, auto_analyze=True)

"""

from chaoscypher_core.services.sources.management.paths import get_original_text_path
from chaoscypher_core.services.sources.management.service import SourceProcessingService


__all__ = [
    "SourceProcessingService",
    "get_original_text_path",
]
