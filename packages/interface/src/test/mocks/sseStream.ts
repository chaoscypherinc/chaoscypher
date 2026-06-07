// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Test helper for stubbing the chat SSE transport.
 *
 * `useChatStream` opens the events endpoint via `fetch(getEventsUrl(id))`
 * (NOT through `apiClient`) and reads `response.body.getReader()`, splitting
 * on newlines and parsing `data: {json}` lines. These helpers let a test
 * drive that exact boundary: build a `Response` whose body is a
 * `ReadableStream` emitting a scripted sequence of SSE events, and install a
 * `globalThis.fetch` mock that serves it for the `/events` request while
 * 404-ing (or erroring) anything else.
 */

import { vi, afterEach } from 'vitest';

/** A single SSE event payload (the parsed object the worker would emit). */
export type SseEvent = Record<string, unknown>;

/**
 * Build a `Response` (status 200, text/event-stream) whose body streams the
 * given events. Events are enqueued one chunk each so the reader loop in
 * `useChatStream` iterates multiple times, mirroring real incremental
 * delivery. The stream then closes (the `done` event, if present, is just a
 * normal data frame — closing the body is what ends the read loop).
 */
export function makeSseResponse(events: SseEvent[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
      }
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  });
}

/** A never-closing SSE response — used to assert that pre-`done` partials
 * render before the stream finishes (e.g. the tool-approval pause, where the
 * server holds the connection open until a decision arrives). The returned
 * `push`/`close` let the test feed events on demand; `abortSignal`, if
 * provided, closes the stream when the consumer aborts. */
export interface ControlledSseResponse {
  response: Response;
  push: (event: SseEvent) => void;
  close: () => void;
}

export function makeControlledSseResponse(): ControlledSseResponse {
  const encoder = new TextEncoder();
  let ctrl: ReadableStreamDefaultController<Uint8Array> | null = null;
  let closed = false;
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      ctrl = controller;
    },
  });
  return {
    response: new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }),
    push: (event: SseEvent) => {
      if (closed || !ctrl) return;
      ctrl.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`));
    },
    close: () => {
      if (closed || !ctrl) return;
      closed = true;
      ctrl.close();
    },
  };
}

/**
 * Install a `globalThis.fetch` mock specialised for chat-stream tests.
 *
 * `onEvents(url)` is consulted for any request whose URL contains `/events`;
 * it must return a `Response` (use `makeSseResponse` / `makeControlledSseResponse`).
 * Any other request resolves to an empty 200 (real CRUD goes through the
 * mocked `apiClient`, not `fetch`, so it never reaches here).
 *
 * Restores the original `fetch` after each test.
 */
export function installSseFetchMock(
  onEvents: (url: string) => Response,
): { fetch: ReturnType<typeof vi.fn> } {
  const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url.includes('/events')) {
      return Promise.resolve(onEvents(url));
    }
    return Promise.resolve(new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } }));
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  return { fetch: fetchMock };
}
