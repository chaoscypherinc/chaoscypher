---
title: Edition API
description: Query the installed Chaos Cypher edition, license validity, and which enterprise or preview features are enabled on this deployment.
---

# Edition API

Query the installed edition, license status, and available features.

**Base path:** `/api/v1/edition`

---

## Get Edition

```
GET /api/v1/edition
```

Returns the installed edition (community or enterprise), license information, and the list of available features.

#### Response

**Status:** `200 OK`

```json
{
  "edition": "community",
  "license": null,
  "features": [
    "auth", "backup", "chats", "counts", "databases", "diagnostics",
    "edges", "export", "graph", "health", "lexicon", "llm", "logs",
    "mcp", "nodes", "quality", "queue", "search", "settings",
    "sources", "templates", "tools", "triggers", "workflows"
  ]
}
```

When an enterprise license is installed:

```json
{
  "edition": "enterprise",
  "license": {
    "type": "enterprise",
    "holder": "Acme Corp",
    "expires": "2027-01-01"
  },
  "features": ["...community features...", "...enterprise features..."]
}
```

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/edition
```

---

## Response Models Reference

### EditionResponse

| Field | Type | Description |
|-------|------|-------------|
| `edition` | string | Edition identifier (`community` or `enterprise`) |
| `license` | LicenseInfo or null | License details, `null` for community edition |
| `features` | string[] | List of available feature identifiers |

### LicenseInfo

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | License type |
| `holder` | string | License holder name |
| `expires` | string or null | Expiration date (ISO format), `null` for perpetual licenses |
