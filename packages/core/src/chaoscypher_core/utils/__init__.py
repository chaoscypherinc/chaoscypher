# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Chaos Cypher Utilities - Generic Utility Functions and Classes.

This package contains ONLY generic, reusable utilities that are used across
multiple services. Feature-specific utilities have been moved to their respective
service directories.

**Moved Utilities (Now in Services):**
- Document loaders → `services/sources/loaders/` (extensible plugin architecture)
- Extraction utilities → `services/sources/extraction/utils/` (AI entities, template matching, etc.)
- Template formatter → `services/sources/extraction/utils/template_formatter.py`
- File integrity → `services/export/utils/file_integrity.py`
- Commit handlers → `services/sources/commit/` (template, entity, relationship handlers)
- Deduplication → `services/sources/deduplication/` (entity deduplication service)
- Source processing utilities → `services/sources/utils/` (AI suggester, file validator, progress tracker)

**Available Generic Utilities:**
- **logging/** - Structured logging configuration
- **id.py** - Unique ID generation (9+ users)
- **chunk.py** - Text chunking service (moved from rag/, cross-cutting concern)
- **rrf.py** - Reciprocal Rank Fusion for merging multiple ranked result lists

Note: Settings conversion lives in `chaoscypher_core.app_config.engine_factory` (build_engine_settings).

All utilities in this package are domain-agnostic and exported via chaoscypher/__init__.py.
"""

# Core utilities
from chaoscypher_core.utils.chunk import ChunkingService
from chaoscypher_core.utils.disk import check_disk_space
from chaoscypher_core.utils.id import generate_id
from chaoscypher_core.utils.rrf import reciprocal_rank_fusion
from chaoscypher_core.utils.settings_validators import max_length_from_settings
from chaoscypher_core.utils.task_callbacks import log_task_exception
from chaoscypher_core.utils.text_patterns import CONTROL_CHAR_PATTERN, UNICODE_WHITESPACE_PATTERN
from chaoscypher_core.utils.tokens import estimate_message_tokens, estimate_tokens


__all__ = [
    "CONTROL_CHAR_PATTERN",
    "UNICODE_WHITESPACE_PATTERN",
    "ChunkingService",
    "check_disk_space",
    "estimate_message_tokens",
    "estimate_tokens",
    "generate_id",
    "log_task_exception",
    "max_length_from_settings",
    "reciprocal_rank_fusion",
]
