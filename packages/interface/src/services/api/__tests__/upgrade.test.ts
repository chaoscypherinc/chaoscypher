// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as client from '../client';
import { applyUpgrades, fetchPendingUpgrades, rollbackUpgrade } from '../upgrade';

describe('upgrade service', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetchPendingUpgrades goes through apiClient (not raw fetch)', async () => {
    const apiGet = vi.spyOn(client.apiClient, 'get').mockResolvedValueOnce({
      data: {
        ready: true,
        blocked_on: [],
        message: 'ok',
        last_backup: null,
      },
      status: 200,
      headers: new Headers(),
    });

    const fetchSpy = vi.spyOn(globalThis, 'fetch');

    const result = await fetchPendingUpgrades();

    expect(apiGet).toHaveBeenCalledWith('/upgrade/pending');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.ready).toBe(true);
  });

  it('fetchPendingUpgrades unwraps blocked_on and last_backup', async () => {
    vi.spyOn(client.apiClient, 'get').mockResolvedValueOnce({
      data: {
        ready: false,
        blocked_on: [
          { revision: '0042', tier: 'needs_confirmation', description: 'add col X' },
          { revision: '0043', tier: 'manual', description: 'data migration' },
        ],
        message: '2 migrations need attention',
        last_backup: '/data/backups/2026-05-19-pre-0042.db',
      },
      status: 200,
      headers: new Headers(),
    });

    const result = await fetchPendingUpgrades();

    expect(result.ready).toBe(false);
    expect(result.blocked_on).toHaveLength(2);
    expect(result.blocked_on[0].tier).toBe('needs_confirmation');
    expect(result.blocked_on[1].tier).toBe('manual');
    expect(result.last_backup).toBe('/data/backups/2026-05-19-pre-0042.db');
  });

  it('applyUpgrades POSTs to /upgrade/apply and returns the body', async () => {
    const apiPost = vi.spyOn(client.apiClient, 'post').mockResolvedValueOnce({
      data: {
        applied: ['0042', '0043'],
        current_revision: '0043',
        backup_path: '/data/backups/2026-05-19-pre-0042.db',
      },
      status: 200,
      headers: new Headers(),
    });

    const result = await applyUpgrades();

    expect(apiPost).toHaveBeenCalledWith('/upgrade/apply');
    expect(result.applied).toEqual(['0042', '0043']);
    expect(result.current_revision).toBe('0043');
    expect(result.backup_path).toBe('/data/backups/2026-05-19-pre-0042.db');
  });

  it('applyUpgrades propagates errors from apiClient', async () => {
    vi.spyOn(client.apiClient, 'post').mockRejectedValueOnce(
      new Error('500 Internal Server Error'),
    );

    await expect(applyUpgrades()).rejects.toThrow('500 Internal Server Error');
  });

  it('rollbackUpgrade POSTs to /upgrade/rollback and returns the body', async () => {
    const apiPost = vi.spyOn(client.apiClient, 'post').mockResolvedValueOnce({
      data: {
        restored_from: '/data/backups/2026-05-19-pre-0042.db',
        revision: '0041',
      },
      status: 200,
      headers: new Headers(),
    });

    const result = await rollbackUpgrade();

    expect(apiPost).toHaveBeenCalledWith('/upgrade/rollback');
    expect(result.restored_from).toBe('/data/backups/2026-05-19-pre-0042.db');
    expect(result.revision).toBe('0041');
  });

  it('rollbackUpgrade handles a null revision (no migrations had been applied)', async () => {
    vi.spyOn(client.apiClient, 'post').mockResolvedValueOnce({
      data: {
        restored_from: '/data/backups/2026-05-19-pre-baseline.db',
        revision: null,
      },
      status: 200,
      headers: new Headers(),
    });

    const result = await rollbackUpgrade();

    expect(result.revision).toBeNull();
  });
});
