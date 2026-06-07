---
title: Discovering Packages
description: Search and download knowledge packages from the Lexicon Hub community via the web UI, CLI, or REST API — no account required for public packages.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';
import PreviewBanner from "@site/src/components/PreviewBanner";

<PreviewBanner service="Lexicon Hub" />

# Discovering Packages

Find and download knowledge packages from the Lexicon Hub community.

## Searching

<Tabs>
<TabItem value="web-ui" label="Web UI">


1. Navigate to **Settings** → **Lexicon Hub**
2. Enter a search query — the search covers package names, descriptions, and tags
3. Use filters to narrow results by type, author, or popularity

![Lexicon Hub search interface with package results](/img/screenshots/lexicon-hub.png)

</TabItem>
<TabItem value="cli" label="CLI">


```bash
# Search by keyword
chaoscypher lexicon search "machine learning"

# Filter by author
chaoscypher lexicon search "nlp" --author john

# Filter by tag
chaoscypher lexicon search "research" --tag biomedical

# Sort by downloads
chaoscypher lexicon search "ontology" --sort downloads

# Limit results
chaoscypher lexicon search "science" --limit 5
```

</TabItem>
<TabItem value="api" label="API">


```bash
# Basic search
curl "http://localhost:8080/api/v1/lexicon/search?query=medical"

# With filters
curl "http://localhost:8080/api/v1/lexicon/search?query=finance&sort_by=stars&package_type=KNOWLEDGE"

# Paginated
curl "http://localhost:8080/api/v1/lexicon/search?query=science&page=2&limit=10"
```

</TabItem>
</Tabs>


### Sort Options

| Sort | Description |
|------|-------------|
| `relevance` | Best match for your query (default for keyword searches) |
| `downloads` | Most downloaded first |
| `stars` | Most starred first (API only) |
| `newest` | Most recently created (API only) |
| `updated` | Most recently updated |
| `name` | Alphabetical |

The CLI `--sort` flag accepts `relevance`, `downloads`, `updated`, and `name`. `stars` and `newest` are available only via the REST API's `sort_by` parameter.

### Package Types

Filter by what the package contains:

| Type | Description |
|------|-------------|
| `FULL` | Complete knowledge graph (templates + entities + relationships) |
| `TEMPLATES` | Schema definitions only (node and edge types) |
| `KNOWLEDGE` | Graph data only (entities and relationships) |
| `WORKFLOWS` | Automation pipeline definitions |
| `MIXED` | Combination of multiple types |

## Viewing Package Details

Before downloading, inspect a package's metadata:

<Tabs>
<TabItem value="cli" label="CLI">


```bash
chaoscypher lexicon info john/medical-ontology
```

``` { .text .no-copy }
Package: john/medical-ontology

╭───────────────── Package Info ─────────────────╮
│ medical-ontology                               │
│ Version: 2.1.0                                 │
│ Owner: john                                    │
│ Comprehensive medical terminology ontology     │
╰────────────────────────────────────────────────╯

Details:
  Package Type: ontology
  Downloads: 3,200
  Stars: 48
  Versions: 5
  Created: 2025-06-15T10:30:00Z
  Updated: 2026-01-20T14:22:00Z

To install:
  chaoscypher pull john/medical-ontology
```

View a specific version:

```bash
chaoscypher lexicon info john/medical-ontology --version 1.2.0
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl "http://localhost:8080/api/v1/lexicon/r/john/medical-ontology"
```

</TabItem>
</Tabs>


## Downloading Packages

<Tabs>
<TabItem value="cli" label="CLI">


```bash
# Download latest version
chaoscypher pull john/medical-ontology

# Download specific version
chaoscypher pull john/medical-ontology --version 1.2.0

# Download and extract
chaoscypher pull john/medical-ontology --extract

# Download to a specific directory
chaoscypher pull john/medical-ontology --output ./packages/
```

After downloading, import the package into your database:

```bash
chaoscypher graph package load john-medical-ontology.ccx
```

</TabItem>
<TabItem value="api" label="API">


Packages are downloaded through the Lexicon Hub URL directly. Use the search or info endpoints to get the package metadata, then download via the Hub's download URL.

</TabItem>
</Tabs>


## Managing Installed Packages

### List Installed

<Tabs>
<TabItem value="cli" label="CLI">


```bash
# List all installed packages
chaoscypher lexicon list

# JSON output for scripting
chaoscypher lexicon list --format json

# Simple format (names only)
chaoscypher lexicon list --format simple

# Show all cached versions
chaoscypher lexicon list --all
```

</TabItem>
</Tabs>


### Remove a Package

<Tabs>
<TabItem value="cli" label="CLI">


```bash
# Remove a package (with confirmation)
chaoscypher lexicon remove john/medical-ontology

# Remove a specific version
chaoscypher lexicon remove john/medical-ontology --version 1.2.0

# Skip confirmation
chaoscypher lexicon remove john/medical-ontology --force
```

</TabItem>
</Tabs>


:::tip

Removing a package from your local cache does not affect data already imported into your databases. It only removes the cached `.ccx` file.

:::
