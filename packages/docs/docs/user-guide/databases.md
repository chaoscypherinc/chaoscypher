---
id: databases
title: Databases
description: Manage multiple isolated Chaos Cypher databases — each is a self-contained workspace with its own knowledge graph, sources, chat history, search indices, and config.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Databases

Chaos Cypher supports multiple isolated databases, each containing its own sources, knowledge graph, chat history, search indexes, and configuration.

## Multi-Database Architecture

Each database is a self-contained directory with:

```
databases/{name}/
└── app.db          # All data: sources, chats, workflows, graph, search indices (SQLite default)
```

:::tip

The storage backend is pluggable via Core's hexagonal architecture: any class implementing the storage protocols in `chaoscypher_core.ports` can replace the default SQLite adapter.

:::

Databases are completely isolated — switching databases loads an entirely different set of data.

## Managing Databases

### List Databases

View all available databases with their size and last modified date:

<Tabs>
<TabItem value="web-ui" label="Web UI">


Go to **Settings** → **Databases** to see all databases with their size, last modified date, and active status.

![Settings page with database selector showing size](/img/screenshots/settings-database-selector.png)

</TabItem>
<TabItem value="cli" label="CLI">


```bash
chaoscypher db list
```

``` { .text .no-copy }
                  Databases
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┓
┃ Name           ┃    Size ┃ Modified            ┃ Status  ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━┩
│ default        │ 2.3 MB  │ 2026-03-09 14:22:01 │ current │
│ research-2026  │ 8.7 MB  │ 2026-03-08 10:15:43 │         │
└────────────────┴─────────┴─────────────────────┴─────────┘
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl http://localhost:8080/api/v1/databases
```

</TabItem>
</Tabs>


### Current Database

Check which database is active:

<Tabs>
<TabItem value="web-ui" label="Web UI">


The current database name is displayed in the header/navigation bar. You can also quickly switch databases from the sidebar dropdown without navigating to the Settings page.

</TabItem>
<TabItem value="cli" label="CLI">


```bash
chaoscypher db current
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl http://localhost:8080/api/v1/databases/current
```

</TabItem>
</Tabs>


### Create a Database

<Tabs>
<TabItem value="web-ui" label="Web UI">


1. Go to **Settings** → **Databases**
2. Click **Create Database**
3. Enter a name (alphanumeric, hyphens, and underscores allowed)

![Database selector with create new database option](/img/screenshots/settings-database-selector.png)

</TabItem>
<TabItem value="cli" label="CLI">


```bash
chaoscypher db create research-project
```

``` { .text .no-copy }
Creating database 'research-project'...

Created database 'research-project'
  Location: ~/.local/share/chaoscypher/databases/research-project

Switch to it with: chaoscypher db switch research-project
```

</TabItem>
<TabItem value="python" label="Python">


```python
from chaoscypher_core import Engine

# Creating an Engine with a new database path initializes it automatically
with Engine("./data/databases/research-project") as engine:
    stats = engine.get_stats()
    print(f"New database: {stats.nodes} nodes")
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X POST http://localhost:8080/api/v1/databases \
  -H "Content-Type: application/json" \
  -d '{"name": "research-project"}'
```

</TabItem>
</Tabs>


New databases are automatically initialized with the required directory structure and a fresh database.

### Switch Database

<Tabs>
<TabItem value="web-ui" label="Web UI">


Click on a database name in **Settings** → **Databases** to switch to it. The UI refreshes to load the new context.

</TabItem>
<TabItem value="cli" label="CLI">


```bash
chaoscypher db switch research-project
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X PATCH http://localhost:8080/api/v1/databases/current \
  -H "Content-Type: application/json" \
  -d '{"name": "research-project"}'
```

</TabItem>
</Tabs>


:::note

After switching databases, the web UI refreshes to load the new context. All subsequent API calls operate on the new database.

:::

### Delete a Database

<Tabs>
<TabItem value="web-ui" label="Web UI">


Click the delete button next to a database in **Settings** → **Databases** and confirm the deletion.

</TabItem>
<TabItem value="cli" label="CLI">


```bash
# Delete with confirmation prompt
chaoscypher db delete research-project

# Skip confirmation
chaoscypher db delete research-project --yes
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X DELETE http://localhost:8080/api/v1/databases/research-project
```

</TabItem>
</Tabs>


:::warning

- You cannot delete the currently active database
- You cannot delete the `default` database
- Deletion is permanent and removes all data

:::

## What's Isolated

| Data | Isolated per database |
|------|-----------------------|
| Sources and document chunks | Yes |
| Knowledge graph (nodes, edges, templates) | Yes |
| Chat conversations and messages | Yes |
| Search indexes (fulltext + vector) | Yes |
| Workflows and triggers | Yes |
| Tool registry | Yes |
| Quality scores | Yes |
| Tags | Yes |

## Backup & Restore

Chaos Cypher includes built-in database backup and restore from the **Settings** > **Backup** tab:

- **Create backups** — Snapshot the current database to a timestamped backup file
- **Scheduled backups** — Configure automatic backups on a schedule
- **Restore** — Restore a database from a previous backup
- **Download** — Download backup files for offline storage
- **Manage** — View, download, or delete existing backups

Backups capture the full database state including sources, knowledge graph, chat history, and search indexes.

## Use Cases

- **Project separation** — Keep different research projects in separate databases
- **Domain isolation** — Separate databases for different knowledge domains
- **Testing** — Create a test database without affecting production data
- **Snapshots** — Create a new database, import a CCX package, and explore without modifying the original
