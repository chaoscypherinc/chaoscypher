---
title: Chat API
description: REST API for managing AI conversations and RAG-powered chat — create conversations, send messages, stream responses, and manage chat history.
---

# Chat API

Manage conversations and interact with AI using RAG-powered chat.

**Base path:** `/api/v1/chats`

---

## Conversations

### List Chats

```
GET /api/v1/chats
```

Returns all chats without message bodies.

#### Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `page` | integer | No | `1` | Page number (>= 1) |
| `page_size` | integer | No | Server default | Items per page (>= 1, clamped to server max) |
| `scoped` | boolean | No | _none_ | Filter by scope status. `true` returns only chats with source scoping, `false` returns only unscoped chats. Omit to return all chats. |

#### Response

**Status:** `200 OK`

```json
{
  "data": [
    {
      "id": "abc123def456",
      "title": "Research Discussion",
      "status": "active",
      "created_at": "2026-03-09T14:30:00.000000",
      "updated_at": "2026-03-09T14:35:00.000000",
      "message_count": 4,
      "source_ids": ["src-uuid-1", "src-uuid-2"]
    }
  ],
  "pagination": {
    "total": 1,
    "page": 1,
    "page_size": 50,
    "total_pages": 1,
    "has_next": false,
    "has_prev": false
  }
}
```

#### curl Example

```bash
# List all chats
curl -s http://localhost:8080/api/v1/chats

# With pagination
curl -s "http://localhost:8080/api/v1/chats?page=1&page_size=10"

# Only scoped chats
curl -s "http://localhost:8080/api/v1/chats?scoped=true"
```

---

### Create Chat

```
POST /api/v1/chats
```

Creates a new conversation. Optionally scope it to specific sources or tags.

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | Chat title |
| `source_ids` | string[] | No | Source IDs to scope the chat to |
| `tag_ids` | string[] | No | Tag IDs (resolved to their source IDs and merged with `source_ids`) |

```json
{
  "title": "Research Discussion",
  "source_ids": ["src-uuid-1"],
  "tag_ids": ["tag-uuid-1"]
}
```

#### Response

**Status:** `201 Created`

```json
{
  "id": "abc123def456",
  "title": "Research Discussion",
  "status": "active",
  "created_at": "2026-03-09T14:30:00.000000",
  "updated_at": "2026-03-09T14:30:00.000000",
  "message_count": 0,
  "source_ids": ["src-uuid-1", "src-uuid-from-tag"],
  "messages": []
}
```

#### curl Example

```bash
# Create a basic chat
curl -s -X POST http://localhost:8080/api/v1/chats \
  -H "Content-Type: application/json" \
  -d '{"title": "Research Discussion"}'

# Create a scoped chat
curl -s -X POST http://localhost:8080/api/v1/chats \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Scoped Discussion",
    "source_ids": ["src-uuid-1"],
    "tag_ids": ["tag-uuid-1"]
  }'
```

---

### Delete All Chats

```
DELETE /api/v1/chats
```

Deletes all chats and their messages for the current database. This operation is irreversible.

#### Response

**Status:** `204 No Content`

No response body.

#### curl Example

```bash
curl -s -X DELETE http://localhost:8080/api/v1/chats
```

---

### Get Chat

```
GET /api/v1/chats/{chat_id}
```

Returns a single chat with all of its messages.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Response

**Status:** `200 OK`

```json
{
  "id": "abc123def456",
  "title": "Research Discussion",
  "status": "active",
  "created_at": "2026-03-09T14:30:00.000000",
  "updated_at": "2026-03-09T14:35:00.000000",
  "message_count": 2,
  "source_ids": ["src-uuid-1"],
  "messages": [
    {
      "id": "msg-uuid-1",
      "role": "user",
      "content": "What are the key findings?",
      "timestamp": "2026-03-09T14:30:05.000000",
      "extra_metadata": null
    },
    {
      "id": "msg-uuid-2",
      "role": "assistant",
      "content": "Based on the documents, the key findings are...",
      "timestamp": "2026-03-09T14:30:12.000000",
      "extra_metadata": null
    }
  ]
}
```

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/chats/abc123def456
```

---

### Update Chat Title

```
PATCH /api/v1/chats/{chat_id}
```

Updates the title of a chat.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | New chat title |

```json
{
  "title": "Updated Title"
}
```

#### Response

**Status:** `200 OK`

Returns the full chat object with updated title. See [Get Chat](#get-chat) for the response schema.

#### curl Example

```bash
curl -s -X PATCH http://localhost:8080/api/v1/chats/abc123def456 \
  -H "Content-Type: application/json" \
  -d '{"title": "Updated Title"}'
```

---

### Update Chat Status

```
PATCH /api/v1/chats/{chat_id}/status
```

Updates the status of a chat.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | New status. Valid values: `active`, `processing`, `completed`, `error` |

```json
{
  "status": "completed"
}
```

#### Response

**Status:** `200 OK`

Returns the full chat object with updated status. See [Get Chat](#get-chat) for the response schema.

#### curl Example

```bash
curl -s -X PATCH http://localhost:8080/api/v1/chats/abc123def456/status \
  -H "Content-Type: application/json" \
  -d '{"status": "completed"}'
```

---

### Delete Chat

```
DELETE /api/v1/chats/{chat_id}
```

Deletes a chat and all its messages.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Response

**Status:** `204 No Content`

No response body.

#### curl Example

```bash
curl -s -X DELETE http://localhost:8080/api/v1/chats/abc123def456
```

---

### Chat Count

```
GET /api/v1/chats/stats/count
```

Returns the total number of chats.

#### Response

**Status:** `200 OK`

```json
{
  "count": 42
}
```

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/chats/stats/count
```

---

## Messages

### Add Message

```
POST /api/v1/chats/{chat_id}/messages
```

Adds a message to an existing chat.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | Message role (e.g. `user`, `assistant`, `system`) |
| `content` | string | Yes | Message content |
| `extra_metadata` | object | No | Additional metadata to attach to the message |

```json
{
  "role": "user",
  "content": "What are the key findings?",
  "extra_metadata": null
}
```

#### Response

**Status:** `201 Created`

```json
{
  "id": "msg-uuid-1",
  "role": "user",
  "content": "What are the key findings?",
  "timestamp": "2026-03-09T14:30:05.000000",
  "extra_metadata": null
}
```

#### curl Example

```bash
curl -s -X POST http://localhost:8080/api/v1/chats/abc123def456/messages \
  -H "Content-Type: application/json" \
  -d '{"role": "user", "content": "What are the key findings?"}'
```

---

### List Messages

```
GET /api/v1/chats/{chat_id}/messages
```

Returns all messages for a chat in chronological order (oldest first).

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Response

**Status:** `200 OK`

```json
[
  {
    "id": "msg-uuid-1",
    "role": "user",
    "content": "What are the key findings?",
    "timestamp": "2026-03-09T14:30:05.000000",
    "extra_metadata": null
  },
  {
    "id": "msg-uuid-2",
    "role": "assistant",
    "content": "Based on the documents, the key findings are...",
    "timestamp": "2026-03-09T14:30:12.000000",
    "extra_metadata": {
      "model": "gpt-4",
      "tokens_used": 350
    }
  }
]
```

#### curl Example

```bash
curl -s http://localhost:8080/api/v1/chats/abc123def456/messages
```

---

## Streaming

### Stream AI Response

```
POST /api/v1/chats/{chat_id}/stream
```

Streams an AI response in real-time using Server-Sent Events (SSE). The user message is saved to the chat, then the AI generates a response using RAG context from the chat's scoped sources (or all sources if unscoped). Tool calls are executed during the stream.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role` | string | Yes | Message role (typically `user`) |
| `content` | string | Yes | User message content |
| `extra_metadata` | object | No | Additional metadata |

```json
{
  "role": "user",
  "content": "What are the key findings from the uploaded paper?"
}
```

#### Response

**Status:** `200 OK`
**Content-Type:** `text/event-stream`

The response is a stream of SSE events. Each event is a single `data:` line containing a JSON object with a `type` field identifying the event kind.

#### Event Types

| Event | Description |
|-------|-------------|
| `content` | Text delta and accumulated response text |
| `thinking_delta` | AI reasoning steps (streamed incrementally) |
| `thinking` | Complete reasoning block |
| `context_info` | Information about RAG context used |
| `tool_calls` | List of tools the AI wants to invoke |
| `tool_start` | A tool execution has begun |
| `tool_result` | A tool execution has completed |
| `iteration_progress` | Progress update for multi-iteration tool calling |
| `cached_tool_calls` | Duplicate tool calls served from cache |
| `warning` | Non-fatal warning during generation |
| `done` | Stream completed successfully (includes final content) |
| `error` | Error during generation |

#### SSE Event Data Examples

**`content` event** -- streamed as the AI generates text:

```
data: {"type": "content", "delta": "Based on", "accumulated": "Based on"}

data: {"type": "content", "delta": " the analysis,", "accumulated": "Based on the analysis,"}
```

**`thinking_delta` event** -- reasoning steps (when thinking is enabled):

```
data: {"type": "thinking_delta", "thinking": "The user is asking about key findings. Let me search the indexed documents..."}
```

**`thinking` event** -- complete thinking block:

```
data: {"type": "thinking", "thinking": "The user is asking about key findings. Let me search the indexed documents and summarize the results."}
```

**`context_info` event** -- RAG context metadata:

```
data: {"type": "context_info", "sources_used": 3, "chunks_retrieved": 12}
```

**`tool_calls` event** -- tools the AI wants to invoke:

```
data: {"type": "tool_calls", "tool_calls": [{"id": "call_1", "name": "search_documents", "arguments": {"query": "key findings"}}], "iteration": 1}
```

**`tool_start` event** -- a tool begins executing:

```
data: {"type": "tool_start", "tool": "search_documents", "arguments": {"query": "key findings"}, "iteration": 1}
```

**`tool_result` event** -- a tool has completed:

```
data: {"type": "tool_result", "tool": "search_documents", "result": "Found 5 relevant chunks...", "iteration": 1}
```

**`done` event** -- stream completed with final content:

```
data: {"type": "done", "content": "Based on the analysis, the three key findings are...", "iterations": 1, "thinking": "...", "referenced_entities": [], "chunk_citations": []}
```

**`error` event** -- an error occurred:

```
data: {"type": "error", "error": "LLM provider returned an error"}
```

#### curl Example

```bash
curl -s -N -X POST http://localhost:8080/api/v1/chats/abc123def456/stream \
  -H "Content-Type: application/json" \
  -d '{"role": "user", "content": "What are the key findings?"}' \
  --no-buffer
```

:::warning[Client disconnection]

If the client disconnects mid-stream (tab closed, network drop), the server cancels the stream and the in-flight LLM work is lost — the assistant message is **not** persisted. For disconnect-safe, durable processing, use [`POST /api/v1/chats/{chat_id}/send`](#send-message-background) to queue the work on the background worker and [`GET /api/v1/chats/{chat_id}/events`](#subscribe-to-chat-events) to observe progress; those survive disconnect.

:::

---

### Approve or Reject a Tool Call

```
POST /api/v1/chats/{chat_id}/tool_decision
```

Resolves a pending tool-call approval for an active streaming session. When the AI requests a tool that requires user approval, it emits a `tool_approval_required` SSE event and pauses. The UI sends this endpoint to approve or reject the queued tool call, waking the paused stream handler.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool_call_id` | string | Yes | LLM-assigned `tool_call_id` from the `tool_approval_required` SSE event |
| `decision` | string | Yes | User's decision: `"approve"` or `"reject"` |

```json
{
  "tool_call_id": "call_abc123",
  "decision": "approve"
}
```

#### Response

**Status:** `204 No Content`

No response body on success. Returns `404 Not Found` if no pending approval matches the given `tool_call_id` for that chat.

#### curl Example

```bash
curl -s -X POST http://localhost:8080/api/v1/chats/abc123def456/tool_decision \
  -H "Content-Type: application/json" \
  -d '{"tool_call_id": "call_abc123", "decision": "approve"}'
```

---

### Generate Title

```
POST /api/v1/chats/{chat_id}/generate_title
```

Auto-generates a short (3-6 word) title for the chat based on the first user message, using a lightweight LLM call. If no user message exists or generation fails, the chat is returned unchanged.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Response

**Status:** `200 OK`

Returns the full chat object with the generated title. See [Get Chat](#get-chat) for the response schema.

#### curl Example

```bash
curl -s -X POST http://localhost:8080/api/v1/chats/abc123def456/generate_title
```

---

## Scoping

Source scoping restricts which documents the AI searches during RAG. When a chat is scoped, only the specified sources are used for context retrieval. When unscoped, all enabled sources are searched.

### Update Scope

```
PATCH /api/v1/chats/{chat_id}/scope
```

Updates the source scope of a chat. Source IDs from `source_ids` and resolved from `tag_ids` are merged with deduplication. A system message is injected noting the scope change.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source_ids` | string[] | No | Source IDs to scope to |
| `tag_ids` | string[] | No | Tag IDs (resolved to source IDs and merged) |

:::note

At least one of `source_ids` or `tag_ids` should be provided for the scope to have an effect. If both are empty or null, the scope is effectively cleared.

:::

```json
{
  "source_ids": ["src-uuid-1"],
  "tag_ids": ["tag-uuid-1"]
}
```

#### Response

**Status:** `200 OK`

Returns the full chat object after scope update, including the injected system message. See [Get Chat](#get-chat) for the response schema. The `source_ids` field reflects the new scope, and `messages` includes the injected system message noting the change.

#### curl Example

```bash
curl -s -X PATCH http://localhost:8080/api/v1/chats/abc123def456/scope \
  -H "Content-Type: application/json" \
  -d '{"source_ids": ["src-uuid-1"], "tag_ids": ["tag-uuid-1"]}'
```

---

### Clear Scope

```
DELETE /api/v1/chats/{chat_id}/scope
```

Removes source scoping from a chat. The AI will search all enabled sources. A system message is injected noting the scope removal.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Response

**Status:** `200 OK`

Returns the full chat object after scope removal, including the injected system message. See [Get Chat](#get-chat) for the response schema. The `source_ids` field is `null` and `messages` includes the injected system message noting the removal.

#### curl Example

```bash
curl -s -X DELETE http://localhost:8080/api/v1/chats/abc123def456/scope
```

---

## Background Processing

`POST /send` enqueues a message for durable background processing. `GET /events` subscribes to the resulting SSE stream. Together they provide a disconnect-safe alternative to `POST /stream`: the worker keeps running even if the client closes the connection, and the client can reattach by opening a new events subscription.

### Send Message (Background)

```
POST /api/v1/chats/{chat_id}/send
```

Saves the user message, sets the chat status to `processing`, and enqueues the AI completion task on the LLM queue. Returns immediately with a task ID; use [Subscribe to Chat Events](#subscribe-to-chat-events) to observe progress.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `content` | string | Yes | User message text (max 500,000 characters) |

```json
{
  "content": "What are the key findings from the uploaded paper?"
}
```

#### Response

**Status:** `202 Accepted`

```json
{
  "task_id": "task-uuid-1",
  "status": "processing"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Unique identifier for the queued task |
| `status` | string | Always `"processing"` on acceptance |

#### curl Example

```bash
curl -s -X POST http://localhost:8080/api/v1/chats/abc123def456/send \
  -H "Content-Type: application/json" \
  -d '{"content": "What are the key findings?"}'
```

---

### Subscribe to Chat Events

```
GET /api/v1/chats/{chat_id}/events
```

Opens a reconnectable Server-Sent Events stream that delivers processing events for a background chat session. On connect, the current chat status is checked:

- If the chat is already `active` or `completed`, a `done` event is emitted immediately and the stream closes.
- If the chat is in `error` state, an `error` event is emitted and the stream closes.
- Otherwise, the endpoint subscribes to Valkey pub/sub and forwards events until a `done` or `error` event is received, or the client disconnects.

#### Path Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `chat_id` | string | Yes | Chat ID |

#### Response

**Status:** `200 OK`
**Content-Type:** `text/event-stream`

#### Event Types

| Event | Description |
|-------|-------------|
| `content` | LLM response content delta |
| `tool_start` | Tool execution started |
| `tool_result` | Tool execution completed |
| `done` | Processing completed successfully |
| `error` | Processing failed or stream error |

#### SSE Event Data Examples

**`content` event:**

```
data: {"type": "content", "delta": "Based on", "accumulated": "Based on"}
```

**`done` event:**

```
data: {"type": "done", "status": "active"}
```

**`error` event:**

```
data: {"type": "error", "error": "Chat processing failed. Please try again.", "error_code": "CHAT_PROCESSING_FAILED"}
```

#### curl Example

```bash
curl -s -N http://localhost:8080/api/v1/chats/abc123def456/events \
  --no-buffer
```

---

## Schema Introspection

### SSE Event Schema Anchor

```
GET /api/v1/chats/_schema/sse_event
```

Schema-only endpoint — **do not call at runtime**. Its sole purpose is to force FastAPI to register `ChatSSEEvent` and all 13 discriminated-union variant models as named `#/components/schemas` entries in `/openapi.json`, enabling TypeScript codegen to produce a typed discriminated union for SSE event handling.

This endpoint always returns `501 Not Implemented` if invoked directly. It appears in the OpenAPI schema so that code generators can reference the `ChatSSEEnvelope` type.

#### Response

**Status:** `501 Not Implemented` (if called at runtime)

#### curl Example

```bash
# Do not call this endpoint in production code.
# It exists solely for OpenAPI schema generation.
curl -s http://localhost:8080/api/v1/chats/_schema/sse_event
```

---

## Response Models Reference

### ChatResponse

Returned by endpoints that operate on a single chat (create, get, update, generate title, scope operations).

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique chat identifier |
| `title` | string | Chat title |
| `status` | string | Chat status (`active`, `processing`, `completed`, `error`) |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |
| `message_count` | integer | Total number of messages |
| `source_ids` | string[] or null | Source IDs the chat is scoped to, or `null` if unscoped |
| `messages` | ChatMessageResponse[] | List of messages (empty in some endpoints) |

### ChatListResponse

Used within `PaginatedChatsResponse` for list operations. Same as `ChatResponse` but without the `messages` field.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique chat identifier |
| `title` | string | Chat title |
| `status` | string | Chat status |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |
| `message_count` | integer | Total number of messages |
| `source_ids` | string[] or null | Source IDs the chat is scoped to |

### PaginatedChatsResponse

Paginated response for listing chats.

| Field | Type | Description |
|-------|------|-------------|
| `data` | ChatListResponse[] | List of chats (without messages) |
| `pagination` | object | Pagination metadata (total, page, page_size, total_pages, has_next, has_prev) |

### ChatMessageResponse

Represents a single message within a chat.

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique message identifier |
| `role` | string | Message role (`user`, `assistant`, `system`) |
| `content` | string | Message content |
| `timestamp` | datetime | Message timestamp |
| `extra_metadata` | object or null | Additional metadata (model info, token counts, etc.) |

### ChatCountResponse

Returned by the chat count endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `count` | integer | Total number of chats |

### ChatSendResponse

Returned by the send-message endpoint.

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | string | Unique identifier for the queued background task |
| `status` | string | Always `"processing"` when the task is accepted |
