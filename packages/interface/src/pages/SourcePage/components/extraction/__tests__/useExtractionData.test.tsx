// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useExtractionData after its TanStack Query migration.
 *
 * Mocks at the apiClient layer so the real sources service + query hooks run
 * unchanged. Covers: the eager all-templates lookup map, the active sub-tab
 * gating (only the selected tab's list fetches), switching tabs, and the
 * empty/error path (lists stay empty, no throw).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { installApiClientMock } from '../../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../../test/renderWithProviders';
import { apiClient } from '../../../../../services/api/client';
import { useExtractionData } from '../useExtractionData';

vi.mock('../../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const ENTITIES = [{ id: 'e1', name: 'Alice' }];
const RELATIONSHIPS = [{ id: 'r1', label: 'knows' }];
const TEMPLATES = [{ id: 't1', name: 'Person', source_id: 's1' }];

function mockRoutes() {
  mockedApiClient.get.mockImplementation((url: string) => {
    if (url === '/sources/s1/entities') {
      return Promise.resolve({ data: { entities: ENTITIES, pagination: {} } });
    }
    if (url === '/sources/s1/relationships') {
      return Promise.resolve({ data: { relationships: RELATIONSHIPS, pagination: {} } });
    }
    if (url === '/sources/s1/templates') {
      return Promise.resolve({ data: { templates: TEMPLATES, pagination: {} } });
    }
    return Promise.resolve({ data: {} });
  });
}

describe('useExtractionData', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads entities for the default sub-tab and builds the template lookup map', async () => {
    mockRoutes();

    const { result } = renderHook(() => useExtractionData('s1'), { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(result.current.entities).toEqual(ENTITIES);
    });

    // Eager all-templates fetch populates the name lookup regardless of sub-tab.
    expect(result.current.templateNameMap.get('person')?.id).toBe('t1');
    // Relationships tab hasn't been activated, so its list is empty.
    expect(result.current.relationships).toEqual([]);
  });

  it('loads relationships only after switching to the relationships sub-tab', async () => {
    mockRoutes();

    const { result } = renderHook(() => useExtractionData('s1'), { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(result.current.entities).toEqual(ENTITIES);
    });

    act(() => {
      result.current.setSubTab(1);
    });

    await waitFor(() => {
      expect(result.current.relationships).toEqual(RELATIONSHIPS);
    });

    const relCalls = mockedApiClient.get.mock.calls.filter(
      (c: unknown[]) => c[0] === '/sources/s1/relationships',
    );
    expect(relCalls.length).toBeGreaterThan(0);
  });

  it('loads templates after switching to the templates sub-tab', async () => {
    mockRoutes();

    const { result } = renderHook(() => useExtractionData('s1'), { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(result.current.entities).toEqual(ENTITIES);
    });

    act(() => {
      result.current.setSubTab(2);
    });

    await waitFor(() => {
      expect(result.current.templates).toEqual(TEMPLATES);
    });
  });

  it('keeps lists empty when fetches fail', async () => {
    mockedApiClient.get.mockRejectedValue(new Error('boom'));

    const { result } = renderHook(() => useExtractionData('s1'), { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });
    expect(result.current.entities).toEqual([]);
    expect(result.current.templateNameMap.size).toBe(0);
  });
});
