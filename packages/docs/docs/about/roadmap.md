---
id: roadmap
title: Roadmap
description: Active development and wishlist for Chaos Cypher — CLI package commands and ideas under consideration.
---

# Roadmap

High-level planned features and improvements for Chaos Cypher.

## In Progress

### CLI Package Commands

Remaining package management commands:

- `chaoscypher graph package init` — Create package structure with manifest
- `chaoscypher graph package validate` — Validate package manifest
- `chaoscypher graph package build` — Build `.ccx` package from directory

Already implemented: `chaoscypher graph package export`, `chaoscypher graph package load`, `chaoscypher compose build/up/down/run`.

## Wishlist

Ideas under consideration without committed timelines. These are directions we find interesting, not commitments. They may be scoped and promoted to In Progress, deprioritised, or dropped based on feedback and usage data.

### Real-Time Graph Updates

WebSocket-based live graph updates for collaborative editing:

- Push notifications when nodes/edges are added or modified
- Live cursor positions for multi-user editing
- Conflict resolution for concurrent changes

### Workflow Builder Auto-Layout

Apply graph layout algorithms (e.g., Dagre) to automatically position workflow nodes in the visual builder.

### LangChain Agent Execution

Reimplement agent execution with LangChain agents for autonomous multi-step reasoning within workflows.

## Completed Recently

- **Chat reliability/UX overhaul (v0.1.1, June 2026)** — cancel, retry, regenerate, export, edit-and-resend, and chat title search
- **Model benchmark v2 (v0.1.1, June 2026)** — composite Overall scoring; model metadata consolidated in `models_registry.yaml`
- **Parallel workflow execution (June 2026)** — DAG fan-out with AND-join semantics in the LangGraph executor
- **Self-healing migrations (June 2026)** — migrations auto-apply on startup, with an MCP maintenance mode while they run (see [ADR-0006](../architecture/adrs/0006-re-adopt-alembic.md))
- **Config unification (June 2026)** — `settings.yaml` is the single config home; `cli.yaml` retired; Lexicon login state moved to `auth.json`
- **Domain confirmation gate + upload wizard (May–June 2026)** — auto-detected extraction domains park for human confirmation before the long extraction runs
- **Import Pipeline Remediation (May 2026)** — Upload settings persist on the source row; per-stage quality counters (the Pipeline flow section on the source page); filtering modes 0–5 redesigned with three previously-dead settings now wired; Cortex / CLI / MCP at extraction parity; 6 new loaders (HTML / RST / DOCX / XLSX / PPTX / EPUB); LLM `finish_reason` propagation; upload contract hardening; `SearchStatusBadge` and `vector_indexing_status`.
- Content Filtering — Pre-extraction filtering of 15 non-essential content categories per domain
- Domain Extraction Limits — Per-domain caps on entity degree, relationship ratios, and streaming entity count
- Container Logs & Diagnostics — Real-time log viewer, runtime log level selector, diagnostic ZIP export
- Queue Cancellation — Cooperative cancellation for running tasks via Valkey flags
- Docker Startup & Error Pages — Branded startup page with live health indicators, custom error pages
- Security Hardening — SSRF protection, error sanitization, CSP headers, body size limits
- UI Redesign — Cyberpunk theme, glass effects, dashboard overhaul, omnibar, graph visualization
- **Alembic Migration Framework (April 2026)** — Schema migrations now ship as Alembic revisions under `packages/core/src/chaoscypher_core/database/migrations/versions/`; Cortex runs `alembic upgrade head` on startup. The reflective auto-migrator was retired (see [ADR-0006](../architecture/adrs/0006-re-adopt-alembic.md)).
- [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) Client-Driven Extraction — Default to client-driven extraction, no server LLM required
- Batch Embedding Processing — Concurrent embedding generation with per-chunk progress
- MCP Server — 31 tools for AI assistants via stdio and Streamable HTTP transports
- Local CPU Embedding Service — sentence-transformers based embeddings replacing LLM-provider embeddings
- GraphRAG Search — graph-enhanced retrieval fusing knowledge graph traversal with vector search
- DX Zero-Boilerplate — typed Pydantic returns, `ChaosCypher` convenience namespace, `check_health()`
- Automations — workflow builder UI, execution engine (now with parallel step execution), 10 tool plugins, triggers
- Source-scoped chat with tag-based scoping
- Tag system redesign with inline editor
- Source scope enforcement on all graph tools
- Multi-LLM provider support (Ollama, OpenAI, Anthropic, Gemini)
- Quality analysis and scoring system
- CCX package export/import
- Lexicon Hub integration
- CLI with interactive chat, source management, and database operations
