// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useOverviewData after its TanStack Query migration.
 *
 * Mocks at the apiClient layer so the real sources service + query hook run
 * unchanged. Covers the loaded-data path (templateList + the derived
 * typeToTemplate lookup) and the error path (empty map, no throw).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { installApiClientMock } from '../../../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../../../test/renderWithProviders';
import { apiClient } from '../../../../../../services/api/client';
import { useOverviewData } from '../useOverviewData';

vi.mock('../../../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const TEMPLATES = [
  { id: 't1', name: 'Person', color: '#fff', icon: 'Person' },
  { id: 't2', name: 'Organization', color: '#000', icon: 'Business' },
];

describe('useOverviewData', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads templates and derives the typeToTemplate lookup', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/sources/s1/templates') {
        return Promise.resolve({ data: { templates: TEMPLATES, pagination: {} } });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useOverviewData('s1'), { wrapper: makeWrapper() });

    await waitFor(() => {
      expect(result.current.templateList).toHaveLength(2);
    });

    // typeToTemplate is keyed by lowercased clean name.
    expect(result.current.typeToTemplate.get('person')?.id).toBe('t1');
    expect(result.current.typeToTemplate.get('organization')?.id).toBe('t2');
  });

  it('returns an empty list + map when the fetch fails', async () => {
    mockedApiClient.get.mockRejectedValue(new Error('boom'));

    const { result } = renderHook(() => useOverviewData('s1'), { wrapper: makeWrapper() });

    // Stays empty; no throw to the caller.
    await waitFor(() => {
      expect(result.current.templateList).toEqual([]);
    });
    expect(result.current.typeToTemplate.size).toBe(0);
  });
});
