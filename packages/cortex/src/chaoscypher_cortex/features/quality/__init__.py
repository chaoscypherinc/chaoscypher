# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality Scoring Feature.

Provides extraction quality evaluation to compare extraction quality
across sources and identify which approaches produce the best results.
"""

from chaoscypher_cortex.features.quality.api import router
from chaoscypher_cortex.features.quality.service import QualityService


__all__ = ["QualityService", "router"]
