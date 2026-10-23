# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""A domain pack's ``loop_max_entity_count`` moves with the filtering slider (decided 2026-10-22).

At the default mode (``balanced``) the pack's cap applies as tuned. A looser
mode the user chose raises the cap to at least that mode's own cap; a stricter
one lowers it to at most that mode's cap. So the cap never moves against the
user's choice and is monotonic along the slider for every pack. Before, the
pack's cap won outright: a user on "unfiltered" (200) still got the technical
pack's 30. A preset the pack itself names is not the user moving the slider:
there the pack's tuning applies unchanged.

The rule is applied once, in the plan (:func:`build_extraction_plan`), so the
plan's limits and its FilteringConfig hold the effective value for every
reader: the chunk loop (``resolve_harvest_limits``), the MCP finalize, the
finalizer.
"""

from __future__ import annotations

from typing import Any, get_args
from unittest.mock import patch

import pytest
from structlog.testing import capture_logs

from chaoscypher_core.constants import SOURCE_ROW_FILTERING_MODE
from chaoscypher_core.ports.types import FilteringMode
from chaoscypher_core.services.sources.engine.extraction.domains import get_domain_registry
from chaoscypher_core.services.sources.engine.extraction.domains.stack import DomainStack
from chaoscypher_core.services.sources.engine.extraction.extraction_plan import (
    ExtractionPlan,
    build_extraction_plan,
    filtering_mode_choice,
)
from chaoscypher_core.services.sources.engine.extraction.utils.ai_entities import (
    AIEntityExtractor,
    HarvestLimits,
)
from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    follow_slider,
    follow_slider_limits,
    preset_value,
)
from chaoscypher_core.settings import EngineSettings, ExtractionSettings


# The slider from its loosest stop to its tightest (``FilteringMode`` lists it tightest first).
SLIDER = tuple(reversed(get_args(FilteringMode)))

# mode -> the cap for a pack below the default (the technical pack's 30), one above it (80),
# and no pack cap (the mode's own cap).
EXPECTED_CAPS: dict[str, tuple[int, int, int]] = {
    "unfiltered": (200, 200, 200),
    "minimal": (100, 100, 100),
    "lenient": (75, 80, 75),
    "balanced": (30, 80, 50),
    "strict": (30, 35, 35),
    "maximum": (25, 25, 25),
}


def _stack(domain: str | None = None, settings: EngineSettings | None = None) -> DomainStack:
    return DomainStack.from_domain_name(get_domain_registry(settings or EngineSettings()), domain)


def _plan_with_limits(limits: dict[str, Any], mode: str) -> ExtractionPlan:
    """The floor's plan for a stored source on ``mode``, its stack's limits replaced by ``limits``."""
    with patch.object(DomainStack, "limits", lambda _self: dict(limits)):
        return build_extraction_plan(
            _stack(), EngineSettings(), source_row={"filtering_mode": mode}
        )


def _harvest(plan: ExtractionPlan) -> HarvestLimits:
    """What the chunk loop resolves from the plan's chunk kwargs (the queue and CLI path)."""
    kwargs = plan.chunk_kwargs
    return AIEntityExtractor(settings=EngineSettings()).resolve_harvest_limits(
        domain_extraction_limits=kwargs["domain_extraction_limits"],
        filtering_mode=kwargs["filtering_mode"],
    )


def test_the_table_covers_every_mode() -> None:
    assert set(EXPECTED_CAPS) == set(SLIDER)
    assert SLIDER[0] == "unfiltered"
    assert SLIDER[-1] == "maximum"


@pytest.mark.parametrize("mode", SLIDER)
@pytest.mark.parametrize(
    ("domain_cap", "column"),
    [(30, 0), (80, 1), (None, 2)],
    ids=["pack-below-default", "pack-above-default", "no-pack-cap"],
)
def test_the_plan_cap_follows_the_slider(mode: str, domain_cap: int | None, column: int) -> None:
    """The plan's config and the chunk loop read the one effective cap."""
    limits = {} if domain_cap is None else {"loop_max_entity_count": domain_cap}
    plan = _plan_with_limits(limits, mode)
    expected = EXPECTED_CAPS[mode][column]
    assert plan.filtering_config.loop_max_entity_count == expected
    assert _harvest(plan).max_entity_count == expected


@pytest.mark.parametrize("domain_cap", [1, 25, 30, 49, 50, 51, 80, 150, 500])
def test_cap_is_monotonic_along_the_slider(domain_cap: int) -> None:
    """Looser never caps lower than tighter, for any pack cap."""
    caps = [
        follow_slider_limits({"loop_max_entity_count": domain_cap}, mode)["loop_max_entity_count"]
        for mode in SLIDER
    ]
    assert caps == sorted(caps, reverse=True), caps
    assert caps[SLIDER.index(SOURCE_ROW_FILTERING_MODE)] == domain_cap


def test_the_technical_pack_follows_the_slider_from_a_stored_source() -> None:
    """End to end from the source row's mode: the real technical pack and its 30."""
    stack = _stack("technical")
    assert stack.limits()["loop_max_entity_count"] == 30
    caps = {}
    for mode in SLIDER:
        plan = build_extraction_plan(stack, EngineSettings(), source_row={"filtering_mode": mode})
        assert plan.filtering_config.loop_max_entity_count == _harvest(plan).max_entity_count
        caps[mode] = _harvest(plan).max_entity_count
    assert caps == {mode: EXPECTED_CAPS[mode][0] for mode in SLIDER}


@pytest.mark.parametrize(
    ("domain", "own_mode", "cap"),
    [("news", "minimal", 25), ("scientific", "strict", 40)],
)
def test_a_packs_own_preset_keeps_its_tuning(domain: str, own_mode: str, cap: int) -> None:
    """No row, no override: the mode is the pack's own, not the user's, so the cap is as tuned."""
    plan = build_extraction_plan(_stack(domain), EngineSettings())
    assert plan.filtering_mode == own_mode
    assert plan.chunk_kwargs["domain_extraction_limits"]["loop_max_entity_count"] == cap
    assert plan.filtering_config.loop_max_entity_count == cap
    assert _harvest(plan).max_entity_count == cap


def test_an_explicit_override_moves_a_packs_cap() -> None:
    """A per-run override is the user's choice, even over the pack's own preset."""
    plan = build_extraction_plan(
        _stack("news"), EngineSettings(), filtering_mode_override="unfiltered"
    )
    assert plan.filtering_config.loop_max_entity_count == 200


def test_filtering_mode_choice_names_the_branch() -> None:
    settings = EngineSettings()
    news = _stack("news")
    assert filtering_mode_choice(news, settings) == ("minimal", False)
    assert filtering_mode_choice(news, settings, override="strict") == ("strict", True)
    assert filtering_mode_choice(news, settings, source_row={"filtering_mode": "lenient"}) == (
        "lenient",
        True,
    )


def test_the_settings_mode_counts_as_the_users_choice() -> None:
    """Reached only when neither the packs nor the floor name a preset; a configured mode moves the cap."""
    settings = EngineSettings(extraction=ExtractionSettings(extraction_filtering_mode="unfiltered"))
    with (
        patch.object(DomainStack, "filtering_mode", lambda _self: None),
        patch.object(DomainStack, "limits", lambda _self: {"loop_max_entity_count": 30}),
    ):
        assert filtering_mode_choice(_stack(), settings) == ("unfiltered", True)
        plan = build_extraction_plan(_stack(), settings)
    assert plan.filtering_config.loop_max_entity_count == 200


def test_alias_length_follows_the_slider_too() -> None:
    """The same rule for the other domain-tuned field the line parser reads."""
    lengths = {}
    for mode in SLIDER:
        plan = _plan_with_limits({"minimum_alias_length": 4}, mode)
        assert plan.filtering_config.minimum_alias_length == _harvest(plan).min_alias_length
        lengths[mode] = plan.filtering_config.minimum_alias_length
    # lenient shares the default's 2, so the pack's 4 holds; strict and maximum (3) raise
    # the length to at least 3, which 4 already is; minimal and unfiltered (1) lower it to 1.
    assert lengths == {
        "unfiltered": 1,
        "minimal": 1,
        "lenient": 4,
        "balanced": 4,
        "strict": 4,
        "maximum": 4,
    }


class TestFollowSlider:
    def test_default_mode_keeps_the_domain_value(self) -> None:
        assert (
            follow_slider("loop_max_entity_count", mode=SOURCE_ROW_FILTERING_MODE, domain_value=7)
            == 7
        )

    def test_direction_is_read_from_the_presets(self) -> None:
        # A larger value than the default's raises the floor; a smaller one lowers the ceiling.
        assert follow_slider("loop_max_entity_count", mode="lenient", domain_value=30) == 75
        assert follow_slider("loop_max_entity_count", mode="strict", domain_value=80) == 35
        # minimum_alias_length runs the other way along the slider; the same rule holds.
        assert follow_slider("minimum_alias_length", mode="strict", domain_value=1) == 3
        assert follow_slider("minimum_alias_length", mode="unfiltered", domain_value=4) == 1

    def test_preset_value_reads_the_preset_then_the_dataclass_default(self) -> None:
        assert preset_value("unfiltered", "loop_max_entity_count") == 200
        assert preset_value("balanced", "max_entity_degree") == 25

    def test_unknown_mode_or_field_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown filtering mode"):
            preset_value("raw", "loop_max_entity_count")
        with pytest.raises(ValueError, match="not a FilteringConfig field"):
            preset_value("balanced", "no_such_field")

    def test_limits_leave_other_keys_alone(self) -> None:
        limits = {"loop_max_entity_count": 30, "max_relationship_ratio": 5.0}
        moved = follow_slider_limits(limits, "unfiltered")
        assert moved == {"loop_max_entity_count": 200, "max_relationship_ratio": 5.0}
        assert limits["loop_max_entity_count"] == 30  # a fresh dict

    def test_limits_log_which_input_set_the_value(self) -> None:
        with capture_logs() as logs:
            follow_slider_limits({"loop_max_entity_count": 30}, "balanced")
            follow_slider_limits({"loop_max_entity_count": 30}, "unfiltered")
            follow_slider_limits({"loop_max_entity_count": 50}, "balanced")
        events = [(e["event"], e["resolved"]) for e in logs if e.get("field")]
        assert events == [
            ("filter_field_overridden_by_domain", 30),
            ("domain_limit_overridden_by_slider", 200),
        ]


@pytest.mark.parametrize("field", ["loop_max_entity_count", "minimum_alias_length"])
def test_the_presets_step_monotonically_along_the_slider(field: str) -> None:
    """``follow_slider`` reads direction from preset values; that is the slider's only if they are monotonic."""
    values = [preset_value(mode, field) for mode in SLIDER]
    assert values == sorted(values) or values == sorted(values, reverse=True), values
