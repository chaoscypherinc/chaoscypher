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
curl -X POST "http://localhost:8080/api/v1/exports?include_templates=true&include_knowledge=true&include_workflows=true&include_sources=true"
```

Returns a `task_id` — the export runs asynchronously. Poll the task status and download the result when complete.

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

Toggle individual components to export exactly what you need. All flags default to `true` — set any to `false` to exclude that component.

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

Import runs asynchronously. The result includes counts of imported templates, nodes, edges, and workflows, plus any errors or warnings.

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
