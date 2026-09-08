---
id: changelog
title: Changelog
description: Release-by-release history of Chaos Cypher features, fixes, and breaking changes.
---

# Changelog

## Recent Changes

Entries from June 2026 onward are grouped by release so you can map them to the version you are running — check yours with `chaoscypher --version` (CLI) or the container image tag.

### September 2026

#### v0.4.2 (2026-09-08)

A second fixes-only patch release — 61 commits since v0.4.1. No new features, no
breaking API changes, no schema migrations. Security hardening, queue and chat
reliability, multi-container deployment repairs, and a large reduction in the
work the API does per poll dominate — and live chat streaming, broken since the
request-scoped adapter cleanup landed, works again.

##### Security

- **A queued task could create SQLite files at an attacker-chosen path** — `metadata.database_name` on `POST /api/v1/queue/tasks` reached a bare `Path` join in `get_db_path`, which then `mkdir`'d and created a database file wherever the value pointed. The sink now enforces the same `[A-Za-z0-9_-]+` fullmatch that `BackupService` and the queue handlers already applied, which covers every reader of that metadata field at once.
- **The health endpoint was an unthrottled bcrypt oracle** — `location = /api/v1/health` was the one `auth_request` location in the nginx templates without a `limit_req`, so an unauthenticated host on your LAN could drive one bcrypt (cost 12) per stored API key per request. It now carries the same rate limit as its siblings.
- **Per-IP auth rate limiting now enforces the policy it declares** — two halves of one broken control. `proxy-public.conf` blanked `X-Auth-Edge-Token` on exactly the public auth routes, collapsing the app layer's per-IP login and setup buckets into a single global bucket keyed to the nginx loopback peer, so any host on your network could starve the operator's own login (an availability problem, not an auth bypass — `X-Auth-User` was never trusted from outside). And the nginx `auth` zone rendered `login_max_requests` as **requests per second** — 60× the configured per-window policy — while every `*_window_seconds` setting was silently ignored and `setup_max_requests` never rendered at all, because both locations shared one zone. Zones now render as floored requests-per-minute, `/setup` gets its own zone, and burst scales with the per-window count.
- **A settings PATCH could brick every subsequent boot** — `allowed_origins: ["*"]` together with `allow_credentials: true` persisted happily through the settings API and then made `create_app` `SystemExit` on every start, with no API path back in. The pair is now rejected at validation time with a 422.
- **MCP read mode no longer leaks write tools** — five bridge handlers (`summarize`, `research_topic`, and three siblings) were absent from `TOOL_DEFINITIONS`, so in read mode they fell through to `bridge.execute` instead of being refused. The gate is now an allowlist derived from `get_tools_for_mode("read")`. Separately, `chaoscypher mcp --mode read` combined with the maintenance path silently dropped the flag while leaving `apply_upgrade` exposed; that combination now fails before the stdio handshake.
- **Archive ingest enforces volume caps at the function boundary** — `extract_archive()` gained the member-count, declared-total, and streamed-byte limits that `ArchiveExtractor` already applied. Untrusted archives reach this path directly (hub downloads, local `.ccx` files), so the caps now sit where the untrusted input lands rather than only on one caller.
- **Credentials-file updates are serialized across processes** — mutators take a blocking lock on a `credentials.json.lock` sidecar in addition to the in-process lock. With `uvicorn_workers > 1`, a per-request `touch_api_key` rewrite could clobber a concurrent `bump_session_epoch`, leaving a cookie you had just logged out valid until its TTL expired.
- **Dependency advisories cleared** — `pypdf`, `transformers`, and `browserslist` (in both the interface and docs trees) bumped to their fixed versions; `qs` (GHSA-x5fp-wj9c-mxmx, GHSA-4mjr-xmp4-gh2g), `@humanfs/node` (GHSA-p498-v437-472g), and `fflate` (GHSA-px8p-9vwx-vf98) cleared in the interface's development tree, and `qs` pinned in the docs tree, where no bump had been proposed; `js-yaml` (GHSA-2883-xcg3-v3hh, both trees) and `svgo` (GHSA-w27v-7q3p-w38r, GHSA-4vpr-x523-8j87, docs tree) bumped the day they were published; plus the routine interface dependency group updates.

##### Data correctness

- **A task failing before handler dispatch was silently lost** — the queue worker's outer `try` had no `except`, the done-callback never retrieved the task exception, and the `finally` removed the task from the `running` set while its hash still read `queued`. The task existed in neither pending nor running, so it was invisible to the reconciler and to rehydration: it simply never ran and never reported. Such a task is now marked failed-terminal (visible and dead-lettered) and the poller logs the exception through the canonical path.
- **A search-index sweep could clobber a permanently failed source back to `indexed`** — the exhaustion branch deletes its queue row before marking the source failed, so a sibling draining later in the same batch saw zero survivors and flipped the source to `indexed`, hiding a source that would never be searchable. Both indexed-flip sites now refuse to overwrite the terminal `failed` status.
- **Plain chat send was the only turn-enqueue path without the double-enqueue guard** — `/retry`, `/regenerate`, and the edit-resend branch all carry the compare-and-swap claim, with comments naming this exact defect; the plain-send branch still had a blind status write. The claim is now hoisted ahead of message persistence, so the losing request does not leave an orphan user row.
- **Confirming a source with a forced domain could extract under the wrong one** — `forced_domain` and other non-`None` overrides were written in a second transaction after the atomic claim, and `gate_decision` short-circuits on the claim timestamp alone. An import analysis snapshotting inside that window saw a confirmed-but-domainless source and extracted with the auto-detected domain instead of the one you chose. Everything now rides the single write-once claim.
- **The stuck-chat sweeper could stamp an error over a completed answer** — it flipped status from a stale snapshot; it now uses a compare-and-swap mirroring the processing claim, so a worker finishing mid-sweep keeps its result.
- **A pause landing mid-tick was relabelled and later auto-lifted** — the two evaluators share one row, so a user pause arriving during a health tick was re-attributed to `health_monitor` and then auto-resumed. Pause writes are now compare-and-swap guarded, and a probe crossing the trip threshold while the system is already auto-paused joins the witness set with the reason re-persisted, so auto-resume no longer fires into a still-degraded system.
- **Vision page retry counters can no longer drift permanently** — a double decrement made `completed + failed < total_pages` a permanent state; the reset now rides a guarded compare-and-swap. A separate race between a retry and a scheduled finalize is closed too.
- **`reset_all()` leaves a usable schema behind** — a filesystem failure in the destructive section left the application pointed at a schema that no longer existed until someone restarted it by hand. The reset now best-effort reinitializes the schema before the original exception propagates.
- **Resetting the knowledge base can no longer leave you with zero templates** — the template delete commits before the default-template seed runs, so a seed failure left an empty template set behind a generic failed task. The seed retries once and otherwise raises a typed error naming the recovery.
- **`enable_normalization` in the MCP `add_document` schema no longer lies** — the schema advertised a `true` default while the handler defaults to the tri-state `None` (auto: structured formats opt out). A client trusting the schema re-introduced the whitespace-stripping corruption the tri-state was added to fix.
- **The migration lock file is no longer unlinked while a waiter holds it** — `flock` binds to the inode, so removing `.upgrade.lock` for tidiness let a third process lock a fresh inode and run backup-and-upgrade concurrently with an in-flight upgrade. The MCP stdio path, which has no outer init lock, was the exposed caller.

##### Reliability

- **Live chat streaming works again** — the middleware that disconnects request-scoped storage adapters ran its teardown as soon as the response *object* existed, before a streaming body had finished. On `GET /chats/{id}/events` the generator's first suspension is the pub/sub subscribe, and the reconcile that follows it hit a just-disconnected adapter, so **every live stream died with `STREAM_INTERNAL_ERROR` before relaying a token** whenever the chat was actually processing. The answer still appeared on reload — the worker had run and persisted the turn — but nothing streamed. The middleware is now pure ASGI and tears down after the whole response is sent, which fixes the class for any streaming endpoint or background task that touches an adapter after the response object is produced. The middleware previously had no tests; it now has seven, two of which fail against the old implementation.
- **A retryable failure no longer aborts an interactive turn the queue then completes** — a transient failure is published as `status="failed"` before the retry path resets it to `queued`. A poll landing in that window raised, ending the turn, while the queue went on to succeed. Readers now give `error_type=transient` failures a bounded grace before raising; terminal transients still raise immediately.
- **A workflow task that exits before its main body no longer leaks its adapter** — the six early-exit paths (validation raises, zero-step return) now disconnect the execution adapter.
- **Queue statistics no longer fabricate a count** — when the stats query failed, the endpoint returned the current page's row count in the documented total field, capped at the page size, so a 2,000-deep backlog reported "50". It now returns `null` for unknown, which the UI already handles.
- **Frontend query retries no longer re-issue 4xx requests** — the retry predicate re-sent client errors, which among other things produced a double 401 redirect.
- **SQLite lock retry logs identify the operation** — every retry and exhaustion line previously read `operation="transaction"` regardless of what was actually retrying.

##### Performance

- **Every search ran a full 57-column source listing at page size 100,000** — `_get_enabled_source_ids()` now uses a single-column projection through a new storage-protocol accessor, which also removes the >999-source SQLite parameter-limit hazard the old path could trigger.
- **`GET /queue/tasks` shipped whole task payloads, including LLM `messages` arrays, on two five-second polls** — the list endpoint now returns a whitelisted subset (`inputs.filename`, `inputs.analysis_depth`, `operations_count`). The detail endpoint is unchanged and still returns the full payload. **If you consume the list endpoint's `data` blob from your own tooling, this is the one change in this release you may need to adjust for.**
- **`get_source` read and twice-copied the entire raw upload on a three-second poll** — `full_text` joins the heavy-column set the response model discards anyway, with a narrow accessor added for the CCX export path that genuinely needs it.
- **Chunk hydration batches instead of looping** — `get_chunks_by_ids_batch` gained a column projection that excludes the ~5 KB-per-hit embedding and the raw content, and four chat and workflow tool handlers (`summarize`, two GraphRAG paths, node search) replaced per-hit `get_chunk_by_id` loops of up to ~100 queries per turn with a single batch fetch each. Chunk-task lifecycle reads got the same projection treatment.
- **GraphRAG personalized-PageRank source scoping happens in SQL** — the source filter is applied before the `LIMIT` rather than after it, so scoped searches no longer discard most of the rows they paid to fetch.
- **Search hydration stops fetching embeddings it never reads** — `get_nodes_batch` takes an `include_embedding` flag and the hydration caller passes `False`; the import path that needs vectors is unaffected. A follow-up applied the same projection to **twelve more batch-fetch paths** — the research engine, grounding, the CLI's source search, and the workflow tool handlers for nodes, edges, analytics, and GraphRAG — none of which read the 1024-float embedding they were hydrating.
- **Vision page retries no longer carry page content through the retry path.**

##### Deployment

- **The multi-container stack's login screen and setup wizard were broken** — `multi-interface-nginx.conf` had drifted from its template and was missing the auth-exempt `settings/public` and `settings/host` locations, so the SPA got 401s before login. The static config is re-synced.
- **The Valkey wipe sentinel is written where the worker reads it** — it went to `/run/chaoscypher/valkey_was_wiped` while Neuron's `_consume_wipe_sentinel` reads `/data/.valkey_was_wiped`, so the forced queue-rehydration recovery path could never fire after an AOF wipe.
- **The boot splash ships its security headers** — nginx drops inherited `add_header` directives, so `location /` in `nginx-startup.conf` served none of the eight headers the rest of the site sets.
- **The production compose file no longer points `LEXICON_URL` at a dev-only host** — it defaults to the public hub and stays overridable, and the Valkey healthcheck gained the same `${QUEUE_PASSWORD:?}` guard its dev twin already had.
- **`.env.example` no longer claims the healthcheck scripts read the health-timing variables** — they are compose interpolation only, and exporting a `10s` value into a container crashes the probe. `CHAOSCYPHER_VALKEY_MAXMEMORY` is documented as multi-container-only.
- **The production image builds again from a bare `packages/interface` copy** — the frontend build stage typechecked test files, and a test that imports a fixture from the cortex package resolved in the monorepo but not inside the image, so `docker build -f packages/docker/Dockerfile .` failed since 2026-09-01. Test files are now excluded from the image build context. Relevant if you build the image from source.
- **Release workflows fire on final `vX.Y.Z` tags only** — the `v*` glob had no pre-release guard, so pushing a tag like `v0.5.0-rc1` would have repointed the `latest` image and published to PyPI, irreversibly. Relevant if you fork the repo and reuse its workflows.
- **The npm audit gate fails closed on a degraded registry report** — during a registry outage npm printed an error object instead of a report; it parsed, classified to zero findings, and the gate printed PASS while a known high-severity root was present. It now refuses to vouch for a tree it could not audit.
- **Build and test gates that could pass while failing were repaired** — the E2E summary now counts errored tests (setup and teardown crashes) in its totals and exit code, the secret scan distinguishes a tooling failure from a finding, the architecture-rules CI job runs all four enforcers rather than one, and the `make` targets were re-synced with the authoritative CI step lists so `make ci` and `scripts/run_ci.py` agree. Relevant if you build from source or run the suites yourself; the shipped image is unaffected.

##### Documentation

- A full accuracy pass over the docs site (API, CLI, and Python reference, plus the user guide) corrected 60-plus points of drift against current behavior, and the API-reference generator's signature handling was fixed so it stops reintroducing them.
- The MCP tool descriptions, the retry-policy checklist, the encoding chain, the `UploadOptions` documentation, quality-counter labels, and the `ebooklib` license note were corrected.
- Two blog posts published — importing an Obsidian vault, and the CCX 3.0 portable package format — and the local-AI post's install steps now show the published image rather than a build from source.

### August 2026

#### v0.4.1 (2026-08-25)

A large fixes-only patch release — 75 commits since v0.4.0. No new features, no
breaking API changes, no schema migrations. Security hardening, data-correctness
fixes, and queue/worker reliability dominate.

##### Security

- **Diagnostic exports could leak API keys when JSON logging is enabled** — the diagnostic collector's secret-scrubbing patterns matched only the unquoted form (`api_key=VALUE`, `api_key: VALUE`). With `USE_JSON_LOGGING=true`, structlog renders log lines as JSON and quotes the field name, so `"api_key": "..."`, `"authorization": "Bearer ..."`, and `"token": "..."` slipped through unmasked and were written into the exported bundle. **This is the headline reason to upgrade if you run the multi-container production stack**, which sets `USE_JSON_LOGGING=true` by default — a diagnostic bundle is something you generate specifically to send to someone else. The all-in-one image defaults to `false` and was not affected. Patterns now match both renderings. If you exported and shared a diagnostic bundle from an earlier version of a JSON-logging deployment, rotate the provider keys that instance was configured with.
- **Prompt injection through ingested documents is fenced** — entity names harvested from graph tool results and source titles interpolated into the chat system prompt are text a document author controls, and they were spliced verbatim into synthesized `role="user"` guidance — the channel the model is told to treat as authoritative. A label carrying a newline plus a fake `SYSTEM:` line could escalate into the instruction channel and, under the default never-ask tool-approval mode, drive an unattended mutating tool call. Names are now sanitized at the single harvest point (control characters stripped, length capped) and the interpolated lists are wrapped in the repo's `<untrusted_document>` fence. Forged closing fence tags inside document content are defanged, and the system prompt now names the fence explicitly.
- **`CHAOSCYPHER_ALLOW_USER_PLUGINS=0` now actually disables user domain plugins** — the kill switch was honoured by the other plugin loaders but not this one, so user-supplied domain plugins were discovered and executed even with discovery switched off. If you set this variable expecting it to hold, it now does.
- **Archive ingest enforces size and file-count caps before inflation** — `tar.gz` inputs are measured on a streaming pass, so a decompression bomb is rejected instead of being expanded first.
- **Secret material is written atomically at `0600`** — the custom-TLS key upload, the session HMAC secret, and the Lexicon OAuth token used write-then-`chmod`, leaving a brief window where the file was readable by other local users. All three now go through one shared atomic-write helper, and the Lexicon config directory's mode is re-applied on every call.
- **A settings PATCH can no longer lock you out permanently** — `local_auth.edge_auth_header` names the nginx→Cortex trust header; it was writable through the settings API, so renaming it made every subsequent request unauthenticated with no way back in. It joins the protected-field list.
- **Custom embedding endpoints are URL-validated** — `EmbeddingSettings.api_base` now carries the same `validate_url_safety` guard as every sibling custom-endpoint setting.
- **Secure-cookie flag follows TLS state** — `cookie_secure` is re-resolved per cookie write and settings are reloaded when TLS configuration changes, instead of being fixed at process start.
- **`settings.yaml` is written atomically at `0600`** — the settings file carries plaintext provider API keys, the edge-auth token, and the queue and supervisor passwords, and it was written at umask permissions (typically world-readable) and only narrowed to `0600` afterwards. It now goes through the same `mkstemp`-based atomic-secret-write helper as the other secret files, so the plaintext never touches disk in a readable mode.
- **`current_database` is format-validated** — the setting was writable via the settings API with no shape check and joins queries as a raw path segment; it now carries a validator, closing a path-injection-shaped hole.

##### Data correctness

- **A busy-database retry could silently discard committed work** — on `SQLITE_BUSY`, the commit retry rolled back and re-applied a snapshot of pending objects; inside `adapter.transaction()` every write has already been flushed, so that snapshot was always empty. The rollback threw the work away, the retry committed nothing, and success was logged. Callers proceeded on that "successful" commit — `delete_source` in particular tore a source apart, removing the search index and file while the SQL rows survived. The retry now re-arms and re-issues the COMMIT that SQLite is still holding open, and raises when it cannot verify that state.
- **A rebound task database could read and write the wrong database file** — the SQLite adapter and graph repository adopted an ambient session without checking it belonged to their own engine, so an adapter rebound to a task's database could silently operate on the surrounding scope's database instead. Symptoms were cross-database data loss on reset, sources stuck pending, and exports reading the wrong database. Both now adopt the ambient session only when its engine matches.
- **The reconciler no longer dispatches a second copy of live work** — its absolute-timeout branch is deliberately heartbeat-blind, and its requeue path refuses only completed/cancelled tasks, so a `running` task reset to `queued` gets claimed by a second worker. Two call sites derived the cutoff from the wrong timeout (Cortex's safety net read the settings default while workers run on the `workers.yaml` override; both worker sites passed their own deadline verbatim, so the cutoff expired at the same instant the worker's did). A single resolver now reads the same override, clamps it with shared policy bounds, floors it at the settings default, and adds a safety margin.
- **Cancelled tasks are not dispatched, and cancellation survives a restart** — the worker's running-claim is now guarded against a task cancelled between claim and dispatch, and `cancel_by_metadata` persists a durable cancellation marker rather than relying on in-flight state alone.
- **Chat retry/regenerate no longer double-enqueues** — status is claimed atomically, and terminal status is reconciled after SSE subscribe via a sentinel, closing the window where a client subscribing late saw a stale non-terminal state.
- **A failed save no longer duplicates chat messages on retry** — message persistence committed one row at a time with no enclosing transaction, so a mid-write failure left earlier rows durable and the retry re-inserted them. The whole batch now commits as one transaction, matching the documented contract.
- **Sources no longer wedge at `vector_indexing_status: pending`** — a failure while enqueuing the search-index retry, after the commit had been marked complete, permanently lost the retry, the degraded mark, and every recovery path. All three enqueue sites are guarded; on failure the source is marked `degraded`, which is recoverable.
- **A source is marked indexed only after its last pending search row drains**, so the indexed state can no longer be reported ahead of the work.
- **Template re-embedding: one bad template no longer poisons the batch, and graph/search can't silently diverge** — each template's work is now isolated and failures are counted rather than forcing a full-batch retry that re-embeds everything already done. The graph-row update and the search-index write also share a single transaction; previously the graph write committed on its own while an index failure was swallowed as a warning, leaving a permanent mismatch with no reconciliation path.
- **Extraction recovery keeps SQLite and the queue in agreement** — when marking a chunk task queued fails after the re-enqueue succeeded, the live queue task is now best-effort cancelled before the error propagates.
- **Vision pages retry instead of being marked failed** — retryable LLM errors from image description are propagated rather than swallowed into a terminal per-page failure.
- **Per-source table rows are scored correctly in quality scoring (v8)** — reference counts and chunk mentions were computed against the wrong source of truth, so quality scores for table-bearing sources were wrong; the Cortex slice, the Neuron recalculation handler, and the CLI now all ride the same canonical helpers and read the per-source extraction tables.
- **Migration backups are recorded before they are needed** — the pre-apply backup path is persisted before the upgrade runs, and `last_backup` survives a successful data-changing apply.
- **A task retry can no longer resurrect a cancelled task** — the retry path blind-wrote `status="queued"` with no compare-and-swap, so a task cancelled while running was revived and executed a second time in full; the reconciler's guards never saw the cancelled state to refuse it. The retry now claims through the same guarded status write as everything else.
- **Switching databases rebinds the system-event bus** — the singleton `event_bus` stayed bound to the previous database's adapter after a switch, silently writing system events into the old database file.
- **Re-importing a CCX package no longer duplicates citations** — import is idempotent on citation rows, matching the upsert-by-IRI behavior nodes and edges already had.

##### Reliability

- **Low-priority LLM work no longer starves** — the admission gate compared total active work against the low-priority allowance instead of the low tier's own counter, so high-priority work consumed the low tier's budget and a low-priority waiter could block forever on an untimed wait. The reserved-interactive slot count is also clamped below `llm_max_concurrent` (matching the UI's own clamp) on load, on live settings reload, and when the maximum alone shrinks. A configuration with `llm_reserved_interactive >= llm_max_concurrent` is now clamped with a warning naming both values rather than starving silently.
- **Databases created before the 2026-06-02 schema squash now refuse startup with guidance instead of failing obscurely** — such a database was re-stamped at the baseline, after which the shipped migrations replayed against a schema they were never written for and the boot died in the drift gate on every supervisor retry. The squash predates every public release, so no released build ever wrote one of these; they are explicitly unsupported. Cortex now exits cleanly with an `UnsupportedDatabaseLineageError` naming the revision and the recovery path (back up, then re-create or export/re-import), and the stamp is left untouched.
- **Queue reconciliation falls back to the canonical queues** when Cortex's reconcile registry is empty, instead of silently reconciling nothing.
- **Blocking work moved off the event loop** — the stuck-chat sweeper's SQLite I/O, the Neuron health-monitor tick (now also inside a session scope), the vision spend-tracker check/record, and the chat status/persist writes all run in threads, matching the offload pattern the surrounding handlers already used.
- **Bulk resume recovers per source** — each recovery in a bulk resume runs in its own adapter session scope, so one failure no longer aborts the rest.
- **Source heartbeats run on their own session** — background beats could execute on the finalize transaction's session from a second thread. Each beat now uses a short-lived session of its own.
- **Upgrade recovery finds its source** — the recovery path reads `source_id` from task metadata, where it actually lives.
- **Cancellation no longer phantom-releases an LLM slot** that a clear-wake had already handed to another waiter.
- **`POST /admin/plugins/reload` actually reloads** — the endpoint cleared a cache map no production code ever populated, returning success while every real registry kept serving stale plugins until a process restart. The loader, domain, and preset registry caches are now registered with the invalidation path — and they are keyed by the resolved plugin root rather than settings-object identity, so per-request settings objects no longer leak one dead registry each.
- **Cortex's health-monitor tick runs in its own session scope** — it shared the process-singleton fallback session with request handlers emitting system events, a not-thread-safe combination whose failure mode was system events silently stopping and auto-pause dying for the life of the process (the Neuron sibling was fixed for exactly this earlier).
- **The Lexicon client no longer leaks connection pools** — download/upload clients were created per request and never closed, and all four `LexiconSettings` knobs (timeouts, retries) were ignored; both fixed.

##### Performance

- **`POST /search/embeddings` batches** — it embedded up to 10,000 nodes one at a time inside the request: one provider round trip, one SELECT+UPDATE+COMMIT, and one single-row vector upsert per node. It now collects the pairs, makes one batched embedding call, and persists in one transaction with a batched index refresh — roughly 156 provider calls for 10,000 nodes at the default batch size instead of 10,000. (Its docstring also no longer claims the work happens in the background; it never did.)
- **CLI `index-file` batches chunk-embedding writes** — one bulk UPDATE and one commit per embedding wave, replacing a full-row SELECT plus a real commit per chunk (a 3 MB import was ~4,000 of each). Resumability is preserved at wave granularity.
- **Search results hydrate faster** — assembling a page of chunk results issued two serial database calls per chunk. Chunks are now fetched in one batched query per page and repeated source lookups are memoized, mirroring how node results already worked. Most noticeable on large result pages.
- **Read paths project only what they use** — chunk-task summaries collapse to one `GROUP BY`, the workflows dashboard replaces per-workflow queries with one aggregate, `update_file`/`update_source_columns` project key columns only, graph-snapshot staleness reads three scalars instead of loading the row's JSON payload, and scoped-chat source titles resolve in one batched call instead of a per-source loop.
- **`GET /sources/{id}/vision_pages` stops shipping every page's LLM description** — the endpoint is polled, and the description text column dominated the payload; `error_message` (which the UI renders) is retained.
- **Chat existence and status checks stop loading 500 message rows** — four `chats` endpoints now use a summary read, and `GET /chats/{id}/messages` no longer reads its messages twice.
- **Chunk-offset matching is no longer quadratic** — exact matching carries a forward cursor and per-chunk location lookups use binary search.
- **Deleting a source batches its orphaned search-index node deletes** instead of one pooled connection and commit per orphan.

##### Interface

- **Light mode is readable again** — the theme swapped `background` on the dark-mode toggle but pinned `text.*` and `divider` to the dark-only slate neutrals unconditionally, so switching to light mode (a live switch in General Settings) rendered near-white text on a near-white background across every component reading the `text.*` token. The slate overrides now apply only in dark mode, with MUI's accessible light defaults otherwise.
- **The Sources status filter is actually applied** — the parameter was accepted but never placed on the outgoing request.
- **The template force-delete dialog can open** — it matched two error-message substrings the API never sends; it now matches the real `409 TEMPLATE_IN_USE` response. The templates empty state also spans the right number of columns.

##### CLI

- **`template list` paginates** (`--page`/`--limit`, the same idiom as `node list`) instead of silently truncating at 50.
- **`template get`/`update`/`delete` report a missing template** rather than falling through dead `None` guards, and the help text lists all 13 property types.
- **`quality` commands read the per-source extraction tables**, matching the scoring fix above.
- **`quality recalculate` exits non-zero on failure** — it counted and printed per-source errors but always exited 0, so scripted invocations could not distinguish success from total failure. Old `cc …` command hints in error messages are also corrected to `chaoscypher …`.

##### Docs

- The `GET /sources/{id}/chunks` API reference shows the `{data, pagination}` envelope the endpoint actually returns, and the `reset/knowledge` and `reset/all` result payload keys are corrected.
- Template docs corrected: CLI examples used names where only IDs resolve, and delete semantics were documented backwards (`force=true` is a cascade delete; a plain delete is blocked, never dangling).
- The upgrading guide and ADR-0006 no longer promise auto-recovery for pre-squash databases.
- **The CCX 3.0 format has a published draft specification** — [`reference/ccx-format`](../reference/ccx-format.md) documents the container, manifest schema, JSON-LD graph members, `sources.jsonl` row schemas, and the validator's conformance classes, written against the shipped `ccx-format` reader.

##### Dependencies

- Frontend minor/patch group updates (7 + 8 packages), a `python` 3.14.6→3.14.7-slim production base-image bump, and documentation-site dev-dependency bumps.

##### Migrations

- **None.** No schema changes in this release; the only edit under `migrations/versions/` is a docstring correction.

##### Upgrade advisory

- No queue payload schemas changed, but the standing guidance applies: drain the queue before swapping the image (stop new submissions, wait for `/api/v1/queue/stats` to report 0 pending on all queues), since payload-version negotiation is not yet implemented.
- If your `settings.yaml` sets `llm_reserved_interactive` at or above `llm_max_concurrent`, startup now clamps it and logs a warning — the effective value changes, so check the log line and set an intentional value.
- If you set `CHAOSCYPHER_ALLOW_USER_PLUGINS=0` but were relying on user domain plugins loading anyway, they will stop loading after this upgrade.

#### v0.4.0 (2026-08-07)

##### Breaking Changes

- **`GET /sources/{id}/recovery_events` now uses standard pagination.** The endpoint previously took `?limit=` (1–200, default 50) and returned `{"events": [...]}`. It now takes `?page=&page_size=` and returns the house `{data, pagination}` envelope, matching every other list endpoint. **Migration:** replace `?limit=N` with `?page_size=N`, and read events from `data` instead of `events`; the pagination block is `{total, page, page_size, total_pages, has_next, has_prev}`. `?limit=` is no longer read. The web UI ships already migrated, so the product is self-consistent — this affects external API consumers only. (Pre-1.0 convention: breaking changes ride MINOR; `0.y.z` carries no compatibility guarantee.)

##### Security

- **Compose package resolver could be made to write outside its extraction directory** — the resolver built its extraction path from the Lexicon hub's own `version` string, so a hostile or compromised hub could place package contents anywhere the process could reach, including the auto-executed user-plugin directory. Path construction is now hardened. The same fix corrects shifted client arguments that had made hub resolution unable to match any package, so no working behavior regresses.
- **`cryptography` 48.0.1 → 50.0.0** — CVE-2026-69247, CVE-2026-69248, CVE-2026-69249. 50.0.0 is the minimal clearing version (69247 does not clear below it).
- **URL import no longer has a DNS-rebinding window.** `POST /api/v1/sources/url` validated each hop and then handed the *hostname* back to the HTTP client, which re-resolved it at connect time — so a name that resolved to a public address during the check could resolve to an internal one for the actual fetch. The fetch now dials the validated IP directly, carrying the original `Host` header and TLS SNI, and redirect targets are re-validated the same way. Earlier releases documented this as an accepted residual in the self-hosted threat model; that entry is gone because the residual is.
- **`pypdf` ×6 CVEs cleared** — ×4 earlier in this cycle, plus CVE-2026-71852 and CVE-2026-71870 in `pypdf 6.14.2`, cleared by 6.15.0 on the day they were published. PDF parsing is an attacker-reachable surface for anyone ingesting untrusted PDFs, so this is the highest-value dependency fix in the release.
- **`js-yaml` CVE-2026-59870** cleared (4.3.1).
- **Further dependency advisories cleared** — `pyasn1`, `fast-uri`, `brace-expansion`, `react-router`, `js-yaml` (via redocly), `nanoid`, `dompurify`, `mermaid`, every other `packages/docs` npm advisory, and `undici` / `ip-address` in the frontend toolchain. `pip-audit` and both `npm audit` runs report clean, and the documentation site's dependencies are now covered by the CI security gate.
- **Self-signed TLS key is created with `0600` from the start** — previously it was briefly world-readable on disk.
- **Typed local-auth errors** for a corrupt password hash and for double-initialize, instead of opaque failures.

##### Data correctness

- **`load_text` silently dropped every document after the first** — multi-document loads were losing content with no error surfaced.
- **Backup restore could lose recent commits.** The restore path unlinked the live WAL/SHM and overwrote `app.db` *before* disposing cached engines, and its "safety backup" was a WAL-blind file copy — so recent committed data could vanish on a restore. Restore now mirrors the upgrade-rollback path and takes its safety copy with `VACUUM INTO`.
- **`env > settings.yaml` precedence restored** — a regression had stopped environment variables from winning.
- **Races that produced duplicate or lost work are closed** — `confirm_extraction` and `retry_task` claim via SQL compare-and-set; queue cancellation and reconciler paths are atomic under a lock; chat tool-approval is first-decision-wins.
- **`StageProgress` no longer reports a stage complete when its body raised.**
- **A failure *after* a chunk was committed no longer discards its extracted entities.** Everything following the chunk-persist commit sat inside the same `try`, so a late failure (for example a full finalize queue) marked an already-committed chunk `failed` — and because the finalizer aggregates only `completed` rows, the entities you had already paid to extract were silently dropped. The post-commit tail is now guarded and the row stays `completed`.
- **Archive and RST ingest fixes** — three P1 defects in the archive/RST loader paths and in force-re-extract.
- PDF image-detection scans are capped at `pdf_max_pages`; workflow-system reset is atomic and reports real deleted counts; chunk-pipeline handlers honour the task's target database.

##### Reliability

- **One call to `DELETE /llm/semaphore` could wedge all LLM traffic for the process lifetime** — `PrioritySemaphore.clear_waiting_queues()` awaited a helper that re-acquired its own non-reentrant lock, deadlocking on itself. A related path leaked a concurrency slot permanently when an exception escaped the worker's cleanup block.
- **MCP extraction hardening** — finalize now rolls back cleanly on failure, quick-depth is forwarded correctly, and a session leak is evicted rather than accumulating.
- Deep audit passes over the chat and queue slices fixed a batch of defects each: citation-grammar alignment and contentless-done refetch in chat; worker-cleanup semaphore leak, swallowed-timeout-as-cancelled, sub-cent LLM cost truncation, and several queue-monitor UI defects.
- **Workflows slice audit — the automation path had four defects that made it unusable in places.** `ai.extract_json` always failed with "Engine settings not provided" and `ai.prompt` silently fell back to hardcoded defaults, because settings were never threaded into the tool context. Queued step execution died with a `TypeError` from an orchestrator/executor signature mismatch hidden behind two `type: ignore`s. Imported or duplicated workflows were stored with an empty database name, so they never appeared in any list. And `summarize` lost every page beyond the first for all sources after the first. Also fixed here: explicit `temperature=0.0` and `chunk_overlap=0` are honoured instead of being replaced by defaults, malformed fenced JSON from a model falls back to text instead of raising, trigger stats no longer grow without bound in the long-lived executor, and CrossEncoder construction is moved off the event loop.
- **Speed-ups on the paths that dominate large imports** — chunk-embedding persistence went from one full-row `SELECT` plus one real `COMMIT` per chunk (up to 2,000 per wave) to a single batched update; the vision finalizer no longer rebuilds the whole document body once per page description; package-import citation writes and workflow-execution listings use the batch primitives that already existed alongside them.
- **Failures stop being invisible** — search-index flag reads log instead of silently returning `false` during the contention they exist to guard, and a close failure while recording spend no longer masks a permanent spend-cap error into one the queue retries forever.

##### Features

- **Per-call extraction overrides now actually take effect.** The source-creation API accepted per-call extraction settings and then ignored them, falling back to the global configuration; the values you pass are now applied to that source's extraction.
- **`chaoscypher source list --limit`** with an honest truncation footer, and **`config set`** now accepts list-valued settings.
- **Filter dropdowns are reachable by their label** for screen readers — the shared search/filter bar rendered its selects with no accessible name.
- **EPUB chapter skips are surfaced** via a new `loader_epub_chapters_skipped` quality counter (migration `0006`).
- **Abandoned `mcp_extracting` sources are marked failed** after a staleness window. *Behaviour change:* sources that previously hung in `mcp_extracting` now transition to `failed`.
- **Guarded-write / atomic-move primitives** on the queue client (internal substrate for the correctness fixes above).

##### Migrations

- **`0006_loader_epub_chapters_skipped`** — additive only (one new quality-counter column), classified `safe_auto`, so it applies on startup with automatic migrations enabled (the default). No destructive operations.
- **Where to find the automatic pre-migration backup.** It is written **per database**, next to the database file itself: `<data_dir>/databases/<database_name>/backups/pre-<revision>-<timestamp>.db` — e.g. `/data/databases/default/backups/pre-0006-20260804T145504Z.db`. Note this is *not* `<data_dir>/backups/`, which is where **manual and scheduled** backups go; that directory exists but stays empty unless you take one. Earlier changelog entries named the wrong path here.

##### Upgrade advisory

- Drain the queue before swapping the image: stop new submissions and wait for `/api/v1/queue/stats` to report 0 pending on all queues. Payload-version negotiation is not yet implemented, so don't run mixed old/new versions against the same queue.

### July 2026

#### v0.3.1 (2026-07-20)

- **Security: OpenAPI archive loader no longer resolves external `$ref`s** — an uploaded OpenAPI spec could reference `file:///...` or `http(s)://...` in a `$ref` and have the loader read local files or internal-network resources into indexed text. Resolution is now restricted to in-document `#/...` references only; anything else is rejected with a clear error. If you ingest OpenAPI specs from sources you don't fully trust, this is the headline reason to upgrade.
- **Security: static file serving hardened** — SPA path containment now uses a real path-prefix check (`Path.is_relative_to`) instead of a string comparison, closing a sibling-directory edge case.
- **Security: CLI `config set` no longer echoes secret values** — setting a known secret path prints a masked confirmation instead of the plaintext value.
- **Security & dependencies** — every advisory flagged since v0.3.0 is patched: pillow 12.3.0 (5 PYSEC advisories), lxml 6.1.1 / lxml-html-clean 0.4.5 / soupsieve 2.8.4 (3 CVEs), setuptools 83.0.0 (PYSEC-2026-3447, unblocked by the torch 2.11 → 2.13 upgrade below), mcp 1.28.1 (3 CVEs), websocket-driver 0.7.5, and a frontend minor/patch dependency sweep. `pip-audit` and `npm audit` both report clean.
- **torch 2.11 → 2.13 (CPU)** — the local-embedding stack's torch is upgraded; embedding output is verified bit-identical across the upgrade, so existing vector indexes are unaffected and nothing needs re-embedding.
- **Queue: unrecoverable filesystem errors fail fast** — a missing file or bad mount during processing is now classified permanent and fails the task immediately with the real error, instead of burning ~4.5 minutes in a transient-retry loop before failing anyway. Genuinely transient conditions (`EINTR`, `EAGAIN`) still retry.
- **Chat: worker errors surface correctly** — if updating a chat's status to `error` itself failed, that secondary failure used to mask the original error and derail retry classification; the original error now always propagates.
- **Export: scoped graph exports no longer miss edges on large graphs** — edge scoping is applied in SQL before the row cap, so a scoped export of a busy database can no longer come back with zero in-scope edges.
- **Ingestion fixes** — compound-suffix archives (`.tar.gz`) route to the right loader; all six file loaders enforce the same size guard; a PDF page-handle leak is closed; whitespace-only chunks are dropped correctly; inverse relationships no longer inflate citation counts; chunk-attempt lookups work for committed sources; CLI `source list --pending`/`--status` use the real status values.
- **Platform fixes** — malformed session cookies raise a proper auth error instead of a 500; the latest-backup pick is timestamp-based (a lexical sort could restore the wrong backup around month boundaries); a diagnostics database-connection leak is closed; full-node reindexing batches its writes instead of issuing one query per node.
- **Graph & search fixes** — empty-graph betweenness returns the canonical empty shape; CLI `node get --include-links` paginates connected edges instead of truncating; the node detail page keeps your edit form open if a save fails; vector-index rebuilds report skipped chunks per source in quality metrics.
- **Migrations** — none. No schema changes in this release.
- **Upgrade advisory** — no queue payload schemas changed, but the standing guidance applies: drain the queue before swapping the image (stop new submissions, wait for `/api/v1/queue/stats` to report 0 pending on all queues), since payload-version negotiation is not yet implemented.

#### v0.3.0 (2026-07-03)

- **Breaking: CCX 3.0 replaces the 2.0 package format** — export and import are rebuilt on the open [`ccx-format`](https://pypi.org/project/ccx-format/) spec: IRI-based identity on nodes, edges, and sources; upsert-by-IRI on import (re-importing a package updates in place instead of duplicating); JSON-LD named graphs; and a `chaoscypher.statistics` graph carrying source/lens/workflow stats. All v2.0 writers and loaders are removed — **`.ccx` bundles exported by v0.2.x will not load in v0.3.0**. Re-export from a v0.3.0 instance (the upgrade preserves your data; only previously exported bundle files are affected). A full semantic round-trip plus idempotency is pinned by the E2E suite.
- **Imported packages are now first-class** — imported sources are searchable (chunk-level RAG), their entities are re-embedded for semantic search and GraphRAG, entity types / source links / counts are finalized on import, bundled embeddings restore directly when the embedding model matches (skipping a full re-embed), tags round-trip as CCX keywords, extraction domains round-trip (correct icons everywhere), imported templates cascade-delete with their source, and the source page is import-aware. The misleading "replace all data" import warning is gone, and nginx accepts large `.ccx` uploads (configurable via `max_upload_bytes`).
- **Chat: click-through citation sentence highlighting** — click a citation and jump to the exact sentence, highlighted in the source text. Migration `0005` recomputes stored sentence offsets, so highlighting is correct for sources indexed by earlier versions too.
- **Richer packages** — exports can embed a `graph_preview.png`, per-domain source breakdowns, and `derived_from`/`dependencies` lineage in the manifest; the source's original text is persisted at index time (`sources.full_text`) and travels with the package. The Lexicon client speaks the Hub's CCX 3.0 contract (async job envelope on upload, `{data}` unwrap fix).
- **Fixes** — driver dashboard repaired (fetches, charts, queue lists); switching the active database now re-points workers immediately, so chunk writes no longer land in the previously-active database, and exports honor the task's target database; a path-handling bug that could delete application files when removing a source with a non-absolute filepath is fixed; MCP worker crashes surface properly and the queue reconciler no longer strands requeued tasks; SPA routes show a styled error page on upstream 5xx; imported-node search filters by the `source_id` column rather than stale properties; embedding retry backoff is jittered to avoid thundering-herd retries.
- **Security & dependencies** — every dependency CVE flagged since v0.2.0 is patched (pypdf, cryptography, starlette, python-multipart, langchain, langsmith, msgpack, pydantic-settings, esbuild, dompurify, the `ws` DoS, and more); both frontend packages report `npm audit` clean; FastAPI is pinned below 0.137 pending an upstream router regression fix.
- **Migrations** — three revisions apply on upgrade: `0003` (adds `ccx_iri` columns to nodes/edges/sources), `0004` (adds `sources.full_text`), and `0005` (data migration: recomputes chunk `sentence_offsets` from content — classified *needs-confirmation*; with automatic migrations enabled, the default, it applies on startup and performs one scan of `document_chunks` on first boot). No destructive schema operations; `0005` rewrites only derived offset values, which are recomputable from content. The automatic pre-migration backup is written per database at `<data_dir>/databases/<database_name>/backups/pre-<revision>-<timestamp>.db` (corrected 2026-08-04 — earlier text named `<data_dir>/backups/`, which is the manual/scheduled backup location, not this one).
- **Upgrade advisory** — drain the queue before swapping the image: stop new submissions and wait for `/api/v1/queue/stats` to report 0 pending tasks on all queues. This release adds new operation types for imported-source indexing; don't run mixed old/new versions against the same queue.

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
