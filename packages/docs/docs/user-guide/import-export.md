---
id: import-export
title: Import & Export
description: Export and import knowledge graphs using the CCX format — share nodes, edges, templates, workflows, and sources between databases or with the Lexicon Hub community.
---

# Import & Export

Export and import knowledge graphs using the CCX (Chaos Cypher eXchange) format. Share your extracted knowledge with others or move it between databases and instances.

## CCX Format

CCX is a compressed archive format with the `.ccx` file extension. A CCX package bundles knowledge graph data and can include:

| Component | Description |
|-----------|-------------|
| **Templates** | Node and edge type definitions |
| **Knowledge** | Graph nodes and edges with properties |
| **Workflows** | Automation definitions and triggers |
| **Sources** | Document metadata, chunks, citations, and tags |

## Exporting

### Full Graph Export

Export the entire knowledge graph from the current database:

```bash
curl -X POST "http://localhost:8080/api/v1/exports?include_templates=true&include_knowledge=true&include_workflows=true&include_sources=true&include_embeddings=false"
```

Returns a `task_id` — the export runs asynchronously. Poll `GET /api/v1/queue/tasks/{task_id}` until the task completes, then download the `.ccx` via `GET /api/v1/queue/tasks/{task_id}/result`. See the [Queue API reference](../reference/api/queue.md) for task-status details.

### Source-Filtered Export

Export only data related to specific sources:

```bash
curl -X POST "http://localhost:8080/api/v1/exports/by_sources?include_templates=true&include_embeddings=false" \
  -H "Content-Type: application/json" \
  -d '["source-id-1", "source-id-2"]'
```

This exports:

- Entities with citations from the specified sources
- Edges where both endpoints are in the entity set
- Source metadata, chunks, citations, and tags
- Linked templates (optional)

### Selective Components

Toggle individual components to export exactly what you need. The four component flags (`include_templates`, `include_knowledge`, `include_workflows`, `include_sources`) default to `true` — set any to `false` to exclude that component.

`include_embeddings` defaults to `false`; enable it only when migrating between instances that use the same embedding model. It is accepted on both `POST /exports` and `POST /exports/by_sources`.

### Web UI

- **Full-graph export** — open **Settings** → **General** → **Import & Export** and click **Export**. Choose which components to include (templates, knowledge, workflows, sources, embeddings) and the browser downloads a `.ccx` file. Exporting requires a Package Name, set under **Settings** → **Export Defaults**.
- **Per-source export** — on the **Sources** page, open a source's action menu and choose **Export Source** to download a `.ccx` scoped to that source.

## Importing

Import a CCX package into the current database:

```bash
curl -X POST "http://localhost:8080/api/v1/exports/import?merge=false" \
  -F "file=@package.ccx"
```

| Mode | Behavior |
|------|----------|
| `merge=false` (default) | Replace existing data with package contents |
| `merge=true` | Merge package data with existing data |

Import runs asynchronously — poll `GET /api/v1/queue/tasks/{task_id}` and fetch the outcome from `GET /api/v1/queue/tasks/{task_id}/result` when complete. The result includes counts of imported templates, nodes, edges, and workflows, plus any errors or warnings.

In the web UI, import a `.ccx` from **Settings** → **General** → **Import & Export** — click **Import** and select the file.

## Lexicon Hub

CCX packages can be shared with the community through [Lexicon Hub](https://lexicon.chaoscypher.com) — a central registry for discovering, downloading, and publishing knowledge packages.

```bash
# Search for packages
chaoscypher lexicon search "medical ontology"

# Download a package
chaoscypher pull john/medical-ontology

# Import into your database
chaoscypher graph package load john-medical-ontology.ccx

# Publish your own
chaoscypher push my-knowledge.ccx
```

For full details, see the [Lexicon Hub guide](../lexicon-hub/index.md).
