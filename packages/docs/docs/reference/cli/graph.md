---
title: Graph Commands
description: Manage knowledge graph entities from the CLI — nodes, edges, templates, workflows, and packages using chaoscypher graph subcommands.
---

# Graph Commands

The `graph` command group manages knowledge graph entities: nodes, links (edges), templates, workflows, and packages.

```bash
chaoscypher graph --help
```

---

## Nodes

Nodes are the fundamental units of knowledge in Chaos Cypher. Each node belongs to a template, has a label, and carries a set of typed properties.

### List Nodes

```bash
chaoscypher graph node list [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--template` | `-t` | | Filter by template ID |
| `--format` | `-f` | `table` | Output format (`table`, `json`, `yaml`) |
| `--page` | `-p` | `1` | Page number |
| `--limit` | `-l` | `50` | Items per page |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# List all nodes (table view)
chaoscypher graph node list

# Filter by template
chaoscypher graph node list --template Person

# JSON output for scripting
chaoscypher graph node list --format json

# Paginate through results
chaoscypher graph node list --page 2 --limit 100
```

**Sample output:**

```
                          Nodes
┌──────────────────┬───────────────┬──────────┬────────────┬────────────┐
│ ID               │ Label         │ Template │ Properties │ Created    │
├──────────────────┼───────────────┼──────────┼────────────┼────────────┤
│ nd_a1b2c3d4e5f6  │ John Doe      │ Person   │          3 │ 2026-01-15 │
│ nd_f6e5d4c3b2a1  │ Acme Corp     │ Company  │          5 │ 2026-01-16 │
│ nd_1234abcd5678  │ Project Alpha │ Project  │          2 │ 2026-02-01 │
└──────────────────┴───────────────┴──────────┴────────────┴────────────┘

Page 1/1 • Total: 3 node(s)
```

### Create a Node

```bash
chaoscypher graph node create [OPTIONS]
```

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--template` | `-t` | Yes | | Template ID to use for the node |
| `--label` | `-l` | Yes | | Label/name of the node |
| `--property` | `-p` | No | | Property in `key=value` format (repeatable) |
| `--json-props` | `-j` | No | | Properties as a JSON string |
| `--database` | `-d` | No | `default` | Database name |
| `--interactive` | `-i` | No | | Use interactive wizard |

**Examples:**

```bash
# Create with required flags
chaoscypher graph node create -t Person -l "John Doe"

# Create with properties
chaoscypher graph node create -t Person -l "Jane Smith" \
  -p role=CEO -p department=Executive

# Create with JSON properties
chaoscypher graph node create -t Event -l "Quarterly Meeting" \
  -j '{"date": "2026-01-15", "location": "Room 401"}'

# Combine -p and -j (values are merged)
chaoscypher graph node create -t Person -l "Bob" \
  -p role=Engineer -j '{"team": "Platform"}'

# Interactive wizard (prompts for template, label, and each property)
chaoscypher graph node create --interactive
```

**Sample output:**

```
Creating node...
✓ Node created successfully!
  ID: nd_a1b2c3d4e5f6
  Template: Person
  Label: Jane Smith
  Properties:
    • role: CEO
    • department: Executive
```

### Get Node Details

```bash
chaoscypher graph node get NODE_ID [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--format` | `-f` | `table` | Output format (`table`, `json`, `yaml`) |
| `--include-links` | `-l` | `false` | Include connected links |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# View node details
chaoscypher graph node get nd_a1b2c3d4e5f6

# Include connected links
chaoscypher graph node get nd_a1b2c3d4e5f6 --include-links

# Export as JSON
chaoscypher graph node get nd_a1b2c3d4e5f6 --format json
```

**Sample output:**

```
            Node: nd_a1b2c3d4e5f6
┌────────────┬───────────────────────────────┐
│ Field      │ Value                         │
├────────────┼───────────────────────────────┤
│ ID         │ nd_a1b2c3d4e5f6               │
│ Label      │ John Doe                      │
│ Template   │ Person                        │
│ Created    │ 2026-01-15 10:30:00           │
│ Updated    │ 2026-02-01 14:22:00           │
│ Properties │   role: Engineer              │
│            │   department: Platform        │
│            │   email: john@example.com     │
│ Position   │ x=120, y=340                  │
│ Embedding  │ [1024 dimensions]             │
└────────────┴───────────────────────────────┘

           Connected Links
┌───────────────┬──────────────┬───────────────┬──────────────┐
│ ID            │ Direction    │ Related Node  │ Relationship │
├───────────────┼──────────────┼───────────────┼──────────────┤
│ eg_abc123def4 │ → (outgoing) │ nd_f6e5d4c3b2 │ works_at     │
│ eg_789xyz012a │ ← (incoming) │ nd_1234abcd56 │ manages      │
└───────────────┴──────────────┴───────────────┴──────────────┘
```

### Update a Node

```bash
chaoscypher graph node update NODE_ID [OPTIONS]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--label` | `-l` | New label for the node |
| `--set` | `-s` | Set a property (`key=value`, repeatable) |
| `--unset` | `-u` | Remove a property by key (repeatable) |
| `--database` | `-d` | Database name (default: `default`) |

**Examples:**

```bash
# Change the label
chaoscypher graph node update nd_a1b2c3d4e5f6 --label "Jonathan Doe"

# Set properties
chaoscypher graph node update nd_a1b2c3d4e5f6 \
  -s role=CTO -s department=Engineering

# Remove a property
chaoscypher graph node update nd_a1b2c3d4e5f6 -u obsolete_field

# Combine operations
chaoscypher graph node update nd_a1b2c3d4e5f6 \
  --label "Jon Doe" -s title=VP -u old_title
```

### Delete a Node

```bash
chaoscypher graph node delete NODE_ID [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--force` | `-f` | `false` | Skip confirmation prompt |
| `--cascade` | `-c` | `false` | Also delete connected links |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# Delete with confirmation prompt
chaoscypher graph node delete nd_a1b2c3d4e5f6

# Skip confirmation
chaoscypher graph node delete nd_a1b2c3d4e5f6 --force

# Delete node and all connected links
chaoscypher graph node delete nd_a1b2c3d4e5f6 --cascade

# Force delete with cascade
chaoscypher graph node delete nd_a1b2c3d4e5f6 -f -c
```

:::warning

Without `--cascade`, connected links become orphaned (they reference a node that no longer exists). Use `--cascade` to clean up all related links.

:::

---

## Links (Edges)

Links are directed relationships between nodes. Each link connects a source node to a target node and carries a relationship type.

### List Links

```bash
chaoscypher graph link list [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--source` | `-s` | | Filter by source node ID |
| `--format` | `-f` | `table` | Output format (`table`, `json`, `yaml`) |
| `--page` | `-p` | `1` | Page number |
| `--limit` | `-l` | `50` | Items per page |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# List all links
chaoscypher graph link list

# Filter by source node
chaoscypher graph link list --source nd_a1b2c3d4e5f6

# JSON output
chaoscypher graph link list --format json

# Paginate
chaoscypher graph link list --page 2 --limit 25
```

**Sample output:**

```
                              Links
┌───────────────┬───────────────┬───┬───────────────┬────────────┬──────────┐
│ ID            │ Source        │ → │ Target        │ Label      │ Template │
├───────────────┼───────────────┼───┼───────────────┼────────────┼──────────┤
│ eg_abc123de…  │ nd_a1b2c3d…   │ → │ nd_f6e5d4c…   │ works_at   │ WorksAt  │
│ eg_789xyz01…  │ nd_1234abc…   │ → │ nd_a1b2c3d…   │ manages    │ Manages  │
│ eg_def456gh…  │ nd_a1b2c3d…   │ → │ nd_9876fed…   │ knows      │ (none)   │
└───────────────┴───────────────┴───┴───────────────┴────────────┴──────────┘

Page 1/1 • Total: 3 link(s)
```

### Create a Link

```bash
chaoscypher graph link create SOURCE_NODE TARGET_NODE [OPTIONS]
```

| Argument | Description |
|----------|-------------|
| `SOURCE_NODE` | Starting node ID |
| `TARGET_NODE` | Ending node ID |

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--type` | `-t` | Yes | | Relationship type/template (e.g., `works_for`, `owns`) |
| `--label` | `-l` | No | | Optional display label (defaults to the type value) |
| `--bidirectional` | `-b` | No | `false` | Create link in both directions |
| `--database` | `-d` | No | `default` | Database name |

**Examples:**

```bash
# Create a directed link
chaoscypher graph link create nd_person1 nd_company1 --type "works_for"

# Create with a custom label
chaoscypher graph link create nd_node1 nd_node2 \
  -t "influences" -l "strongly influences"

# Create bidirectional links (creates two edges)
chaoscypher graph link create nd_node1 nd_node2 \
  -t "related_to" --bidirectional
```

**Sample output:**

```
Creating link: nd_person1 → nd_company1
  Type: works_for
✓ Link created successfully!
  ID: eg_abc123def456
```

### Get Link Details

```bash
chaoscypher graph link get LINK_ID [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--format` | `-f` | `table` | Output format (`table`, `json`, `yaml`) |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
chaoscypher graph link get eg_abc123def456
chaoscypher graph link get eg_abc123def456 --format json
```

**Sample output:**

```
╭──────── Link ────────╮
│ works_for            │
│ ID: eg_abc123def456  │
╰──────────────────────╯
Source Node          nd_a1b2c3d4e5f6
Target Node          nd_f6e5d4c3b2a1
Relationship Type    works_for
Template             WorksAt
Weight               1.0
Created              2026-01-15 10:30:00

Properties:
  start_date: 2025-06-01
  role: Senior Engineer
```

### Update a Link

```bash
chaoscypher graph link update LINK_ID [OPTIONS]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--label` | `-l` | New label for the link |
| `--set` | `-s` | Set a property (`key=value`, repeatable) |
| `--unset` | `-u` | Remove a property by key (repeatable) |
| `--database` | `-d` | Database name (default: `default`) |

**Examples:**

```bash
# Change the label
chaoscypher graph link update eg_abc123def456 --label "Works For"

# Set a property
chaoscypher graph link update eg_abc123def456 -s context="Updated context"

# Remove a property
chaoscypher graph link update eg_abc123def456 -u obsolete_field
```

### Delete a Link

```bash
chaoscypher graph link delete [LINK_ID] [OPTIONS]
```

Links can be deleted by ID or by specifying the source and target node pair.

| Argument | Required | Description |
|----------|----------|-------------|
| `LINK_ID` | No | Link ID to delete (alternative to `--source`/`--target`) |

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--source` | `-s` | | Source node ID (used with `--target`) |
| `--target` | `-t` | | Target node ID (used with `--source`) |
| `--type` | | | Link type to filter by (used with `--source`/`--target`) |
| `--force` | `-f` | `false` | Skip confirmation prompt |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# Delete by link ID
chaoscypher graph link delete eg_abc123def456

# Delete by source/target pair (deletes all links between them)
chaoscypher graph link delete --source nd_person1 --target nd_company1

# Delete a specific type between two nodes
chaoscypher graph link delete -s nd_node1 -t nd_node2 --type "works_for"

# Force delete without confirmation
chaoscypher graph link delete eg_abc123def456 --force
```

:::note

When using `--source` and `--target`, all matching links between those nodes are deleted. Add `--type` to narrow the selection.

:::

---

## Templates

Templates define the schema for nodes and edges -- the property names, types, and constraints that instances must follow.

### List Templates

```bash
chaoscypher graph template list [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--format` | `-f` | `table` | Output format (`table`, `json`, `yaml`) |
| `--verbose` | `-v` | `false` | Show properties and descriptions |
| `--type` | `-t` | | Filter by template type (`node` or `edge`) |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# List all templates
chaoscypher graph template list

# Show detailed view with properties
chaoscypher graph template list --verbose

# Filter to node templates only
chaoscypher graph template list --type node

# JSON output
chaoscypher graph template list --format json
```

**Sample output:**

```
                     Templates
┌──────────────────┬──────────────┬──────┐
│ ID               │ Name         │ Type │
├──────────────────┼──────────────┼──────┤
│ tmpl_a1b2c3d4e5  │ Person       │ node │
│ tmpl_f6e5d4c3b2  │ Company      │ node │
│ tmpl_1234abcd56  │ Project      │ node │
│ tmpl_9876fedc54  │ WorksAt      │ edge │
│ tmpl_abcd1234ef  │ Manages      │ edge │
└──────────────────┴──────────────┴──────┘

Total: 5 template(s)
```

**Verbose output** (`--verbose`):

```
                              Templates
┌──────────────────┬─────────┬──────┬─────────────────────────────┬──────────────────┐
│ ID               │ Name    │ Type │ Properties                  │ Description      │
├──────────────────┼─────────┼──────┼─────────────────────────────┼──────────────────┤
│ tmpl_a1b2c3d4e5  │ Person  │ node │ name:string, role:string,   │ A person entity  │
│                  │         │      │ email:email (+2 more)       │                  │
│ tmpl_f6e5d4c3b2  │ Company │ node │ name:string, industry:str…  │ A company or or… │
└──────────────────┴─────────┴──────┴─────────────────────────────┴──────────────────┘

Total: 2 template(s)
```

### Create a Template

```bash
chaoscypher graph template create [OPTIONS]
```

| Option | Short | Required | Default | Description |
|--------|-------|----------|---------|-------------|
| `--name` | `-n` | Yes* | | Template name |
| `--type` | `-t` | No | `node` | Template type (`node` or `edge`) |
| `--description` | | No | | Template description |
| `--property` | `-p` | No | | Property definition in `name:type[:required]` format (repeatable) |
| `--database` | `-d` | No | `default` | Database name |
| `--interactive` | `-i` | No | | Use interactive wizard |

*Required unless using `--interactive`.

**Property format:** `name:type[:required]`

**Examples:**

```bash
# Create a node template with properties
chaoscypher graph template create -n Person \
  -p name:string:required \
  -p age:integer \
  -p email:email:required

# Create an edge template
chaoscypher graph template create -n WorksAt -t edge \
  -p start_date:date \
  -p role:string

# Create with description
chaoscypher graph template create -n Project \
  --description "A project or initiative" \
  -p name:string:required \
  -p status:string \
  -p deadline:date

# Interactive wizard (prompts for everything)
chaoscypher graph template create --interactive
```

**Sample output:**

```
Creating template: Person
✓ Template created successfully!
  ID: tmpl_a1b2c3d4e5
  Name: Person
  Type: node
  Properties:
    • name: STRING (required)
    • age: INTEGER
    • email: EMAIL (required)
```

#### Property Type Reference

Templates use typed properties. The following types are available:

| Type | Description | Example Values |
|------|-------------|----------------|
| `STRING` | Short text (single line) | `"John Doe"`, `"Active"` |
| `TEXT` | Long text (multi-line) | `"A detailed description..."` |
| `INTEGER` | Whole number | `42`, `-7`, `0` |
| `FLOAT` | Decimal number | `3.14`, `-0.5` |
| `BOOLEAN` | True/false | `true`, `false` |
| `DATE` | Calendar date | `"2026-01-15"` |
| `DATETIME` | Date and time | `"2026-01-15T10:30:00"` |
| `URL` | Web address | `"https://example.com"` |
| `EMAIL` | Email address | `"user@example.com"` |
| `JSON` | Structured JSON data | `'{"key": "value"}'` |

Types are case-insensitive in the CLI (e.g., `string`, `String`, `STRING` all work).

### Get Template Details

```bash
chaoscypher graph template get TEMPLATE_ID [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--format` | `-f` | `table` | Output format (`table`, `json`, `yaml`) |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
chaoscypher graph template get Person
chaoscypher graph template get tmpl_a1b2c3d4e5 --format json
```

**Sample output:**

```
╭─────────── Template ───────────╮
│ Person  node                   │
│ ID: tmpl_a1b2c3d4e5            │
╰────────────────────────────────╯
Description          A person entity
Type                 node
Created              2026-01-15 10:30:00
Updated              2026-02-01 14:22:00

Properties:
Name           Type       Required   Display Name
name           STRING     yes        Name
role           STRING     no         Role
email          EMAIL      yes        Email
age            INTEGER    no         Age
bio            TEXT       no         Bio
```

### Update a Template

```bash
chaoscypher graph template update TEMPLATE_ID [OPTIONS]
```

| Option | Short | Description |
|--------|-------|-------------|
| `--name` | `-n` | New template name |
| `--description` | | New description |
| `--add-property` | `-a` | Add a property (`name:type[:required]`, repeatable) |
| `--remove-property` | `-r` | Remove a property by name (repeatable) |
| `--database` | `-d` | Database name (default: `default`) |

**Examples:**

```bash
# Rename a template
chaoscypher graph template update Person --name "Individual"

# Update description
chaoscypher graph template update Person --description "A person entity"

# Add new properties
chaoscypher graph template update Person \
  -a phone:string -a address:text

# Remove a property
chaoscypher graph template update Person -r obsolete_field

# Combine operations
chaoscypher graph template update Person \
  --name "Person v2" -a linkedin:url -r old_field
```

### Delete a Template

```bash
chaoscypher graph template delete TEMPLATE_ID [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--force` | `-f` | `false` | Skip confirmation |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
chaoscypher graph template delete Person
chaoscypher graph template delete tmpl_a1b2c3d4e5 --force
```

:::warning

Deleting a template does not delete nodes created from it, but those nodes will no longer have a valid template reference.

:::

---

## Workflows

View workflow definitions from the CLI. Workflow creation and execution is available via the web UI or API.

### List Workflows

```bash
chaoscypher graph workflow list [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--format` | `-f` | `table` | Output format (`table`, `json`, `yaml`) |
| `--verbose` | `-v` | `false` | Show step counts and descriptions |
| `--category` | `-c` | | Filter by category |
| `--active/--inactive` | | | Filter by active status |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# List all workflows
chaoscypher graph workflow list

# Verbose view with step counts
chaoscypher graph workflow list --verbose

# Filter by category
chaoscypher graph workflow list --category research

# Show only active workflows
chaoscypher graph workflow list --active
```

### Get Workflow Details

```bash
chaoscypher graph workflow get WORKFLOW_ID [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--format` | `-f` | `table` | Output format (`table`, `json`, `yaml`) |
| `--database` | `-d` | `default` | Database name |

You can pass either the workflow ID or its name.

**Examples:**

```bash
chaoscypher graph workflow get entity-extraction
chaoscypher graph workflow get wf_abc123 --format json
```

---

## Packages

Export and import knowledge graph packages in CCX (Chaos Cypher eXchange) format. Packages are portable ZIP archives containing templates, nodes, edges, and workflows in JSON-LD format.

### Export

```bash
chaoscypher graph package export [OPTIONS]
```

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--output` | `-o` | Auto-generated | Output `.ccx` file path |
| `--templates/--no-templates` | | `--templates` | Include templates |
| `--knowledge/--no-knowledge` | | `--knowledge` | Include knowledge nodes and edges |
| `--workflows/--no-workflows` | | `--workflows` | Include workflows |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# Export everything (auto-generated filename)
chaoscypher graph package export

# Export to a specific file
chaoscypher graph package export --output my-backup.ccx

# Export only templates and knowledge (no workflows)
chaoscypher graph package export --no-workflows

# Export from a specific database
chaoscypher graph package export -d my-project -o project.ccx
```

**Sample output:**

```
Exporting to: my-backup.ccx
         Export Summary
┌──────────────────┬──────────────┐
│ Item             │        Value │
├──────────────────┼──────────────┤
│ Output file      │ my-backup.ccx│
│ File size        │ 124.5 KB     │
│ Database         │ default      │
│ Nodes in DB      │ 47           │
│ Edges in DB      │ 83           │
│ Templates in DB  │ 12           │
└──────────────────┴──────────────┘

✓ Export complete: my-backup.ccx
```

### Load (Import)

```bash
chaoscypher graph package load PACKAGE [OPTIONS]
```

| Argument | Description |
|----------|-------------|
| `PACKAGE` | Path to the `.ccx` file to import (must exist) |

| Option | Short | Default | Description |
|--------|-------|---------|-------------|
| `--merge/--replace` | | `--merge` | Merge with existing data (skip duplicates) or replace |
| `--templates/--no-templates` | | `--templates` | Import templates |
| `--knowledge/--no-knowledge` | | `--knowledge` | Import knowledge nodes and edges |
| `--workflows/--no-workflows` | | `--workflows` | Import workflows |
| `--database` | `-d` | `default` | Database name |

**Examples:**

```bash
# Import a package (merge mode — skip existing templates)
chaoscypher graph package load my-knowledge.ccx

# Replace mode (overwrite existing data)
chaoscypher graph package load backup.ccx --replace

# Import only knowledge, skip templates
chaoscypher graph package load export.ccx --no-templates

# Import into a specific database
chaoscypher graph package load data.ccx -d my-project
```

**Sample output:**

```
Importing: my-knowledge.ccx
Mode: Merge (skip existing templates)
         Import Results
┌─────────────────────────┬───────┐
│ Category                │ Count │
├─────────────────────────┼───────┤
│ Templates imported      │     8 │
│ Templates skipped       │     2 │
│ Nodes imported          │    47 │
│ Edges imported          │    83 │
│ Workflows imported      │     2 │
│ Workflow edges imported │     6 │
└─────────────────────────┴───────┘
✓ Checksums verified
✓ Imported 164 items
```

---

## Global Options

All graph subcommands support the `--database` (`-d`) option to target a specific database. The default database is `default`.

```bash
# All commands work with any database
chaoscypher graph node list -d my-project
chaoscypher graph template list -d research-db
chaoscypher graph package export -d production -o prod-backup.ccx
```

Most commands also support `--format` (`-f`) for machine-readable output:

| Format | Description |
|--------|-------------|
| `table` | Human-readable Rich tables (default) |
| `json` | JSON output for scripting and piping |
| `yaml` | YAML output (requires PyYAML) |
