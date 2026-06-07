---
title: Publishing Packages
description: Export your Chaos Cypher knowledge graph as a CCX package and publish it to Lexicon Hub so others can import your entities, relationships, and workflows.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';
import PreviewBanner from "@site/src/components/PreviewBanner";

<PreviewBanner service="Lexicon Hub" />

# Publishing Packages

Share your extracted knowledge with the community by publishing CCX packages to the Lexicon Hub.

## Prerequisites

1. **Authenticate** — Run `chaoscypher lexicon login` to connect to the Hub (see [Authentication](authentication.md))
2. **Export** — Create a CCX package from your knowledge graph

## Creating a Package

Before publishing, export your knowledge graph as a CCX package:

<Tabs>
<TabItem value="cli" label="CLI">


```bash
# Export everything
chaoscypher graph package export --output my-knowledge.ccx

# Export only templates (share your schema)
chaoscypher graph package export --output my-templates.ccx \
  --no-knowledge --no-workflows

# Export only knowledge (share your data)
chaoscypher graph package export --output my-data.ccx \
  --no-templates --no-workflows

# Export from a specific database
chaoscypher graph package export -d research -o research-export.ccx
```

</TabItem>
<TabItem value="api" label="API">


```bash
# Full export (returns task_id for async processing)
curl -X POST "http://localhost:8080/api/v1/exports?include_templates=true&include_knowledge=true"
```

</TabItem>
</Tabs>


### Inspecting Before Publishing

Review what's in your package before uploading:

```bash
chaoscypher lexicon info ./my-knowledge.ccx --local
```

``` { .text .no-copy }
Package: my-knowledge.ccx

╭───────────── Package Info ─────────────╮
│ my-knowledge.ccx                       │
│ Compressed: 245.3 KB                   │
│ Uncompressed: 1.2 MB                   │
╰────────────────────────────────────────╯

Files: (12 total)
  - manifest.json
  - graph/entities.jsonld
  - graph/relationships.jsonld
  - templates/person.json
  - templates/organization.json
  ... and 7 more

Archive size: 245.3 KB
```

## Publishing

<Tabs>
<TabItem value="cli" label="CLI">


```bash
# Publish as public (default)
chaoscypher push my-knowledge.ccx

# Publish with a release message
chaoscypher push my-knowledge.ccx --message "Initial release — medical ontology v1"

# Publish as private
chaoscypher push my-knowledge.ccx --private

# Skip confirmation prompt
chaoscypher push my-knowledge.ccx --force
```

``` { .text .no-copy }
Pushing package: my-knowledge
  File: my-knowledge.ccx
  Size: 245.3 KB
  Visibility: Public

Proceed with upload? [Y/n]: y

Uploading my-knowledge... ━━━━━━━━━━━━━━━━━━━━━ 245.3 KB

✓ Published my-knowledge v1.0.0
  URL: https://lexicon.example.com/packages/my-knowledge

Share with:
  chaoscypher pull my-knowledge
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/upload?public=true&message=Initial+release" \
  -F "file=@my-knowledge.ccx"
```

</TabItem>
</Tabs>


## Visibility

| Visibility | Who can see it | Who can download it |
|------------|---------------|---------------------|
| **Public** | Everyone | Everyone |
| **Private** | Only you | Only you |

Public packages appear in search results and can be downloaded by anyone. Private packages are only visible and downloadable by the authenticated owner.

## Versioning

Each upload creates a new version of the package. The Lexicon Hub tracks version history, so users can pull specific versions:

```bash
# Users can pull any published version
chaoscypher pull your-username/package-name --version 1.2.0
```

## Best Practices

- **Write clear descriptions** — Help others understand what your package contains and what domain it covers
- **Use tags** — Add relevant tags so your package is discoverable in filtered searches
- **Include templates** — Sharing templates alongside knowledge helps others understand your schema
- **Version meaningfully** — Publish new versions when you add significant new data or fix extraction issues
- **Start public** — Public packages grow the community; use private only for proprietary data
