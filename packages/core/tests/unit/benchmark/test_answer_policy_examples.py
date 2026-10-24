# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The grounded answer policy hands no benchmark answer to the model (H2, 2026-10-06 audit).

The policy's examples were War and Peace's own ("Bóry is Borís", "the elder
sister", "the niece"), answers and aliases of fixture and holdout questions.
These tests read every shipped fixture and every holdout and fail when a name
or an example phrase of the policy appears among their answer terms, aliases,
gold entities or wrong facts.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

from chaoscypher_core.benchmark.queries import load_queries
from chaoscypher_core.services.chat.engine.constants import GROUNDED_ANSWER_POLICY


_REPO = Path(__file__).resolve().parents[5]
_DATASETS = (
    _REPO / "packages" / "cli" / "src" / "chaoscypher_cli" / "benchmark" / "data" / "datasets"
)
_HOLDOUTS = _REPO / "internal" / "benchmarks" / "holdout"


def _fold(text: str) -> str:
    """Lower-case, strip accents and straighten apostrophes."""
    decomposed = unicodedata.normalize("NFD", text.lower().replace("’", "'"))  # noqa: RUF001 - curly to straight
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _fixture_files() -> list[Path]:
    """Every shipped dataset fixture and every holdout fixture."""
    return sorted(_DATASETS.glob("*/queries.yaml")) + sorted(_HOLDOUTS.glob("*.yaml"))


def _fixture_phrases(path: Path) -> set[str]:
    """Every answer-bearing phrase of one fixture, folded.

    Answer terms and each alias of an alias group, gold entities,
    ``must_not_contain``, the confusable and decoy entities, the aliases
    ``alias_evidence`` names and the people ``roles`` binds.
    """
    phrases: set[str] = set()
    for q in load_queries(path).queries:
        for term in q.answer_terms:
            phrases.update([term] if isinstance(term, str) else term)
        phrases.update(q.gold_entities)
        phrases.update(q.must_not_contain)
        phrases.update(q.wrong_entities)
        phrases.update(q.decoy_entities)
        phrases.update(q.alias_evidence)
        for role in q.roles:
            phrases.update(role["person"])
    return {_fold(p) for p in phrases if p.strip()}


def _policy_proper_nouns(policy: str) -> set[str]:
    """Capitalised words of the policy that do not merely open a line or a sentence.

    A single letter ("X and Y") is a placeholder, not a name.
    """
    nouns: set[str] = set()
    for m in re.finditer(r"[A-ZÀ-Þ][\w'’-]*", policy):  # noqa: RUF001 - either apostrophe
        before = policy[: m.start()].rstrip(" ")
        if not before or before.endswith(("\n", "-", ".", ":", "!", "?")) or len(m.group(0)) == 1:
            continue
        nouns.add(_fold(m.group(0)))
    return nouns


def _policy_examples(policy: str) -> list[str]:
    """The policy's examples: each parenthesised item (split at ';' and ',') and each quoted phrase."""
    examples: list[str] = []
    for group in re.findall(r"\(([^()]*)\)", policy):
        examples.extend(part.strip() for part in re.split(r"[;,]", group) if part.strip())
    examples.extend(re.findall(r"\"([^\"]+)\"", policy))
    return sorted({_fold(e.strip('" ')) for e in examples})


def _whole_phrase_in(phrase: str, text: str) -> bool:
    """``phrase`` in ``text`` with no letter or digit on either side."""
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def _leaks(policy: str) -> list[str]:
    """Every way the policy's names or examples coincide with a fixture phrase."""
    leaks: list[str] = []
    nouns = _policy_proper_nouns(policy)
    examples = _policy_examples(policy)
    for path in _fixture_files():
        phrases = _fixture_phrases(path)
        words = {w for p in phrases for w in re.findall(r"[a-z0-9']+", p)}
        leaks.extend(f"{path.name}: name {n!r}" for n in sorted(nouns & words))
        leaks.extend(
            f"{path.name}: {p!r} in example {e!r}"
            for e in examples
            for p in sorted(phrases)
            if _whole_phrase_in(p, e)
        )
        # a multi-word example phrase inside a fixture phrase ('the niece' in 'the niece of X')
        leaks.extend(
            f"{path.name}: example {e!r} in {p!r}"
            for e in examples
            if " " in e
            for p in sorted(phrases)
            if _whole_phrase_in(e, p)
        )
    return leaks


def test_the_fixtures_the_guard_reads_are_there() -> None:
    """The guard is only as good as its inputs: the shipped fixture is always read; the holdout
    when the private tree is present (the Docker CI runs on the public export, without
    ``internal/``).
    """
    names = [p.name for p in _fixture_files()]
    assert "queries.yaml" in names
    if _HOLDOUTS.is_dir():
        assert "war_and_peace_book1-holdout.yaml" in names
    else:
        pytest.skip("internal/benchmarks/holdout is not in this checkout (public export)")


def test_the_policy_names_no_fixture_answer() -> None:
    """No proper noun or example phrase of the policy is a term, alias, gold entity or wrong fact."""
    assert _leaks(GROUNDED_ANSWER_POLICY) == []


@pytest.mark.parametrize(
    ("example", "leak"),
    [
        ("(Bóry is Borís; Princess Drubetskáya is Anna Mikháylovna)", "name 'boris'"),
        ('("he", "the count", "the elder sister", "the niece")', "elder sister"),
    ],
)
def test_the_guard_catches_the_old_examples(example: str, leak: str) -> None:
    """The examples the policy carried until 2026-10-06 fail the guard."""
    leaks = _leaks(f"Names vary: a role refers to one person {example}.")
    assert any(leak in line for line in leaks), leaks


def test_a_one_letter_placeholder_is_no_name() -> None:
    """The policy's "X and Y" are placeholders: a fixture's 'x-api-key' header is no leak of them."""
    nouns = _policy_proper_nouns("Answer in parts: when asked about X and Y, name Pierre.")
    assert nouns == {_fold("Pierre")}
