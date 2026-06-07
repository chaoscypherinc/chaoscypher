// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';

// A logged-in user gives the wizard its per-user sessionStorage key.
vi.mock('../../../contexts/useAuth', () => ({
  useAuth: () => ({ user: { username: 'alice' } }),
}));
// Keep the cloud-model / preset seeding effects inert so the test is
// deterministic and offline.
vi.mock('../../../services/api/settings', () => ({
  settingsApi: {
    getCloudModels: vi.fn().mockResolvedValue({ providers: {} }),
    listPresets: vi.fn().mockResolvedValue({ presets: [] }),
    update: vi.fn().mockResolvedValue({}),
  },
}));

import { useSetupWizard } from '../useSetupWizard';
import { makeWrapper } from '../../../test/renderWithProviders';

const SESSION_KEY = 'setup-wizard-state:alice';

beforeEach(() => {
  sessionStorage.clear();
});

describe('useSetupWizard — sessionStorage secret handling', () => {
  it('never persists a typed API key to sessionStorage', async () => {
    const { result } = renderHook(() => useSetupWizard({ initialStep: 1 }), {
      wrapper: makeWrapper(),
    });

    // The seed-from-context effect populates the working draft.
    await waitFor(() => expect(result.current.working).not.toBeNull());

    act(() => {
      const w = result.current.working;
      if (!w) throw new Error('working draft not seeded');
      result.current.setWorking({
        ...w,
        llm: { ...w.llm, openai_api_key: 'sk-LEAK-123' },
      });
    });

    // The draft itself carries the key (so Finish can PATCH it)...
    await waitFor(() => expect(result.current.working?.llm.openai_api_key).toBe('sk-LEAK-123'));

    // ...but it must never reach sessionStorage in cleartext.
    const stored = sessionStorage.getItem(SESSION_KEY);
    expect(stored).not.toBeNull();
    expect(stored).not.toContain('sk-LEAK-123');
    expect(JSON.parse(stored as string).llm.openai_api_key).toBeUndefined();
  });
});
