// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
// Side-effect import: registers jest-dom matchers AND augments Vitest's
// `expect` types so `.toBeInTheDocument()` etc. type-check in tests.
import '@testing-library/jest-dom/vitest';

// Cleanup after each test
afterEach(() => {
  cleanup();
});

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

interface MutableGlobal {
  IntersectionObserver: unknown;
  ResizeObserver: unknown;
}

// Mock IntersectionObserver
(globalThis as unknown as MutableGlobal).IntersectionObserver = class IntersectionObserver {
  constructor() {}
  disconnect() {}
  observe() {}
  takeRecords() {
    return [];
  }
  unobserve() {}
};

// Mock ResizeObserver
(globalThis as unknown as MutableGlobal).ResizeObserver = class ResizeObserver {
  constructor() {}
  disconnect() {}
  observe() {}
  unobserve() {}
};

// jsdom does not implement Element.prototype.scrollIntoView; several
// components call it (e.g., chat auto-scroll). Stub it as a no-op so tests
// don't crash on "scrollIntoView is not a function".
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}
