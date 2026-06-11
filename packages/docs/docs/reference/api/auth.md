---
title: Authentication API
description: Single-user auth API for Chaos Cypher — login, logout, session status, and password management. Every /api/ request is gated by nginx auth_request.
---

# Authentication

Single-user local auth for the Chaos Cypher deployment. Every `/api/` request passes through an nginx `auth_request` subrequest before reaching Cortex.

**Base path:** `/api/v1/auth`

:::tip[Related pages]

- [Security: Self-hosted threat model](../../security/self-hosted-threat-model.md) — what Chaos Cypher defends against, hardening checklist, and auth misconfiguration diagnostics
- [Security: Plugin trust model](../../security/plugin-trust.md) — how user-installed plugins are trusted and how to restrict them

:::

---

## How Auth Works

```
Browser / API client
    │
    ▼
nginx (port 80 / 443)
    │  auth_request → GET /api/v1/auth/verify
    │  ← 200 + X-Auth-User header   (authenticated)
    │  ← 401                          (unauthenticated → nginx returns 401 to client)
    ▼
Cortex (internal only)
    reads X-Auth-User from the verified header
```

**Single-user model.** There is one local operator account. No admin/user tiers, no multi-user, no registration endpoint.

**Two credential mechanisms are accepted:**

| Mechanism | How to use |
|-----------|-----------|
| Session cookie | Set at login/setup; sent automatically by browsers. Name: `cc_session` (configurable). |
| API key | `Authorization: Bearer cc_live_<key>`. Useful for scripts and CI. |

Cookie takes priority when both are present. Every request goes through `/verify`; both mechanisms are checked there.

**Server-side invalidation.** The session cookie is stateless HMAC-SHA256 — no session store. Invalidation works by incrementing `session_epoch` in the credentials file. Any cookie that carries a stale epoch is rejected. Epoch is bumped on: logout, password change, username change.

---

## Endpoint Reference

### Check Status

```
GET /api/v1/auth/status
```

Returns setup state and whether the current caller is authenticated. No auth required. Safe to poll on page load.

**Response — 200 OK**

```json
{"setup_needed": false, "authenticated": true, "username": "alice"}
```

| Field | Type | Description |
|-------|------|-------------|
| `setup_needed` | boolean | `true` when no credentials file exists (first-run state). |
| `authenticated` | boolean | `true` when the request carries a valid session cookie. |
| `username` | string \| null | Present only when `authenticated` is `true`. |

**curl example**

```bash
curl -s http://localhost/api/v1/auth/status
{"setup_needed": true, "authenticated": false}
```

---

### First-Run Setup

```
POST /api/v1/auth/setup
```

Creates the single admin account. Only available when no credentials file exists (i.e., `setup_needed: true`). Returns a session cookie on success.

**Request body**

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `username` | string | Yes | 3–64 chars |
| `password` | string | Yes | 8–128 chars |

```json
{"username": "alice", "password": "a-good-passphrase"}
```

**Response — 201 Created** (+ `Set-Cookie: cc_session=...`)

```json
{"username": "alice"}
```

**Errors**

| Status | Condition |
|--------|-----------|
| `409 Conflict` | Already initialized — setup has been completed. |
| `422 Unprocessable Entity` | Validation failed (username/password constraints). |

**curl example**

```bash
curl -s -X POST http://localhost/api/v1/auth/setup \
    -H "Content-Type: application/json" \
    -d '{"username": "alice", "password": "a-good-passphrase"}'
{"username": "alice"}

# Already initialized:
curl -s -X POST http://localhost/api/v1/auth/setup \
    -d '{"username": "alice", "password": "a-good-passphrase"}' \
    -H "Content-Type: application/json"
{"error": "HTTP_409", "message": "already initialized"}
```

---

### Login

```
POST /api/v1/auth/login
```

Validate username + password and receive a session cookie.

**Request body**

| Field | Type | Required |
|-------|------|----------|
| `username` | string | Yes |
| `password` | string | Yes |

```json
{"username": "alice", "password": "a-good-passphrase"}
```

**Response — 200 OK** (+ `Set-Cookie: cc_session=...`)

```json
{"username": "alice"}
```

**Errors**

| Status | Condition |
|--------|-----------|
| `401 Unauthorized` | Invalid username or password. |
| `409 Conflict` | Setup has not been run yet (`setup_needed: true`). |

**curl example**

```bash
curl -s -c cookies.txt -X POST http://localhost/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"username": "alice", "password": "a-good-passphrase"}'
{"username": "alice"}

# Subsequent authenticated request using the saved cookie jar:
curl -s -b cookies.txt http://localhost/api/v1/auth/me
{"username": "alice"}
```

---

### Logout

```
POST /api/v1/auth/logout
```

Clears the session cookie and bumps `session_epoch`, which immediately invalidates every outstanding session for the account — including sessions on other browsers or API clients using the old cookie.

**Response — 204 No Content**

No body. The `Set-Cookie` header in the response clears the cookie.

**curl example**

```bash
curl -s -o /dev/null -w "%{http_code}" -b cookies.txt \
    -X POST http://localhost/api/v1/auth/logout
204
```

---

### Verify (internal nginx target)

```
GET /api/v1/auth/verify
```

:::warning[Internal endpoint]

This endpoint is the `auth_request` subrequest target called by nginx before forwarding requests to Cortex. Do not call it directly in application code — it is not rate-limited separately and the response is only meaningful in the nginx subrequest context.

:::

Checks the session cookie or `Authorization: Bearer` header. On success, returns 200 and sets the `X-Auth-User` response header to the authenticated username. nginx extracts this header and forwards it as `X-Auth-User` on the proxied request to Cortex.

**Response — 200 OK** (authenticated)
`X-Auth-User: alice`

**Response — 401 Unauthorized** (not authenticated or invalid credentials)

---

### Get current user

```
GET /api/v1/auth/me
```

Returns the authenticated caller's username. Useful for confirming which account a credential belongs to.

**Auth required:** session cookie or `Authorization: Bearer cc_live_...`

**Response — 200 OK**

```json
{"username": "alice"}
```

**Errors**

| Status | Condition |
|--------|-----------|
| `401 Unauthorized` | No valid session cookie or API key. |

**curl example**

```bash
# With session cookie:
curl -s -b cookies.txt http://localhost/api/v1/auth/me
{"username": "alice"}

# With API key:
curl -s http://localhost/api/v1/auth/me \
    -H "Authorization: Bearer cc_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
{"username": "alice"}
```

---

### Change password

```
POST /api/v1/auth/password
```

Rotates the admin password. Bumps `session_epoch` and clears the caller's session cookie, requiring re-login. All other outstanding sessions are also invalidated.

**Auth required:** session cookie or API key.

**Request body**

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `old_password` | string | Yes | Current password |
| `new_password` | string | Yes | 8–128 chars |

```json
{"old_password": "a-good-passphrase", "new_password": "an-even-better-one"}
```

**Response — 204 No Content**

The `Set-Cookie` header in the response clears the caller's session cookie. Re-login with `POST /login`.

**Errors**

| Status | Condition |
|--------|-----------|
| `401 Unauthorized` | No valid session. |
| `403 Forbidden` | `old_password` does not match. |

**curl example**

```bash
curl -s -o /dev/null -w "%{http_code}" -b cookies.txt \
    -X POST http://localhost/api/v1/auth/password \
    -H "Content-Type: application/json" \
    -d '{"old_password": "a-good-passphrase", "new_password": "an-even-better-one"}'
204
```

---

### Change username

```
POST /api/v1/auth/username
```

Renames the admin account. Requires the current password. Issues a fresh session cookie bound to the new username. Bumps `session_epoch`, invalidating any other outstanding sessions.

**Auth required:** session cookie or API key.

**Request body**

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `password` | string | Yes | Current password (for confirmation) |
| `new_username` | string | Yes | 3–64 chars |

```json
{"password": "an-even-better-one", "new_username": "bob"}
```

**Response — 200 OK** (+ new `Set-Cookie: cc_session=...` bound to `new_username`)

```json
{"username": "bob"}
```

**Errors**

| Status | Condition |
|--------|-----------|
| `401 Unauthorized` | No valid session. |
| `403 Forbidden` | Password confirmation failed. |

---

## API Keys

API keys allow token-based access without a browser session. Useful for scripts, CI/CD pipelines, and any non-interactive client.

**Format:** `cc_live_<32 url-safe base64 chars>`

**Storage:** bcrypt-hashed in the credentials file. The plaintext key is shown exactly once, at creation time.

**Usage:** `Authorization: Bearer cc_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

**Verification:** The prefix `cc_live_` is checked first (cheap constant-time string comparison). bcrypt verification against stored hashes runs only for keys that pass the prefix check.

---

### Create API key

```
POST /api/v1/auth/keys
```

Mints a new API key. The plaintext key is returned once — store it immediately.

**Auth required:** session cookie or API key.

**Request body**

| Field | Type | Required | Constraints |
|-------|------|----------|-------------|
| `name` | string | Yes | 1–64 chars. Human-readable label. |

```json
{"name": "CI Pipeline"}
```

**Response — 201 Created**

```json
{
  "id": "key_a1b2c3d4",
  "name": "CI Pipeline",
  "key": "cc_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "created_at": "2026-04-26T14:30:00.000000"
}
```

:::warning[Save the key now]

The `key` field is returned only at creation time. It cannot be retrieved later — the stored value is a bcrypt hash.

:::

**Errors**

| Status | Condition |
|--------|-----------|
| `401 Unauthorized` | No valid session. |
| `422 Unprocessable Entity` | Validation failed (e.g., empty name). |

**curl example**

```bash
curl -s -X POST http://localhost/api/v1/auth/keys \
    -b cookies.txt \
    -H "Content-Type: application/json" \
    -d '{"name": "CI Pipeline"}'
{
  "id": "key_a1b2c3d4",
  "name": "CI Pipeline",
  "key": "cc_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "created_at": "2026-04-26T14:30:00.000000"
}
```

---

### List API keys

```
GET /api/v1/auth/keys
```

Returns all keys for the account. No secret material is included.

**Auth required:** session cookie or API key.

**Response — 200 OK**

```json
[
  {
    "id": "key_a1b2c3d4",
    "name": "CI Pipeline",
    "created_at": "2026-04-26T14:30:00.000000",
    "last_used_at": "2026-04-26T09:22:00.000000"
  }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Stable key identifier. Use for revocation. |
| `name` | string | Human-readable label set at creation. |
| `created_at` | datetime | ISO-8601 UTC creation timestamp. |
| `last_used_at` | datetime \| null | `null` until the key is first used. Updated on each successful verification. |

**curl example**

```bash
curl -s -b cookies.txt http://localhost/api/v1/auth/keys
[{"id": "key_a1b2c3d4", "name": "CI Pipeline", "created_at": "...", "last_used_at": null}]
```

---

### Revoke API key

```
DELETE /api/v1/auth/keys/{key_id}
```

Permanently revokes the API key. The key becomes unusable immediately.

**Auth required:** session cookie or API key.

**Path parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `key_id` | string | Key ID returned by the create or list endpoint. |

**Response — 204 No Content**

**Errors**

| Status | Condition |
|--------|-----------|
| `401 Unauthorized` | No valid session. |
| `404 Not Found` | No key with that ID exists. |

**curl example**

```bash
curl -s -o /dev/null -w "%{http_code}" \
    -b cookies.txt \
    -X DELETE http://localhost/api/v1/auth/keys/key_a1b2c3d4
204
```

---

## Cookie Semantics

| Attribute | Value |
|-----------|-------|
| Name | `cc_session` (default; configurable via `local_auth.cookie_name`) |
| Signing | HMAC-SHA256 (stateless; no session store) |
| TTL | 30 days (configurable via `local_auth.cookie_ttl_seconds`) |
| `HttpOnly` | Yes — not accessible from JavaScript |
| `SameSite` | `Strict` |
| `Secure` | Set when TLS is active (`local_auth.cookie_secure`) |
| `Path` | `/` |

**Invalidation:** bumping `session_epoch` in the credentials file immediately invalidates all outstanding cookies without any network round-trip. Epoch is bumped on logout, password change, and username change.

---

## Error Envelope

Error responses from Cortex use the unified envelope:

```json
{"error": "ERROR_CODE", "message": "Human-readable message.", "details": {...}}
```

Common codes for auth endpoints:

| HTTP Status | `error` | When |
|-------------|---------|------|
| `401` | `authentication_required` | No session cookie or API key — nginx answers the request itself with the minimal body `{"error":"authentication_required"}` (no `message` field). |
| `401` | `HTTP_401` | `invalid credentials` on `/login`; `not authenticated` on session-gated endpoints reached past nginx. |
| `403` | `HTTP_403` | `invalid password` — wrong password confirmation on `/password` or `/username`. |
| `409` | `HTTP_409` | `already initialized` (on setup) or `setup required` (on login before setup). |
| `422` | `VALIDATION_FAILED` | Request body failed field validation. |

:::note

`AUTH_REQUIRED` ("Authentication required") appears only on requests made directly to Cortex that lack the nginx identity header — in the default deployment, unauthenticated requests never reach Cortex, so you will see nginx's `authentication_required` body instead.

:::

Validation error structure:

```json
{
  "error": "VALIDATION_FAILED",
  "message": "Request body failed validation",
  "details": {"errors": [{"loc": ["body", "password"], "msg": "...", "type": "..."}]}
}
```

---

## What Is NOT in the Auth Model

The following do not exist in Chaos Cypher and should not appear in any configuration:

- **JWT tokens** — there is no `jwt_secret_key`, `jwt_algorithm`, or `jwt_expiration_minutes`. The session cookie is HMAC-SHA256, not JWT.
- **Refresh tokens** — the session cookie is long-lived and reissued on login. There is no `/refresh` endpoint.
- **Multi-user accounts** — there is one operator account. No registration, no user list, no admin vs. user roles.
- **`auth.enabled` setting** — auth is always active. It cannot be disabled.
