# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""The shipped carrier tier: two real chunks, the same 21 instructions in each."""

from __future__ import annotations

from chaoscypher_core.benchmark.probes import Probe, load_probe_sections, prepare_probe_passage
from chaoscypher_core.mcp.benchmark.suites.probes import PROBE_PACK_DIR, PROBE_SEED


def _own_checks(probe: Probe) -> list[dict[str, object]]:
    """A carrier variant's checks less the two every variant gets."""
    return [c for c in probe.checks if c["type"] not in ("carrier_coverage", "no_invention")]


def test_carrier_tier_is_the_same_probes_in_two_chunks() -> None:
    """39 probes in section H: 20 ending -carrier (wp_g2) and 19 -carrier2 twins (wp_bet_nameday).

    Doubling the carrier count halves what one verdict moves Overall by
    (1.9 points on the 2026-10-01 board). A twin keeps its base probe's
    instruction and substantive checks; only the chunk, its cast and the
    id differ. Three variants are skipped by the generator (2026-10-02)
    because an exclusion check names someone the chunk contains: D6-easy
    on both chunks (Alexander is at the soirée, Kutuzov at the name day)
    and D2-easy on the name day (Kutuzov).
    """
    probes = load_probe_sections(PROBE_PACK_DIR, ["H_carrier.yaml"])
    assert len(probes) == 39
    ids = {p.id for p in probes}
    assert not ids & {"D6-easy-carrier", "D6-easy-carrier2", "D2-easy-carrier2"}
    first = {p.id: p for p in probes if p.id.endswith("-carrier")}
    second = {p.id: p for p in probes if p.id.endswith("-carrier2")}
    assert (len(first), len(second)) == (20, 19)
    assert {p.carrier for p in first.values()} == {"carriers/wp_g2.txt"}
    assert {p.carrier for p in second.values()} == {"carriers/wp_bet_nameday.txt"}
    for pid, a in first.items():
        if pid == "D2-easy-carrier":  # its name-day twin is a skip (Kutuzov is in that chunk)
            continue
        b = second[pid + "2"]
        assert (a.probe, a.instruction, a.passage) == (b.probe, b.instruction, b.passage)
        assert _own_checks(a) == _own_checks(b)
        assert a.carrier_cast != b.carrier_cast


def test_second_carrier_is_production_sized_and_splices_deterministically() -> None:
    """The chunk is a real extraction group's size, and the splice is seeded like the first."""
    probe = next(
        p
        for p in load_probe_sections(PROBE_PACK_DIR, ["H_carrier.yaml"])
        if p.id == "D6-hard-carrier2"
    )
    chunk = (PROBE_PACK_DIR / "carriers" / "wp_bet_nameday.txt").read_text(encoding="utf-8")
    assert 2900 <= len(chunk) <= 3600
    once, offset = prepare_probe_passage(probe, PROBE_PACK_DIR, seed=PROBE_SEED)
    again, offset_again = prepare_probe_passage(probe, PROBE_PACK_DIR, seed=PROBE_SEED)
    assert (once, offset) == (again, offset_again)
    assert probe.passage.strip().split(".")[0] in once and offset > 1
    assert "Dólokhov" in once and "Rostóvs" in once


def test_b5_carrier_variants_scope_traceability_to_the_probe_entities() -> None:
    """B5 in chunks checks property values on Napoleon and company, not on the chunk's cast."""
    probes = {p.id: p for p in load_probe_sections(PROBE_PACK_DIR, ["H_carrier.yaml"])}
    expected = {
        "B5-easy-carrier": ["Berthier", "Napoleon"],
        "B5-easy-carrier2": ["Berthier", "Napoleon"],
        "B5-hard-carrier": ["Alexander", "Bagration", "Barclay", "Kutuzov", "Napoleon", "Toll"],
        "B5-hard-carrier2": ["Alexander", "Bagration", "Barclay", "Kutuzov", "Napoleon", "Toll"],
    }
    for pid, names in expected.items():
        check = next(c for c in probes[pid].checks if c["type"] == "property_values_from_text")
        assert check["names"] == names, pid
