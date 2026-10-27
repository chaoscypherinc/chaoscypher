# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Guards on public-surface facts the code owns and the docs quote by hand.

* The MCP tool count ("44 tools", "25 read", "19 write-only") is typed on the
  README, the homepage and docs pages; the registry is the source. A surface
  that quotes any other number fails here, so adding a tool means updating
  every surface (SECTION_MAP § mcp's extra check), not finding out from a
  reader. Every docs page is scanned (the changelog's dated history aside):
  the glossary said "36 MCP tools (21 read, 15 write)" while the registry had
  44, outside the hand-kept list of surfaces and in a phrasing the pattern
  did not read.
* The domain-config keys the 2026-10-07 restructure removed (``REMOVED_KEYS``)
  or reserved for the floor (``FLOOR_ONLY_KEYS``) must not be shown as keys to
  declare in the domain docs: the 2026-10-15 docs audit found five pages still
  telling a pack author to set ``normalizer_overrides``,
  ``evidence_validation_mode`` and ``extraction_overrides``, each of which
  makes the schema refuse the whole pack.
* Every ``QualityCounter`` member has a row in the Quality Metrics API
  reference: the page carried a hand-typed count (45, then 44 + 2) and was
  four counters short.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from chaoscypher_core.mcp import get_tools
from chaoscypher_core.services.quality.counters import QualityCounter
from chaoscypher_core.services.sources.engine.extraction.domains.config_schema import (
    FLOOR_ONLY_KEYS,
    REMOVED_KEYS,
)


_ROOT = Path(__file__).resolve().parents[5]
_DOCS = _ROOT / "packages" / "docs" / "docs"

# Public surfaces that must quote the tool count (SECTION_MAP § mcp / § docs).
_TOOL_COUNT_SURFACES = (
    _ROOT / "README.md",
    _ROOT / "packages" / "docs" / "src" / "pages" / "index.tsx",
    _DOCS / "user-guide" / "mcp.md",
    _DOCS / "architecture" / "core.md",
    _DOCS / "getting-started" / "configuration.md",
    # Joined 2026-10-22: it still said 36 after two tool additions, unguarded.
    _DOCS / "about" / "roadmap.md",
    # Joined 2026-10-22: "36 MCP tools (21 read, 15 write)", unguarded.
    _DOCS / "glossary.md",
)
# Pages whose tool counts are dated history, not the current registry.
_HISTORY = (_DOCS / "about" / "changelog.md",)
# "44 tools", "44 MCP tools", "25 read tools", a heading's "Write Tools (19)" (the MCP
# page's tables said 17 after two tools were added: the first pattern alone never read a
# heading), and a "(25 read, 19 write)" split.
_TOOL_COUNT_RE = re.compile(
    r"\b(\d+)\s+(?:MCP\s+)?(?:read |write-only |write )?tools\b"
    r"|\btools\s+\((\d+)\)"
    r"|\((\d+)\s+read,\s+(\d+)\s+write(?:-only)?\b",
    re.IGNORECASE,
)


def _quoted_counts(text: str) -> set[int]:
    """Every tool count *text* quotes (each number any alternative of the pattern caught)."""
    return {int(n) for m in _TOOL_COUNT_RE.finditer(text) for n in m.groups() if n is not None}


# The pages that document what a domain pack declares.
_DOMAIN_DOCS = (
    *sorted((_DOCS / "architecture" / "extraction-pipeline").glob("*.md")),
    _DOCS / "developer-guide" / "building-domains.md",
    _DOCS / "developer-guide" / "extraction-pipeline.md",
    _DOCS / "user-guide" / "domains.md",
)


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path.relative_to(_ROOT)} is not in this tree")
    return path.read_text(encoding="utf-8")


def test_every_public_surface_quotes_the_registry_tool_count() -> None:
    total = len(get_tools("write"))
    read = len(get_tools("read"))
    allowed = {total, read, total - read}
    pages = sorted(_DOCS.rglob("*.md")) if _DOCS.exists() else []
    scanned = dict.fromkeys([*_TOOL_COUNT_SURFACES, *pages])
    for path in scanned:
        if path in _HISTORY:
            continue
        quoted = _quoted_counts(_read(path) if path in _TOOL_COUNT_SURFACES else path.read_text())
        if path in _TOOL_COUNT_SURFACES:
            assert quoted, f"{path.relative_to(_ROOT)} no longer quotes a tool count"
        wrong = quoted - allowed
        assert not wrong, (
            f"{path.relative_to(_ROOT)} quotes {sorted(wrong)} tools; the registry has "
            f"{total} ({read} read + {total - read} write-only)"
        )


def test_domain_docs_never_show_a_removed_or_floor_only_key_as_declarable() -> None:
    keys = "|".join(re.escape(k) for k in (*REMOVED_KEYS, *FLOOR_ONLY_KEYS))
    declared = re.compile(rf'^\s*"?({keys})"?\s*:', re.M)
    offenders = []
    for path in _DOMAIN_DOCS:
        for m in declared.finditer(_read(path)):
            line = path.read_text(encoding="utf-8").count("\n", 0, m.start()) + 1
            offenders.append(f"{path.relative_to(_ROOT)}:{line} declares `{m.group(1)}`")
    assert not offenders, "\n".join(offenders)


def test_quality_metrics_reference_has_a_row_for_every_counter() -> None:
    text = _read(_DOCS / "reference" / "api" / "quality-metrics.md")
    missing = [c.value for c in QualityCounter if f"| `{c.value}` |" not in text]
    assert not missing, f"quality-metrics.md has no row for: {missing}"


def _tool_table(text: str, heading: str) -> set[str]:
    """The tool names in the table under ``### <heading> (N)`` of the MCP page."""
    match = re.search(
        rf"^### {re.escape(heading)} \(\d+\)\n(.*?)(?=^#{{2,3}} )", text, re.MULTILINE | re.DOTALL
    )
    assert match, f"the MCP page has no '### {heading} (N)' section"
    return set(re.findall(r"^\| `([a-z_]+)` \|", match.group(1), re.MULTILINE))


def test_the_mcp_page_tables_name_exactly_the_registered_tools() -> None:
    """The Read/Write tables on the MCP page list the registry's tools, no more, no fewer.

    The count guard above reads the numbers; this reads the names, so a tool
    added to the registry without a table row (or renamed, or removed) fails
    here instead of being found by a reader. Write-only tools are the ones
    ``get_tools("write")`` serves beyond ``get_tools("read")``.
    """
    text = (_DOCS / "user-guide" / "mcp.md").read_text(encoding="utf-8")
    read = {t.name for t in get_tools("read")}
    write_only = {t.name for t in get_tools("write")} - read
    assert _tool_table(text, "Read Tools") == read
    assert _tool_table(text, "Write Tools") == write_only


def test_the_memory_profile_docs_say_which_tools_each_mode_serves() -> None:
    """Every surface describing the ``memory`` profile names its read-mode tools.

    The profile is five tools in write mode and three in read mode (the
    default): ``remember`` and ``forget`` are write-only. Three pages said
    "the five memory tools" with no mode, so the documented Claude Desktop
    setup, which passes no ``--mode``, promised two tools it never lists.
    """
    read_names = sorted(t.name for t in get_tools("read", "memory"))
    write_names = sorted(t.name for t in get_tools("write", "memory"))
    assert read_names != write_names, "the guard is moot if the profile has no write-only tool"
    surfaces = (
        _DOCS / "user-guide" / "mcp.md",
        _DOCS / "user-guide" / "memory.md",
        _DOCS / "reference" / "cli" / "mcp.md",
        _DOCS / "getting-started" / "configuration.md",
    )
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        assert "memory" in text, path
        for name in read_names:
            assert f"`{name}`" in text, f"{path.name} never names the read-mode memory tool {name}"
        assert "read mode" in text, (
            f"{path.name} describes the memory profile without saying what read mode serves"
        )
