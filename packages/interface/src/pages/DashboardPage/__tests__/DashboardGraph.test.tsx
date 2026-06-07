// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * DashboardGraph (data-crystal background) tests.
 *
 * Canvas 2D is not rendered by jsdom, so we do NOT pixel-test canvas output —
 * the crystal/orbit crossfade lives inside the draw loop, not in DOM styles.
 * Instead we assert that the single scene canvas mounts and survives the
 * loading → loaded transition without throwing.
 *
 * useGraphData is mocked so no real HTTP requests are made.
 */

import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

vi.mock('../useGraphData', () => ({
  useGraphData: vi.fn(),
}));

import { useGraphData } from '../useGraphData';
import DashboardGraph from '../DashboardGraph';

type MockGraph = ReturnType<typeof useGraphData>;

const EMPTY: MockGraph = { nodes: [], edges: [], loading: false, totalNodes: 0, totalEdges: 0 };
const WITH_DATA: MockGraph = {
  nodes: [
    { id: 'n1', x: 10, y: -20, z: 0.1, radius: 3, color: '#00e5ff', opacity: 0.4 },
    { id: 'n2', x: -15, y: 30, radius: 2, color: '#ff0080', opacity: 0.3 },
  ],
  edges: [{ source: 'n1', target: 'n2', color: '#00e5ff', opacity: 0.5 }],
  loading: false,
  totalNodes: 2,
  totalEdges: 1,
};

function makeWrapper(): React.FC<{ children: ReactNode }> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Wrapper({ children }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };
}

const mockUseGraphData = useGraphData as ReturnType<typeof vi.fn>;

describe('DashboardGraph', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the scene canvas while loading / empty', () => {
    mockUseGraphData.mockReturnValue(EMPTY);
    const { getByTestId, queryByTestId } = render(<DashboardGraph />, { wrapper: makeWrapper() });

    expect(getByTestId('dashboard-graph-canvas')).toBeTruthy();
    // The old two-canvas crossfade is gone — only one unified scene canvas.
    expect(queryByTestId('dashboard-placeholder-canvas')).toBeNull();
  });

  it('renders the scene canvas when real graph data is present', () => {
    mockUseGraphData.mockReturnValue(WITH_DATA);
    const { getByTestId } = render(<DashboardGraph />, { wrapper: makeWrapper() });

    expect(getByTestId('dashboard-graph-canvas')).toBeTruthy();
  });

  it('survives the empty → data transition without throwing', () => {
    mockUseGraphData.mockReturnValue(EMPTY);
    const { getByTestId, rerender } = render(<DashboardGraph />, { wrapper: makeWrapper() });
    expect(getByTestId('dashboard-graph-canvas')).toBeTruthy();

    // Data arrives — the same canvas stays mounted as orbiting data fades in.
    mockUseGraphData.mockReturnValue(WITH_DATA);
    expect(() => rerender(<DashboardGraph />)).not.toThrow();
    expect(getByTestId('dashboard-graph-canvas')).toBeTruthy();
  });
});
