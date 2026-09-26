# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""ProbeScorer - pass/fail checks over probe records.

Every check is a pure function of one probe record. A probe passes only if
every one of its checks passes. The headline is the tier-weighted pass rate
(easy 1, medium 2, hard 3): tiers are ordinal difficulty, not a trade-off,
so a weighted sum is defensible where a quality/speed composite was not.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from chaoscypher_core.benchmark.types import RawOutput, ScoreResult


PROBE_SCORER_VERSION = 1
TIER_WEIGHTS: dict[str, int] = {"easy": 1, "medium": 2, "hard": 3}

Check = Callable[[dict[str, Any], dict[str, Any]], tuple[bool, str]]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _fold(s: str) -> str:
    """Lowercase and strip accents so Vasíli matches Vasili."""
    import unicodedata

    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn"
    )


def _names(entity: dict[str, Any]) -> list[str]:
    """Return the entity's name and aliases, lowercased and accent-folded, as a flat list."""
    out = [str(entity.get("name", ""))]
    aliases = entity.get("aliases")
    if isinstance(aliases, list):
        out.extend(str(a) for a in aliases)
    elif isinstance(aliases, str):
        out.extend(a.strip() for a in aliases.split(";"))
    return [_fold(n) for n in out if n]


def _matches(entity: dict[str, Any], needles: list[str]) -> bool:
    """True if any needle is a substring of any of the entity's names or aliases (accent-folded)."""
    hay = _names(entity)
    return any(any(_fold(n) in h for h in hay) for n in needles)


def _properties(entity: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return (key, value) pairs however the pipeline attached them."""
    props = entity.get("properties")
    if isinstance(props, dict):
        return list(props.items())
    if isinstance(props, list):
        return [(str(p.get("key", "")), p.get("value")) for p in props if isinstance(p, dict)]
    return []


def _pair(rel: dict[str, Any]) -> tuple[str, str]:
    """Return the relationship's (source, target) endpoints as strings."""
    return (str(rel.get("source")), str(rel.get("target")))


# --------------------------------------------------------------------------
# checks - each returns (passed, detail)
# --------------------------------------------------------------------------


def finish_stop(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """The run ended on finish_reason 'stop', not the token budget."""
    fr = rec.get("finish_reason", "unknown")
    return fr == "stop", f"finish_reason={fr}"


def not_loop_aborted(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """The stream loop detector did not cut the run short."""
    aborted = bool(rec.get("aborted_by_loop"))
    return not aborted, f"aborted_by_loop={aborted}"


def entity_count(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Total entities emitted lies within [min, max]."""
    n = len(rec.get("entities") or [])
    lo, hi = args.get("min", 0), args.get("max")
    ok = n >= lo and (hi is None or n <= hi)
    return ok, f"entities={n} (min={lo}, max={hi})"


def entity_count_matching(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Number of entities matching any of ``names`` lies within [min, max]."""
    needles = [str(n) for n in args.get("names", [])]
    n = sum(1 for e in rec.get("entities") or [] if _matches(e, needles))
    lo, hi = args.get("min", 0), args.get("max")
    ok = n >= lo and (hi is None or n <= hi)
    return ok, f"matching {needles}={n} (min={lo}, max={hi})"


def no_entity_matching(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """No emitted entity's name or aliases match any of the forbidden ``names``."""
    needles = [str(n) for n in args.get("names", [])]
    hits = [e.get("name") for e in rec.get("entities") or [] if _matches(e, needles)]
    return not hits, f"forbidden present={hits}"


def entities_present(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Every name in ``names`` matches at least one emitted entity."""
    missing = [
        n
        for n in args.get("names", [])
        if not any(_matches(e, [str(n)]) for e in rec.get("entities") or [])
    ]
    return not missing, f"missing={missing}"


def property_key_unique_per_entity(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """No entity carries the same property key twice (case-insensitive)."""
    dupes: list[str] = []
    for e in rec.get("entities") or []:
        keys = [k.lower() for k, _v in _properties(e)]
        if len(keys) != len(set(keys)):
            dupes.append(str(e.get("name")))
    return not dupes, f"entities with repeated keys={dupes}"


def property_count_per_entity(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """No single entity carries more than ``max`` properties."""
    hi = args.get("max")
    worst = max((len(_properties(e)) for e in rec.get("entities") or []), default=0)
    ok = hi is None or worst <= hi
    return ok, f"max properties on one entity={worst} (max={hi})"


def relationship_count(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Total relationships emitted lies within [min, max]."""
    n = len(rec.get("relationships") or [])
    lo, hi = args.get("min", 0), args.get("max")
    ok = n >= lo and (hi is None or n <= hi)
    return ok, f"relationships={n} (min={lo}, max={hi})"


def relationship_count_pair(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """No unordered entity pair carries more than ``max`` relationships."""
    counts: dict[tuple[str, str], int] = {}
    for r in rec.get("relationships") or []:
        a, b = _pair(r)
        key = (a, b) if a <= b else (b, a)
        counts[key] = counts.get(key, 0) + 1
    worst = max(counts.values(), default=0)
    hi = args.get("max")
    ok = hi is None or worst <= hi
    return ok, f"max relationships on one pair={worst} (max={hi})"


def no_duplicate_relationships(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """No (source, target, type) emitted more than ``max`` times.

    Calibration 2026-09-23: a per-pair cap counted legitimate variety as spam -
    the prompt rewards specific types, so eight distinct relationships between
    one couple is correct. Spam is the *same* relationship restated.
    """
    hi = int(args.get("max", 1))
    counts: dict[tuple[str, str, str], int] = {}
    for r in rec.get("relationships") or []:
        key = (*_pair(r), str(r.get("type", "")).lower())
        counts[key] = counts.get(key, 0) + 1
    worst = max(counts.values(), default=0)
    return worst <= hi, f"max repeats of one (source,target,type)={worst} (max={hi})"


def no_invalid_relationship_indices(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """No relationship referenced an index outside the entity list."""
    n = int(rec.get("invalid_relationship_count", 0) or 0)
    return n == 0, f"invalid_relationship_indices={n}"


def output_tokens_ratio(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Output tokens do not exceed ``max_ratio`` times input tokens."""
    i, o = int(rec.get("input_tokens", 0) or 0), int(rec.get("output_tokens", 0) or 0)
    ratio = (o / i) if i else float("inf")
    hi = float(args.get("max_ratio", 3.0))
    return ratio <= hi, f"output/input tokens={ratio:.2f} (max={hi})"


def only_format_lines(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """Every non-empty, non-header line of the raw response is E|/P|/R|."""
    raw = str(rec.get("raw_llm_response", ""))
    bad: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("=== PASS"):
            continue
        if not s.startswith(("E|", "P|", "R|")):
            bad.append(s[:60])
    return not bad, f"non-format lines={bad[:3]}"


def all_lines_parse(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """The parser dropped no lines: every emitted line matched the grammar."""
    n = int(rec.get("parser_lines_dropped", 0) or 0)
    return n == 0, f"parser_lines_dropped={n}"


def refs_in_bounds(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """Every entity/relationship sent_ref parses and lies within the passage."""
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        parse_sent_ref,
    )

    n_sent = len(rec.get("sentences") or [])
    bad: list[str] = []
    for item in list(rec.get("entities") or []) + list(rec.get("relationships") or []):
        ref = item.get("sent_ref")
        idxs = parse_sent_ref(str(ref)) if ref else None
        if not idxs or any(i < 1 or i > n_sent for i in idxs):
            bad.append(str(ref))
    return not bad, f"bad refs={bad[:3]} (sentences={n_sent})"


# --------------------------------------------------------------------------
# checks for sections B-D (evidence, entity and relationship discipline)
# --------------------------------------------------------------------------


def _ref_sentences(item: dict[str, Any], rec: dict[str, Any]) -> list[str]:
    """Return the passage sentences an item's sent_ref points at (lowercased)."""
    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        parse_sent_ref,
    )

    sentences = rec.get("sentences") or []
    idxs = parse_sent_ref(str(item.get("sent_ref") or "")) or []
    return [str(sentences[i - 1]).lower() for i in idxs if 1 <= i <= len(sentences)]


def _entity_by_index(rec: dict[str, Any], idx: Any) -> dict[str, Any] | None:
    """Return the entity at a relationship's integer index, or None if unresolvable."""
    ents: list[dict[str, Any]] = rec.get("entities") or []
    try:
        return ents[int(idx)]
    except TypeError, ValueError, IndexError:
        return None


def _name_in(text: str, entity: dict[str, Any]) -> bool:
    """Loose containment: any name/alias, or any significant word of the name."""
    for n in _names(entity):
        if n and n in text:
            return True
    words = [w for w in str(entity.get("name", "")).lower().split() if len(w) >= 4]
    return any(w in text for w in words)


def refs_name_entity(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """Every entity's cited sentence(s) actually mention it."""
    bad = [
        e.get("name")
        for e in rec.get("entities") or []
        if not any(_name_in(s, e) for s in _ref_sentences(e, rec))
    ]
    return not bad, f"entities whose cited sentences do not name them={bad[:3]}"


def rel_refs_contain_both(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """Every relationship cites at least one sentence containing BOTH endpoints."""
    bad: list[str] = []
    for r in rec.get("relationships") or []:
        a, b = _entity_by_index(rec, r.get("source")), _entity_by_index(rec, r.get("target"))
        if a is None or b is None:
            bad.append(f"{r.get('source')}->{r.get('target')} (unresolved)")
            continue
        if not any(_name_in(s, a) and _name_in(s, b) for s in _ref_sentences(r, rec)):
            bad.append(f"{a.get('name')}->{b.get('name')}")
    return not bad, f"relationships whose citations lack both endpoints={bad[:3]}"


def relationship_type_absent(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """No emitted relationship type contains any of the given substrings."""
    needles = [str(n).lower() for n in args.get("types", [])]
    # A negated type ("not_spouse_of") is the model correctly declining
    # the relation, not asserting it. Calibration 2026-09-23.
    negated = ("not_", "no_", "never_", "un")
    hits = sorted(
        {
            str(r.get("type"))
            for r in rec.get("relationships") or []
            if any(n in str(r.get("type", "")).lower() for n in needles)
            and not str(r.get("type", "")).lower().startswith(negated)
        }
    )
    return not hits, f"forbidden relationship types present={hits}"


def relationship_type_present(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """At least one emitted relationship type contains one of the substrings."""
    needles = [str(n).lower() for n in args.get("types", [])]
    ok = any(
        any(n in str(r.get("type", "")).lower() for n in needles)
        for r in rec.get("relationships") or []
    )
    return (
        ok,
        f"looking for any of {needles} in {sorted({str(r.get('type')) for r in rec.get('relationships') or []})}",
    )


def no_self_relationships(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """No relationship has the same entity as source and target."""
    n = sum(
        1 for r in rec.get("relationships") or [] if str(r.get("source")) == str(r.get("target"))
    )
    return n == 0, f"self-relationships={n}"


def every_entity_has_relationship(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """Every emitted entity appears in at least one relationship."""
    ents = rec.get("entities") or []
    seen = {str(r.get("source")) for r in rec.get("relationships") or []} | {
        str(r.get("target")) for r in rec.get("relationships") or []
    }
    lonely = [e.get("name") for i, e in enumerate(ents) if str(i) not in seen]
    return not lonely, f"entities with no relationship={lonely[:3]}"


def justification_length(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Every relationship justification is between ``min`` and ``max`` characters."""
    lo, hi = int(args.get("min", 50)), int(args.get("max", 300))
    bad = [
        len(str(r.get("justification", "")))
        for r in rec.get("relationships") or []
        if not lo <= len(str(r.get("justification", ""))) <= hi
    ]
    return not bad, f"justification lengths out of {lo}-{hi}={bad[:4]}"


def justification_evidence_only(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """No deliberation, alternatives or notes-to-self in justifications."""
    markers = (
        "i think",
        "i believe",
        "alternatively",
        "perhaps",
        "note:",
        "note to self",
        "unclear",
        "not sure",
        "might be",
        "could be",
    )
    bad = [
        str(r.get("justification", ""))[:50]
        for r in rec.get("relationships") or []
        if any(m in str(r.get("justification", "")).lower() for m in markers)
    ]
    return not bad, f"deliberating justifications={bad[:2]}"


def description_min_length(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Descriptions of the named (or all) entities are at least ``min`` chars.

    Calibration 2026-09-24: applied to every entity this failed 9/16 models on
    minor entities the passage states one fact about - a 100-char description
    there would require invention. ``names`` restricts it to the major entity.
    """
    lo = int(args.get("min", 100))
    wanted = [str(n) for n in args.get("names", [])]
    ents = [e for e in rec.get("entities") or [] if not wanted or _matches(e, wanted)]
    short = [e.get("name") for e in ents if len(str(e.get("description", ""))) < lo]
    return not short, f"descriptions under {lo} chars={short[:3]}"


def confidence_in_band(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """The entity matching ``name`` has confidence within [lo, hi]."""
    needle, lo, hi = str(args.get("name", "")), float(args.get("min", 0)), float(args.get("max", 1))
    for e in rec.get("entities") or []:
        if _matches(e, [needle]):
            c = float(e.get("confidence") or 0)
            return lo <= c <= hi, f"{e.get('name')} confidence={c} (band {lo}-{hi})"
    return False, f"entity {needle!r} not found"


def aliases_include(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """The entity matching ``name`` lists every expected alias (substring, case-insensitive)."""
    needle = str(args.get("name", ""))
    want = [str(a).lower() for a in args.get("aliases", [])]
    for e in rec.get("entities") or []:
        if _matches(e, [needle]):
            have = " | ".join(_names(e))
            missing = [a for a in want if a not in have]
            return not missing, f"{e.get('name')} missing aliases={missing}"
    return False, f"entity {needle!r} not found"


def aliases_exclude(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """No entity lists any of the forbidden strings as an alias (roles, groups, null markers)."""
    forbid = [str(a).lower() for a in args.get("aliases", [])]
    hits = []
    for e in rec.get("entities") or []:
        al = [a.lower() for a in (e.get("aliases") or []) if isinstance(a, str)]
        hits += [f"{e.get('name')}:{a}" for a in al if any(f in a for f in forbid)]
    return not hits, f"forbidden aliases={hits[:3]}"


def types_within(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Every emitted entity type is one of the allowed ``types``."""
    allowed = {str(t).lower() for t in args.get("types", [])}
    bad = sorted(
        {
            str(e.get("type"))
            for e in rec.get("entities") or []
            if str(e.get("type", "")).lower() not in allowed
        }
    )
    return not bad, f"types outside {sorted(allowed)}={bad}"


_NUMBER_WORDS = {
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "hundred": "100",
}
_SUFFIXES = ("ians", "ian", "ing", "ed", "er", "es", "s", "ish", "ic", "al")


def _stem(word: str) -> str:
    """Crude stem so 'commanded' ~ 'commander', 'singing' ~ 'sang' (via prefix)."""
    w = _NUMBER_WORDS.get(word, word)
    for suf in _SUFFIXES:
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _text_stems(text: str) -> set[str]:
    """Return the set of crude stems for every token in the passage text."""
    import re as _re

    return {_stem(w) for w in _re.findall(r"[a-z0-9]+", text.lower())}


def _value_supported(value: str, stems: set[str], raw_text: str) -> bool:
    """Return True when ``value`` is traceable to the passage.

    Verbatim match, or every significant token (number-normalised, crudely
    stemmed) shares a 4-char prefix with a token in the text. So
    'military commander' passes when 'commanded' is present and 'french'
    fails when nothing French-ish appears. Known limit: irregular verb forms
    (sang / singing) are not bridged; probe passages avoid relying on them.
    """
    import re as _re

    v = value.lower().strip()
    if not v or v in raw_text:
        return True
    toks = [_stem(w) for w in _re.findall(r"[a-z0-9]+", v)]
    sig = [w for w in toks if len(w) >= 3 and w not in {"the", "and", "of", "a", "an"}]
    if not sig:
        return True
    supported = sum(
        1
        for w in sig
        if any(s.startswith(w[:4]) or w.startswith(s[:4]) for s in stems if len(s) >= 3)
    )
    # Majority rule: a compound value survives one irregular verb ("wore" vs
    # "wears") or one reciprocal word ("father of <name in text>"), while a
    # value with NO support ("emperor", "french", "military commander" for a
    # passage that never mentions command) still fails.
    return supported / len(sig) >= 0.6


def property_values_from_text(rec: dict[str, Any], _: dict[str, Any]) -> tuple[bool, str]:
    """Every property value is traceable to the passage.

    Calibration 2026-09-23: the first version compared raw substrings and
    failed faithful paraphrase - 'sixteen' emitted as age=16, 'sang and
    danced' as talent=singing, 'commanded' as occupation=commander. Values
    are now compared on number-normalised stems with a 4-char prefix match,
    which still rejects genuine world-knowledge injection such as
    title=emperor for a passage that never mentions it.
    """
    raw_text = " ".join(str(s) for s in rec.get("sentences") or []).lower()
    stems = _text_stems(raw_text)
    wanted = [str(n) for n in _.get("names", [])]  # restrict to the probe's own entities when set
    bad = []
    for e in rec.get("entities") or []:
        if wanted and not _matches(e, wanted):
            continue
        for k, raw in _properties(e):
            if not _value_supported(str(raw), stems, raw_text):
                bad.append(f"{e.get('name')}.{k}={str(raw).lower()[:20]}")
    return not bad, f"property values not in text={bad[:3]}"


def _cast_entries(rec: dict[str, Any]) -> list[list[str]]:
    """Carrier cast as [[name, alias, ...], ...], lowercased."""
    return [
        [p.strip().lower() for p in entry.split("|") if p.strip()]
        for entry in rec.get("carrier_cast") or []
    ]


def carrier_coverage(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """At least ``min_fraction`` of the carrier's known cast was extracted.

    Guards the carrier tier against a model that copes with density by
    dropping the surrounding text: the probe's own entity may be found while
    the real chunk's people vanish.
    """
    cast = _cast_entries(rec)
    if not cast:
        return True, "no carrier cast declared"
    found = sum(1 for names in cast if any(_matches(e, names) for e in rec.get("entities") or []))
    frac = found / len(cast)
    lo = float(args.get("min_fraction", 0.6))
    return frac >= lo, f"carrier cast found {found}/{len(cast)} ({frac:.0%}, min {lo:.0%})"


def no_invention(rec: dict[str, Any], args: dict[str, Any]) -> tuple[bool, str]:
    """Every emitted entity is named in the input the model actually saw.

    Judged against the record's own sentences (probe passage plus carrier), so
    it needs no hand-maintained list: an entity is an invention when neither
    its name, an alias, nor any significant word of its name appears in the
    text. Calibration 2026-09-24: the first version compared against a
    name list derived from other checks and flagged the probe's own entities.
    """
    text = _fold(" ".join(str(s) for s in rec.get("sentences") or []))
    extra = [e.get("name") for e in rec.get("entities") or [] if not _name_in(text, e)]
    return not extra, f"entities not named in the input={extra[:4]}"


# --------------------------------------------------------------------------
# scorer
# --------------------------------------------------------------------------


class ProbeScorer:
    """Evaluate every probe's checks; headline = tier-weighted pass rate."""

    version: int = PROBE_SCORER_VERSION

    CHECKS: ClassVar[dict[str, Check]] = {
        "finish_stop": finish_stop,
        "not_loop_aborted": not_loop_aborted,
        "entity_count": entity_count,
        "entity_count_matching": entity_count_matching,
        "no_entity_matching": no_entity_matching,
        "entities_present": entities_present,
        "property_key_unique_per_entity": property_key_unique_per_entity,
        "property_count_per_entity": property_count_per_entity,
        "relationship_count": relationship_count,
        "relationship_count_pair": relationship_count_pair,
        "no_duplicate_relationships": no_duplicate_relationships,
        "no_invalid_relationship_indices": no_invalid_relationship_indices,
        "output_tokens_ratio": output_tokens_ratio,
        "only_format_lines": only_format_lines,
        "all_lines_parse": all_lines_parse,
        "refs_in_bounds": refs_in_bounds,
        "refs_name_entity": refs_name_entity,
        "rel_refs_contain_both": rel_refs_contain_both,
        "relationship_type_absent": relationship_type_absent,
        "relationship_type_present": relationship_type_present,
        "no_self_relationships": no_self_relationships,
        "every_entity_has_relationship": every_entity_has_relationship,
        "justification_length": justification_length,
        "justification_evidence_only": justification_evidence_only,
        "description_min_length": description_min_length,
        "confidence_in_band": confidence_in_band,
        "aliases_include": aliases_include,
        "aliases_exclude": aliases_exclude,
        "types_within": types_within,
        "property_values_from_text": property_values_from_text,
        "carrier_coverage": carrier_coverage,
        "no_invention": no_invention,
    }

    def score(self, output: RawOutput, fixture: Any) -> ScoreResult:
        """Score every probe record against its checks.

        ``fixture`` is the list of :class:`Probe` objects (checks live there);
        ``output.extras["probes"]`` holds the per-probe records.
        """
        selected = set((output.extras or {}).get("selected_ids") or [])
        # When the run was restricted with `only`, score only what ran: a probe
        # that was never attempted is not a failure. Calibration 2026-09-24: the
        # section-H sweep reported 4/85 because 64 unattempted probes counted
        # as fails.
        probes = {p.id: p for p in (fixture or []) if not selected or p.id in selected}
        records = {r.get("id"): r for r in (output.extras or {}).get("probes", [])}

        verdicts: list[dict[str, Any]] = []
        by_section: dict[str, dict[str, list[bool]]] = {}
        weighted_pass = weighted_total = 0.0

        for pid, probe in probes.items():
            rec = records.get(pid)
            details: list[str] = []
            if rec is None or rec.get("error"):
                passed = False
                details.append(f"no result: {rec.get('error') if rec else 'missing'}")
            elif rec.get("finish_reason", "stop") != "stop" or rec.get("aborted_by_loop"):
                # Completion gate. A run cut off at the token budget or by the
                # loop detector produced a partial answer, and several checks
                # pass vacuously on partial output ("no forbidden entity" when
                # there are no entities at all). Calibration 2026-09-24: GPT-OSS
                # "passed" 5 of 8 hard probes this way. Incomplete = fail.
                passed = False
                details.append(
                    f"did not complete: finish_reason={rec.get('finish_reason')} "
                    f"aborted_by_loop={bool(rec.get('aborted_by_loop'))}"
                )
            else:
                passed = True
                for check in probe.checks:
                    fn = self.CHECKS[check["type"]]
                    ok, why = fn(rec, check)
                    details.append(f"{'PASS' if ok else 'FAIL'} {check['type']}: {why}")
                    passed = passed and ok
            w = TIER_WEIGHTS.get(probe.tier, 1)
            weighted_total += w
            weighted_pass += w if passed else 0
            by_section.setdefault(probe.section, {}).setdefault(probe.tier, []).append(passed)
            verdicts.append(
                {
                    "id": pid,
                    "probe": probe.probe,
                    "section": probe.section,
                    "tier": probe.tier,
                    "passed": passed,
                    "details": details,
                }
            )

        headline = (weighted_pass / weighted_total * 100.0) if weighted_total else 0.0
        section_rates = {
            sec: {tier: (sum(v) / len(v) * 100.0 if v else None) for tier, v in tiers.items()}
            for sec, tiers in by_section.items()
        }
        return ScoreResult(
            headline_score=headline,
            metrics={
                "probe_scorer_version": self.version,
                "probes_total": len(probes),
                "probes_passed": sum(1 for v in verdicts if v["passed"]),
                "section_rates": section_rates,
                "verdicts": verdicts,
            },
        )


def rescore(row: Any, probes: list[Any]) -> ScoreResult:
    """Re-apply the current checkers to a saved result row's raw records.

    Lets a checker fix, or a re-tier, be evaluated against every model in
    seconds instead of re-running the LLMs for hours. Requires the row to
    carry ``extras["probes"]`` (rows written before 2026-09-23 do not).
    """
    extras = getattr(row, "extras", None) or {}
    raw = RawOutput(
        entities=[],
        relationships=[],
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        error=None,
        extras=extras,
    )
    return ProbeScorer().score(raw, probes)


__all__ = ["PROBE_SCORER_VERSION", "TIER_WEIGHTS", "ProbeScorer", "rescore"]
