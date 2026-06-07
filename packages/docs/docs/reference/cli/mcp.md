---
title: MCP Server CLI
description: Start a stdio MCP server with chaoscypher mcp to let Claude Desktop, Cursor, and other MCP-compatible AI assistants connect to your Chaos Cypher knowledge graph.
---

# MCP Server

Start an MCP (Model Context Protocol) server over stdio transport. This allows MCP-compatible AI assistants like Claude Desktop, Cursor, and others to connect directly to your Chaos Cypher knowledge graph.

## Usage

```bash
chaoscypher mcp [OPTIONS]
```

## Options

| Option | Description |
|--------|-------------|
| `--database, -d` | Database name (default: from settings) |
| `--mode, -m` | Tool access mode: `read` or `write` (default: from settings, usually `read`) |
| `--server-extraction` | Use server-side LLM for extraction instead of client-driven |
| `--help` | Show help message |

By default, extraction is client-driven: the MCP client (e.g. Claude) performs entity extraction itself after indexing. Use `--server-extraction` to have the server's LLM handle extraction instead.

## Examples

```bash
# Start MCP server with default database
chaoscypher mcp

# Start MCP server with a specific database
chaoscypher mcp --database my-project

# Start MCP server in write mode
chaoscypher mcp --mode write

# Start with server-side extraction
chaoscypher mcp --server-extraction
```

## AI Assistant Configuration

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "chaoscypher": {
      "command": "chaoscypher",
      "args": ["mcp"]
    }
  }
}
```

### Claude Code

```bash
claude mcp add chaoscypher -- chaoscypher mcp
```

### Cursor

Add to Cursor MCP settings:

```json
{
  "mcpServers": {
    "chaoscypher": {
      "command": "chaoscypher",
      "args": ["mcp"],
      "transportType": "stdio"
    }
  }
}
```

## How It Works

The MCP server creates an `Engine` instance connected to your local database and exposes Chaos Cypher tools via the stdio transport. The server runs until interrupted (Ctrl+C).

No Docker or Valkey required — the CLI connects directly to the SQLite database via the Core library.

## Configuration

MCP tool access is controlled by settings in [`settings.yaml`](../../getting-started/configuration.md):

```yaml
mcp:
  mode: read           # "read" or "write"
  auto_extract: false  # Run extraction after document upload (default: false — MCP client drives extraction)
```

See [MCP Server](../../user-guide/mcp.md) for the full list of available tools and configuration details.

## HTTP Transport

For HTTP-based MCP access (web clients, remote access), use the Cortex API endpoint at `/api/v1/mcp` instead. This is available automatically when running the Docker stack with `make docker-dev`.

## See also

- [User guide: MCP Server](../../user-guide/mcp.md) — full list of available tools, write mode, client-driven extraction, and configuration details
- [API reference: Grounding (MCP)](../../reference/api/grounding.md) — HTTP endpoints for node search, edge traversal, and neighbor discovery (used by the HTTP transport)
