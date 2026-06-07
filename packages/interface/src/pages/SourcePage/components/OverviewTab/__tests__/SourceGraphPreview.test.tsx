// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for SourceGraphPreview — the Overview Knowledge-map card.
 *
 * The data hook and useNavigate are mocked; canvas paint is a no-op in jsdom
 * (getContext returns null), so we assert wrapper behaviour: gating, the
 * counts caption, the click-through to the filtered graph, and loading.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import type { Source } from '../../../../../types';
import type { SourceGraphPreviewData } from '../hooks/useSourceGraphPreview';

const navigate = vi.fn();
vi.mock('react-router', () => ({ useNavigate: () => navigate }));

const useSourceGraphPreview = vi.fn<() => SourceGraphPreviewData>();
vi.mock('../hooks/useSourceGraphPreview', () => ({
  useSourceGraphPreview: () => useSourceGraphPreview(),
}));

import { SourceGraphPreview } from '../SourceGraphPreview';

function makeSource(overrides: Partial<Source> = {}): Source {
  return {
    id: 's1',
    status: 'committed',
    extraction_entities_count: 12,
    ...overrides,
  } as Source;
}

function hookResult(overrides: Partial<SourceGraphPreviewData> = {}): SourceGraphPreviewData {
  return {
    nodes: [{ id: 'a', x: 0, y: 0, radius: 5, color: '#00e5ff', opacity: 0.5 }],
    edges: [],
    entityCount: 12,
    relationshipCount: 7,
    loading: false,
    isEmpty: false,
    ...overrides,
  };
}

beforeEach(() => {
  navigate.mockReset();
  useSourceGraphPreview.mockReset();
});

describe('SourceGraphPreview', () => {
  it('renders the map, counts, and click affordance for a committed source with entities', () => {
    useSourceGraphPreview.mockReturnValue(hookResult());
    render(<SourceGraphPreview source={makeSource()} />);

    expect(screen.getByTestId('source-graph-canvas')).toBeInTheDocument();
    expect(screen.getByText('Knowledge map')).toBeInTheDocument();
    expect(screen.getByText(/12 entities/)).toBeInTheDocument();
    expect(screen.getByText(/7 relationships/)).toBeInTheDocument();
    expect(screen.getByText('View full graph')).toBeInTheDocument();
  });

  it('navigates to the source-filtered graph when clicked', () => {
    useSourceGraphPreview.mockReturnValue(hookResult());
    render(<SourceGraphPreview source={makeSource({ id: 'abc' })} />);

    fireEvent.click(screen.getByTestId('source-graph-preview'));
    expect(navigate).toHaveBeenCalledWith('/graph?source_ids=abc');
  });

  it('navigates on Enter key as well (keyboard accessible)', () => {
    useSourceGraphPreview.mockReturnValue(hookResult());
    render(<SourceGraphPreview source={makeSource({ id: 'kb' })} />);

    fireEvent.keyDown(screen.getByTestId('source-graph-preview'), { key: 'Enter' });
    expect(navigate).toHaveBeenCalledWith('/graph?source_ids=kb');
  });

  it('renders nothing for a non-committed source', () => {
    useSourceGraphPreview.mockReturnValue(hookResult());
    const { container } = render(
      <SourceGraphPreview source={makeSource({ status: 'extracted' })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing for a committed source that produced no entities', () => {
    useSourceGraphPreview.mockReturnValue(hookResult({ isEmpty: true }));
    const { container } = render(
      <SourceGraphPreview source={makeSource({ extraction_entities_count: 0 })} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing once loaded if the source has no graph (isEmpty)', () => {
    useSourceGraphPreview.mockReturnValue(hookResult({ isEmpty: true, nodes: [] }));
    const { container } = render(<SourceGraphPreview source={makeSource()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a loading state while the subgraph is being fetched', () => {
    useSourceGraphPreview.mockReturnValue(hookResult({ loading: true, nodes: [], isEmpty: false }));
    render(<SourceGraphPreview source={makeSource()} />);
    expect(screen.getByText('Building map…')).toBeInTheDocument();
  });
});
