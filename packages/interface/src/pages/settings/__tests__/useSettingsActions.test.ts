// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type PropsWithChildren } from 'react';
import type { Settings } from '../../../types';

function makeWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper({ children }: PropsWithChildren) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

// ---------------------------------------------------------------------------
// Service mocks
// ---------------------------------------------------------------------------

const mockSettingsApi = {
  get: vi.fn(),
  update: vi.fn(),
  reset: vi.fn(),
};

const mockDataApi = {
  export: vi.fn(),
  import: vi.fn(),
};

const mockDatabaseApi = {
  list: vi.fn(),
  create: vi.fn(),
  delete: vi.fn(),
  switch: vi.fn(),
};

vi.mock('../../../services/api/settings', () => ({
  settingsApi: mockSettingsApi,
}));

vi.mock('../../../services/api/data', () => ({
  dataApi: mockDataApi,
}));

vi.mock('../../../services/api/databases', () => ({
  databaseApi: mockDatabaseApi,
}));

// useSettings context — returns a no-op refreshSettings
vi.mock('../../../contexts/useSettings', () => ({
  useSettings: () => ({ refreshSettings: vi.fn() }),
}));

// useConfirmDialog — minimal stub
vi.mock('../../../hooks/useConfirmDialog', () => ({
  useConfirmDialog: () => ({ open: vi.fn(), close: vi.fn(), state: null }),
}));

// ---------------------------------------------------------------------------
// Shared test settings fixture
// ---------------------------------------------------------------------------

function makeSettings(packageName: string): Settings {
  return {
    // Minimal required shape — only export group matters for these tests.
    current_database: 'default',
    export: {
      export_package_name: packageName,
      export_version: '1.0.0',
      export_license: 'CC-BY-SA-4.0',
      export_author: null,
      export_description: null,
      export_tags: [],
      export_derived_from: {},
      export_dependencies: {},
    },
  } as unknown as Settings;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('useSettingsActions — handleExport validation', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    // Default: settings load successfully
    mockSettingsApi.get.mockResolvedValue(makeSettings(''));
    mockDatabaseApi.list.mockResolvedValue([]);
  });

  it('does NOT call dataApi.export and sets importError when Package Name is blank', async () => {
    // Pre-seed settings with empty package name
    mockSettingsApi.get.mockResolvedValue(makeSettings(''));

    const { useSettingsActions } = await import('../useSettingsActions');
    const { result } = renderHook(() => useSettingsActions(), { wrapper: makeWrapper() });

    // Wait for initial load
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 0));
    });

    await act(async () => {
      await result.current.handleExport();
    });

    expect(mockDataApi.export).not.toHaveBeenCalled();
    expect(result.current.importError).toBe(
      'Package Name is required. Set it under Settings -> Export Defaults before exporting.',
    );
  });

  it('calls dataApi.export when Package Name is set', async () => {
    mockSettingsApi.get.mockResolvedValue(makeSettings('org/my-package'));

    // Return a minimal blob
    mockDataApi.export.mockResolvedValue(new Blob(['fake'], { type: 'application/zip' }));

    // jsdom doesn't implement URL.createObjectURL — stub it
    const createObjectURL = vi.fn().mockReturnValue('blob:fake');
    const revokeObjectURL = vi.fn();
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL });

    // Stub document.body.appendChild / removeChild to avoid jsdom errors
    const appendSpy = vi.spyOn(document.body, 'appendChild').mockImplementation(node => node);
    const removeSpy = vi.spyOn(document.body, 'removeChild').mockImplementation(node => node);

    const { useSettingsActions } = await import('../useSettingsActions');
    const { result } = renderHook(() => useSettingsActions(), { wrapper: makeWrapper() });

    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 0));
    });

    await act(async () => {
      await result.current.handleExport();
    });

    expect(mockDataApi.export).toHaveBeenCalledOnce();
    expect(result.current.importError).toBeNull();

    appendSpy.mockRestore();
    removeSpy.mockRestore();
    vi.unstubAllGlobals();
  });
});

describe('useSettingsActions — handleSave', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockDatabaseApi.list.mockResolvedValue([]);
  });

  it('leaves the cache section out of the page save (it has its own endpoint)', async () => {
    const loaded = {
      ...makeSettings('org/pkg'),
      cache: {
        dir: null,
        completions: { enabled: true, max_bytes: 1, require_hit: false },
        embeddings: { enabled: true, max_bytes: 1, require_hit: false },
      },
    } as Settings;
    mockSettingsApi.get.mockResolvedValue(loaded);
    mockSettingsApi.update.mockResolvedValue({ settings: loaded, warnings: [] });

    const { useSettingsActions } = await import('../useSettingsActions');
    const { result } = renderHook(() => useSettingsActions(), { wrapper: makeWrapper() });
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 0));
    });

    await act(async () => {
      await result.current.handleSave();
    });

    expect(mockSettingsApi.update).toHaveBeenCalledTimes(1);
    const sent = mockSettingsApi.update.mock.calls[0][0] as Record<string, unknown>;
    expect(sent).not.toHaveProperty('cache');
    expect(sent).toHaveProperty('export');
  });

  it('toggling the chat-context switch, then saving the page, keeps the toggle', async () => {
    const loaded = {
      ...makeSettings('org/pkg'),
      graphrag: { include_unevidenced_edges: false },
    } as Settings;
    const toggled = { ...loaded, graphrag: { include_unevidenced_edges: true } } as Settings;
    mockSettingsApi.get.mockResolvedValue(loaded);
    mockSettingsApi.update.mockResolvedValue({ settings: toggled, warnings: [] });

    const { useSettingsActions } = await import('../useSettingsActions');
    const { useGraphRagSettings } = await import('../hooks/useGraphRagSettings');
    const { result } = renderHook(
      () => ({ page: useSettingsActions(), graphrag: useGraphRagSettings() }),
      { wrapper: makeWrapper() },
    );
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 0));
    });

    // the accordion saves the switch on its own…
    await act(async () => {
      await result.current.graphrag.update({ include_unevidenced_edges: true });
    });
    // …and the page's Save (holding the copy loaded before the toggle) must not send it back
    await act(async () => {
      await result.current.page.handleSave();
    });

    expect(mockSettingsApi.update).toHaveBeenCalledTimes(2);
    expect(mockSettingsApi.update.mock.calls[0][0]).toEqual({
      graphrag: { include_unevidenced_edges: true },
    });
    const pageSave = mockSettingsApi.update.mock.calls[1][0] as Record<string, unknown>;
    expect(pageSave).not.toHaveProperty('graphrag');
    expect(pageSave).not.toHaveProperty('cache');
  });
});

describe('pageSaveUpdates', () => {
  it('leaves every self-saved section out and keeps the rest', async () => {
    const { pageSaveUpdates, SELF_SAVED_SECTIONS } = await import('../../../utils/settingsSections');
    const settings = {
      ...makeSettings('org/pkg'),
      cache: { dir: null },
      graphrag: { include_unevidenced_edges: true },
    } as unknown as Settings;
    const updates = pageSaveUpdates(settings) as Record<string, unknown>;
    for (const section of SELF_SAVED_SECTIONS) expect(updates).not.toHaveProperty(section);
    expect(updates).toHaveProperty('export');
    expect([...SELF_SAVED_SECTIONS]).toEqual(['cache', 'graphrag']);
  });
});
