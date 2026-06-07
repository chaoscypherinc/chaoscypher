# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ConfigurableDomain.get_type_aliases() and live plugin wiring.

Confirms the accessor reads the domain plugin's optional ``type_aliases``
block, and that the two shipped plugins that declare aliases
(``literary`` and ``news``) actually load and expose them.
"""

from __future__ import annotations

import json
from pathlib import Path

from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    ConfigurableDomain,
)


_PLUGINS_DIR = Path(__file__).resolve().parents[6] / (
    "src/chaoscypher_core/services/sources/engine/extraction/domains/plugins"
)


def _load_plugin(name: str) -> ConfigurableDomain:
    """Load a shipped plugin .jsonld into a ConfigurableDomain."""
    path = _PLUGINS_DIR / f"{name}.jsonld"
    with path.open() as f:
        return ConfigurableDomain(json.load(f))


class TestGetTypeAliasesAccessor:
    """Direct accessor behavior on ConfigurableDomain."""

    def test_missing_block_returns_empty_dict(self) -> None:
        domain = ConfigurableDomain({"name": "test"})
        assert domain.get_type_aliases() == {}

    def test_returns_declared_aliases(self) -> None:
        domain = ConfigurableDomain(
            {
                "name": "test",
                "type_aliases": {
                    "Historical Figure": "Character",
                    "Historical Event": "Event",
                },
            }
        )
        assert domain.get_type_aliases() == {
            "Historical Figure": "Character",
            "Historical Event": "Event",
        }

    def test_non_dict_value_returns_empty(self) -> None:
        """Bad shape in jsonld (e.g. a list) is treated as no aliases."""
        domain = ConfigurableDomain(
            {"name": "test", "type_aliases": ["Historical Figure", "Character"]}
        )
        assert domain.get_type_aliases() == {}

    def test_drops_non_string_entries(self) -> None:
        """Individual entries with non-string keys or values are skipped."""
        domain = ConfigurableDomain(
            {
                "name": "test",
                "type_aliases": {
                    "Historical Figure": "Character",
                    "BadValueType": 42,
                    "": "Character",  # empty key
                    "Suspect": "",  # empty value
                },
            }
        )
        assert domain.get_type_aliases() == {"Historical Figure": "Character"}


class TestShippedPluginAliases:
    """Lock in the alias mappings shipped on ``literary`` and ``news``.

    Wiring guard: catches a regression where someone removes the
    type_aliases block or accidentally breaks the json structure.
    """

    def test_literary_ships_historical_figure_and_event_aliases(self) -> None:
        domain = _load_plugin("literary")
        aliases = domain.get_type_aliases()
        assert aliases.get("Historical Figure") == "Character"
        assert aliases.get("Historical Event") == "Event"

    def test_news_ships_person_role_aliases(self) -> None:
        domain = _load_plugin("news")
        aliases = domain.get_type_aliases()
        assert aliases.get("Suspect") == "Person"
        assert aliases.get("Victim") == "Person"
        assert aliases.get("Stakeholder") == "Person"

    def test_literary_canonical_targets_are_real_node_templates(self) -> None:
        """Each canonical target must be a defined NodeTemplate in the same plugin.

        Catches a typo like aliasing to ``Charactor`` instead of ``Character``.
        """
        domain = _load_plugin("literary")
        aliases = domain.get_type_aliases()
        templates = domain.get_templates()
        node_names = {t["name"] for t in templates["node_templates"] if t.get("name")}
        for alias, canonical in aliases.items():
            assert canonical in node_names, (
                f"literary type_aliases[{alias!r}] = {canonical!r} but "
                f"{canonical!r} is not a defined NodeTemplate"
            )

    def test_news_canonical_targets_are_real_node_templates(self) -> None:
        domain = _load_plugin("news")
        aliases = domain.get_type_aliases()
        templates = domain.get_templates()
        node_names = {t["name"] for t in templates["node_templates"] if t.get("name")}
        for alias, canonical in aliases.items():
            assert canonical in node_names, (
                f"news type_aliases[{alias!r}] = {canonical!r} but "
                f"{canonical!r} is not a defined NodeTemplate"
            )

    def test_investigation_ships_role_aliases(self) -> None:
        """Investigation domain aliases parallel news: role-suffix → Person."""
        domain = _load_plugin("investigation")
        aliases = domain.get_type_aliases()
        assert aliases.get("Suspect") == "Person"
        assert aliases.get("Victim") == "Person"
        assert aliases.get("Witness") == "Person"

    def test_investigation_canonical_targets_are_real_node_templates(self) -> None:
        domain = _load_plugin("investigation")
        aliases = domain.get_type_aliases()
        templates = domain.get_templates()
        node_names = {t["name"] for t in templates["node_templates"] if t.get("name")}
        for alias, canonical in aliases.items():
            assert canonical in node_names, (
                f"investigation type_aliases[{alias!r}] = {canonical!r} but "
                f"{canonical!r} is not a defined NodeTemplate"
            )

    def test_no_slash_named_templates_in_shipped_plugins(self) -> None:
        """Slash-named NodeTemplates are forbidden in shipped plugins.

        Before this PR, educational and financial had compound templates like
        ``Author/Educator``, ``Theory/Framework``, ``Analyst/Investor`` that
        smushed two distinct concepts together. The user pushed back ("if
        it's both then it has two, no?") so the templates were split. Guard
        against the pattern returning.
        """
        for plugin_name in (
            "biographical",
            "cybersecurity",
            "educational",
            "financial",
            "generic",
            "historical",
            "investigation",
            "legal",
            "literary",
            "medical",
            "news",
            "philosophical",
            "political",
            "scientific",
            "technical",
            "theological",
        ):
            domain = _load_plugin(plugin_name)
            templates = domain.get_templates()
            slash_templates = [
                t["name"] for t in templates["node_templates"] if t.get("name") and "/" in t["name"]
            ]
            assert slash_templates == [], (
                f"plugin {plugin_name!r} has slash-named NodeTemplate(s): "
                f"{slash_templates}. Split into distinct templates instead."
            )

    def test_educational_split_templates_present(self) -> None:
        """Each former educational slash-template is now two distinct templates."""
        domain = _load_plugin("educational")
        templates = domain.get_templates()
        node_names = {t["name"] for t in templates["node_templates"] if t.get("name")}
        # Author/Educator → Author + Educator
        assert "Author" in node_names
        assert "Educator" in node_names
        # Theory/Framework → Theory + Framework
        assert "Theory" in node_names
        assert "Framework" in node_names
        # Diagram/Figure → Diagram + Figure
        assert "Diagram" in node_names
        assert "Figure" in node_names
        # Standard/Benchmark → Standard + Benchmark
        assert "Standard" in node_names
        assert "Benchmark" in node_names
        # Subject/Discipline → Subject + Discipline
        assert "Subject" in node_names
        assert "Discipline" in node_names

    def test_financial_split_templates_present(self) -> None:
        """Each former financial slash-template is now two distinct templates."""
        domain = _load_plugin("financial")
        templates = domain.get_templates()
        node_names = {t["name"] for t in templates["node_templates"] if t.get("name")}
        # Analyst/Investor → Analyst + Investor
        assert "Analyst" in node_names
        assert "Investor" in node_names
        # Index/Benchmark → Index + Benchmark
        assert "Index" in node_names
        assert "Benchmark" in node_names
        # Sector/Industry → Sector + Industry
        assert "Sector" in node_names
        assert "Industry" in node_names

    def test_educational_teacher_alias_kept(self) -> None:
        """``Teacher`` is a very common LLM emission; alias it to the canonical ``Educator``."""
        domain = _load_plugin("educational")
        aliases = domain.get_type_aliases()
        assert aliases.get("Teacher") == "Educator"

    def test_educational_canonical_targets_are_real_node_templates(self) -> None:
        domain = _load_plugin("educational")
        aliases = domain.get_type_aliases()
        templates = domain.get_templates()
        node_names = {t["name"] for t in templates["node_templates"] if t.get("name")}
        for alias, canonical in aliases.items():
            assert canonical in node_names, (
                f"educational type_aliases[{alias!r}] = {canonical!r} but "
                f"{canonical!r} is not a defined NodeTemplate"
            )
