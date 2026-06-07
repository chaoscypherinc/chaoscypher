# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for FilteringConfig preset resolution."""

import pytest

from chaoscypher_core.services.sources.engine.extraction.utils.filtering_config import (
    resolve_filtering_config,
)


class TestResolveFilteringConfig:
    """Tests for resolve_filtering_config preset resolution."""

    def test_balanced_preset_defaults(self) -> None:
        """Balanced preset returns expected default values."""
        config = resolve_filtering_config("balanced")
        assert config.evidence_validation_mode == "standard"
        assert config.strict_edge_type_constraints is False
        assert config.enable_type_constraints is True
        assert config.enable_plausibility_filter is True
        assert config.plausibility_threshold == 0.30
        assert config.enable_relationship_limits is True
        assert config.max_entity_degree == 25
        assert config.protect_orphans is False

    def test_maximum_preset(self) -> None:
        """Maximum preset enables all filters with drop behavior."""
        config = resolve_filtering_config("maximum")
        assert config.evidence_validation_mode == "strict"
        assert config.strict_edge_type_constraints is True
        assert config.plausibility_threshold == 0.40
        assert config.visual_content_plausibility_factor == 1.0
        assert config.protect_orphans is False

    def test_strict_preset(self) -> None:
        """Strict preset uses strict evidence but standard plausibility."""
        config = resolve_filtering_config("strict")
        assert config.evidence_validation_mode == "strict"
        assert config.strict_edge_type_constraints is True
        assert config.plausibility_threshold == 0.30
        assert config.visual_content_plausibility_factor == 1.0

    def test_lenient_preset(self) -> None:
        """Lenient preset uses narrative evidence and lower plausibility."""
        config = resolve_filtering_config("lenient")
        assert config.evidence_validation_mode == "narrative"
        assert config.strict_edge_type_constraints is False
        assert config.plausibility_threshold == 0.20
        assert config.plausibility_threshold_non_named == 0.10
        assert config.protect_orphans is False

    def test_minimal_preset(self) -> None:
        """Minimal preset disables most filters."""
        config = resolve_filtering_config("minimal")
        assert config.evidence_validation_mode == "relaxed"
        assert config.enable_type_constraints is False
        assert config.enable_plausibility_filter is False
        assert config.enable_structural_filter is False
        assert config.enable_entity_exclusions is False
        assert config.enable_relationship_limits is True
        assert config.max_entity_degree == 50
        assert config.protect_orphans is True

    def test_unfiltered_preset(self) -> None:
        """Unfiltered preset disables all quality filters."""
        config = resolve_filtering_config("unfiltered")
        assert config.evidence_validation_mode == "off"
        assert config.enable_type_constraints is False
        assert config.enable_plausibility_filter is False
        assert config.enable_relationship_limits is False
        assert config.protect_orphans is True

    def test_legacy_name_standard_rejected(self) -> None:
        """Legacy name 'standard' is rejected — use 'balanced'."""
        with pytest.raises(ValueError, match="standard"):
            resolve_filtering_config("standard")

    def test_legacy_name_narrative_rejected(self) -> None:
        """Legacy name 'narrative' is rejected — use 'lenient'."""
        with pytest.raises(ValueError, match="narrative"):
            resolve_filtering_config("narrative")

    def test_legacy_name_precise_rejected(self) -> None:
        """Legacy name 'precise' is rejected — use 'strict'."""
        with pytest.raises(ValueError, match="precise"):
            resolve_filtering_config("precise")

    def test_legacy_name_permissive_rejected(self) -> None:
        """Legacy name 'permissive' is rejected — use 'minimal'."""
        with pytest.raises(ValueError, match="permissive"):
            resolve_filtering_config("permissive")

    def test_legacy_name_raw_rejected(self) -> None:
        """Legacy name 'raw' is rejected — use 'unfiltered'."""
        with pytest.raises(ValueError, match="raw"):
            resolve_filtering_config("raw")

    def test_domain_overrides_layer_on_preset(self) -> None:
        """Domain overrides take precedence over preset defaults."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={
                "evidence_validation_mode": "narrative",
                "max_entity_degree": 40,
            },
        )
        assert config.evidence_validation_mode == "narrative"
        assert config.max_entity_degree == 40
        assert config.enable_plausibility_filter is True
        assert config.plausibility_threshold == 0.30

    def test_source_overrides_highest_priority(self) -> None:
        """Per-source overrides take precedence over domain and preset."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={"evidence_validation_mode": "narrative"},
            source_overrides={"evidence_validation_mode": "relaxed"},
        )
        assert config.evidence_validation_mode == "relaxed"

    def test_unknown_mode_raises_value_error(self) -> None:
        """Unknown mode name raises ValueError (no silent fallback)."""
        with pytest.raises(ValueError, match="unknown_mode"):
            resolve_filtering_config("unknown_mode")

    def test_unknown_domain_keys_logged_by_default(self) -> None:
        """Default behavior: typo logged at WARNING, filtering proceeds with sanitized dict."""
        import structlog.testing

        with structlog.testing.capture_logs() as captured:
            config = resolve_filtering_config(
                "balanced",
                domain_overrides={
                    "enable_structural_filter": False,
                    "enable_typeconstraints": True,  # typo of enable_type_constraints
                },
            )
        events = [e["event"] for e in captured]
        assert "domain_config_unknown_keys_dropped" in events
        dropped_event = next(
            e for e in captured if e["event"] == "domain_config_unknown_keys_dropped"
        )
        assert "enable_typeconstraints" in dropped_event["unknown_keys"]
        # The valid override DID apply.
        assert config.enable_structural_filter is False

    def test_unknown_domain_keys_raise_in_strict_mode(self) -> None:
        """strict_validation=True elevates unknown-key drops to ValidationError."""
        from chaoscypher_core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="enable_typeconstraints"):
            resolve_filtering_config(
                "balanced",
                domain_overrides={"enable_typeconstraints": True},
                strict_validation=True,
            )

    def test_no_unknown_keys_no_log(self) -> None:
        """Domain config with all-valid keys: no warning, no raise."""
        import structlog.testing

        with structlog.testing.capture_logs() as captured:
            config = resolve_filtering_config(
                "balanced",
                domain_overrides={"enable_structural_filter": False},
            )
        events = [e["event"] for e in captured]
        assert "domain_config_unknown_keys_dropped" not in events
        assert config.enable_structural_filter is False

    def test_invalid_override_keys_ignored(self) -> None:
        """Override keys that aren't FilteringConfig fields are silently dropped by default."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={
                "not_a_real_field": 42,
                "evidence_validation_mode": "narrative",
            },
        )
        assert config.evidence_validation_mode == "narrative"
        assert not hasattr(config, "not_a_real_field")

    # -----------------------------------------------------------------------
    # Phase 4 (2026-05-08): enable_direction_correction cascade tests
    # -----------------------------------------------------------------------

    def test_enable_direction_correction_default_true(self) -> None:
        """FilteringConfig defaults enable_direction_correction to True."""
        config = resolve_filtering_config("balanced")
        assert config.enable_direction_correction is True

    def test_enable_direction_correction_domain_override_false(self) -> None:
        """Domain override can set enable_direction_correction=False."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={"enable_direction_correction": False},
        )
        assert config.enable_direction_correction is False

    def test_enable_direction_correction_source_override_wins_over_domain(self) -> None:
        """Per-source override beats domain override (source > domain > global)."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={"enable_direction_correction": False},
            source_overrides={"enable_direction_correction": True},
        )
        assert config.enable_direction_correction is True

    def test_enable_direction_correction_cascade_source_none_domain_wins(self) -> None:
        """When source_overrides does not include the key, domain wins."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={"enable_direction_correction": False},
            source_overrides={"evidence_validation_mode": "relaxed"},
        )
        # domain: False; source does not override direction_correction → domain wins
        assert config.enable_direction_correction is False

    def test_enable_direction_correction_cascade_all_default(self) -> None:
        """When no override provides enable_direction_correction, preset default=True wins."""
        config = resolve_filtering_config(
            "strict",
            domain_overrides={"evidence_validation_mode": "strict"},
            source_overrides={"max_entity_degree": 10},
        )
        assert config.enable_direction_correction is True

    # -----------------------------------------------------------------------
    # Phase 4 (2026-05-08): protect_orphans cascade tests
    # -----------------------------------------------------------------------

    def test_protect_orphans_preset_defaults(self) -> None:
        """Preset table maps to expected protect_orphans values.

        unfiltered/minimal preserve orphans (protect_orphans=True);
        lenient/balanced/strict/maximum drop them (protect_orphans=False).
        """
        assert resolve_filtering_config("unfiltered").protect_orphans is True
        assert resolve_filtering_config("minimal").protect_orphans is True
        assert resolve_filtering_config("lenient").protect_orphans is False
        assert resolve_filtering_config("balanced").protect_orphans is False
        assert resolve_filtering_config("strict").protect_orphans is False
        assert resolve_filtering_config("maximum").protect_orphans is False

    def test_protect_orphans_default_false_semantics_preserved(self) -> None:
        """Default protect_orphans=False means orphans are dropped (same as
        the old filter_orphan_entities=True default).
        """
        config = resolve_filtering_config("balanced")
        assert config.protect_orphans is False

    def test_protect_orphans_domain_override_true(self) -> None:
        """Domain override can set protect_orphans=True (keep orphans)."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={"protect_orphans": True},
        )
        assert config.protect_orphans is True

    def test_protect_orphans_source_override_wins_over_domain(self) -> None:
        """Per-source override beats domain override (source > domain > preset)."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={"protect_orphans": True},
            source_overrides={"protect_orphans": False},
        )
        assert config.protect_orphans is False

    def test_protect_orphans_cascade_source_none_domain_wins(self) -> None:
        """When source_overrides does not include the key, domain wins."""
        config = resolve_filtering_config(
            "balanced",
            domain_overrides={"protect_orphans": True},
            source_overrides={"evidence_validation_mode": "relaxed"},
        )
        # domain: True; source does not override protect_orphans → domain wins
        assert config.protect_orphans is True

    def test_protect_orphans_cascade_per_source_wins(self) -> None:
        """Per-source > domain > preset: full cascade resolution."""
        config = resolve_filtering_config(
            "balanced",  # preset → False
            domain_overrides={"protect_orphans": True},  # domain → True
            source_overrides={"protect_orphans": False},  # per-source → False (wins)
        )
        assert config.protect_orphans is False

    def test_protect_orphans_unfiltered_preset_is_true(self) -> None:
        """Unfiltered preset explicitly sets protect_orphans=True (keep all entities)."""
        config = resolve_filtering_config("unfiltered")
        assert config.protect_orphans is True
        # Verify source override can still override unfiltered preset
        config_override = resolve_filtering_config(
            "unfiltered",
            source_overrides={"protect_orphans": False},
        )
        assert config_override.protect_orphans is False


class TestSelectorRejectedInDomainOverrides:
    """The preset selector must never appear in ``domain_overrides``.

    Regression for the historical ``domain_config_unknown_keys_dropped``
    warning: ``extraction_filtering_mode`` is the preset selector and has
    its own ``mode=`` argument. Earlier code threaded it through the
    overrides dict and ``resolve_filtering_config`` soft-dropped it with a
    warning on every chunk. The selector is now rejected hard so any
    re-occurrence fails fast in test instead of becoming silent log noise.
    """

    def test_selector_in_domain_overrides_raises(self) -> None:
        from chaoscypher_core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="mode= argument"):
            resolve_filtering_config(
                "balanced",
                domain_overrides={"extraction_filtering_mode": "strict"},
            )

    def test_selector_in_domain_overrides_raises_even_when_other_keys_valid(self) -> None:
        from chaoscypher_core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="mode= argument"):
            resolve_filtering_config(
                "balanced",
                domain_overrides={
                    "extraction_filtering_mode": "strict",
                    "max_entity_degree": 30,  # legitimately a FilteringConfig field
                },
            )


class TestConfigurableDomainSeparatesModeFromLimits:
    """``ConfigurableDomain`` must surface the preset selector and field
    overrides through two distinct accessors.

    Conflating them into ``get_extraction_limits()`` (the historical bug)
    led the selector to be passed as a domain override and silently
    dropped. Keeping them split at the source removes the leak.
    """

    @staticmethod
    def _make_domain(config: dict[str, object]) -> object:
        from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
            ConfigurableDomain,
        )

        return ConfigurableDomain(config)

    def test_get_extraction_limits_excludes_mode_selector(self) -> None:
        domain = self._make_domain(
            {
                "name": "test-domain",
                "extraction_filtering_mode": "strict",
                "extraction_limits": {"max_entity_degree": 30},
            }
        )
        limits = domain.get_extraction_limits()  # type: ignore[attr-defined]
        assert "extraction_filtering_mode" not in limits, (
            "get_extraction_limits() must not inline the preset selector — "
            "use get_filtering_mode() instead"
        )
        assert limits == {"max_entity_degree": 30}

    def test_get_filtering_mode_returns_selector(self) -> None:
        domain = self._make_domain({"name": "t", "extraction_filtering_mode": "strict"})
        assert domain.get_filtering_mode() == "strict"  # type: ignore[attr-defined]

    def test_get_filtering_mode_none_when_absent(self) -> None:
        domain = self._make_domain({"name": "t"})
        assert domain.get_filtering_mode() is None  # type: ignore[attr-defined]

    def test_limits_and_mode_can_round_trip_through_resolver(self) -> None:
        """A real domain config feeds cleanly into resolve_filtering_config."""
        domain = self._make_domain(
            {
                "name": "t",
                "extraction_filtering_mode": "strict",
                "extraction_limits": {"max_entity_degree": 30},
            }
        )
        config = resolve_filtering_config(
            mode=domain.get_filtering_mode(),  # type: ignore[attr-defined]
            domain_overrides=domain.get_extraction_limits(),  # type: ignore[attr-defined]
        )
        # strict preset baseline + the per-domain max_entity_degree override
        assert config.strict_edge_type_constraints is True
        assert config.max_entity_degree == 30
