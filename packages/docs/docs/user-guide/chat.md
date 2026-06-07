---
id: chat
title: Chat
description: Ask questions about your documents using RAG-powered AI chat in Chaos Cypher — the AI retrieves relevant passages and generates answers grounded in your content.
---

import Tabs from '@theme/Tabs';
import TabItem from '@theme/TabItem';

# Chat

Chat lets you ask questions about your documents using retrieval-augmented generation (RAG). The AI searches your indexed content, retrieves relevant passages, and generates answers grounded in your actual documents.

## Conversations

Each chat is a conversation with its own message history. You can maintain multiple conversations simultaneously.

### Creating a Conversation

<Tabs>
<TabItem value="web-ui" label="Web UI">


Start a new chat from the sidebar. Give it a title or let the system auto-generate one from your first message.

![Chat sidebar with conversation list and search](/img/screenshots/chat-sidebar.png)

</TabItem>
<TabItem value="cli" label="CLI">


```bash
# Start an interactive chat session (creates a new conversation)
chaoscypher chat

# Or send a one-shot message
chaoscypher chat "What are the key findings?"
```

</TabItem>
<TabItem value="python" label="Python">


```python
from chaoscypher_core import Engine

async with Engine("./data/databases/default") as engine:
    response = await engine.chat("What are the key findings?")
    print(response.content)
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X POST http://localhost:8080/api/v1/chats \
  -H "Content-Type: application/json" \
  -d '{"title": "Research Questions"}'
```

</TabItem>
</Tabs>


### Auto-Generated Titles

After your first message, Chaos Cypher can automatically generate a concise 3-6 word title using a lightweight LLM call. This keeps your conversation list organized without manual naming.

## Sending Messages

Type your question and the AI will:

1. Search your indexed documents for relevant chunks
2. Include the most relevant passages as context
3. Generate a response grounded in that context
4. Include citations linking back to source documents

<Tabs>
<TabItem value="web-ui" label="Web UI">


Type your message in the chat input and press Enter or click Send. Responses stream in real-time.

![Chat conversation with AI response and citations](/img/screenshots/chat-conversation.png)

</TabItem>
<TabItem value="cli" label="CLI">


```bash
chaoscypher chat "How are the entities connected?"
```

``` { .text .no-copy }
  → search_chunks(query=entity connections)
  → search_nodes(query=entities)

Based on the knowledge graph, the entities are connected through
several relationship types...

   │ The report identifies three primary connection patterns
   │ between organizational entities.
   └─ quarterly-report.pdf
```

</TabItem>
<TabItem value="python" label="Python">


```python
from chaoscypher_core import Engine

async with Engine("./data/databases/default") as engine:
    response = await engine.chat("How are the entities connected?")
    print(response.content)
    for citation in response.citations:
        print(f"  Source: {citation.filename}")
```

</TabItem>
<TabItem value="api" label="API">


```bash
# Add a message and stream the AI response (SSE)
curl -X POST http://localhost:8080/api/v1/chats/{chat_id}/stream \
  -H "Content-Type: application/json" \
  -d '{"content": "How are the entities connected?"}'

# Add a message to the history without generating a response
curl -X POST http://localhost:8080/api/v1/chats/{chat_id}/messages \
  -H "Content-Type: application/json" \
  -d '{"content": "How are the entities connected?", "role": "user"}'
```

</TabItem>
</Tabs>


### AI Tools

The chat assistant has access to several tools for retrieving information:

| Tool | Description |
|------|-------------|
| **GraphRAG Search** | Graph-enhanced retrieval that fuses knowledge graph traversal with vector search. Automatically prioritized when your database has extracted entities. Best for multi-hop questions spanning multiple documents. |
| **Semantic Search** | Vector similarity search across document chunks. Used for direct content retrieval. |
| **Graph Search** | Search for specific nodes and relationships in the knowledge graph. |
| **Summarize** | Retrieves document chunks, clusters them for representative selection, and generates a compressed summary using the LLM. Useful for condensing long documents or sets of sources. |

The system automatically selects the best tool based on your question and the available data. When a knowledge graph with entities exists, GraphRAG is prioritized for richer, more connected answers.

### Message Types

| Role | Description |
|------|-------------|
| **User** | Your questions and messages |
| **Assistant** | AI-generated responses with citations |
| **System** | Automatic messages (e.g., scope changes) |

## Scoped Chat

By default, chat searches across all enabled sources in the current database. **Scoped chat** restricts the AI's context to specific sources.

### Source Scoping

<Tabs>
<TabItem value="web-ui" label="Web UI">


Open the chat dropdown on a source in the Sources list to start a source-scoped conversation. The AI will only search content from those specific documents.

![Sources list with action buttons](/img/screenshots/sources-list.png)

</TabItem>
<TabItem value="cli" label="CLI">


```bash
# Scope to a specific document
chaoscypher chat -s "source-id-123" "Summarize this document"

# Scope to multiple sources
chaoscypher chat -s "source-1" -s "source-2" "Compare these papers"
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X PATCH http://localhost:8080/api/v1/chats/{chat_id} \
  -H "Content-Type: application/json" \
  -d '{"source_ids": ["source-id-123"]}'
```

</TabItem>
</Tabs>


### Tag Scoping

Scope by tags to include all sources with matching tags:

<Tabs>
<TabItem value="web-ui" label="Web UI">


Select tag(s) when creating or updating chat scope.

</TabItem>
<TabItem value="cli" label="CLI">


```bash
# Scope to all sources with a tag
chaoscypher chat -t "research" "What are the common themes?"

# Combine source and tag scoping
chaoscypher chat -s "source-1" -t "notes" "Find connections"
```

</TabItem>
<TabItem value="api" label="API">


```bash
curl -X PATCH http://localhost:8080/api/v1/chats/{chat_id} \
  -H "Content-Type: application/json" \
  -d '{"tag_ids": ["tag-id-1", "tag-id-2"]}'
```

</TabItem>
</Tabs>


### Combining Scopes

You can combine source IDs and tag IDs — the system merges them with deduplication.

### Clearing Scope

Remove the scope to return to searching all sources. Scope changes are logged as system messages in the conversation.

:::tip

Scoped chat is useful when you have many sources but want to ask questions about a specific document or topic. It reduces noise and ensures the AI focuses on relevant content.

:::

## Citations

When the AI references information from your documents, responses include citations — links back to the specific source chunks used. Click a citation to see:

- The source document name
- The exact text passage
- Page number and section (when available)

![Chat showing AI response with inline source citations](/img/screenshots/chat-conversation.png)

Citations help you verify the AI's answers against your original documents.

## Streaming

Chat responses use Server-Sent Events for real-time streaming. The stream sends several event types:

| Event | Description |
|-------|-------------|
| `content` | Text chunks as they're generated |
| `thinking_delta` | AI reasoning steps (when thinking is enabled) |
| `tool_calls` | Tool invocations during response generation |
| `tool_result` | Results from tool calls |
| `done` | Response complete |
| `error` | Error during generation |

If you close the browser during streaming, the response continues in the background and is saved to the conversation.

## LLM Configuration

Chat behavior is controlled through LLM settings:

- **Provider** — Ollama, OpenAI, Anthropic, or Gemini
- **Temperature** — Controls response creativity (default: 0.3)
- **Max tokens** — Maximum response length (default: 65536)
- **Thinking mode** — Enable to see the AI's reasoning process

Configure these in **Settings** or `settings.yaml`. See [Configuration](../getting-started/configuration.md) for details.
