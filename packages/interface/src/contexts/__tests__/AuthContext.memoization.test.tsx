// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render } from '@testing-library/react';
import { useContext, useRef } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { AuthContext } from '../authContextValue';
import { AuthProvider } from '../AuthContext';

// Mock the auth API service so AuthProvider's mount-time effects don't hit
// the network. AuthContext calls authApi.getStatus() on mount.
vi.mock('../../services/api/auth', () => ({
  authApi: {
    getStatus: vi.fn().mockResolvedValue({
      setup_needed: false,
      authenticated: true,
      username: 'testuser',
    }),
    getMe: vi.fn().mockResolvedValue({ username: 'testuser' }),
    login: vi.fn(),
    logout: vi.fn(),
  },
}));

describe('AuthContext memoization', () => {
  it('AuthContext.value is referentially stable across unrelated parent re-renders', () => {
    const captured: ('stable' | 'new')[] = [];

    function Consumer() {
      const value = useContext(AuthContext);
      const prev = useRef<typeof value | null>(null);
      captured.push(value === prev.current ? 'stable' : 'new');
      prev.current = value;
      return null;
    }

    const { rerender } = render(
      <AuthProvider>
        <Consumer />
      </AuthProvider>
    );

    rerender(
      <AuthProvider>
        <Consumer />
      </AuthProvider>
    );

    // First render: prev.current is null → 'new'.
    // Second render: if value is memoized, identity is preserved → 'stable'.
    expect(captured).toEqual(['new', 'stable']);
  });
});
