// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Replace `globalThis.fetch` with a stub that returns an immediately-closed
 * ReadableStream. Used by tests that render components depending on SSE
 * streaming (ChatPage / useChat) so the component doesn't hang on a real
 * network request.
 */

import { vi, afterEach } from 'vitest';

interface InstalledFetchMock {
  fetch: ReturnType<typeof vi.fn>;
}

export function installFetchMock(): InstalledFetchMock {
  const fetchMock = vi.fn().mockImplementation(() => {
    const stream = new ReadableStream({
      start(controller) {
        controller.close();
      },
    });
    return Promise.resolve(
      new Response(stream, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      }),
    );
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  return { fetch: fetchMock };
}
