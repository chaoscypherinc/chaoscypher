// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, afterEach } from 'vitest';
import { copyToClipboard } from '../clipboard';

const originalClipboard = navigator.clipboard;

afterEach(() => {
  Object.defineProperty(navigator, 'clipboard', {
    value: originalClipboard,
    configurable: true,
  });
});

describe('copyToClipboard', () => {
  it('uses the async Clipboard API when available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    });
    expect(await copyToClipboard('hello')).toBe(true);
    expect(writeText).toHaveBeenCalledWith('hello');
  });

  it('falls back to execCommand when the API is unavailable', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
    });
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec as unknown as typeof document.execCommand;
    expect(await copyToClipboard('fallback text')).toBe(true);
    expect(exec).toHaveBeenCalledWith('copy');
  });

  it('reports failure instead of throwing', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockRejectedValue(new Error('denied')) },
      configurable: true,
    });
    const exec = vi.fn().mockReturnValue(false);
    document.execCommand = exec as unknown as typeof document.execCommand;
    expect(await copyToClipboard('x')).toBe(false);
  });
});
