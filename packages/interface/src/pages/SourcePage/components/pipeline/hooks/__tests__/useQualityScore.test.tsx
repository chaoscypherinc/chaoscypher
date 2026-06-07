// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useQualityScore after its TanStack Query migration.
 *
 * Mocks at the apiClient layer so the real quality service + query hook run
 * unchanged. Covers: the loaded path, the disabled gate, recalculateQuality
 * issuing a forced refetch (force_recalculate param), and the swallowed-error
 * path (score stays null rather than surfacing an error to the caller).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { installApiClientMock } from '../../../../../../test/mocks/apiClient';
import { makeWrapper } from '../../../../../../test/renderWithProviders';
import { apiClient } from '../../../../../../services/api/client';
import { useQualityScore } from '../useQualityScore';

vi.mock('../../../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

const SCORE = { grade: 'A', score: 0.95 };

describe('useQualityScore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads the quality score when enabled', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/quality/sources/s1') return Promise.resolve({ data: SCORE });
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useQualityScore('s1', true), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => {
      expect(result.current.qualityScore).toEqual(SCORE);
    });
    expect(result.current.qualityLoading).toBe(false);
  });

  it('does not fetch while disabled', async () => {
    const { result } = renderHook(() => useQualityScore('s1', false), {
      wrapper: makeWrapper(),
    });

    // Give any (incorrect) fetch a tick to fire.
    await waitFor(() => {
      expect(result.current.qualityLoading).toBe(false);
    });
    // The provider stack hits /auth/status; assert the quality endpoint
    // specifically was never queried while the hook is disabled.
    const qualityCalls = mockedApiClient.get.mock.calls.filter(
      (c: unknown[]) => c[0] === '/quality/sources/s1',
    );
    expect(qualityCalls).toHaveLength(0);
    expect(result.current.qualityScore).toBeNull();
  });

  it('recalculateQuality refetches with force_recalculate=true', async () => {
    mockedApiClient.get.mockResolvedValue({ data: SCORE });

    const { result } = renderHook(() => useQualityScore('s1', true), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => {
      expect(result.current.qualityScore).toEqual(SCORE);
    });

    await act(async () => {
      await result.current.recalculateQuality();
    });

    // The forced refetch passes force_recalculate via params.
    expect(mockedApiClient.get).toHaveBeenLastCalledWith(
      '/quality/sources/s1',
      expect.objectContaining({ params: { force_recalculate: true } }),
    );
  });

  it('keeps the score null when the fetch fails', async () => {
    mockedApiClient.get.mockRejectedValue(new Error('boom'));

    const { result } = renderHook(() => useQualityScore('s1', true), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => {
      expect(result.current.qualityLoading).toBe(false);
    });
    expect(result.current.qualityScore).toBeNull();
  });
});
