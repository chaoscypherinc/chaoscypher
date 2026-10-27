// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider, createTheme } from '@mui/material';
import { installApiClientMock } from '../../../../test/mocks/apiClient';
import { apiClient } from '../../../../services/api/client';
import { PropertiesPanel } from '../PropertiesPanel';
import type { GraphNodeData } from '../../types';
import { unresolvedBucketId } from '../../types';

/** The hub for unresolved nodes with no known source. */
const UNRESOLVED_BUCKET_ID = unresolvedBucketId(null);

vi.mock('../../../../services/api/client', () => installApiClientMock());

const mockedApiClient = apiClient as unknown as ReturnType<
  typeof installApiClientMock
>['apiClient'];

function Providers({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <QueryClientProvider client={qc}>
      <ThemeProvider theme={createTheme({ palette: { mode: 'dark' } })}>{children}</ThemeProvider>
    </QueryClientProvider>
  );
}

const HUB: GraphNodeData = {
  nodeId: UNRESOLVED_BUCKET_ID,
  title: 'Unresolved',
  content: {},
  templateId: '__unresolved__',
  tags: [],
  createdAt: '',
  updatedAt: '',
};

describe('PropertiesPanel', () => {
  it('shows the Unresolved hub read-only: what it holds, nothing to edit or delete', () => {
    mockedApiClient.get.mockResolvedValue({ data: {} });
    render(
      <PropertiesPanel
        open
        onClose={vi.fn()}
        selectedNodeId={UNRESOLVED_BUCKET_ID}
        selectedNodeData={HUB}
        selectedEdgeId={null}
        selectedEdgeData={null}
        onNodeUpdate={vi.fn()}
        onNodeDelete={vi.fn()}
        onEdgeDelete={vi.fn()}
        unresolvedCount={3}
      />,
      { wrapper: Providers },
    );

    expect(screen.getByText('Unresolved (3)')).toBeInTheDocument();
    expect(screen.getByText(/Double-click it to show or hide them/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Save/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Title')).not.toBeInTheDocument();
    expect(mockedApiClient.get).not.toHaveBeenCalled();
  });
});
