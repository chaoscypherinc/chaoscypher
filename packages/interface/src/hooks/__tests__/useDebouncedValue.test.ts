// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useDebouncedValue } from '../useDebouncedValue';

describe('useDebouncedValue', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('yields a value only once it has stopped changing for the delay', () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(({ value }) => useDebouncedValue(value, 800), {
      initialProps: { value: 'h' },
    });

    rerender({ value: 'https://docs' });
    act(() => vi.advanceTimersByTime(500));
    rerender({ value: 'https://docs.example.com' });
    act(() => vi.advanceTimersByTime(500));
    // Typing restarted the wait: still the first value.
    expect(result.current).toBe('h');

    act(() => vi.advanceTimersByTime(300));
    expect(result.current).toBe('https://docs.example.com');
  });
});
