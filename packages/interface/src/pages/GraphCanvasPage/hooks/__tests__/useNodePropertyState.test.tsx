// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useNodePropertyState — the selected-node editor state hook.
 *
 * Pins the behaviour across the TanStack Query migration: it seeds the editor
 * from the (minimal) canvas node data, then overlays the richer
 * properties/tags from the full-node query and loads the template for property
 * field definitions. Mocks at the apiClient layer so the real `templateApi` /
 * `nodeApi` services and the `useTemplate` / `useNode` hooks run unchanged.
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { useNodePropertyState } from '../useNodePropertyState';
import type { GraphNodeData } from '../../types';
import { apiClient } from '../../../../services/api/client';

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

function makeWrapper(): React.FC<{ children: ReactNode }> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

const SELECTED: GraphNodeData = {
  nodeId: 'n-1',
  title: 'Canvas Title',
  content: { canvasProp: 'canvas' },
  templateId: 'tpl-1',
  tags: ['canvas-tag'],
  createdAt: '2026-01-01T00:00:00Z',
  updatedAt: '2026-01-02T00:00:00Z',
};

describe('useNodePropertyState', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('seeds the editor from the minimal canvas node data', () => {
    mockedApiClient.get.mockResolvedValue({ data: {} });
    const { result } = renderHook(() => useNodePropertyState(SELECTED), {
      wrapper: makeWrapper(),
    });

    expect(result.current.nodeTitle).toBe('Canvas Title');
    expect(result.current.nodeProperties).toEqual({ canvasProp: 'canvas' });
    expect(result.current.nodeTags).toEqual(['canvas-tag']);
    expect(result.current.hasChanges).toBe(false);
  });

  it('loads the template and overlays full-node properties/tags', async () => {
    mockedApiClient.get.mockImplementation((url: string) => {
      if (url === '/templates/tpl-1') {
        return Promise.resolve({ data: { id: 'tpl-1', name: 'Person', template_type: 'node', properties: [], is_system: false } });
      }
      if (url === '/nodes/n-1') {
        return Promise.resolve({
          data: { id: 'n-1', properties: { rich: 'value' }, tags: ['server-tag'] },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const { result } = renderHook(() => useNodePropertyState(SELECTED), {
      wrapper: makeWrapper(),
    });

    await waitFor(() => expect(result.current.template?.name).toBe('Person'));
    await waitFor(() => expect(result.current.nodeProperties).toEqual({ rich: 'value' }));
    expect(result.current.nodeTags).toEqual(['server-tag']);
  });

  it('tracks unsaved changes via handlePropertyChange and clearChanges', () => {
    mockedApiClient.get.mockResolvedValue({ data: {} });
    const { result } = renderHook(() => useNodePropertyState(SELECTED), {
      wrapper: makeWrapper(),
    });

    expect(result.current.hasChanges).toBe(false);
    act(() => result.current.handlePropertyChange('canvasProp', 'edited'));
    expect(result.current.hasChanges).toBe(true);
    expect(result.current.nodeProperties).toEqual({ canvasProp: 'edited' });

    act(() => result.current.clearChanges());
    expect(result.current.hasChanges).toBe(false);
  });

  it('adds and removes tags', () => {
    mockedApiClient.get.mockResolvedValue({ data: {} });
    const { result } = renderHook(() => useNodePropertyState(SELECTED), {
      wrapper: makeWrapper(),
    });

    act(() => result.current.setNewTag('extra'));
    act(() => result.current.handleAddTag());
    expect(result.current.nodeTags).toContain('extra');
    expect(result.current.hasChanges).toBe(true);

    act(() => result.current.handleDeleteTag('extra'));
    expect(result.current.nodeTags).not.toContain('extra');
  });

  it('returns an empty template and pristine state when no node is selected', () => {
    mockedApiClient.get.mockResolvedValue({ data: {} });
    const { result } = renderHook(() => useNodePropertyState(null), {
      wrapper: makeWrapper(),
    });

    expect(result.current.template).toBeNull();
    expect(result.current.nodeTitle).toBe('');
    expect(result.current.nodeProperties).toEqual({});
    expect(result.current.nodeTags).toEqual([]);
  });
});
