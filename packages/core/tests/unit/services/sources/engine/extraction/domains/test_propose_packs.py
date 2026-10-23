# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""DomainRegistry.propose_packs: detection proposes packs, a hint the user confirms (spec 2026-10-07 § 5).

Every pack whose evidence (score minus its own base score) is at least
``_DETECTION_MIN_EVIDENCE`` is proposed, by score, at most two. The runner-up
margin only names the order; it never empties the proposal. Nothing above
the floor is the floor alone with ``low_confidence``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from chaoscypher_core.services.sources.engine.extraction.domains.configurable import (
    DEFAULT_DETECTION_BASE_SCORE,
)
from chaoscypher_core.services.sources.engine.extraction.domains.packs import (
    MAX_PROPOSED_PACKS,
)
from chaoscypher_core.services.sources.engine.extraction.domains.registry import (
    FLOOR_ONLY_CONFIDENCE,
    DomainRegistry,
    PackProposal,
    RankedDomain,
)


_BASE = DEFAULT_DETECTION_BASE_SCORE
_FLOOR = DomainRegistry._DETECTION_MIN_EVIDENCE
_MARGIN = DomainRegistry._DETECTION_MIN_MARGIN

_LITERARY_HISTORICAL = (
    "The novel opens in 1805, in the reign of Emperor Alexander, as the Napoleonic Wars spread "
    "across Europe. In the first chapter the narrator introduces the protagonist, a young prince, "
    "and his sister; the author draws each character with irony and metaphor, and long passages "
    "of dialogue carry the fiction. The plot follows the heroine through the war of the Third "
    "Coalition, the Battle of Austerlitz and the Treaty of Tilsit. Historians of the period "
    "describe the empire, its dynasty and its aristocracy; the archives record the campaign, the "
    "regiment, the general and the treaty. The story is told in the third person; its themes are "
    "love, death and fate, and the novel's prose moves between drawing rooms and battlefields. "
    "The century saw the French Revolution, the fall of the monarchy and the rise of an emperor; "
    "chroniclers, primary sources and a historian's account of the era frame each chapter of "
    "the book."
)
"""A novel about a war, written about as both: literary and historical evidence over the floor."""

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


def _plugin(name: str, confidence: float, *, can_handle: bool = True) -> Any:
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


def _install(reg: DomainRegistry, **scores: float) -> None:
    reg._plugins = {name: _plugin(name, score) for name, score in scores.items()}
    reg._plugins["generic"] = _plugin("generic", 5.0)  # the floor never competes


# --- The rule, on stub plugins ---------------------------------------------


def test_every_pack_over_the_floor_is_proposed_by_score(registry: DomainRegistry) -> None:
    """Two packs with evidence: both, the higher first."""
    _install(registry, literary=_BASE + 0.40, historical=_BASE + 0.80, news=_BASE + 0.10)
    proposal = registry.propose_packs("t", "x.txt", {})
    assert proposal.packs == ("historical", "literary")
    assert proposal.low_confidence is False
    assert proposal.reason == "packs_above_floor"


def test_at_most_two_packs_are_proposed(registry: DomainRegistry) -> None:
    """Three packs over the floor: the top two."""
    _install(registry, a=_BASE + 0.90, b=_BASE + 0.70, c=_BASE + 0.50)
    proposal = registry.propose_packs("t", "x.txt", {})
    assert MAX_PROPOSED_PACKS == 2
    assert proposal.packs == ("a", "b")
    assert [r.domain.name for r in proposal.ranking] == ["a", "b", "c"]


def test_one_strong_pack_proposes_one(registry: DomainRegistry) -> None:
    """A runner-up under the floor is ranked but not proposed."""
    _install(registry, technical=_BASE + 0.90, news=_BASE + _FLOOR - 0.01)
    proposal = registry.propose_packs("t", "x.txt", {})
    assert proposal.packs == ("technical",)
    assert proposal.reason == "one_pack_above_floor"
    assert [r.domain.name for r in proposal.ranking] == ["technical", "news"]


def test_nothing_over_the_floor_is_the_floor_alone(registry: DomainRegistry) -> None:
    """No pack with enough evidence: ``()``, low confidence, the floor's confidence."""
    _install(registry, news=_BASE + _FLOOR - 0.001, legal=_BASE + 0.05)
    proposal = registry.propose_packs("t", "x.txt", {})
    assert proposal.packs == ()
    assert proposal.low_confidence is True
    assert proposal.reason == "no_pack_above_floor"
    assert registry.proposal_confidence(proposal) == FLOOR_ONLY_CONFIDENCE
    assert [r.domain.name for r in proposal.ranking] == ["news", "legal"]


def test_no_candidates_is_the_floor_alone(registry: DomainRegistry) -> None:
    """Every plugin declining is the same floor-only proposal, with an empty ranking."""
    registry._plugins = {"news": _plugin("news", 0.9, can_handle=False)}
    proposal = registry.propose_packs("t", "x.txt", None)
    assert proposal == PackProposal(
        packs=(), ranking=[], low_confidence=True, reason="no_pack_above_floor"
    )


def test_the_margin_orders_and_never_empties(registry: DomainRegistry) -> None:
    """Two packs within the margin are both proposed, the higher first (before packs: generic)."""
    _install(registry, literary=0.74, intelligence=0.74 - _MARGIN + 0.001)
    proposal = registry.propose_packs("t", "x.txt", {})
    assert proposal.packs == ("literary", "intelligence")
    assert proposal.low_confidence is False
    assert proposal.reason == "packs_within_margin"


def test_a_lead_at_the_margin_is_not_within_it(registry: DomainRegistry) -> None:
    """The margin is inclusive, as before: a lead of exactly the margin is a clear order."""
    _install(registry, literary=0.75, intelligence=0.75 - _MARGIN)
    assert registry.propose_packs("t", "x.txt", {}).reason == "packs_above_floor"


def test_evidence_exactly_at_the_floor_is_proposed(registry: DomainRegistry) -> None:
    """The floor is inclusive."""
    _install(registry, biographical=_BASE + _FLOOR)
    assert registry.propose_packs("t", "x.txt", {}).packs == ("biographical",)


def test_the_floor_reads_each_packs_own_base_score(registry: DomainRegistry) -> None:
    """The same raw score clears the floor from a low base and not from a high one."""
    _install(registry, technical=0.55, legal=0.55)
    registry._configs = {
        "technical": {"detection": {"confidence": {"base_score": 0.2}}},
        "legal": {"detection": {"confidence": {"base_score": 0.3}}},
    }
    assert registry.propose_packs("t", "x.txt", {}).packs == ("technical",)


def test_the_confidence_is_the_first_packs_capped_score(registry: DomainRegistry) -> None:
    """Ranked raw, reported capped at the first pack's ``max_confidence``."""
    _install(registry, literary=1.62, investigation=0.90)
    registry._configs = {
        "literary": {"detection": {"confidence": {"base_score": 0.2, "max_confidence": 0.95}}}
    }
    proposal = registry.propose_packs("t", "x.txt", {})
    assert proposal.packs == ("literary", "investigation")
    assert proposal.ranking[0] == RankedDomain(registry._plugins["literary"], 1.62)
    assert registry.proposal_confidence(proposal) == pytest.approx(0.95)


def test_get_best_domain_is_the_first_proposed_pack(registry: DomainRegistry) -> None:
    """The deprecated wrapper: ``packs[0]`` at its confidence, else the floor at 0.1."""
    _install(registry, literary=_BASE + 0.40, historical=_BASE + 0.80)
    domain, confidence = registry.get_best_domain("t", "x.txt", {})
    assert domain.name == "historical"
    assert confidence == pytest.approx(_BASE + 0.80)

    _install(registry, news=_BASE + 0.10)
    domain, confidence = registry.get_best_domain("t", "x.txt", {})
    assert domain.name == "generic"
    assert confidence == FLOOR_ONLY_CONFIDENCE


# --- The rule, on the real plugins -----------------------------------------


def test_a_novel_about_a_war_proposes_historical_and_literary() -> None:
    """Literary and historical evidence over the floor: both, in score order."""
    registry = DomainRegistry(database_name="default")
    proposal = registry.propose_packs(_LITERARY_HISTORICAL, "novel.txt", {})
    assert proposal.packs == ("historical", "literary")
    scores = {r.domain.name: r.score for r in proposal.ranking}
    assert scores["historical"] > scores["literary"]
    assert proposal.low_confidence is False


def test_a_technical_page_proposes_technical_alone() -> None:
    """API and configuration docs: technical, and no second pack."""
    registry = DomainRegistry(database_name="default")
    proposal = registry.propose_packs(_TECHNICAL, "api.md", {})
    assert proposal.packs == ("technical",)


def test_a_short_neutral_text_is_the_floor_alone() -> None:
    """Too few keyword hits for any pack: the floor alone, low confidence."""
    registry = DomainRegistry(database_name="default")
    proposal = registry.propose_packs("Meeting moved to Tuesday. Bring the notes.", "n.txt", {})
    assert proposal.packs == ()
    assert proposal.low_confidence is True
    assert proposal.reason == "no_pack_above_floor"


def test_the_first_proposed_pack_is_not_always_ranking_zero(registry: DomainRegistry) -> None:
    """The top raw score short of its own floor, the runner-up clearing it: the runner-up is first.

    legal's base score is 0.3 and technical's 0.2, so legal's higher raw score
    carries less evidence. The wrapper and the confidence read the proposal's
    first pack, never ``ranking[0]``.
    """
    # legal 0.58 on base 0.3 (evidence 0.28); technical 0.55 on base 0.2 (evidence 0.35)
    _install(registry, legal=0.3 + _FLOOR - 0.02, technical=0.2 + _FLOOR + 0.05)
    registry._configs = {
        "legal": {"detection": {"confidence": {"base_score": 0.3}}},
        "technical": {"detection": {"confidence": {"base_score": 0.2}}},
    }
    proposal = registry.propose_packs("t", "x.txt", {})
    assert proposal.ranking[0].domain.name == "legal"  # the top raw score ...
    assert proposal.packs == ("technical",)  # ... is not proposed
    first = registry.first_proposed(proposal)
    assert first is not None and first.domain.name == "technical"
    assert registry.proposal_confidence(proposal) == pytest.approx(0.2 + _FLOOR + 0.05)
    domain, confidence = registry.get_best_domain("t", "x.txt", {})
    assert domain.name == "technical"
    assert confidence == pytest.approx(0.2 + _FLOOR + 0.05)
    assert registry.first_proposed(PackProposal((), [], True, "no_pack_above_floor")) is None
