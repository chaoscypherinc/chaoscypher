// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Render + behavior tests for the omnibar SearchMode results renderer.
 *
 * Covers: short-query early-out, the debounced parallel search
 * (searchApi.hybrid + sourcesApi.list), grouped result rendering and
 * onItemCount reporting, selected-row highlighting, click-to-navigate
 * (with recent-item recording), Enter-to-execute, and the loading /
 * empty / error states.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import type { SearchResult } from '../../../../types';
import type { ModeResultsProps } from '../../types';

/**
 * `highlightMatch` wraps the matched substring of a title in a <mark>, so
 * the title text is split across several DOM nodes. RTL's default text
 * matcher only inspects a single node, so we match on the *full*
 * textContent of an element instead.
 */
function byFullText(expected: string) {
  return (_content: string, element: Element | null): boolean =>
    element?.textContent === expected;
}

// --- Mocks (one directory deeper than the component: ../../ -> ../../../../) ---

// `vi.mock` factories are hoisted above module-level `const`s, so anything
// they close over must be created inside `vi.hoisted` to exist when the
// factory runs. (Without this the factory captures an uninitialised binding
// and silently no-ops, e.g. addRecentItem never firing.)
const { hybridMock, listMock, addRecentItemMock, navigateMock } = vi.hoisted(() => ({
  hybridMock: vi.fn<(query: string, limit?: number) => Promise<SearchResult[]>>(),
  listMock:
    vi.fn<(params?: { search?: string; page_size?: number }) => Promise<{ data: unknown[] }>>(),
  addRecentItemMock: vi.fn<(item: unknown) => void>(),
  navigateMock: vi.fn<(to: string) => void>(),
}));

vi.mock('../../../../services/api/search', () => ({
  searchApi: { hybrid: hybridMock },
}));

vi.mock('../../../../services/api/sources', () => ({
  sourcesApi: { list: listMock },
}));

vi.mock('../../../../contexts/useAppConfig', () => ({
  useAppConfig: () => ({
    search_omnibar_entity_limit: 10,
    search_omnibar_source_limit: 5,
  }),
}));

vi.mock('../../useRecentItems', () => ({
  useRecentItems: () => ({
    items: [],
    addRecentItem: addRecentItemMock,
    clearRecentItems: vi.fn(),
  }),
}));

vi.mock('react-router', () => ({
  useNavigate: () => navigateMock,
}));

// Import after mocks are registered.
import { SearchMode } from '../SearchMode';

const DEBOUNCE_MS = 300;

// --- Test data builders ---

function entityResult(id: string, label: string, edgeCount = 3): SearchResult {
  return {
    result_type: 'node',
    score: 0.9,
    node: { id, label, template_id: null, edge_count: edgeCount },
  };
}

function chunkResult(id: string, sourceId: string, content: string): SearchResult {
  return {
    result_type: 'chunk',
    score: 0.7,
    chunk: {
      chunk_id: id,
      source_id: sourceId,
      chunk_index: 0,
      content,
      page_number: 4,
      filename: 'doc.pdf',
    },
  };
}

function sourceSummary(id: string, title: string) {
  return {
    id,
    title,
    filename: `${title}.pdf`,
    source_type: 'pdf',
    status: 'active',
    chunk_count: 12,
  } as unknown;
}

type Overrides = Partial<ModeResultsProps>;

function renderMode(overrides: Overrides = {}) {
  const props: ModeResultsProps = {
    query: 'acme',
    selectedIndex: 0,
    onExecute: vi.fn<(index: number) => void>(),
    onClose: vi.fn<() => void>(),
    onItemCount: vi.fn<(count: number) => void>(),
    ...overrides,
  };
  const utils = render(<SearchMode {...props} />);
  return { ...utils, props };
}

/** Advance past the debounce window and flush the resolved promises. */
async function flushDebounce() {
  await act(async () => {
    vi.advanceTimersByTime(DEBOUNCE_MS);
    // Let the awaited Promise.all + setState microtasks settle.
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('<SearchMode />', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    hybridMock.mockReset();
    listMock.mockReset();
    addRecentItemMock.mockReset();
    navigateMock.mockReset();
    // Sensible defaults; individual tests override.
    hybridMock.mockResolvedValue([]);
    listMock.mockResolvedValue({ data: [] });
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('renders nothing and reports zero count for a query shorter than 2 chars', () => {
    const { container, props } = renderMode({ query: 'a' });
    expect(container.firstChild).toBeNull();
    expect(props.onItemCount).toHaveBeenCalledWith(0);
    expect(hybridMock).not.toHaveBeenCalled();
    expect(listMock).not.toHaveBeenCalled();
  });

  it('shows the loading state before the debounce fires', () => {
    hybridMock.mockResolvedValue([]);
    listMock.mockResolvedValue({ data: [] });
    renderMode({ query: 'acme' });
    // Debounce timer not advanced yet -> loading with no results.
    expect(screen.getByText('Searching...')).toBeInTheDocument();
    expect(hybridMock).not.toHaveBeenCalled();
  });

  it('runs the debounced parallel search and renders grouped results', async () => {
    hybridMock.mockResolvedValue([
      entityResult('n1', 'Acme Corp'),
      chunkResult('c1', 's9', 'Acme is a company that does things'),
    ]);
    listMock.mockResolvedValue({ data: [sourceSummary('s1', 'Acme Report')] });

    const { props } = renderMode({ query: 'acme' });
    await flushDebounce();

    // API contract: both services called with the query + configured limits.
    expect(hybridMock).toHaveBeenCalledWith('acme', 10);
    expect(listMock).toHaveBeenCalledWith({ search: 'acme', page_size: 5 });

    // Grouped category headers render.
    expect(screen.getByText('Entities')).toBeInTheDocument();
    expect(screen.getByText('Sources')).toBeInTheDocument();
    expect(screen.getByText('Chunks')).toBeInTheDocument();

    // Item titles render. highlightMatch splits the matched substring into a
    // <mark>, so we match against the full element textContent.
    expect(screen.getAllByText(byFullText('Acme Report')).length).toBeGreaterThan(0);

    // The non-highlighted subtitle is a reliable single-node assertion.
    expect(screen.getByText(/Entity · 3 connections/)).toBeInTheDocument();

    // onItemCount reports the total across all three groups.
    expect(props.onItemCount).toHaveBeenLastCalledWith(3);
  });

  it('highlights the row matching selectedIndex via data-selected', async () => {
    hybridMock.mockResolvedValue([
      entityResult('n1', 'Alpha'),
      entityResult('n2', 'Beta'),
    ]);
    listMock.mockResolvedValue({ data: [] });

    const { container } = renderMode({ query: 'al', selectedIndex: 1 });
    await flushDebounce();

    const selectedRows = container.querySelectorAll('[data-selected="true"]');
    expect(selectedRows).toHaveLength(1);
    // The Enter affordance (↵) only renders on the selected row.
    expect(screen.getByText('↵')).toBeInTheDocument();
  });

  it('navigates to the node detail and records a recent item when an entity is clicked', async () => {
    hybridMock.mockResolvedValue([entityResult('n1', 'Acme Corp')]);
    listMock.mockResolvedValue({ data: [] });

    const { props } = renderMode({ query: 'acme', selectedIndex: 0 });
    await flushDebounce();

    // Locate the row via its (un-highlighted) subtitle text.
    const row = screen
      .getByText(/Entity · 3 connections/)
      .closest('[data-selected]') as HTMLElement;
    expect(row).not.toBeNull();
    fireEvent.click(row);

    expect(navigateMock).toHaveBeenCalledWith('/nodes/n1');
    expect(addRecentItemMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'n1', type: 'entity', title: 'Acme Corp' }),
    );
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('navigates to the source detail when a source result is clicked', async () => {
    hybridMock.mockResolvedValue([]);
    listMock.mockResolvedValue({ data: [sourceSummary('s7', 'Acme Report')] });

    const { props } = renderMode({ query: 'acme' });
    await flushDebounce();

    const row = screen
      .getByText(/pdf · 12 chunks · active/)
      .closest('[data-selected]') as HTMLElement;
    fireEvent.click(row);

    expect(navigateMock).toHaveBeenCalledWith('/sources/s7');
    expect(addRecentItemMock).toHaveBeenCalledWith(
      expect.objectContaining({ id: 's7', type: 'source' }),
    );
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('navigates to the source with a chunk highlight when a chunk result is clicked', async () => {
    hybridMock.mockResolvedValue([chunkResult('c1', 's9', 'some chunk body text here')]);
    listMock.mockResolvedValue({ data: [] });

    renderMode({ query: 'chunk' });
    await flushDebounce();

    const chunkRow = screen
      .getByText(/doc\.pdf · Page 4/)
      .closest('[data-selected]') as HTMLElement;
    fireEvent.click(chunkRow);

    expect(navigateMock).toHaveBeenCalledWith('/sources/s9?highlight=c1');
  });

  it('executes the selected item on Enter keydown', async () => {
    hybridMock.mockResolvedValue([
      entityResult('n1', 'First'),
      entityResult('n2', 'Second'),
    ]);
    listMock.mockResolvedValue({ data: [] });

    const { props } = renderMode({ query: 'fi', selectedIndex: 1 });
    await flushDebounce();

    act(() => {
      fireEvent.keyDown(window, { key: 'Enter' });
    });

    expect(navigateMock).toHaveBeenCalledWith('/nodes/n2');
    expect(props.onClose).toHaveBeenCalledTimes(1);
  });

  it('renders the empty state when the search returns no results', async () => {
    hybridMock.mockResolvedValue([]);
    listMock.mockResolvedValue({ data: [] });

    const { props } = renderMode({ query: 'nothingmatches' });
    await flushDebounce();

    expect(screen.getByText(/No results for/)).toBeInTheDocument();
    expect(props.onItemCount).toHaveBeenLastCalledWith(0);
  });

  it('falls back to the empty state and reports zero when the search rejects', async () => {
    hybridMock.mockRejectedValue(new Error('boom'));
    listMock.mockResolvedValue({ data: [] });

    const { props } = renderMode({ query: 'acme' });
    await flushDebounce();

    expect(screen.getByText(/No results for/)).toBeInTheDocument();
    expect(props.onItemCount).toHaveBeenLastCalledWith(0);
  });

  it('treats a missing sources data array as an empty source group', async () => {
    hybridMock.mockResolvedValue([entityResult('n1', 'Acme Corp')]);
    // data omitted -> component uses `?? []`.
    listMock.mockResolvedValue({} as { data: unknown[] });

    const { props } = renderMode({ query: 'acme' });
    await flushDebounce();

    expect(screen.getByText(/Entity · 3 connections/)).toBeInTheDocument();
    expect(screen.queryByText('Sources')).not.toBeInTheDocument();
    expect(props.onItemCount).toHaveBeenLastCalledWith(1);
  });
});
