# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Vision processing — state enums and constants.

The vision pipeline lives across two surfaces: a queue-decomposed
per-page handler (PR 2 of the 2026-05-13 vision-pipeline-resilience
spec) and a per-source coordinator (vision_jobs). This package holds
the lightweight, dependency-free shared types both surfaces need.
"""

from chaoscypher_core.vision.states import VisionPageKind, VisionPageStatus


__all__ = ["VisionPageKind", "VisionPageStatus"]
