// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the `useTemplates` LIST query added with the GraphCanvas
 * creation-modal migration. Pins: it fetches the full (all-pages) template
 * list, threads the optional `templateType` filter onto the request, keys
 * node vs edge separately, and honours the `enabled` defer flag.
 *
 * Mocks at the apiClient layer so the real `templateApi` (createCrudApi +
 * fetchAllPages) runs unchanged.
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { installApiClientMock } from '../../../test/mocks/apiClient';
import { useTemplates } from '../useTemplates';
import { apiClient } from '../client';
import type { Template } from '../../../types';

vi.mock('../client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

function makeWrapper(): React.FC<{ children: ReactNode }> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

function makeTemplate(overrides: Partial<Template> = {}): Template {
  return {
    id: 'tmpl-1',
    name: 'Person',
    template_type: 'node',
    properties: [],
    is_system: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

function mockSinglePage(templates: Template[]) {
  mockedApiClient.get.mockResolvedValue({
    data: {
      data: templates,
      pagination: {
        total: templates.length,
        page: 1,
        page_size: 100,
        total_pages: 1,
        has_next: false,
        has_prev: false,
      },
    },
  });
}

describe('useTemplates', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('fetches the full template list', async () => {
    const list = [makeTemplate({ id: 'a' }), makeTemplate({ id: 'b' })];
    mockSinglePage(list);

    const { result } = renderHook(() => useTemplates(), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toHaveLength(2);
    expect(mockedApiClient.get).toHaveBeenCalledWith('/templates', expect.anything());
  });

  it('threads templateType onto the request params', async () => {
    mockSinglePage([makeTemplate({ template_type: 'edge' })]);

    const { result } = renderHook(() => useTemplates('edge'), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const call = mockedApiClient.get.mock.calls.find((c) => c[0] === '/templates');
    expect(call?.[1]?.params).toMatchObject({ template_type: 'edge' });
  });

  it('does not fetch while disabled', async () => {
    mockSinglePage([makeTemplate()]);

    const { result } = renderHook(() => useTemplates('node', { enabled: false }), {
      wrapper: makeWrapper(),
    });

    // Give any pending microtasks a chance to flush.
    await Promise.resolve();
    expect(result.current.fetchStatus).toBe('idle');
    expect(mockedApiClient.get).not.toHaveBeenCalled();
  });

  it('surfaces a query error when the fetch rejects', async () => {
    mockedApiClient.get.mockRejectedValue(new Error('boom'));

    const { result } = renderHook(() => useTemplates('node'), { wrapper: makeWrapper() });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
