---
title: Source Commands
description: Upload, process, search, and evaluate document sources from the CLI — add files or URLs, trigger extraction, and query your knowledge graph with chaoscypher source.
---

# Source Commands

The `source` command group manages document sources -- uploading, processing, searching, and quality evaluation.

```bash
chaoscypher source --help
```

---

## Add Sources

Upload files, directories, or URLs for processing through the full pipeline: upload, index, extract, and commit.

```bash
# Single file
chaoscypher source add document.pdf

# Multiple files
chaoscypher source add doc1.pdf doc2.txt notes.md

# Directory (all supported files, non-recursive)
chaoscypher source add ./research-papers/

# URL
chaoscypher source add https://example.com/article

# Fast extraction (~30 seconds)
chaoscypher source add document.pdf --quick

# Force a specific extraction domain
chaoscypher source add document.pdf --domain technical

# Index only (no LLM required)
chaoscypher source add document.pdf --index-only

# Enable content normalization (OCR cleaning, encoding fixes)
chaoscypher source add document.pdf --normalize

# Target a specific database
chaoscypher source add document.pdf --database research
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--quick` | | Fast extraction (3 groups max, ~30 seconds) |
| `--skip-extract` | | Skip LLM entity extraction |
| `--skip-index` | | Skip indexing (use existing chunks) |
| `--skip-commit` | | Extract but don't commit to graph |
| `--skip-embeddings` | | Skip embedding generation during indexing (faster) |
| `--index-only` | | Stop after indexing (no extraction or commit) |
| `--extract-only` | | Stop after extraction (skip commit) |
| `--normalize` / `--no-normalize` | | Force content normalization on or off (OCR cleaning, encoding fixes). Omit to use the file-type default. |
| `--vision` / `--no-vision` | | Use the vision model on images and scanned PDFs (default: on). |
| `--content-filtering` / `--no-content-filtering` | | Apply domain content-exclusion rules during extraction (default: on). |
| `--filtering-mode MODE` | | Extraction filtering mode preset: `maximum`, `strict`, `balanced`, `lenient`, `minimal`, `unfiltered`. Overrides the domain default. See [Filtering Modes](../filtering-modes.md). |
| `--domain DOMAIN` | | Domain for extraction (see [Extraction Domains](#extraction-domains)) |
| `--no-confirm` | `-y` | Skip the domain confirmation gate and extract immediately with the auto-detected domain. |
| `--skip-duplicates` | | Skip upload if identical content already exists (matched by SHA-256 hash). |
| `--database DATABASE` | `-d` | Target database (default: `default`) |
| `--verbose` | `-v` | Show real-time processing logs |
| `--quiet` | `-q` | Minimal output (just OK/FAILED) |
| `--json` | | Output results as JSON |
| `--resume` | `-r` | Interactive picker for resuming pending files |

:::info[CLI flags match the API contract]

The `--vision/--no-vision`, `--content-filtering/--no-content-filtering`,
`--normalize/--no-normalize`, `--filtering-mode`, and `--skip-duplicates`
flags map 1-to-1 to the equivalent fields on the [POST /sources](../api/sources.md#upload-single-file)
upload endpoint. Whatever you can do via the API you can do via the
CLI, and vice versa — there's no longer a feature gap between the two.

The choices you set here persist on the source row, so a later
`Re-extract` (or a worker recovery) reuses them automatically.

:::

### Extraction Domains

Available domains: `auto` (default), `generic`, `technical`, `scientific`, `medical`, `legal`, `financial`, `news`, `educational`, `biographical`, `historical`, `literary`, `philosophical`, `political`, `theological`.

:::info[Domain confirmation gate]

By default, when the domain is auto-detected the source is parked in an
`awaiting_confirmation` state instead of immediately running the (potentially
long) extraction. Confirm the detected domain with
`chaoscypher source confirm <ID>` (or `--all` to confirm every parked source),
or pass `--no-confirm`/`-y` to `source add` to skip the gate and extract with
the auto-detected domain right away. Forcing a domain with `--domain` also
bypasses the gate.

:::

### Resume Processing

If processing was interrupted, you can resume from where it left off.

#### Interactive picker

```bash
chaoscypher source add --resume
```

``` { .text .no-copy }
Select file to resume:

 #  Filename              ID                Status      Chunks
 1  research-paper.pdf    if_a1b2c3d4e5f6   indexed        42
 2  quarterly-report.pdf  if_f6e5d4c3b2a1   uploaded       --

Enter number to resume (or 'q' to quit):
```

#### Resume a specific file by ID

```bash
chaoscypher source add if_a1b2c3d4e5f6
```

``` { .text .no-copy }
Resuming: research-paper.pdf (if_a1b2c3d4e5f6)

  Extracting entities ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
  Committing to graph ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%

  ✓ Done in 127.3s
    Entities: 84 | Relationships: 112
    Nodes created: 84 | Edges created: 112
    Domain: scientific (auto-detected)
```

### Sample Output

#### Default output (single file)

```bash
chaoscypher source add research-paper.pdf
```

``` { .text .no-copy }
  Uploading  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
  Indexing   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
  Extracting ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%
  Committing ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%

  ✓ Done in 142.8s
    Chunks: 42 | Tokens: 18,340
    Entities: 84 | Relationships: 112
    Nodes created: 84 | Edges created: 112
    Domain: scientific (auto-detected)
```

#### Quiet output

```bash
chaoscypher source add research-paper.pdf --quiet
```

``` { .text .no-copy }
OK if_a1b2c3d4e5f6
```

#### JSON output

```bash
chaoscypher source add research-paper.pdf --json
```

```json
{
  "file_id": "if_a1b2c3d4e5f6",
  "filename": "research-paper.pdf",
  "success": true,
  "status": "committed",
  "stages_completed": ["upload", "index", "extract", "commit"],
  "stages_skipped": [],
  "chunks_count": 42,
  "tokens_count": 18340,
  "entities_count": 84,
  "relationships_count": 112,
  "nodes_created": 84,
  "edges_created": 112,
  "detected_domain": "scientific",
  "duration_seconds": 142.8,
  "error": null,
  "llm_metrics": {
    "total_calls": 18,
    "successful_calls": 17,
    "failed_calls": 0,
    "retry_calls": 1,
    "retry_rate": 0.056,
    "success_rate": 0.944,
    "total_input_tokens": 52400,
    "total_output_tokens": 14200,
    "total_tokens": 66600,
    "wasted_tokens": 320,
    "estimated_cost_usd": 0.042,
    "model": "llama3.1:8b"
  }
}
```

#### Batch output (multiple files)

```bash
chaoscypher source add doc1.pdf doc2.pdf doc3.pdf
```

``` { .text .no-copy }
╭─ ✓ Batch Complete (3/3) ───────────────────────────╮
│                                                     │
│  #  File       Status   Duration                    │
│  1  doc1.pdf   done       42.3s                     │
│  2  doc2.pdf   done       38.7s                     │
│  3  doc3.pdf   done       51.2s                     │
│                                                     │
│     Total      3 succeeded              132.2s      │
│                                                     │
╰─────────────────────────────────────────────────────╯
```

---

## List Sources

List all ingested source files with their processing status.

```bash
chaoscypher source list
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--status STATUS` | `-s` | Filter by status (`uploaded`, `indexed`, `extracted`, `committed`, `failed`) |
| `--pending` | `-p` | Show only files not yet committed (excludes committed and failed) |
| `--awaiting` | `-a` | Show only sources awaiting domain confirmation |
| `--format FORMAT` | `-f` | Output format: `table` (default), `json`, `yaml` |
| `--database DATABASE` | `-d` | Database name (default: `default`) |

### Sample Table Output

```bash
chaoscypher source list
```

``` { .text .no-copy }
                         Ingested Files
 ID                Filename              Type   Size   Status      Quality  Created
 if_a1b2c3d4e5f6   research-paper.pdf    pdf    2.4 MB committed   92 A    2026-03-08 14:22
 if_f6e5d4c3b2a1   quarterly-report.pdf  pdf    1.1 MB indexed      -     2026-03-08 15:10
 if_c3d4e5f6a1b2   meeting-notes.md      md     12.3 KB committed  78 B    2026-03-07 09:45

Total: 3 file(s)
```

### Pending Files

```bash
chaoscypher source list --pending
```

``` { .text .no-copy }
                         Ingested Files
 ID                Filename              Type   Size    Status    Quality  Created
 if_f6e5d4c3b2a1   quarterly-report.pdf  pdf    1.1 MB  indexed    -      2026-03-08 15:10

Total: 1 file(s)

To resume: cc source add <ID>
Or use:    cc source add --resume
```

### Sample JSON Output

```bash
chaoscypher source list --format json
```

```json
[
  {
    "id": "if_a1b2c3d4e5f6",
    "filename": "research-paper.pdf",
    "file_type": "application/pdf",
    "file_size": 2457600,
    "status": "committed",
    "cached_quality_grade": 92,
    "cached_quality_label": "A",
    "created_at": "2026-03-08T14:22:30Z",
    "updated_at": "2026-03-08T14:25:12Z"
  },
  {
    "id": "if_f6e5d4c3b2a1",
    "filename": "quarterly-report.pdf",
    "file_type": "application/pdf",
    "file_size": 1126400,
    "status": "indexed",
    "cached_quality_grade": null,
    "cached_quality_label": null,
    "created_at": "2026-03-08T15:10:05Z",
    "updated_at": "2026-03-08T15:10:42Z"
  }
]
```

---

## Get Source Details

Display detailed information about a specific source file.

```bash
chaoscypher source get SOURCE_ID
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--database DATABASE` | `-d` | Database name (default: `default`) |

### Sample Output

```bash
chaoscypher source get if_a1b2c3d4e5f6
```

``` { .text .no-copy }
╭──────── Source File ────────╮
│ research-paper.pdf          │
│ ID: if_a1b2c3d4e5f6        │
╰─────────────────────────────╯
 Status              committed
 File Type           application/pdf
 File Size           2,457,600 bytes (2.4 MB)
 File Path           /data/databases/default/staging/research-paper.pdf
 Created             2026-03-08 14:22:30
 Updated             2026-03-08 14:25:12
 Extraction Depth    full
 Domain              scientific (auto-detected)
 Extract Entities    Yes

Quality Score:
 Grade               92/100 (A)
 Entity Quality      88/100
 Relationship Quality 94/100
 Topology Score      91/100

Extraction Results:
  Entities: 84
  Relationships: 112

  Entities:
    • Neural Network (Concept)
    • Gradient Descent (Algorithm)
    • Backpropagation (Algorithm)
    • ImageNet (Dataset)
    • ResNet (Model)
    ... and 79 more

Indexing Stats:
  Chunks: 42
  Tokens: 18,340
```

---

## Extract (standalone)

Run entity/relationship extraction on a source that has already been indexed
(for example, one added with `--index-only`). The source must be in `indexed`
status, or in `committed` status when `--force` is given.

```bash
chaoscypher source extract SOURCE_ID
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--depth {quick,full}` | | Extraction depth: `quick` (fast sample) or `full` (all chunks, default). |
| `--domain DOMAIN` | | Force extraction domain (default: auto-detect from content). |
| `--filtering-mode MODE` | | Extraction filtering mode preset (overrides the domain default). |
| `--force` | | Re-extract a committed source. Deletes existing graph nodes and edges before re-running extraction. |
| `--yes` | `-y` | Skip the destructive-action confirmation prompt (use with `--force`). |
| `--database DATABASE` | `-d` | Target database (default: `default`). |
| `--quiet` | `-q` | Minimal output. |

```bash
# Re-extract a committed source (deletes graph artifacts), no prompt
chaoscypher source extract if_abc123 --force --yes
```

---

## Confirm a Parked Source

When a source is added with auto-detection and the [domain confirmation
gate](#extraction-domains) is active, it is parked at `awaiting_confirmation`
with a stored detection proposal. Confirm the recommended domain (or override
it), flip the source back to `indexed`, and run extraction:

```bash
# Accept the recommended domain
chaoscypher source confirm if_abc123

# Override the recommended domain
chaoscypher source confirm if_abc123 --domain legal

# Confirm every parked source non-interactively
chaoscypher source confirm --all --yes
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--all` | | Confirm every parked source. |
| `--domain DOMAIN` | | Override the detected domain (default: accept the recommendation). |
| `--depth {quick,full}` | | Extraction depth (default: `full`). |
| `--filtering-mode MODE` | | Extraction filtering mode preset (overrides the domain default). |
| `--yes` | `-y` | Accept the recommended domain without prompting (required when not a TTY). |
| `--database DATABASE` | `-d` | Target database (default: `default`). |
| `--quiet` | `-q` | Minimal output. |

List parked sources with `chaoscypher source list --awaiting`.

---

## Delete a Source

Remove a source file record and its associated data.

```bash
chaoscypher source delete FILE_ID
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--force` | `-f` | Skip confirmation prompt |
| `--database DATABASE` | `-d` | Database name (default: `default`) |

### Sample Output

```bash
chaoscypher source delete if_a1b2c3d4e5f6
```

``` { .text .no-copy }
File to delete:
  ID: if_a1b2c3d4e5f6
  Filename: research-paper.pdf
  Status: committed

Are you sure you want to delete this file? [y/N]: y
✓ File deleted successfully
```

Skip the confirmation prompt with `--force`:

```bash
chaoscypher source delete if_a1b2c3d4e5f6 --force
```

``` { .text .no-copy }
File to delete:
  ID: if_a1b2c3d4e5f6
  Filename: research-paper.pdf
  Status: committed
✓ File deleted successfully
```

---

## Search

Search across all indexed sources using keyword, semantic, or hybrid search.

```bash
chaoscypher source search "machine learning algorithms"
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--mode MODE` | `-m` | Search mode: `hybrid` (default), `keyword` (fast, no LLM), `semantic` (AI only) |
| `--limit N` | `-l` | Maximum results (default: `10`) |
| `--format FORMAT` | `-f` | Output format: `table` (default), `json` |
| `--database DATABASE` | `-d` | Database name (default: `default`) |

### Search Modes

| Mode | Description | LLM Required |
|------|-------------|:------------:|
| `hybrid` | Semantic + keyword fallback (most robust) | Yes |
| `keyword` | Fast full-text search | No |
| `semantic` | Pure vector similarity | Yes |

If LLM is not configured and `hybrid` or `semantic` mode is requested, the command automatically falls back to `keyword` mode.

### Sample Table Output

```bash
chaoscypher source search "neural network architecture"
```

``` { .text .no-copy }
Searching: neural network architecture (hybrid mode)
          Search Results (5 found)
 Score   Label                                       Template      ID
 0.923   Convolutional Neural Network                Concept       n_a1b2c3d4e5f6g7...
 0.891   Transformer Architecture                    Concept       n_h8i9j0k1l2m3n4...
 0.845   Deep learning uses multiple layer...        chunk #3      chunk:abc123-def4...
 0.812   Recurrent Neural Network                    Model         n_o5p6q7r8s9t0u1...
 0.778   Attention mechanism enables the m...        chunk #7      chunk:567890-abcd...
```

### Sample JSON Output

```bash
chaoscypher source search "neural networks" --format json
```

```json
[
  {
    "id": "n_a1b2c3d4e5f6g7h8",
    "label": "Convolutional Neural Network",
    "template_id": "Concept",
    "score": 0.923,
    "properties": {
      "description": "A class of deep neural networks commonly applied to visual imagery."
    },
    "result_type": "node"
  },
  {
    "id": "chunk:abc123-def456",
    "label": "Deep learning uses multiple layers of nonlinear processing units for feature...",
    "template_id": "chunk #3",
    "score": 0.845,
    "properties": {
      "source_id": "if_a1b2c3d4e5f6"
    },
    "result_type": "chunk"
  }
]
```

---

## Rebuild Search Indexes

Rebuild search indexes with auto-detection of whether embeddings need regeneration. If the embedding model or dimensions have changed since the indexes were last built, all embeddings are regenerated. Otherwise, only the FTS full-text index is rebuilt (fast).

```bash
chaoscypher source rebuild-search
```

### Options

| Option | Short | Description |
|--------|-------|-------------|
| `--database DATABASE` | `-d` | Database name (default: `default`) |
| `--json` | | Output as JSON |

### Sample Output

```bash
chaoscypher source rebuild-search
```

``` { .text .no-copy }
Rebuilding search indexes...

  FTS index rebuilt: 3400 chunks, 1500 nodes
  Vector index: embeddings current (no regeneration needed)

Done in 2.1s
```

When embeddings need regeneration:

``` { .text .no-copy }
Rebuilding search indexes...

  FTS index rebuilt: 3400 chunks, 1500 nodes
  Embedding model changed: regenerating embeddings...
  Re-embedding ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100%

Done in 84.3s (4900 embeddings regenerated)
```

---

## Quality

Evaluate extraction quality for sources. See [Quality](quality.md) for the full reference.
