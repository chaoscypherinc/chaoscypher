// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for useDatabaseSelector — the AppBar database selector's state +
 * actions. The behaviour under test is the bugfix for the silent
 * create-and-switch failure: when create or switch rejects, the user must get
 * a toast (and the page must NOT reload), instead of the dialog quietly
 * resetting with no feedback. Distinct toasts per stage (create vs. switch)
 * are asserted so a failure tells us which call broke.
 *
 * `databaseApi` and `useNotification` are mocked; the real `utils/errors`
 * helper is pure so we exercise it directly.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type { DatabaseInfo, DatabaseCreate } from '../../types';

type CreateFn = (database: DatabaseCreate) => Promise<DatabaseInfo>;
type SwitchFn = (name: string) => Promise<{ success: boolean; message: string; database: string }>;
type GetCurrentFn = () => Promise<{ current: string; info: DatabaseInfo }>;
type ListFn = () => Promise<DatabaseInfo[]>;

vi.mock('../../services/api/databases', () => ({
  databaseApi: {
    create: vi.fn<CreateFn>(),
    switch: vi.fn<SwitchFn>(),
    getCurrent: vi.fn<GetCurrentFn>(),
    list: vi.fn<ListFn>(),
  },
}));

const notify = vi.fn<(message: string, severity?: string) => void>();
vi.mock('../../contexts/useNotification', () => ({
  useNotification: () => ({ notify }),
}));

vi.mock('../../utils/logger', () => ({
  logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));

import { databaseApi } from '../../services/api/databases';
import { useDatabaseSelector } from '../useDatabaseSelector';

const mockedApi = databaseApi as unknown as {
  create: ReturnType<typeof vi.fn>;
  switch: ReturnType<typeof vi.fn>;
  getCurrent: ReturnType<typeof vi.fn>;
  list: ReturnType<typeof vi.fn>;
};

const info: DatabaseInfo = { name: 'new-db', path: '/data/new-db', size: 0, exists: true } as DatabaseInfo;

let reloadSpy: ReturnType<typeof vi.fn>;
const originalLocation = window.location;

beforeEach(() => {
  vi.clearAllMocks();
  // Default the initial load effect to resolve quietly.
  mockedApi.getCurrent.mockResolvedValue({ current: 'default', info });
  mockedApi.list.mockResolvedValue([]);
  reloadSpy = vi.fn();
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: { ...originalLocation, reload: reloadSpy },
  });
});

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation });
});

describe('useDatabaseSelector.createDatabase', () => {
  it('creates, switches, and reloads on success', async () => {
    mockedApi.create.mockResolvedValue(info);
    mockedApi.switch.mockResolvedValue({ success: true, message: 'ok', database: 'new-db' });

    const { result } = renderHook(() => useDatabaseSelector());
    await act(async () => {
      await result.current.createDatabase('new-db');
    });

    expect(mockedApi.create).toHaveBeenCalledWith({ name: 'new-db' });
    expect(mockedApi.switch).toHaveBeenCalledWith('new-db');
    expect(reloadSpy).toHaveBeenCalledTimes(1);
    expect(notify).not.toHaveBeenCalled();
  });

  it('notifies and does not switch or reload when create fails', async () => {
    mockedApi.create.mockRejectedValue(
      Object.assign(new Error('boom'), { response: { data: { message: "Database 'new-db' already exists" } } }),
    );

    const { result } = renderHook(() => useDatabaseSelector());
    await act(async () => {
      await result.current.createDatabase('new-db');
    });

    expect(mockedApi.switch).not.toHaveBeenCalled();
    expect(reloadSpy).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledTimes(1);
    const [message, severity] = notify.mock.calls[0];
    expect(message).toContain('create');
    expect(message).toContain("Database 'new-db' already exists");
    expect(severity).toBe('error');
  });

  it('notifies (created-but-not-switched) and does not reload when switch fails', async () => {
    mockedApi.create.mockResolvedValue(info);
    mockedApi.switch.mockRejectedValue(
      Object.assign(new Error('boom'), { response: { data: { message: 'server exploded' } } }),
    );

    const { result } = renderHook(() => useDatabaseSelector());
    await act(async () => {
      await result.current.createDatabase('new-db');
    });

    expect(mockedApi.create).toHaveBeenCalled();
    expect(reloadSpy).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledTimes(1);
    const [message, severity] = notify.mock.calls[0];
    expect(message.toLowerCase()).toContain('created');
    expect(message).toContain('server exploded');
    expect(severity).toBe('error');
  });
});

describe('useDatabaseSelector.switchDatabase', () => {
  it('notifies and does not reload when switch fails', async () => {
    mockedApi.switch.mockRejectedValue(
      Object.assign(new Error('boom'), { response: { data: { message: 'switch failed' } } }),
    );

    const { result } = renderHook(() => useDatabaseSelector());
    // Let the initial load settle so currentDatabase === 'default'.
    await waitFor(() => expect(mockedApi.getCurrent).toHaveBeenCalled());

    await act(async () => {
      await result.current.switchDatabase('other-db');
    });

    expect(reloadSpy).not.toHaveBeenCalled();
    expect(notify).toHaveBeenCalledTimes(1);
    expect(notify.mock.calls[0][0]).toContain('switch failed');
    expect(notify.mock.calls[0][1]).toBe('error');
  });

  it('is a no-op when switching to the current database', async () => {
    const { result } = renderHook(() => useDatabaseSelector());
    await waitFor(() => expect(mockedApi.getCurrent).toHaveBeenCalled());

    await act(async () => {
      await result.current.switchDatabase('default');
    });

    expect(mockedApi.switch).not.toHaveBeenCalled();
    expect(reloadSpy).not.toHaveBeenCalled();
  });
});
