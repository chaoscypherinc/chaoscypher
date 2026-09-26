# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Probe fixtures: the Probe model, section loading and passage transforms.

A probe is a hand-written passage constructed so the correct extraction is
unambiguous, plus checks a reader can verify by reading it. Section YAML files
declare probes; :func:`load_probe_sections` validates them (every check type
must be one :class:`ProbeScorer` knows) and builds :class:`Probe` objects.

The passage transforms (:func:`splice_into_carrier`, :func:`pad_with_filler`)
turn a short probe passage into production-scale input without changing what
is extractable from it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from chaoscypher_core.benchmark.scorers.probes import ProbeScorer


# Entity-free filler: weather and landscape with no proper nouns, so padding a
# passage to production scale adds sentences but nothing extractable.
_FILLER_SENTENCES: tuple[str, ...] = (
    "The rain had not stopped since morning.",
    "Water ran off the roof in a steady sheet and pooled in the yard.",
    "The road beyond the gate was mud to the axles.",
    "Smoke from the wet wood hung low over the village.",
    "By afternoon the wind had turned and the clouds began to break.",
    "A thin light lay across the fields and the ditches shone.",
    "The birch trees along the lane dripped for an hour after the rain.",
    "Somewhere a dog barked and was answered from farther off.",
    "The river was high and brown and carried branches past the ford.",
    "Evening came early and the lamps were lit before supper.",
    "Frost was expected, and the shutters were closed against it.",
    "The night was still, and the only sound was the drip from the eaves.",
)


def splice_into_carrier(passage: str, carrier: str, *, seed: int = 42) -> tuple[str, int]:
    """Insert ``passage`` into ``carrier`` at a seeded sentence boundary.

    Returns the combined text and the 1-based index of the probe's first
    sentence, so targeted reference checks can be offset if needed. The probe's
    sentences stay contiguous; the insertion point is chosen from the carrier's
    sentence boundaries with a fixed seed so runs are reproducible.
    """
    import random

    from chaoscypher_core.services.sources.engine.extraction.utils.sentence_splitter import (
        split_into_sentences,
    )

    sents = split_into_sentences(carrier)
    if not sents:
        return passage, 1
    rng = random.Random(seed)  # noqa: S311 - reproducible splice position, not security
    cut = rng.randint(1, max(1, len(sents) - 1))  # never before the first sentence
    before, after = sents[:cut], sents[cut:]
    combined = " ".join(before) + "\n" + passage.strip() + "\n" + " ".join(after)
    return combined + "\n", cut + 1


def pad_with_filler(passage: str, target_chars: int) -> str:
    """Append entity-free filler sentences until ``passage`` reaches ``target_chars``."""
    out = passage.rstrip()
    i = 0
    while len(out) < target_chars:
        out += ("\n" if i == 0 else " ") + _FILLER_SENTENCES[i % len(_FILLER_SENTENCES)]
        i += 1
    return out + "\n"


_VALID_TIERS = frozenset({"easy", "medium", "hard"})


@dataclass(frozen=True)
class Probe:
    """One probe: a passage, the instruction it tests, and its checks."""

    id: str
    probe: str
    section: str
    tier: str
    instruction: str
    passage: str
    checks: tuple[dict[str, Any], ...]
    entity_exclusions: tuple[dict[str, Any], ...] = ()
    entity_types: tuple[str, ...] = ()
    strict_types: bool = False
    carrier: str | None = None
    """Relative path of a real production chunk to splice this passage into.

    A carrier tier tests the same instruction, with the same checks, inside a
    ~3,600-char extraction group taken from a real corpus through the real
    chunker - production density, not just production length. The probe's
    sentences are inserted contiguously at a seeded sentence boundary.
    """
    carrier_cast: tuple[str, ...] = ()
    """Names (with aliases, ``name|alias|alias``) the carrier is known to
    contain, for the ``carrier_coverage`` and ``no_invention`` checks."""


def load_probe_sections(pack_dir: Path, section_files: list[str]) -> list[Probe]:
    """Load every probe from the listed section files under ``pack_dir``."""
    probes: list[Probe] = []
    seen: set[str] = set()
    for name in section_files:
        path = pack_dir / name
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "probes" not in data:
            msg = f"{path}: section file must be a mapping with a 'probes' list"
            raise ValueError(msg)
        section = str(data.get("section", Path(name).stem))
        for raw in data["probes"]:
            probe = _parse_probe(raw, section=section, pack_dir=pack_dir, path=path)
            if probe.id in seen:
                msg = f"{path}: duplicate probe id {probe.id!r}"
                raise ValueError(msg)
            seen.add(probe.id)
            probes.append(probe)
    return probes


def _parse_probe(raw: dict[str, Any], *, section: str, pack_dir: Path, path: Path) -> Probe:
    """Validate one raw probe mapping from a section file and build a Probe."""
    for key in ("id", "probe", "tier", "instruction", "checks"):
        if key not in raw:
            msg = f"{path}: probe missing required field {key!r}"
            raise ValueError(msg)
    tier = str(raw["tier"])
    if tier not in _VALID_TIERS:
        msg = f"{path}: probe {raw['id']!r} has unknown tier {tier!r}"
        raise ValueError(msg)
    if "passage_ref" in raw:
        passage = (pack_dir / str(raw["passage_ref"])).read_text(encoding="utf-8")
    elif "passage" in raw:
        passage = str(raw["passage"])
    else:
        msg = f"{path}: probe {raw['id']!r} needs 'passage' or 'passage_ref'"
        raise ValueError(msg)
    checks = raw["checks"]
    if not isinstance(checks, list) or not checks:
        msg = f"{path}: probe {raw['id']!r} needs a non-empty 'checks' list"
        raise ValueError(msg)
    for c in checks:
        if not isinstance(c, dict) or "type" not in c:
            msg = f"{path}: probe {raw['id']!r} has a check without a 'type'"
            raise ValueError(msg)
        if c["type"] not in ProbeScorer.CHECKS:
            msg = f"{path}: probe {raw['id']!r} uses unknown check type {c['type']!r}"
            raise ValueError(msg)
    return Probe(
        id=str(raw["id"]),
        probe=str(raw["probe"]),
        section=section,
        tier=tier,
        instruction=str(raw["instruction"]),
        passage=passage,
        checks=tuple(dict(c) for c in checks),
        entity_exclusions=tuple(dict(e) for e in (raw.get("entity_exclusions") or [])),
        entity_types=tuple(str(t) for t in (raw.get("entity_types") or [])),
        strict_types=bool(raw.get("strict_types", False)),
        carrier=str(raw["carrier"]) if raw.get("carrier") else None,
        carrier_cast=tuple(str(c) for c in (raw.get("carrier_cast") or [])),
    )


def prepare_probe_passage(
    probe: Probe, pack_dir: Path, *, seed: int = 42, pad_to_chars: int = 0
) -> tuple[str, int]:
    """Return the text a probe sends to the extractor and its first sentence index.

    A carrier probe is spliced into its carrier chunk (resolved against
    ``pack_dir``) at a ``seed``-chosen sentence boundary; with ``pad_to_chars``
    > 0 entity-free filler is appended until the text reaches that length.
    ``probe_offset`` is the 1-based index of the probe's first sentence (1
    when nothing precedes it). The local runner and the MCP benchmark both
    call this, so the two send byte-identical passages.
    """
    passage = probe.passage
    probe_offset = 1
    if probe.carrier:
        carrier_text = (pack_dir / probe.carrier).read_text(encoding="utf-8")
        passage, probe_offset = splice_into_carrier(passage, carrier_text, seed=seed)
    if pad_to_chars > 0:
        passage = pad_with_filler(passage, pad_to_chars)
    return passage, probe_offset


def probe_node_templates(probe: Probe, default_node_templates: str) -> str:
    """Return the node templates a probe's entity prompt lists.

    A probe that declares ``entity_types`` replaces the domain's node
    templates with exactly those types (one ``- Type`` line each); otherwise
    the domain's formatted templates are used unchanged.
    """
    if probe.entity_types:
        return "\n".join(f"- {t}" for t in probe.entity_types)
    return default_node_templates


def completion_counters(records: list[dict[str, Any]]) -> tuple[int, int]:
    """Count probe runs cut off by the token limit and by the loop detector.

    Probe rows must carry the same integrity counters as corpus rows so the
    leaderboard's truncation warning fires for them: measured 2026-09-24, GLM
    4.7 Flash hit the limit or the loop guard on 19/21 carrier probes and
    rendered as a clean row because the counters stayed at zero.
    """
    truncated = sum(1 for r in records if r.get("finish_reason") == "length")
    aborted = sum(1 for r in records if r.get("aborted_by_loop"))
    return truncated, aborted


__all__ = [
    "Probe",
    "completion_counters",
    "load_probe_sections",
    "pad_with_filler",
    "prepare_probe_passage",
    "probe_node_templates",
    "splice_into_carrier",
]
