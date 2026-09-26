// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for the graph-canvas SearchBar.
 *
 * `onSearch` drives useSearchHighlight, which swaps a brand-new Set into
 * the sigma reducer effect — every call re-runs nodeReducer for every node
 * and edgeReducer for every edge. So the callback is debounced by
 * `search_debounce_ms` (the same setting the omnibar and lexicon honour)
 * while the typed value stays immediate, and the clear button fires
 * straight through.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';

vi.mock('../../../../contexts/useAppConfig', () => ({
  useAppConfig: () => ({ search_debounce_ms: 300 }),
}));

// Import after the mock is registered.
import { SearchBar } from '../SearchBar';

const DEBOUNCE_MS = 300;

/** Advance past the debounce window. */
function flushDebounce() {
  act(() => {
    vi.advanceTimersByTime(DEBOUNCE_MS);
  });
}

function typeInto(value: string) {
  const input = screen.getByPlaceholderText('Search graph...');
  fireEvent.change(input, { target: { value } });
  return input;
}

describe('<SearchBar />', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
  });

  it('does not call onSearch before the debounce window elapses', () => {
    const onSearch = vi.fn();
    render(<SearchBar onSearch={onSearch} />);

    typeInto('a');
    act(() => {
      vi.advanceTimersByTime(DEBOUNCE_MS - 1);
    });

    expect(onSearch).not.toHaveBeenCalled();
  });

  it('coalesces a burst of keystrokes into a single onSearch call', () => {
    const onSearch = vi.fn();
    render(<SearchBar onSearch={onSearch} />);

    for (const value of ['a', 'ac', 'acm', 'acme']) {
      typeInto(value);
      act(() => {
        vi.advanceTimersByTime(50);
      });
    }
    flushDebounce();

    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith('acme');
  });

  it('keeps the typed value visible immediately', () => {
    const onSearch = vi.fn();
    render(<SearchBar onSearch={onSearch} />);

    const input = typeInto('acme') as HTMLInputElement;

    expect(input.value).toBe('acme');
    expect(onSearch).not.toHaveBeenCalled();
  });

  it('clears immediately and drops the pending rescan', () => {
    const onSearch = vi.fn();
    render(<SearchBar onSearch={onSearch} />);

    typeInto('acme');
    fireEvent.click(screen.getByLabelText('Clear search'));

    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith('');

    // The debounced 'acme' must not land after the clear.
    flushDebounce();
    expect(onSearch).toHaveBeenCalledTimes(1);
  });
});
