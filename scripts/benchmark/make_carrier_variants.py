# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""Emit carrier-tier variants of selected probes into a new section file.

Usage (from the repo root):

    uv run python scripts/benchmark/make_carrier_variants.py \
        packages/core/src/chaoscypher_core/benchmark/data/probes/carriers/wp_g2.cast.json \
        packages/core/src/chaoscypher_core/benchmark/data/probes/carriers/wp_bet_nameday.cast.json

For each spec file, for each selected probe id: copy its passage,
instruction, exclusions, types and checks; add carrier + carrier_cast; append
carrier_coverage and no_invention (names = the probe's own expected entities,
taken from its entities_present / entity_count_matching / confidence_in_band /
aliases_include checks). The verified cast is read from a JSON file:
{"carrier": "...", "cast": [...], "probes": [...], "id_suffix": "-carrier"}.
Every spec's variants land in one section H, in spec order; ``id_suffix``
(default ``-carrier``) keeps the ids of different chunks apart.

A probe gets no variant on a chunk whose cast contains someone its exclusion
checks name (``relationship_count_matching``, ``no_entity_matching``): the
chunk makes extracting that person, or linking them, a legitimate reading,
and the check would score it as invention. 2026-10-02: D6-easy's "Kutuzov,
Napoleon, Alexander stay unlinked" spliced into the soirée, where Anna
Pávlovna calls Buonaparte a villain the sovereign must stop; 11 of 16 local
models linked Emperor Alexander to Buonaparte from the chunk and failed.
Skips are listed in the generated header.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import yaml


D = Path("packages/core/src/chaoscypher_core/benchmark/data/probes")

# The checks every carrier variant gets for free; anything else is substantive.
GENERIC_CHECKS = frozenset({"finish_stop", "not_loop_aborted", "carrier_coverage", "no_invention"})
# Global counts do not survive a carrier (the chunk adds its own entities).
GLOBAL_COUNTS = ("entity_count", "relationship_count", "every_entity_has_relationship")
# Checks that forbid a name: on a chunk that contains the name they forbid the chunk.
EXCLUSION_CHECKS = frozenset({"relationship_count_matching", "no_entity_matching"})
SECTION_FILES = (
    "A_format.yaml",
    "B_evidence.yaml",
    "C_entities.yaml",
    "D_relationships.yaml",
    "E_robustness.yaml",
)


def _fold(s: str) -> str:
    """The scorers' fold (:func:`~chaoscypher_core.benchmark.scorers.probes.fold`): one fold for names."""
    from chaoscypher_core.benchmark.scorers.probes import fold

    return fold(str(s))


def exclusion_collisions(probe: dict[str, Any], cast: list[str]) -> list[str]:
    """Names an exclusion check of ``probe`` forbids that the carrier ``cast`` contains.

    Cast entries are ``Name|alias|alias``; a collision is a substring match
    either way after accent folding, the way the scorer's own matching works.
    """
    members = [_fold(part) for entry in cast for part in str(entry).split("|") if part]
    hits: list[str] = []
    for check in probe.get("checks", []):
        if check.get("type") not in EXCLUSION_CHECKS:
            continue
        for name in check.get("names", []):
            folded = _fold(name)
            if any(folded in m or m in folded for m in members) and str(name) not in hits:
                hits.append(str(name))
    return hits


def _own_names(p: dict[str, Any]) -> list[str]:
    """The probe's own expected entities: what traceability and no_invention are scoped to."""
    names = [str(n) for n in p.get("expected_entities", [])]
    for c in p["checks"]:
        if c["type"] in ("entities_present", "entity_count_matching", "description_min_length"):
            names += [str(n) for n in c.get("names", [])]
        if c["type"] in ("confidence_in_band", "aliases_include") and c.get("name"):
            names.append(str(c["name"]))
    return names


def _variant(p: dict[str, Any], spec: dict[str, Any], suffix: str) -> dict[str, Any]:
    names = _own_names(p)
    q: dict[str, Any] = {
        # keep the source tier in the id: B5-easy and B5-hard both carry
        "id": p["id"] + suffix,
        "probe": p["probe"],
        "tier": "hard",
        "instruction": p["instruction"] + " — inside a real production chunk",
        "carrier": spec["carrier"],
        "carrier_cast": spec["cast"],
    }
    for k in ("entity_exclusions", "entity_types", "strict_types"):
        if k in p:
            q[k] = copy.deepcopy(p[k])  # the section is shared across specs: no YAML anchors
    if "passage_ref" in p:
        q["passage_ref"] = p["passage_ref"]
    else:
        q["passage"] = p["passage"]
    # global counts do not survive a carrier; drop them, keep targeted checks
    checks = [copy.deepcopy(c) for c in p["checks"] if c["type"] not in GLOBAL_COUNTS]
    # Traceability is checked on the probe's own entities (expected_entities
    # or the names its checks list). B5 was the exception until 2026-10-01:
    # on the chunk's own cast its check caught 'hostess' and 'father', which
    # are inference, not knowledge, and every Claude row failed both B5
    # carrier probes on them in one run after passing in another. The trap
    # B5 is for ('military commander' for Kutuzov) sits on its own entities.
    for c in checks:
        if c["type"] == "property_values_from_text" and names:
            c["names"] = sorted(set(names))
    # A variant whose substantive checks were ALL dropped tests nothing the two
    # generic checks below do not already cover, and reads as a pass at every
    # density (E7 2026-09-24, then D6 — the same class twice).
    if not [c for c in checks if c["type"] not in GENERIC_CHECKS]:
        msg = (
            f"{p['id']}: every substantive check is a global count, so its carrier "
            "variant would be vacuous - give the base probe a carrier-surviving "
            "check (e.g. the name-scoped twin of the count being dropped)"
        )
        raise SystemExit(msg)
    checks += [
        {"type": "carrier_coverage", "min_fraction": 0.6},
        {"type": "no_invention", "names": sorted(set(names))},
    ]
    q["checks"] = checks
    return q


def build_section(
    specs: list[dict[str, Any]], sections: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[tuple[str, str, list[str]]]]:
    """Section H from the specs over the loaded section files; plus the (probe, carrier, names) skipped."""
    out: dict[str, Any] = {
        "section": "H",
        "title": "Carrier (production-density hard tier)",
        "probes": [],
    }
    skipped: list[tuple[str, str, list[str]]] = []
    for spec in specs:
        want = set(spec["probes"])
        suffix = str(spec.get("id_suffix", "-carrier"))
        for data in sections.values():
            for p in data["probes"]:
                if p["id"] not in want:
                    continue
                hit = exclusion_collisions(p, spec["cast"])
                if hit:
                    skipped.append((p["id"], spec["carrier"], hit))
                    continue
                out["probes"].append(_variant(p, spec, suffix))
    return out, skipped


def main(argv: list[str]) -> None:
    specs = [json.loads(Path(arg).read_text(encoding="utf-8")) for arg in argv]
    sections = {f: yaml.safe_load((D / f).read_text()) for f in SECTION_FILES}
    out, skipped = build_section(specs, sections)
    text = (
        "# Section H — Carrier tier: production-density hard versions\n#\n"
        "# Each probe here is an existing probe's passage spliced into a REAL extraction\n"
        "# group (war_and_peace_tiny through the real chunker), so the model meets the\n"
        "# instruction at production density: 20-35 competing names, cross-sentence\n"
        "# links, room to run away. Same instruction, same targeted checks, plus\n"
        "# carrier_coverage (the chunk's own people must still be found) and\n"
        "# no_invention (nothing outside probe + carrier may appear). Global counts\n"
        "# (entity_count, relationship_count) are dropped because the carrier adds its\n"
        "# own entities; the targeted checks carry the verdict.\n#\n"
        "# GENERATED by make_carrier_variants.py from the verified carrier cast(s):\n"
        + "".join(
            f"#   {spec['carrier']} -> ids ending {spec.get('id_suffix', '-carrier')}\n"
            for spec in specs
        )
        + "# Skipped (an exclusion check names someone the chunk contains):\n"
        + "".join(
            f"#   {pid} on {carrier}: {', '.join(names)}\n" for pid, carrier, names in skipped
        )
        + "\n"
    )
    (D / "H_carrier.yaml").write_text(
        text + yaml.safe_dump(out, sort_keys=False, allow_unicode=True, width=100)
    )
    print(f"wrote H_carrier.yaml with {len(out['probes'])} carrier probes; skipped {len(skipped)}")
    for pid, carrier, names in skipped:
        print(f"  skipped {pid} on {carrier}: {', '.join(names)}")


if __name__ == "__main__":
    main(sys.argv[1:])
