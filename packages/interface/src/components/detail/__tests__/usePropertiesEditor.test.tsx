// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { usePropertiesEditor } from '../usePropertiesEditor';

describe('usePropertiesEditor', () => {
  it('initializes with empty newPropertyKey', () => {
    const onChange = vi.fn();
    const { result } = renderHook(() => usePropertiesEditor({}, onChange));
    expect(result.current.newPropertyKey).toBe('');
  });

  it('handleChange calls onChange with updated properties', () => {
    const onChange = vi.fn();
    const { result } = renderHook(() => usePropertiesEditor({ a: 1 }, onChange));
    act(() => result.current.handleChange('b', 2));
    expect(onChange).toHaveBeenCalledWith({ a: 1, b: 2 });
  });

  it('handleChange tolerates undefined properties', () => {
    const onChange = vi.fn();
    const { result } = renderHook(() => usePropertiesEditor(undefined, onChange));
    act(() => result.current.handleChange('x', 'value'));
    expect(onChange).toHaveBeenCalledWith({ x: 'value' });
  });

  it('handleAdd adds new key with empty string value and clears input', () => {
    const onChange = vi.fn();
    const { result } = renderHook(() => usePropertiesEditor({ existing: 1 }, onChange));
    act(() => result.current.setNewPropertyKey('fresh'));
    act(() => result.current.handleAdd());
    expect(onChange).toHaveBeenCalledWith({ existing: 1, fresh: '' });
    expect(result.current.newPropertyKey).toBe('');
  });

  it('handleAdd ignores blank and whitespace-only keys', () => {
    const onChange = vi.fn();
    const { result } = renderHook(() => usePropertiesEditor({}, onChange));
    act(() => result.current.setNewPropertyKey('   '));
    act(() => result.current.handleAdd());
    expect(onChange).not.toHaveBeenCalled();
  });

  it('handleRemove removes a key immutably', () => {
    const onChange = vi.fn();
    const { result } = renderHook(() => usePropertiesEditor({ a: 1, b: 2 }, onChange));
    act(() => result.current.handleRemove('a'));
    expect(onChange).toHaveBeenCalledWith({ b: 2 });
  });

  it('handleRemove tolerates undefined properties', () => {
    const onChange = vi.fn();
    const { result } = renderHook(() => usePropertiesEditor(undefined, onChange));
    act(() => result.current.handleRemove('nonexistent'));
    expect(onChange).toHaveBeenCalledWith({});
  });
});
