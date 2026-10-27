# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only
"""The graph tools through a real ``chaoscypher mcp`` over stdio, on a database without templates.

A client's database built by an earlier release holds only the client's own node templates:
none of the shipped ones, so the default edge template (``system_template_link``) was not
there. ``create_edge`` with no template failed its foreign key, and the failure broke the
server's session for every later call. These are a client's four calls end to end: two
nodes, an edge with no template, the templates listed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from chaoscypher_core.database.migrations.startup import run_startup_migrations
from chaoscypher_core.settings import GraphSettings


CHILD = Path(__file__).with_name("memory_cli_child.py")
CLIENT_TEMPLATE = "template_note_by_a_client"


def _database_without_templates(data_dir: Path) -> Path:
    """The CLI's default database: the schema, and one node template a client made."""
    db = data_dir / "databases" / "default" / "app.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    run_startup_migrations(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO graph_templates (id, database_name, name, template_type, is_system,"
            " properties, created_at, updated_at) VALUES (?, 'default', 'Note', 'node', 0, '[]',"
            " '2026-10-15 00:00:00', '2026-10-15 00:00:00')",
            (CLIENT_TEMPLATE,),
        )
        assert conn.execute("SELECT count(*) FROM graph_templates").fetchone() == (1,)
    return db


class _Server:
    """A ``chaoscypher mcp --mode write`` child spoken to in JSON-RPC lines."""

    def __init__(self) -> None:
        self.child = subprocess.Popen(  # noqa: S603 - the CLI under test
            [sys.executable, str(CHILD), "raise", "mcp", "--mode", "write"],
            env={
                **os.environ,
                "CHAOSCYPHER_SEARCH__ENABLE_VECTOR_SEARCH": "false",
                "CHAOSCYPHER_LLM_PROVIDER": "ollama",
            },
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.next_id = 0

    def send(self, message: dict[str, Any]) -> None:
        assert self.child.stdin is not None
        self.child.stdin.write(json.dumps(message) + "\n")
        self.child.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        assert self.child.stdout is not None
        while True:
            line = self.child.stdout.readline()
            assert line, self.child.communicate()
            message = json.loads(line)
            if message.get("id") == self.next_id:
                return message

    def tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})["result"]
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    def close(self) -> None:
        if self.child.stdin is not None:
            self.child.stdin.close()
        try:
            self.child.wait(timeout=60)
        finally:
            if self.child.poll() is None:
                self.child.kill()
                self.child.wait()


def test_a_clients_four_calls_all_succeed_and_the_edge_is_saved(data_dir_in_tmp: Path) -> None:
    db = _database_without_templates(data_dir_in_tmp)
    server = _Server()
    try:
        server.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            },
        )
        server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        a = server.tool(
            "create_node", {"template_id": CLIENT_TEMPLATE, "label": "a", "properties": {}}
        )
        b = server.tool(
            "create_node", {"template_id": CLIENT_TEMPLATE, "label": "b", "properties": {}}
        )
        edge = server.tool(
            "create_edge",
            {"source_node_id": a["node_id"], "target_node_id": b["node_id"], "label": "about"},
        )
        listed = server.tool("list_templates", {})
    finally:
        server.close()
    assert edge["success"] is True
    assert listed["success"] is True
    with sqlite3.connect(db) as conn:
        saved = conn.execute(
            "SELECT template_id, source_node_id, target_node_id FROM graph_edges WHERE id = ?",
            (edge["edge_id"],),
        ).fetchone()
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert saved == (GraphSettings().default_edge_template, a["node_id"], b["node_id"])
