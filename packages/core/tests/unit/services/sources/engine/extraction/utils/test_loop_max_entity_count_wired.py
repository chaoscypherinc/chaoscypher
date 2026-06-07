# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""loop_max_entity_count from resolved FilteringConfig must abort streams.

The integration-level promise is: when ``harvest_entities_and_relationships``
is given a FilteringConfig whose ``loop_max_entity_count`` is N, the
``_StreamLoopDetector`` it constructs must abort at the N-th E| line.

This file exercises two layers:

1. The detector itself respects ``max_entity_count_override`` (already-true
   contract; pinned here so refactors don't regress).
2. The harvest flow resolves the override from the *FilteringConfig* — not
   only from domain extraction limits — so that the slider drives the cap.
"""

from __future__ import annotations

from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    _resolve_loop_max_entity_count,
    _StreamLoopDetector,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    resolve_filtering_config,
)
from chaoscypher_core.settings import ExtractionSettings


def _feed_entity_line(detector: _StreamLoopDetector) -> bool:
    """Feed one E| line and return whether the detector aborted."""
    line = "E|Foo|Person|||S1|some entity"
    return detector.check_line(line, content_length=len(line))


def test_strict_mode_caps_entities_at_filtering_config_value() -> None:
    """With strict mode (loop_max_entity_count=35), the 35th line aborts."""
    cfg_strict = resolve_filtering_config("strict")
    extraction_cfg = ExtractionSettings()  # default loop_max_entity_count=50

    detector = _StreamLoopDetector(
        extraction_cfg=extraction_cfg,
        max_entity_count_override=cfg_strict.loop_max_entity_count,
    )

    aborted_at: int | None = None
    for i in range(1, 200):
        if _feed_entity_line(detector):
            aborted_at = i
            break

    assert aborted_at == cfg_strict.loop_max_entity_count == 35
    assert detector.aborted


def test_maximum_mode_caps_entities_lower_than_strict() -> None:
    """With maximum mode (loop_max_entity_count=25), abort fires earlier."""
    cfg_max = resolve_filtering_config("maximum")
    extraction_cfg = ExtractionSettings()

    detector = _StreamLoopDetector(
        extraction_cfg=extraction_cfg,
        max_entity_count_override=cfg_max.loop_max_entity_count,
    )

    aborted_at: int | None = None
    for i in range(1, 200):
        if _feed_entity_line(detector):
            aborted_at = i
            break

    assert aborted_at == cfg_max.loop_max_entity_count == 25
    assert detector.aborted


def test_resolver_prefers_filtering_config_over_settings_default() -> None:
    """The harvest flow resolves the cap from FilteringConfig, not settings."""
    cfg = resolve_filtering_config("strict")  # loop_max_entity_count=35
    extraction_cfg = ExtractionSettings()  # default 50

    resolved = _resolve_loop_max_entity_count(
        filtering_config=cfg,
        domain_limits={},
        extraction_cfg=extraction_cfg,
    )
    # The slider-driven value (35) wins over the settings default (50).
    assert resolved == 35


def test_resolver_lets_domain_limits_override_filtering_config() -> None:
    """Domain extraction limits remain the highest-precedence source.

    Domain configs already shipped with ``loop_max_entity_count`` overrides;
    the FilteringConfig wiring must layer below those, not above.
    """
    cfg = resolve_filtering_config("strict")  # 35
    extraction_cfg = ExtractionSettings()  # 50
    domain_limits = {"loop_max_entity_count": 12}

    resolved = _resolve_loop_max_entity_count(
        filtering_config=cfg,
        domain_limits=domain_limits,
        extraction_cfg=extraction_cfg,
    )
    assert resolved == 12
