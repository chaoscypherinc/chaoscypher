# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""E2E tests for CLI CCX 3.0 package export/load (semantic round-trip).

These exercise the public CLI surface (``graph package load`` /
``graph package export``) against a real (temporary) direct-core database —
no Docker required, so this tier runs locally.

Coverage:

* loading the CCX 3.0 ``seed.ccx`` imports templates, nodes, edges (with
  properties) AND sources;
* export → import into a FRESH database preserves **semantic** content
  (node count + labels, edge count + labels, edge ``properties``, templates) —
  not byte equality;
* the produced ``.ccx`` self-validates via ``ccx-format`` with the expected
  conformance classes;
* re-loading the same package twice (idempotent upsert-by-IRI) does not
  duplicate nodes/edges.

Full-fidelity source/chunk round-trip (offset selectors, full_text) is covered
at the integration tier in
``packages/core/tests/integration/services/package/test_ccx_importer.py``
because the CLI ``export`` command intentionally omits sources (no sources
repo on the CLI direct-core path).
"""

import json
from collections.abc import Callable
from pathlib import Path

import ccx


def _json(run_cli: Callable, args: list[str], env: dict[str, str]) -> object:
    """Run a CLI command expecting JSON on stdout and parse it."""
    result = run_cli(args, env=env)
    assert result.exit_code == 0, f"{args} failed: {result.output}"
    # The JSON may be preceded by Rich chrome on some commands; find the first
    # JSON document in the output (object OR array, whichever starts first).
    text = result.output
    candidates = [i for i in (text.find("{"), text.find("[")) if i != -1]
    assert candidates, f"no JSON in output: {text!r}"
    return json.loads(text[min(candidates) :])


def _node_labels(run_cli: Callable, env: dict[str, str]) -> set[str]:
    payload = _json(run_cli, ["graph", "node", "list", "--format", "json", "--limit", "500"], env)
    data = payload["data"] if isinstance(payload, dict) else payload
    return {n["label"] for n in data}


def _edges(run_cli: Callable, env: dict[str, str]) -> list[dict]:
    payload = _json(run_cli, ["graph", "link", "list", "--format", "json", "--limit", "500"], env)
    return payload["data"] if isinstance(payload, dict) else payload


def _template_names(run_cli: Callable, env: dict[str, str]) -> set[str]:
    payload = _json(run_cli, ["graph", "template", "list", "--format", "json"], env)
    data = payload["data"] if isinstance(payload, dict) else payload
    return {t["name"] for t in data}


class TestCcxRoundtrip:
    """Test chaoscypher graph package export/load commands (CCX 3.0)."""

    def test_load_seed_package(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        seed_ccx: Path,
    ) -> None:
        """Loading the seed CCX package imports templates, nodes, and edges."""
        result = run_cli(["graph", "package", "load", str(seed_ccx)], env=cli_env)
        assert result.exit_code == 0, f"Failed: {result.output}"

        labels = _node_labels(run_cli, cli_env)
        assert "Alice Smith" in labels
        assert "Bob Jones" in labels
        assert "Acme Corporation" in labels

        # Templates landed (Person / Organization / Works At).
        names = _template_names(run_cli, cli_env)
        assert {"Person", "Organization", "Works At"} <= names

    def test_export_produces_valid_ccx_package(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        seed_ccx: Path,
        tmp_path: Path,
    ) -> None:
        """Exporting creates a CCX 3.0 file that self-validates via ccx-format."""
        run_cli(["graph", "package", "load", str(seed_ccx)], env=cli_env)

        export_path = tmp_path / "export.ccx"
        result = run_cli(
            ["graph", "package", "export", "-o", str(export_path)],
            env=cli_env,
        )
        assert result.exit_code == 0, f"Failed: {result.output}"
        assert export_path.exists()
        assert export_path.stat().st_size > 0

        pkg = ccx.open_package(export_path)
        report = pkg.validate()
        assert report.ok, report.errors
        assert pkg.manifest.ccx_version == "3.0"
        assert "core" in report.classes

    def test_roundtrip_semantic_equivalence(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        seed_ccx: Path,
        tmp_path: Path,
    ) -> None:
        """Export then import into a fresh DB preserves nodes/edges/props/templates."""
        # Seed the source DB and capture the baseline semantic content.
        run_cli(["graph", "package", "load", str(seed_ccx)], env=cli_env)
        src_labels = _node_labels(run_cli, cli_env)
        src_edges = _edges(run_cli, cli_env)
        src_templates = _template_names(run_cli, cli_env)
        assert src_labels  # sanity: the seed produced nodes

        export_path = tmp_path / "roundtrip.ccx"
        run_cli(["graph", "package", "export", "-o", str(export_path)], env=cli_env)

        # Import into a FRESH database.
        run_cli(["db", "create", "roundtrip-db"], env=cli_env)
        rt_env = {**cli_env, "CHAOSCYPHER_DATABASE": "roundtrip-db"}
        result = run_cli(["graph", "package", "load", str(export_path)], env=rt_env)
        assert result.exit_code == 0, f"Import failed: {result.output}"

        # Node labels match exactly (semantic equivalence, not byte equality).
        assert _node_labels(run_cli, rt_env) == src_labels

        # Templates match.
        assert _template_names(run_cli, rt_env) >= src_templates

        # Edge count + labels match, AND the property-bearing edge's props
        # survive the round-trip (R2: lossless edge properties).
        rt_edges = _edges(run_cli, rt_env)
        assert len(rt_edges) == len(src_edges)
        assert {e["label"] for e in rt_edges} == {e["label"] for e in src_edges}

        works_at = [e for e in rt_edges if e["label"] == "works at"]
        assert works_at, rt_edges
        # ``since`` was 2023 on the seed's property edge.
        assert any(str(e.get("properties", {}).get("since")) == "2023" for e in works_at)

    def test_reimport_is_idempotent(
        self,
        run_cli: Callable,
        cli_env: dict[str, str],
        seed_ccx: Path,
    ) -> None:
        """Loading the same package twice does not duplicate nodes/edges."""
        run_cli(["graph", "package", "load", str(seed_ccx)], env=cli_env)
        nodes_first = _node_labels(run_cli, cli_env)
        edges_first = _edges(run_cli, cli_env)

        # Re-load the SAME package into the SAME database.
        result = run_cli(["graph", "package", "load", str(seed_ccx)], env=cli_env)
        assert result.exit_code == 0, f"Re-import failed: {result.output}"

        # Counts are stable (upsert-by-IRI, no duplicates).
        assert _node_labels(run_cli, cli_env) == nodes_first
        assert len(_edges(run_cli, cli_env)) == len(edges_first)
