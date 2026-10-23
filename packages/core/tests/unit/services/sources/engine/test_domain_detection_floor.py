# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""DomainRegistry.get_best_domain (the first proposed pack): the evidence floor and the floor fallback.

A plugin's score is its own ``base_score`` plus keyword/pattern boosts, so
the floor is on the pack's own scale (score minus base, its *evidence*). The
old raw floor of 1.0 was unreachable on the product's sample (Book One scored
literary 0.74), so every auto-detect fell back to generic (audit W2-5); the
real-plugin tests below pin that a novel auto-detects as literary with high
confidence and a technical page does not.

Since packs (spec 2026-10-07 § 5) ``get_best_domain`` is a deprecated wrapper
over ``propose_packs`` (``test_propose_packs.py``): it returns the first
proposed pack, and a near-tie no longer falls back (both are proposed). No
pack over the floor returns the ``generic`` plugin at confidence 0.1.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains import (
    create_domain_sample_text,
)
from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    DEFAULT_DETECTION_BASE_SCORE,
)
from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
    DomainRegistry,
)
from chaoscypher_core.services.sources.engine.extraction.orchestration import (
    detect_extraction_domain,
)


_BOOK_ONE = (
    Path(__file__).parents[6]
    / "cli/src/chaoscypher_cli/benchmark/data/datasets/war_and_peace_book1/war_and_peace_book1.txt"
)

_TECHNICAL = (
    "The API server reads its configuration from a YAML file at startup. Set the port, "
    "the database URL and the log level in settings.yaml, or override any key with an "
    "environment variable. Each endpoint returns JSON; errors use a standard envelope with "
    "a code and a message. To deploy, build the Docker image, push it to your registry and "
    "run it behind a reverse proxy such as nginx. The CLI exposes the same operations: run "
    "`app config show` to print the effective configuration, and `app migrate` to apply "
    "database schema migrations before upgrading. Requests are authenticated with a bearer "
    "token in the Authorization header; tokens expire after one hour and can be refreshed "
    "through the /auth/refresh endpoint. Rate limiting is applied per client IP with a "
    "default of 100 requests per minute. The Python SDK wraps these endpoints with typed "
    "functions, retries on HTTP 503 with exponential backoff, and supports async clients."
)

# A stub plugin has no config, so its base score is the configurable default.
_BASE = DEFAULT_DETECTION_BASE_SCORE
_FLOOR = DomainRegistry._DETECTION_MIN_EVIDENCE
_MARGIN = DomainRegistry._DETECTION_MIN_MARGIN


def _make_plugin(name: str, can_handle: bool, confidence: float) -> Any:
    """Return a minimal plugin stub for the registry's lookup loop."""
    plug = MagicMock()
    plug.name = name
    plug.can_analyze = MagicMock(return_value=(can_handle, confidence))
    return plug


@pytest.fixture
def registry() -> DomainRegistry:
    """A DomainRegistry with its plugin map emptied (tests install stubs)."""
    reg = DomainRegistry(database_name="default")
    reg._plugins = {}
    reg._configs = {}
    return reg


def _set_plugins(reg: DomainRegistry, plugins: dict[str, Any]) -> None:
    """Install stub plugins."""
    reg._plugins = plugins


def test_a_clear_winner_wins(registry: DomainRegistry) -> None:
    """Evidence over the floor and a lead over the margin: returned as-is."""
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin("biographical", True, 1.20),
            "news": _make_plugin("news", True, 0.85),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "biographical"
    assert conf == pytest.approx(1.0)  # ranked raw, reported capped at 1


def test_a_winner_under_the_old_raw_floor_wins_on_its_own_scale(
    registry: DomainRegistry,
) -> None:
    """0.74 never cleared the old 1.0 floor; on the plugin's own scale it is +0.64 of evidence."""
    _set_plugins(
        registry,
        {
            "literary": _make_plugin("literary", True, 0.74),
            "technical": _make_plugin("technical", True, 0.46),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "literary"
    assert conf == pytest.approx(0.74)


def test_the_floor_reads_the_winners_own_base_score(registry: DomainRegistry) -> None:
    """The same raw score clears the floor from a low base and not from a high one."""
    score = 0.55
    _set_plugins(
        registry,
        {
            "technical": _make_plugin("technical", True, score),
            "generic": _make_plugin("generic", True, 0.1),
        },
    )
    registry._configs = {"technical": {"detection": {"confidence": {"base_score": 0.2}}}}
    assert registry.get_best_domain("t", "x.txt", {})[0].name == "technical"
    registry._configs = {"technical": {"detection": {"confidence": {"base_score": 0.3}}}}
    assert registry.get_best_domain("t", "x.txt", {})[0].name == "generic"


def test_evidence_exactly_at_the_floor_wins(registry: DomainRegistry) -> None:
    """The floor is inclusive."""
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin("biographical", True, _BASE + _FLOOR),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    assert registry.get_best_domain("t", "x.txt", {})[0].name == "biographical"


def test_evidence_just_below_the_floor_falls_to_generic(registry: DomainRegistry) -> None:
    """A winner with too few keyword hits is generic at 0.1."""
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin("biographical", True, _BASE + _FLOOR - 0.001),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("t", "x.txt", {})
    assert domain.name == "generic"
    assert conf == pytest.approx(0.10)


def test_a_winner_close_to_the_runner_up_is_still_the_first_pack(registry: DomainRegistry) -> None:
    """A near-tie proposes both (literary 0.74, intelligence 0.70); the wrapper reads the higher."""
    _set_plugins(
        registry,
        {
            "literary": _make_plugin("literary", True, 0.74),
            "intelligence": _make_plugin("intelligence", True, 0.74 - _MARGIN + 0.001),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("t", "x.txt", {})
    assert domain.name == "literary"
    assert conf == pytest.approx(0.74)
    assert registry.propose_packs("t", "x.txt", {}).packs == ("literary", "intelligence")


def test_a_lead_exactly_at_the_margin_wins(registry: DomainRegistry) -> None:
    """The margin is inclusive."""
    _set_plugins(
        registry,
        {
            "literary": _make_plugin("literary", True, 0.75),
            "intelligence": _make_plugin("intelligence", True, 0.75 - _MARGIN),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    assert registry.get_best_domain("t", "x.txt", {})[0].name == "literary"


def test_no_can_handle_candidates_falls_to_generic(registry: DomainRegistry) -> None:
    """If every plugin says can_handle=False, fall back to generic."""
    _set_plugins(
        registry,
        {
            "news": _make_plugin("news", False, 0.30),
            "biographical": _make_plugin("biographical", False, 0.20),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "generic"
    assert conf == pytest.approx(0.10)


def test_generic_is_never_a_primary_winner(registry: DomainRegistry) -> None:
    """The registry excludes generic from the competition; it is only the fallback."""
    _set_plugins(
        registry,
        {
            "biographical": _make_plugin("biographical", True, 1.20),
            "generic": _make_plugin("generic", True, 5.0),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "biographical"
    assert conf == pytest.approx(1.0)  # ranked raw, reported capped at 1


def test_no_plugins_returns_minimal_fallback(registry: DomainRegistry) -> None:
    """When no plugins are registered at all, return the minimal fallback (not crash)."""
    _set_plugins(registry, {})
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "fallback"
    assert conf == 0.0


def test_plugin_raising_exception_is_skipped(registry: DomainRegistry) -> None:
    """A plugin whose can_analyze raises does not crash the detection pass."""
    broken = MagicMock()
    broken.name = "broken"
    broken.can_analyze = MagicMock(side_effect=RuntimeError("boom"))
    _set_plugins(
        registry,
        {
            "broken": broken,
            "biographical": _make_plugin("biographical", True, 1.20),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    domain, conf = registry.get_best_domain("any text", "x.txt", {})
    assert domain.name == "biographical"
    assert conf == pytest.approx(1.0)  # ranked raw, reported capped at 1


# --- The real plugins on real text -----------------------------------------


def _book_one_paragraphs(count: int) -> list[str]:
    """The first ``count`` paragraphs of War and Peace, Book One."""
    text = _BOOK_ONE.read_text(encoding="utf-8")
    return [p.strip() for p in text.split("\n\n") if p.strip()][:count]


def test_a_few_paragraphs_of_a_novel_auto_detect_as_literary() -> None:
    """Fourteen paragraphs of Book One: literary, not the low-confidence generic fallback."""
    registry = DomainRegistry(database_name="default")
    sample = "\n\n".join(_book_one_paragraphs(14))
    result = detect_extraction_domain(registry, None, sample, "war_and_peace_book1.txt", {})
    assert result["packs"] == ("literary",)
    assert result["detected_domain"] == "literary"
    assert result["low_confidence"] is False


def test_the_product_sample_of_book_one_auto_detects_as_literary() -> None:
    """The import path's own sampling (``create_domain_sample_text``) over Book One's paragraphs."""
    registry = DomainRegistry(database_name="default")
    paragraphs = _book_one_paragraphs(10_000)
    chunks: list[str] = []
    current = ""
    for p in paragraphs:  # ~800-char chunks, the size the chunker produces
        current = f"{current}\n\n{p}" if current else p
        if len(current) >= 800:
            chunks.append(current)
            current = ""
    sample = create_domain_sample_text(chunks)
    result = detect_extraction_domain(registry, None, sample, "war_and_peace_book1.txt", {})
    assert result["detected_domain"] == "literary"
    assert result["low_confidence"] is False


def test_a_technical_page_does_not_auto_detect_as_literary() -> None:
    """API and configuration docs land on technical, never literary."""
    registry = DomainRegistry(database_name="default")
    result = detect_extraction_domain(registry, None, _TECHNICAL, "api.md", {})
    assert result["detected_domain"] != "literary"
    assert result["detected_domain"] == "technical"


_SLICE = 8_000
"""The raw slice the wave-3 auditor took: what a caller that slices instead of sampling reads."""


@pytest.mark.parametrize("where", ["start", "50k", "end"])
def test_a_raw_slice_of_book_one_auto_detects_as_literary(where: str) -> None:
    """8,000 characters from the start, from character 50,000 and from the end: literary, confidently.

    Before audit wave 3 the start fell to generic (literary 0.74, intelligence
    0.70 on "secret" and "Morio"), 50k read as biographical (father, child,
    family, friend, career) and every plugin matched keywords inside other
    words ("nist" in "minister", "verse" in "conversation").
    """
    text = _BOOK_ONE.read_text(encoding="utf-8")
    start = {"start": 0, "50k": 50_000, "end": len(text) - _SLICE}[where]
    registry = DomainRegistry(database_name="default")
    result = detect_extraction_domain(
        registry, None, text[start : start + _SLICE], "war_and_peace_book1.txt", {}
    )
    assert result["detected_domain"] == "literary", result["ranking"][:3]
    assert result["low_confidence"] is False
    others = {r["domain"] for r in result["ranking"][1:4]}
    assert not others & {"intelligence", "news", "biographical"}, result["ranking"][:4]


def test_the_queue_and_the_import_path_sample_a_source_alike() -> None:
    """``source_domain_sample`` is ``create_domain_sample_text`` over the source's chunks for extraction."""
    from unittest.mock import MagicMock

    from chaoscypher_core.services.sources.engine.extraction.domains import source_domain_sample

    chunks = [{"id": f"c{i}", "content": f"Paragraph {i}. " * 50} for i in range(10)]
    adapter = MagicMock()
    adapter.get_chunks_for_extraction.return_value = chunks
    assert source_domain_sample(adapter, "s1", "db") == create_domain_sample_text(
        chunks, content_key="content"
    )
    adapter.get_chunks_for_extraction.assert_called_once_with(source_id="s1", database_name="db")


@pytest.mark.parametrize(
    ("term", "text", "hit"),
    [
        ("NIST", "the minister arrived", False),
        ("NIST", "per NIST guidance", True),
        ("MORI", "the Abbé Morio smiled", False),
        ("FLIR", "she liked to flirt", False),
        ("verse", "their conversation", False),
        ("verse", "a verse of Pushkin", True),
        ("plea", "with pleasure", False),
        ("import", "an important guest", False),
        ("exploit", "they exploited it", True),
        ("teach", "the teaching of Buddha", True),
        ("spirit", "spiritual life", True),
        ("he said", "he said nothing", True),
        ("v.", "Marbury v. Madison", True),
    ],
)
def test_a_keyword_matches_as_a_word_never_inside_one(term: str, text: str, hit: bool) -> None:
    """``keyword_pattern``: acronyms by case, words with their inflections, never a substring."""
    from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
        keyword_pattern,
    )

    assert bool(keyword_pattern(term).search(text)) is hit


def test_no_domain_config_carries_a_replacement_character() -> None:
    """A U+FFFD in a plugin is lost text: a life-span regex that matched nothing, a § gone from guidance."""
    plugins = Path(__file__).parents[5] / (
        "src/chaoscypher_core/services/sources/engine/extraction/domains/plugins"
    )
    files = sorted(plugins.glob("*.jsonld"))
    assert files
    broken = [f.name for f in files if "�" in f.read_text(encoding="utf-8")]
    assert broken == []


def test_the_reported_confidence_is_capped_at_the_domains_max_confidence(
    registry: DomainRegistry,
) -> None:
    """A whole novel scores literary 1.62: ranked and judged raw, reported at its ``max_confidence``."""
    _set_plugins(
        registry,
        {
            "literary": _make_plugin("literary", True, 1.62),
            "investigation": _make_plugin("investigation", True, 0.90),
            "generic": _make_plugin("generic", True, 0.10),
        },
    )
    registry._configs = {
        "literary": {"detection": {"confidence": {"base_score": 0.2, "max_confidence": 0.95}}}
    }
    domain, conf = registry.get_best_domain("t", "x.txt", {})
    assert domain.name == "literary"
    assert conf == pytest.approx(0.95)
    # the margin is read on raw scores: a capped runner-up does not tie the winner
    assert [d.name for d, _ in registry.rank_domains("t", "x.txt", {})][:2] == [
        "literary",
        "investigation",
    ]
