// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { render, screen, fireEvent, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { PostUpgradeNotice } from '../PostUpgradeNotice';
import { usePendingUpgrades, useRollbackUpgrade } from '../../services/api/useMaintenance';
import { useNotification } from '../../contexts/useNotification';

vi.mock('../../services/api/useMaintenance');
vi.mock('../../contexts/useNotification');

const mutate = vi.fn();
const DISMISS_KEY = 'chaoscypher-upgrade-notice-dismissed';

const SILENT = {
  ready: true,
  blocked_on: [],
  message: '',
  last_backup: '/data/backups/pre-0042-x.db',
  last_applied: ['0042', '0043'],
  data_changing: true,
};

function setPending(data: unknown): void {
  vi.mocked(usePendingUpgrades).mockReturnValue({ data } as never);
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  vi.mocked(useRollbackUpgrade).mockReturnValue({ mutate, isPending: false } as never);
  vi.mocked(useNotification).mockReturnValue({ notify: vi.fn() } as never);
});

describe('PostUpgradeNotice', () => {
  it('shows after a silent data-changing upgrade', () => {
    setPending(SILENT);
    render(<PostUpgradeNotice />);
    expect(screen.getByText(/Database auto-upgraded/)).toBeInTheDocument();
    expect(screen.getByText(/2 migrations applied/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /roll back/i })).toBeInTheDocument();
  });

  it.each([
    ['not data-changing', { ...SILENT, data_changing: false }],
    ['no migrations applied', { ...SILENT, last_applied: [] }],
    ['blocked (not ready)', { ...SILENT, ready: false }],
  ])('hidden when %s', (_label, data) => {
    setPending(data);
    const { container } = render(<PostUpgradeNotice />);
    expect(container).toBeEmptyDOMElement();
  });

  it('hidden when already dismissed for this backup', () => {
    localStorage.setItem(DISMISS_KEY, SILENT.last_backup);
    setPending(SILENT);
    const { container } = render(<PostUpgradeNotice />);
    expect(container).toBeEmptyDOMElement();
  });

  it('re-shows for a new backup after a prior dismissal', () => {
    localStorage.setItem(DISMISS_KEY, '/old/backup.db');
    setPending(SILENT);
    render(<PostUpgradeNotice />);
    expect(screen.getByText(/Database auto-upgraded/)).toBeInTheDocument();
  });

  it('dismiss writes localStorage and hides', () => {
    setPending(SILENT);
    render(<PostUpgradeNotice />);
    fireEvent.click(screen.getByRole('button', { name: /close/i }));
    expect(localStorage.getItem(DISMISS_KEY)).toBe(SILENT.last_backup);
    expect(screen.queryByText(/Database auto-upgraded/)).not.toBeInTheDocument();
  });

  it('rollback fires only after confirming the dialog', () => {
    setPending(SILENT);
    render(<PostUpgradeNotice />);
    fireEvent.click(screen.getByRole('button', { name: /roll back/i }));
    expect(mutate).not.toHaveBeenCalled();
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: /roll back/i }));
    expect(mutate).toHaveBeenCalledTimes(1);
  });
});
