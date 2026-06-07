// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Shared Axios-instance mock for tests.
 *
 * Use in a test file like so:
 *
 *   import { installApiClientMock } from '../../test/mocks/apiClient';
 *
 *   vi.mock('../../services/api/client', () => installApiClientMock());
 *
 * Every method resolves to `{ data: {} }` by default. Individual tests can
 * override a response with `mockedApiClient.get.mockResolvedValueOnce(...)`.
 */

import { vi } from 'vitest';

interface MockApiClient {
  get: ReturnType<typeof vi.fn>;
  post: ReturnType<typeof vi.fn>;
  put: ReturnType<typeof vi.fn>;
  patch: ReturnType<typeof vi.fn>;
  delete: ReturnType<typeof vi.fn>;
  request: ReturnType<typeof vi.fn>;
  interceptors: {
    request: { use: ReturnType<typeof vi.fn>; eject: ReturnType<typeof vi.fn> };
    response: { use: ReturnType<typeof vi.fn>; eject: ReturnType<typeof vi.fn> };
  };
}

/**
 * Build a fresh mock client. Called once per test module's `vi.mock` factory.
 * Because `vi.mock` is hoisted, the object must be built inside the factory
 * rather than imported from a singleton.
 */
function buildMockApiClient(): MockApiClient {
  const emptyOk = { data: {} };
  return {
    get: vi.fn().mockResolvedValue(emptyOk),
    post: vi.fn().mockResolvedValue(emptyOk),
    put: vi.fn().mockResolvedValue(emptyOk),
    patch: vi.fn().mockResolvedValue(emptyOk),
    delete: vi.fn().mockResolvedValue(emptyOk),
    request: vi.fn().mockResolvedValue(emptyOk),
    interceptors: {
      request: { use: vi.fn().mockReturnValue(0), eject: vi.fn() },
      response: { use: vi.fn().mockReturnValue(0), eject: vi.fn() },
    },
  };
}

/**
 * Return the shape `vi.mock('../../services/api/client', factory)` expects —
 * both a named `apiClient` export and a `default` export pointing to the same
 * instance (matching `src/services/api/client.ts`). Also exports a synchronous
 * stub of `isApiError` so consumers that import it (e.g. LexiconPage's 503
 * detection) continue to work under mock.
 */
export function installApiClientMock(): {
  apiClient: MockApiClient;
  default: MockApiClient;
  isApiError: (e: unknown) => boolean;
  API_BASE: string;
} {
  const client = buildMockApiClient();
  // Mirror the real isApiError: matches the duck-typed marker field that
  // normalizeError() attaches to thrown errors. Tests can throw a plain
  // object `{ isApiError: true, status: 503 }` and have downstream code
  // recognize it without standing up a real ApiClientError.
  const isApiError = (e: unknown): boolean =>
    !!(e && typeof e === 'object' && (e as { isApiError?: unknown }).isApiError === true);
  // `API_BASE` is a named export of the real client module. Service modules
  // that build URLs from it (e.g. `chatApi.getEventsUrl`) import it directly,
  // so the mock must provide it or those modules throw at import-resolution.
  return { apiClient: client, default: client, isApiError, API_BASE: '/api/v1' };
}
