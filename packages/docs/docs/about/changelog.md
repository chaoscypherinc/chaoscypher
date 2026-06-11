---
id: changelog
title: Changelog
description: Release-by-release history of Chaos Cypher features, fixes, and breaking changes.
---

# Changelog

## Recent Changes

Entries from June 2026 onward are grouped by release so you can map them to the version you are running — check yours with `chaoscypher --version` (CLI) or the container image tag.

### June 2026

#### v0.2.0 (2026-06-11)

- **Chat reliability & UX overhaul** — Chat now runs on a `POST /chats/{id}/send` + `GET /chats/{id}/events` (Server-Sent Events) flow. New per-chat endpoints: `/cancel`, `/retry`, `/regenerate`, and `/export` (JSON or Markdown), plus edit-and-resend (`replace_from_message_id` on send) and server-side title search (`?q=` on the chat list). In the UI: a Stop button while a reply is generating, regenerate, edit-and-resend, copy message / copy code block, chat export, search in the chat switcher, entity hover cards with live previews, and automatic stream re-attach after a page refresh.
- **Tool approval** — When `chat.tool_approval` is `always-ask` or `ask-on-write`, chat tool calls pause for your approval in both the web UI and the CLI (`chat.tool_approval_timeout_seconds`, default 120, denies on timeout; poll interval `intervals.chat_approval_poll_ms`, default 500). The CLI chat loop was rebuilt on the same engine as the web UI.
- **Breaking: legacy chat streaming endpoint removed** — `POST /chats/{id}/stream` is gone; use `/send` + `/events`. Settings keys `llm.thinking_auto_detect`, `llm.chat_interactive_streaming`, and `chat.enable_response_validation` were removed (an older `settings.yaml` containing them is cleaned up automatically on startup).
- **Default Ollama URL is now `http://localhost:11434`** for standalone (pip) installs, overridable with the `CHAOSCYPHER_OLLAMA_URL` environment variable. Docker images keep reaching the host's Ollama via `host.docker.internal` automatically. Existing installs are unaffected (`settings.yaml` is never rewritten).
- **Documentation accuracy overhaul** — A full audit of the docs, blog, and READMEs fixed 300+ inaccuracies: every documented endpoint, command, flag, and settings key now matches shipped behavior, and previously undocumented features (MCP maintenance mode, parallel workflow DAGs, spend caps, benchmark v2 scoring, and more) are covered.
- **CLI audit fixes** — Dead code removed, ~120 stale help strings corrected, and correctness fixes across `chat --tag`, `db delete`, the import pipeline (cached-skip re-billing), `serve` validation, setup-wizard rollback, and Lexicon error handling. `chaoscypher health` and `doctor` now exit non-zero when checks fail, so they work in scripts and CI.
- **Chat citation fixes** — Mixed-reference citation markers render correctly, hallucinated or unresolved citations are scrubbed, and long multi-hop prompts no longer silently truncate on Ollama.

#### v0.1.1 (2026-06-09)

- **Model benchmark v2** — The extraction benchmark now reports a composite **Overall** score, and model metadata (names, pricing, context windows) lives in a single `models_registry.yaml` source of truth.
- **Homepage redesign** — New hero, guided tour, and mobile polish on [chaoscypher.com](https://chaoscypher.com), plus chunk-offset and pagination fixes.

#### v0.1.0 (2026-06-07) — public launch

- **Public launch** — First public release: source at [github.com/chaoscypherinc/chaoscypher](https://github.com/chaoscypherinc/chaoscypher), with the all-in-one image published to GHCR and packages on PyPI.
- **Self-healing migrations** — Pending Alembic migrations now auto-apply on startup, so upgraded installs never sit on a stale schema. While migrations run, the MCP server enters a maintenance mode instead of erroring. See [ADR-0006](../architecture/adrs/0006-re-adopt-alembic.md).
- **Parallel workflow execution** — Workflows execute as a `depends_on` DAG: independent branches run in parallel and joins wait for all of their dependencies (AND-join semantics).
- **Domain confirmation gate + upload wizard** — When the extraction domain is auto-detected, the source parks as `awaiting_confirmation` until you confirm or change the domain (the upload wizard proposes it upfront), so an hour-long extraction never runs against the wrong domain. Pass `auto_confirm` to bypass.
- **Config unification** — `settings.yaml` in the platform data directory is now the single home for engine configuration. The separate `cli.yaml` has been retired; an older install's leftover `cli.yaml` is silently ignored and the CLI prints a one-line note so you can delete it. Lexicon Hub login state now lives in `auth.json` rather than the old client config.
- **GHCR is the primary install path** — The published GitHub Container Registry image is now the recommended way to run Chaos Cypher. `docker pull` the all-in-one image and start the container — no source checkout or local build required.
- **License: AGPL-3.0-only** — The project license identifier is now `AGPL-3.0-only`. See [License](./license.md) for what this means for self-hosting, modifications, and the enterprise edition.

### May 2026

- **Universal LLM stage progress facility** — Per-page, per-batch, and per-chunk progress for every LLM-bound stage of the import pipeline (vision, embedding, MCP extraction) now flows through a single `StageProgress` async context manager backed by a new `llm_stage_progress` table (now part of the consolidated `0001` baseline migration). Each stage row carries an exponentially-weighted moving average (`avg_ms`) of milliseconds-per-item; the UI converts it into a live `X/Y items · ~remaining` estimate that ticks every page. Replaces three previously-divergent timing sources (queue stats, a hardcoded size/chunk/entity heuristic, a legacy MCP-only EMA service) with one source of truth — both the per-row top-right slot and the "Processing Documents" header now read the same number. The six legacy `extraction_chunks_*` columns were dropped at the same time; CC-049 blocks their re-introduction.
- **40 quality counters (up from 18)** — The quality-counter view (now the **Pipeline flow** section on the source’s Overview tab) surfaces every silent-drop / silent-merge / silent-skip site in the pipeline, including 22 counters previously not allowlisted at the adapter layer (so increments were silently rejected). The SQLite adapter now derives its allowlist from the `QualityCounter` enum so adding a new counter is sufficient; a drift test pins the relationship. New stage section: Embedding (chunk failures, dimension mismatches). Two counters (`loader_html_dropped_tags`, `loader_pptx_shapes_skipped`) are JSON-shaped per-key breakdowns rather than scalars.
- **Large PDF uploads (>1MB)** — nginx's auth_request subrequest no longer inherits the server-level `client_max_body_size`; the per-route override applies to the auth check too. Restriction-only "encrypted" PDFs (Adobe Acrobat output, OCR scans, journal articles where encryption only signals permission restrictions) now load normally — the PDF loader attempts an empty-password decrypt before raising `EncryptedPDFError`. Frontend translates 413 responses to a size-specific message rather than the generic "server error" copy.
- **Chunker coalesces short chunks** — The `min_chunk_size` filter no longer drops sub-threshold chunks. It now coalesces them with a neighbor (merging into the next chunk that lifts the combination over the threshold) so natural-prose imports — dialogue, transitions, short paragraphs — keep all content reaching extraction. Fixes a W5 data-loss regression observed on `war_and_peace.txt`, where 80 chunks of real Tolstoy prose were being silently discarded. Default `min_chunk_size` lowered from 500 to 100 to keep the merging gentle on natural prose. The renamed `chunks_coalesced_count` counter (and the "Chunks coalesced" tile in the Pipeline flow section) now records merge events, not drops.
- **Upload-settings persistence** — Every choice you make at upload time (`auto_analyze`, `enable_normalization`, `enable_vision`, `content_filtering`, `filtering_mode`) is now a real column on the source row. Recovery, retry, and re-extract reuse what you set without you having to re-pass it.
- **Pipeline flow quality counters** — Every silent-drop site in the pipeline (loader / cleaner / chunking / LLM / post-extraction / commit) now increments a typed counter on the source row. The Pipeline flow section on the source detail page’s Overview tab surfaces every counter with plain-English explanations, distinct from the existing Quality grade. Counters reset on Re-extract so you can compare runs.
- **Filtering modes 0–5 redesigned** — The slider now produces distinct results at every level. Three previously-dead settings (`loop_max_entity_count`, `semantic_dedup_threshold`, `minimum_alias_length`) are now wired so each preset (`unfiltered` / `minimal` / `lenient` / `balanced` / `strict` / `maximum`) tunes the pipeline differently. Plain-English documentation at [Filtering Modes](../reference/filtering-modes.md).
- **Production extraction parity** — Cortex, the standalone CLI, and the MCP path all share one post-extraction helper (`apply_structural_and_normalization`). The same source produces the same graph regardless of which entry point ran the extraction.
- **Normalization & chunking honesty** — Operator's `NormalizerSettings` now actually reach the cleaners (was silently ignored). `min_chunk_size` / `max_chunk_size` / `respect_boundaries` are wired through to the splitter. Zero-chunk sources raise `ValidationError` with an actionable hint instead of committing silently. The OCR cleaner is scoped to OCR-derived content via `applies_to(metadata)` so short identifiers like `git` / `npm` / `K8s` survive on plain text and HTML.
- **Loader correctness** — Shared `detect_encoding()` helper for all text-shaped loaders (UTF-8 strict → cp1252 strict → charset-normalizer → Latin-1, no silent `errors="replace"`). JSONL parsed line-by-line with per-line error isolation. CSV uses a dialect sniffer. Scanned-PDF specific errors. `application/octet-stream` removed from the default upload allowlist (operators who need it can opt back in).
- **6 new built-in loaders** — HTML, RST, DOCX, XLSX, PPTX, EPUB. EPUB hand-rolled to avoid taking on an AGPL `ebooklib` dependency.
- **LLM observability** — `finish_reason` populated by all 4 providers (Ollama, OpenAI, Anthropic, Gemini) and normalized to a stable vocabulary (`stop` / `length` / `content_filter` / `tool_calls` / `error` / `unknown`). Streaming line-buffer flushes the trailing partial line so the last entity isn't silently dropped. Chunk-level `finish_reason` and `aborted_by_loop` surface on the extraction-task API; chunk truncation and abort counters surface on the source row.
- **Upload contract hardening** — URL fetcher validates the upstream `Content-Type` against the allowlist, honors any `charset=…` parameter, and routes binary responses through the binary loader path. CLI fully matches the API contract: `--vision/--no-vision`, `--content-filtering/--no-content-filtering`, `--normalize/--no-normalize`, `--filtering-mode`, `--skip-duplicates`.
- **Vector search visibility** — `vector_indexing_status` field with four states (`pending`, `indexed`, `degraded`, `failed`). New `SearchStatusBadge` UI component on the source list and detail page. The orphan-sweep worker drives `degraded` → `indexed` retry and `degraded` → `failed` retry-exhaustion.

### April 2026

- **Content Filtering** — Pre-extraction content filtering removes non-essential content (table of contents, changelogs, legal boilerplate, etc.) before entity extraction while keeping it searchable via RAG. 15 built-in categories with domain-specific exclusion rules. Enabled by default on upload, configurable per source.
- **Domain Extraction Limits** — Each extraction domain now defines hard caps on entity degree, same-pair relationships, total relationship ratio, and per-chunk entity count. Prevents runaway LLM generation and controls graph density per domain. Includes orphan protection to ensure isolated entities keep at least one connection.
- **Container Logs & Diagnostics** — Logs tab in the web UI with real-time merged logs from all services (Cortex, Neuron, Nginx, Valkey), color-coded rendering, and runtime log level selector with cross-process hot-reload via Valkey pub/sub. Diagnostic export bundles system info, database stats, sanitized settings, logs, queue stats, and service status into a ZIP file.
- **Queue Cancellation** — Running tasks can now be cancelled, not just queued tasks. Uses a Valkey flag that workers check between processing batches. UI updates immediately while the handler gracefully exits.
- **Docker Startup Page** — Friendly branded page shown instead of raw 502/503 errors while services start. Shows component health status, live log viewer with colored rendering, and auto-redirects when the app becomes ready.
- **Docker Error Pages** — Custom branded error pages for all common HTTP error codes (400, 403, 404, 408, 413, 429, 500, 504) with contextual messages and pre-filled GitHub issue templates.
- **Security Hardening** — Comprehensive security audit with SSRF protection, request body size limits, error message sanitization across all endpoints, CSP headers, exception type leak prevention, and temp file suffix sanitization.
- **UI Redesign** — Cyberpunk-themed interface overhaul with neon palette, glass effects, ghost components, constellation loading animation, immersive dashboard with ambient graph, omnibar command terminal, frosted glass sidebar, and graph visualization improvements (glow sprites, colored edges, mindmap layout).
- **Alembic Migration Framework** — Every schema change (columns, tables, constraints) now ships as an Alembic migration file in `packages/core/src/chaoscypher_core/database/migrations/versions/`. Cortex runs `alembic upgrade head` on startup to apply pending migrations. Replaces the earlier reflective auto-migrator, which was retired in April 2026 because it couldn't cover constraint / FK changes and made schema evolution inscrutable. An autogenerate-diff test in CI catches SQLModel changes that lack a matching migration.
- **v7 Extraction Quality Scoring** — Re-weighted grade formula (R 50% / E 35% / T 15%), bell-shaped density score so over-dense graphs are penalized (stops models padding edges for score), and a new structural penalty combining hub-skew and reciprocal-rate signals that catch a single entity being over-connected or the same relationship emitted in both directions.
- **MCP Client-Driven Extraction** — MCP server defaults to client-driven extraction with no server LLM required. Fixes for anyio deadlocks, status propagation, and processor queue bypass.
- **CLI Embedding Config** — `chaoscypher setup` wizard now configures embedding providers with auto-default to Ollama.
- **Valkey AOF Repair** — All-in-one container automatically validates and repairs corrupted Valkey AOF files on startup. Falls back to clean slate if repair fails. Queue data is transient, so no permanent data is lost.
- **Batch Embedding Processing** — Concurrent embedding generation with per-chunk progress reporting and configurable batch sizes.
- **Codebase Refactoring** — Settings consolidation (deduplicated ChunkingSettings, EmbeddingSettings, MCPSettings, PathSettings into core), SourceStatus enum replacing raw strings, cross-package name collision fixes, 338 ruff + 239 mypy error resolutions.

### March 2026

- **MCP Server** — Built-in [Model Context Protocol](https://modelcontextprotocol.io/) server with 31 tools for AI assistants (Claude Desktop, Cursor, ChatGPT). Supports stdio transport (CLI) and Streamable HTTP (Cortex API). Read-only by default with optional write mode.
- **Authentication System** — Optional auth with setup wizard, login, user management, API keys, and TLS support.
- **Template Visual Identity** — Templates now support icon and color fields for visual identification across the graph, search results, and extraction views.
- **Vision Processing** — Optional vision model support for extracting content from images in PDFs and standalone image files. Includes image gallery on source detail pages.
- **Embedding Provider System** — Multi-provider embedding support (local CPU, Ollama, OpenAI, Gemini) with configurable model and provider settings.
- **Search Index Rebuild** — Rebuild search indexes from Settings UI or CLI (`chaoscypher source rebuild-search`), with auto-detection of embedding model changes.
- **System Health Monitoring** — Consolidated health check endpoint and UI status dropdown with subsystem diagnostics.
- **Ollama Model Management** — Pull, remove, and inspect Ollama models directly from the Settings UI.
- **Settings Restructure** — Settings reorganized into five tabs: General, Models, Search, Access, and Maintenance.
- **Local CPU Embedding Service** — Dedicated embedding pipeline using sentence-transformers (Qwen/Qwen3-Embedding-0.6B). Multi-provider support (local CPU, Ollama, OpenAI, Gemini) with configurable model and provider settings. No API keys or external services required for the default local mode.
- **GraphRAG Search** — Graph-enhanced retrieval that fuses knowledge graph traversal with vector search. Uses entity extraction from queries, Personalized PageRank, and Reciprocal Rank Fusion to answer multi-hop questions that pure vector RAG misses.
- **DX Zero-Boilerplate Audit** — Typed Pydantic return models for all Engine public methods, `ChaosCypher` convenience namespace, `check_health()` API, and documentation restructuring.
- **Documentation site** — Docusaurus documentation site with landing page, user guide, API reference, CLI reference, architecture docs, and development guide
- **Workflow execution engine** — LangGraph-based workflow orchestrator with step execution and state management
- **Visual workflow builder** — ReactFlow-based drag-and-drop UI for designing workflows
- **Compose CLI commands** — `chaoscypher compose build/up/down/run` for composition management
- **ADR-0001: Remove Discovery and Lenses** — Removed discovery sessions and lenses features per architectural decision
- **ADR-0003: PyMuPDF replacement** — Replaced PyMuPDF with alternative PDF processing
- **Scoped chat** — Chat conversations can be scoped to specific sources or tags for focused AI interaction
- **Tag system redesign** — Inline tag editor with tags displayed in the sources list
- **Source scope enforcement** — All graph tools respect source scope filtering
- **Production readiness** — Lint cleanup and production configuration fixes

### Earlier

For detailed release notes, see the public package repositories and the project discussions.

---

This changelog covers notable feature additions and changes. For detailed technical changes, refer to individual commit messages in the repository.
