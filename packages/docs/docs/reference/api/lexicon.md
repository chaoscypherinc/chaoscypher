---
title: Lexicon Hub API
description: REST API for the Lexicon Hub package registry — OAuth 2.0 device auth, package search, publish, and download endpoints at /api/v1/lexicon.
---

# Lexicon Hub

Package registry for sharing and discovering Chaos Cypher knowledge packages. Supports device authorization (OAuth 2.0 RFC 8628), username/password login, and direct token authentication for CI/automation.

**Base path:** `/api/v1/lexicon`

---

## Authentication

### Initiate Device Authorization

```
POST /api/v1/lexicon/auth/device
```

Starts an OAuth 2.0 Device Authorization Grant flow (RFC 8628). Returns a user code and verification URL for the user to complete authentication in a browser.

#### Request Body

| Field         | Type   | Required | Default          | Description                |
|---------------|--------|----------|------------------|----------------------------|
| `lexicon_url` | string | No       | `LEXICON_URL` env var or `https://lexicon.chaoscypher.com` | Lexicon server URL |
| `client_id`   | string | No       | `chaoscypher`    | OAuth client identifier    |
| `scope`       | string | No       | `read write`     | Requested OAuth scopes     |

#### Example

```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/auth/device" \
  -H "Content-Type: application/json" \
  -d '{
    "lexicon_url": "https://lexicon.chaoscypher.com",
    "client_id": "chaoscypher",
    "scope": "read write"
  }'
```

#### Response

`200 OK`

```json
{
  "device_code": "d1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "user_code": "ABCD-1234",
  "verification_uri": "https://lexicon.chaoscypher.com/device",
  "verification_uri_complete": "https://lexicon.chaoscypher.com/device?code=ABCD-1234",
  "expires_in": 900,
  "interval": 5
}
```

##### LexiconDeviceCodeResponse

| Field                        | Type        | Description                              |
|------------------------------|-------------|------------------------------------------|
| `device_code`                | string      | Code for polling the token endpoint      |
| `user_code`                  | string      | Code the user enters at the verification URL |
| `verification_uri`           | string      | URL where the user completes auth        |
| `verification_uri_complete`  | string/null | URL with the user code pre-filled        |
| `expires_in`                 | int         | Seconds until the codes expire (default: 900) |
| `interval`                   | int         | Minimum polling interval in seconds (default: 5) |

---

### Poll for Device Token

```
POST /api/v1/lexicon/auth/poll
```

Single non-blocking poll to check whether the user has completed browser authentication. Returns immediately with a success or pending status.

#### Request Body

| Field         | Type   | Required | Default          | Description                     |
|---------------|--------|----------|------------------|---------------------------------|
| `device_code` | string | Yes      | --               | Device code from the initial request |
| `lexicon_url` | string | No       | `LEXICON_URL` env var or `https://lexicon.chaoscypher.com` | Lexicon server URL |
| `client_id`   | string | No       | `chaoscypher`    | OAuth client identifier         |

#### Example

```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/auth/poll" \
  -H "Content-Type: application/json" \
  -d '{
    "device_code": "d1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "lexicon_url": "https://lexicon.chaoscypher.com",
    "client_id": "chaoscypher"
  }'
```

#### Response -- Pending

`200 OK`

```json
{
  "success": false,
  "username": null,
  "lexicon_url": "https://lexicon.chaoscypher.com",
  "message": "Authorization pending - user has not completed auth"
}
```

#### Response -- Success

`200 OK`

```json
{
  "success": true,
  "username": "johndoe",
  "lexicon_url": "https://lexicon.chaoscypher.com",
  "message": "Successfully authenticated"
}
```

#### Error Responses

| Status | Condition                     |
|--------|-------------------------------|
| `403`  | Access denied by user         |
| `408`  | Device code expired           |

---

### Login with Username and Password

```
POST /api/v1/lexicon/auth/login
```

Authenticates with the Lexicon using username and password credentials.

#### Request Body

| Field         | Type   | Required | Default          | Description           |
|---------------|--------|----------|------------------|-----------------------|
| `username`    | string | Yes      | --               | Lexicon username      |
| `password`    | string | Yes      | --               | Lexicon password      |
| `lexicon_url` | string | No       | `LEXICON_URL` env var or `https://lexicon.chaoscypher.com` | Lexicon server URL |

#### Example

```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "johndoe",
    "password": "s3cret"
  }'
```

#### Response

`200 OK`

```json
{
  "success": true,
  "username": "johndoe",
  "lexicon_url": "https://lexicon.chaoscypher.com",
  "message": "Successfully logged in"
}
```

##### LexiconAuthResponse

| Field         | Type        | Description                             |
|---------------|-------------|-----------------------------------------|
| `success`     | bool        | Whether the operation succeeded         |
| `username`    | string/null | Authenticated username (if applicable)  |
| `lexicon_url` | string      | Lexicon URL used for the operation      |
| `message`     | string      | Human-readable status message           |

#### Error Responses

| Status | Condition              |
|--------|------------------------|
| `401`  | Invalid credentials    |
| `503`  | Lexicon server unavailable |

---

### Set Token Directly

```
POST /api/v1/lexicon/auth/token
```

Sets a JWT access token directly, bypassing interactive login. Designed for CI/CD pipelines and automation.

#### Request Body

| Field         | Type        | Required | Default          | Description                         |
|---------------|-------------|----------|------------------|-------------------------------------|
| `token`       | string      | Yes      | --               | JWT access token                    |
| `username`    | string/null | No       | `null`           | Optional username (for display)     |
| `lexicon_url` | string      | No       | `LEXICON_URL` env var or `https://lexicon.chaoscypher.com` | Lexicon server URL |

#### Example

```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/auth/token" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "eyJhbGciOiJIUzI1NiIs...",
    "username": "ci-bot"
  }'
```

#### Response

`200 OK`

```json
{
  "success": true,
  "username": "ci-bot",
  "lexicon_url": "https://lexicon.chaoscypher.com",
  "message": "Token saved successfully"
}
```

---

### Logout

```
POST /api/v1/lexicon/auth/logout
```

Clears all stored Lexicon credentials.

#### Example

```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/auth/logout"
```

#### Response

`200 OK`

```json
{
  "success": true,
  "username": null,
  "lexicon_url": "https://lexicon.chaoscypher.com",
  "message": "Successfully logged out"
}
```

---

### Get Auth Status

```
GET /api/v1/lexicon/auth/status
```

Returns the current authentication status without making any external requests.

#### Example

```bash
curl "http://localhost:8080/api/v1/lexicon/auth/status"
```

#### Response -- Authenticated

`200 OK`

```json
{
  "authenticated": true,
  "username": "johndoe",
  "lexicon_url": "https://lexicon.chaoscypher.com",
  "token_present": true
}
```

#### Response -- Not Authenticated

`200 OK`

```json
{
  "authenticated": false,
  "username": null,
  "lexicon_url": "https://lexicon.chaoscypher.com",
  "token_present": false
}
```

##### LexiconAuthStatus

| Field           | Type        | Description                          |
|-----------------|-------------|--------------------------------------|
| `authenticated` | bool        | Whether the user is authenticated    |
| `username`      | string/null | Authenticated username (if any)      |
| `lexicon_url`   | string/null | Configured Lexicon URL               |
| `token_present` | bool        | Whether a token is currently stored  |

---

## Packages

### Search Packages

```
GET /api/v1/lexicon/search
```

Search for packages on the Lexicon registry. An empty query returns all packages.

#### Query Parameters

| Parameter      | Type        | Required | Default     | Description                                                        |
|----------------|-------------|----------|-------------|--------------------------------------------------------------------|
| `query`        | string      | No       | `""`        | Search query string (empty returns all)                            |
| `page`         | int         | No       | `1`         | Page number (1-indexed, minimum: 1)                                |
| `limit`        | int/null    | No       | server default (50) | Results per page (minimum: 1, capped at server max: 1000) |
| `sort_by`      | string      | No       | `downloads` | Sort field: `relevance`, `stars`, `downloads`, `newest`, `updated`, `name` |
| `is_public`    | bool/null   | No       | `null`      | Filter by visibility (`true` or `false`)                           |
| `owner_id`     | string/null | No       | `null`      | Filter by owner ID                                                 |
| `package_type` | string/null | No       | `null`      | Filter by type: `FULL`, `TEMPLATES`, `KNOWLEDGE`, `WORKFLOWS`, `MIXED` |

#### Examples

```bash
# Search all packages
curl "http://localhost:8080/api/v1/lexicon/search"

# Search by keyword
curl "http://localhost:8080/api/v1/lexicon/search?query=medical"

# Search with filters
curl "http://localhost:8080/api/v1/lexicon/search?query=finance&sort_by=stars&is_public=true&package_type=KNOWLEDGE"

# Paginated results
curl "http://localhost:8080/api/v1/lexicon/search?query=science&page=2&limit=10"
```

#### Response

`200 OK`

```json
{
  "packages": [
    {
      "id": "repo-abc123",
      "name": "medical-ontology",
      "description": "Comprehensive medical knowledge graph with ICD-10 mappings",
      "owner_username": "johndoe",
      "owner_name": "John Doe",
      "owner_id": "user-xyz789",
      "is_public": true,
      "package_type": "KNOWLEDGE",
      "star_count": 42,
      "version_count": 3,
      "download_count": 1250,
      "created_at": 1704067200000,
      "updated_at": 1709251200000
    }
  ],
  "total": 1,
  "page": 1,
  "limit": 50
}
```

##### LexiconSearchResponse

| Field      | Type                   | Description                      |
|------------|------------------------|----------------------------------|
| `packages` | `LexiconPackageInfo[]` | Array of matching packages       |
| `total`    | int                    | Total number of matches          |
| `page`     | int                    | Current page number              |
| `limit`    | int                    | Results per page                 |

##### LexiconPackageInfo

| Field            | Type   | Description                                                                      |
|------------------|--------|----------------------------------------------------------------------------------|
| `id`             | string | Unique repository ID                                                             |
| `name`           | string | Repository/package name                                                          |
| `description`    | string | Package description                                                              |
| `owner_username` | string | Owner's username                                                                 |
| `owner_name`     | string | Owner's display name                                                             |
| `owner_id`       | string | Owner's user ID                                                                  |
| `is_public`      | bool   | Public visibility                                                                |
| `package_type`   | string | Package type: `FULL`, `TEMPLATES`, `KNOWLEDGE`, `WORKFLOWS`, `MIXED`   |
| `star_count`     | int    | Number of stars                                                                  |
| `version_count`  | int    | Number of published versions                                                     |
| `download_count` | int    | Total downloads across all versions                                              |
| `created_at`     | int    | Creation timestamp (Unix milliseconds)                                           |
| `updated_at`     | int    | Last update timestamp (Unix milliseconds)                                        |

---

### Get Package Info

```
GET /api/v1/lexicon/r/{owner}/{name}
```

Retrieve metadata for a specific package by owner and name.

#### Path Parameters

| Parameter | Type   | Required | Description              |
|-----------|--------|----------|--------------------------|
| `owner`   | string | Yes      | Package owner's username |
| `name`    | string | Yes      | Repository/package name  |

#### Example

```bash
curl "http://localhost:8080/api/v1/lexicon/r/johndoe/medical-ontology"
```

#### Response

`200 OK`

```json
{
  "id": "repo-abc123",
  "name": "medical-ontology",
  "description": "Comprehensive medical knowledge graph with ICD-10 mappings",
  "owner_username": "johndoe",
  "owner_name": "John Doe",
  "owner_id": "user-xyz789",
  "is_public": true,
  "package_type": "KNOWLEDGE",
  "star_count": 42,
  "version_count": 3,
  "download_count": 1250,
  "created_at": 1704067200000,
  "updated_at": 1709251200000
}
```

#### Error Responses

| Status | Condition         |
|--------|-------------------|
| `404`  | Package not found |

---

### Import Package

```
POST /api/v1/lexicon/import
```

Queue a Lexicon package import from the registry into the current Chaos Cypher database. The operation runs asynchronously in the background — poll the returned `task_id` via [`GET /api/v1/queue/tasks/{task_id}`](queue.md#get-task) to track progress.

#### Request Body

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `owner_username` | string | Yes | -- | Lexicon package owner username |
| `repo_name` | string | Yes | -- | Lexicon package repository name |
| `version` | string | No | `"latest"` | Package version tag |

```json
{
  "owner_username": "acme",
  "repo_name": "research-graph",
  "version": "latest"
}
```

#### Response

**Status:** `202 Accepted`

```json
{
  "message": "Import of acme/research-graph queued. Check Queue Monitor for status.",
  "task_id": "task-abc123def456",
  "status": "queued",
  "owner_username": "acme",
  "repo_name": "research-graph",
  "version": "latest"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `message` | string | Human-readable status message |
| `task_id` | string | Queue task ID to poll for progress |
| `status` | string | Initial task status (`"queued"`) |
| `owner_username` | string | Package owner |
| `repo_name` | string | Package repository name |
| `version` | string | Package version being imported |

#### curl Example

```bash
curl -X POST http://localhost:8080/api/v1/lexicon/import \
  -H "Content-Type: application/json" \
  -d '{
    "owner_username": "acme",
    "repo_name": "research-graph",
    "version": "latest"
  }'
```

#### Errors

| Status | Description |
|--------|-------------|
| `503`  | Queue service unavailable |

---

### Upload Package

```
POST /api/v1/lexicon/upload
```

Upload a package archive (`.ccx`) to the Lexicon registry. Requires authentication.

#### Request

Multipart form upload with query parameters for metadata.

#### Query Parameters

| Parameter | Type        | Required | Default | Description                         |
|-----------|-------------|----------|---------|-------------------------------------|
| `public`  | bool        | No       | `true`  | Make package publicly visible       |
| `message` | string/null | No       | `null`  | Optional upload/commit message      |

#### File Field

| Field  | Type       | Required | Description                        |
|--------|------------|----------|------------------------------------|
| `file` | UploadFile | Yes      | Package archive file (`.ccx`)      |

#### Example

```bash
curl -X POST "http://localhost:8080/api/v1/lexicon/upload?public=true&message=Initial+release" \
  -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIs..." \
  -F "file=@my-package.ccx"
```

#### Response

`201 Created`

```json
{
  "id": "repo-def456",
  "name": "my-package",
  "description": "My knowledge package",
  "owner_username": "johndoe",
  "owner_name": "John Doe",
  "owner_id": "user-xyz789",
  "is_public": true,
  "package_type": "FULL",
  "star_count": 0,
  "version_count": 1,
  "download_count": 0,
  "created_at": 1709251200000,
  "updated_at": 1709251200000
}
```

#### Error Responses

| Status | Condition                     |
|--------|-------------------------------|
| `401`  | Authentication required       |
| `403`  | Insufficient permissions      |
| `503`  | Lexicon server unavailable    |

---

## Error Handling

All Lexicon endpoints return errors in a consistent format:

```json
{
  "detail": {
    "message": "Human-readable error message",
    "details": {}
  }
}
```

### Status Code Mapping

Errors from the upstream Lexicon server are mapped to HTTP status codes:

| Upstream Status | API Status | Meaning              |
|-----------------|------------|----------------------|
| `401`           | `401`      | Unauthorized         |
| `403`           | `403`      | Forbidden            |
| `404`           | `404`      | Not found            |
| `408`           | `408`      | Request timeout      |
| `410`           | `408`      | Gone (code expired)  |
| Other           | `503`      | Service unavailable  |
