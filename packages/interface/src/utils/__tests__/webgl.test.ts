// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for isWebGLSupported — the guard the graph canvas uses to decide
 * whether Sigma (WebGL-only) can mount. Each case stubs
 * HTMLCanvasElement.prototype.getContext to simulate a browser/GPU state:
 * a working context, a webgl1-only context, no context at all, and a
 * getContext that throws (Firefox resistFingerprinting can do this).
 */

import { describe, it, expect, afterEach, vi } from 'vitest';
import { isWebGLSupported } from '../webgl';

const original = HTMLCanvasElement.prototype.getContext;

afterEach(() => {
  HTMLCanvasElement.prototype.getContext = original;
  vi.restoreAllMocks();
});

type GetContext = typeof HTMLCanvasElement.prototype.getContext;

describe('isWebGLSupported', () => {
  it('returns true when a webgl2 context is available', () => {
    HTMLCanvasElement.prototype.getContext = vi.fn((id: string) =>
      id === 'webgl2' ? ({} as RenderingContext) : null,
    ) as GetContext;
    expect(isWebGLSupported()).toBe(true);
  });

  it('falls back to webgl1 when webgl2 is unavailable', () => {
    HTMLCanvasElement.prototype.getContext = vi.fn((id: string) =>
      id === 'webgl' ? ({} as RenderingContext) : null,
    ) as GetContext;
    expect(isWebGLSupported()).toBe(true);
  });

  it('returns false when no context can be created', () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null) as GetContext;
    expect(isWebGLSupported()).toBe(false);
  });

  it('returns false when getContext throws (e.g. resistFingerprinting)', () => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => {
      throw new Error('blocked');
    }) as GetContext;
    expect(isWebGLSupported()).toBe(false);
  });
});
