# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Shared constants for extraction utilities.

Default loop detection thresholds used by both streaming (ai_entities) and
post-hoc (line_parser) extraction output parsing.

These defaults mirror ``ExtractionSettings`` and are kept for callers that
do not have access to a settings instance (e.g. the standalone ``line_parser``
module-level functions).  When a settings instance is available, callers
should read from ``settings.extraction`` instead.
"""

from chaoscypher_core.settings import ExtractionSettings


_DEFAULTS = ExtractionSettings()

# Maximum consecutive out-of-bounds indices before aborting
LOOP_MAX_OUT_OF_BOUNDS: int = _DEFAULTS.loop_max_out_of_bounds

# Maximum consecutive identical (source, type) pairs before aborting.
# Must be high enough to avoid false positives on dense entities
# (e.g., a major character with 8+ relationships of the same type).
LOOP_MAX_SOURCE_TYPE_REPEAT: int = _DEFAULTS.loop_max_source_type_repeat
