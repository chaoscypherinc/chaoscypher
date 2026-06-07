// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { installApiClientMock } from '../../../test/mocks/apiClient';
import { makeWrapper } from '../../../test/renderWithProviders';
import { apiClient } from '../client';
import { useConfirmExtraction } from '../useSources';

vi.mock('../client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

describe('useConfirmExtraction', () => {
  beforeEach(() => vi.clearAllMocks());

  it('POSTs the chosen domain + overrides to /sources/{id}/confirmation', async () => {
    mockedApiClient.post.mockResolvedValue({ data: { source_id: 'src-1', status: 'indexed' } });

    const { result } = renderHook(() => useConfirmExtraction(), { wrapper: makeWrapper() });

    await result.current.mutateAsync({
      sourceId: 'src-1',
      options: { domain: 'science', analysis_depth: 'full', filtering_mode: 'balanced' },
    });

    expect(mockedApiClient.post).toHaveBeenCalledWith(
      '/sources/src-1/confirmation',
      { domain: 'science', analysis_depth: 'full', filtering_mode: 'balanced' },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});
