// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Tests for BackupTab.tsx after its migration to TanStack Query.
 *
 * The tab no longer calls `backupApi` directly — list/create/restore/delete/
 * download flow through the `useBackups` family of hooks in
 * `../hooks/useBackups`, which call the `backup` service module. We mock that
 * service module and render the tab inside `makeWrapper` (which provides a
 * QueryClient), then assert the same user-visible behaviour as before:
 * loaded rows, the create/restore/delete success + error alerts, and the
 * confirmation dialogs.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import BackupTab from '../BackupTab';
import { makeWrapper } from '../../../test/renderWithProviders';
import type { Settings } from '../../../types';
import type { BackupInfo } from '../../../services/api/backup';

interface RestoreResult {
  database: string;
  restored_from: string;
}

// BackupTab -> useBackups hooks -> backup service module. Mock the service so
// every hook resolves against our controllable fakes.
const create = vi.fn<() => Promise<BackupInfo>>();
const list = vi.fn<() => Promise<BackupInfo[]>>();
const restore = vi.fn<(filename: string) => Promise<RestoreResult>>();
const download = vi.fn<(filename: string) => Promise<void>>();
const deleteBackup = vi.fn<(filename: string) => Promise<void>>();

vi.mock('../../../services/api/backup', () => ({
  backupApi: {
    create: () => create(),
    list: () => list(),
    restore: (filename: string) => restore(filename),
    download: (filename: string) => download(filename),
    delete: (filename: string) => deleteBackup(filename),
  },
}));

function makeSettings(over: Partial<NonNullable<Settings['backup']>> = {}): Settings {
  return {
    backup: {
      enabled: true,
      interval: 'daily',
      retention_count: 7,
      backup_dir: '/data/backups',
      ...over,
    },
  } as unknown as Settings;
}

function makeBackup(over: Partial<BackupInfo> = {}): BackupInfo {
  return {
    database: 'chaoscypher',
    filename: 'backup_20260101_120000.db',
    size: 1024 * 1024,
    created_at: '20260101_120000',
    ...over,
  };
}

function renderTab(settings: Settings = makeSettings(), setSettings = vi.fn()) {
  render(<BackupTab settings={settings} setSettings={setSettings} />, {
    wrapper: makeWrapper(),
  });
  return { setSettings };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Default: empty backup list resolves successfully.
  list.mockResolvedValue([]);
  create.mockResolvedValue(makeBackup());
  restore.mockResolvedValue({ database: 'chaoscypher', restored_from: 'backup_20260101_120000.db' });
  download.mockResolvedValue(undefined);
  deleteBackup.mockResolvedValue(undefined);
});

describe('BackupTab', () => {
  describe('initial load', () => {
    it('shows the empty state when there are no backups', async () => {
      renderTab();
      await waitFor(() => {
        expect(screen.getByText(/no backups yet/i)).toBeInTheDocument();
      });
      expect(list).toHaveBeenCalledTimes(1);
    });

    it('renders backup rows from the API and the summary count', async () => {
      list.mockResolvedValue([
        makeBackup({ filename: 'backup_20260101_120000.db', size: 2048 }),
        makeBackup({ filename: 'backup_20260102_120000.db', size: 4096 }),
      ]);
      renderTab();
      await waitFor(() => {
        expect(screen.getByText('backup_20260101_120000.db')).toBeInTheDocument();
      });
      expect(screen.getByText('backup_20260102_120000.db')).toBeInTheDocument();
      // Summary: "(2 backups, ... total)"
      expect(screen.getByText(/2 backups/i)).toBeInTheDocument();
    });

    it('shows an error when the initial list fails', async () => {
      list.mockRejectedValue(new Error('boom'));
      renderTab();
      await waitFor(() => {
        expect(screen.getByText(/failed to load backups/i)).toBeInTheDocument();
      });
    });
  });

  describe('schedule / retention settings', () => {
    it('reflects the enabled switch state from settings', async () => {
      renderTab(makeSettings({ enabled: true }));
      await waitFor(() => expect(screen.getByText(/no backups yet/i)).toBeInTheDocument());
      const toggle = screen.getByLabelText('Enabled');
      expect(toggle).toBeChecked();
    });

    it('calls setSettings with backup.enabled flipped when toggled', async () => {
      const { setSettings } = renderTab(makeSettings({ enabled: true }));
      await waitFor(() => expect(screen.getByText(/no backups yet/i)).toBeInTheDocument());
      fireEvent.click(screen.getByLabelText('Enabled'));
      expect(setSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          backup: expect.objectContaining({ enabled: false }),
        }),
      );
    });

    it('calls setSettings with the new interval from the select', async () => {
      const { setSettings } = renderTab();
      await waitFor(() => expect(screen.getByText(/no backups yet/i)).toBeInTheDocument());
      fireEvent.mouseDown(screen.getByRole('combobox'));
      const option = await screen.findByRole('option', { name: 'Weekly' });
      fireEvent.click(option);
      expect(setSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          backup: expect.objectContaining({ interval: 'weekly' }),
        }),
      );
    });

    it('calls setSettings with a clamped retention_count', async () => {
      const { setSettings } = renderTab();
      await waitFor(() => expect(screen.getByText(/no backups yet/i)).toBeInTheDocument());
      const input = screen.getByRole('spinbutton');
      // 999 should clamp to 100 (max).
      fireEvent.change(input, { target: { value: '999' } });
      expect(setSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          backup: expect.objectContaining({ retention_count: 100 }),
        }),
      );
    });

    it('clamps an invalid retention_count up to the minimum of 1', async () => {
      const { setSettings } = renderTab();
      await waitFor(() => expect(screen.getByText(/no backups yet/i)).toBeInTheDocument());
      const input = screen.getByRole('spinbutton');
      fireEvent.change(input, { target: { value: '0' } });
      expect(setSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          backup: expect.objectContaining({ retention_count: 1 }),
        }),
      );
    });
  });

  describe('create backup', () => {
    it('creates a backup, shows a success alert, and refreshes the list', async () => {
      list.mockResolvedValueOnce([]); // initial load
      create.mockResolvedValue(makeBackup({ filename: 'backup_new.db', size: 1024 }));
      list.mockResolvedValueOnce([makeBackup({ filename: 'backup_new.db', size: 1024 })]); // refresh
      renderTab();
      await waitFor(() => expect(screen.getByText(/no backups yet/i)).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: /create backup now/i }));

      await waitFor(() => {
        expect(create).toHaveBeenCalledTimes(1);
      });
      await waitFor(() => {
        expect(screen.getByText(/backup created: backup_new\.db/i)).toBeInTheDocument();
      });
      // Refresh via invalidateQueries: list called twice (initial + after create).
      await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    });

    it('shows an error alert when creating a backup fails', async () => {
      create.mockRejectedValue(new Error('nope'));
      renderTab();
      await waitFor(() => expect(screen.getByText(/no backups yet/i)).toBeInTheDocument());

      fireEvent.click(screen.getByRole('button', { name: /create backup now/i }));

      await waitFor(() => {
        expect(screen.getByText(/failed to create backup/i)).toBeInTheDocument();
      });
    });
  });

  describe('download backup', () => {
    it('calls download with the backup filename', async () => {
      list.mockResolvedValue([makeBackup({ filename: 'backup_dl.db' })]);
      renderTab();
      await waitFor(() => expect(screen.getByText('backup_dl.db')).toBeInTheDocument());

      const row = screen.getByText('backup_dl.db').closest('tr');
      expect(row).not.toBeNull();
      fireEvent.click(within(row as HTMLElement).getByLabelText('Download'));

      await waitFor(() => {
        expect(download).toHaveBeenCalledWith('backup_dl.db');
      });
    });

    it('shows an error alert when download fails', async () => {
      list.mockResolvedValue([makeBackup({ filename: 'backup_dl.db' })]);
      download.mockRejectedValue(new Error('fail'));
      renderTab();
      await waitFor(() => expect(screen.getByText('backup_dl.db')).toBeInTheDocument());

      const row = screen.getByText('backup_dl.db').closest('tr');
      fireEvent.click(within(row as HTMLElement).getByLabelText('Download'));

      await waitFor(() => {
        expect(screen.getByText(/failed to download backup_dl\.db/i)).toBeInTheDocument();
      });
    });
  });

  describe('restore backup', () => {
    async function openRestoreDialog(filename = 'backup_restore.db') {
      list.mockResolvedValue([makeBackup({ filename })]);
      renderTab();
      await waitFor(() => expect(screen.getByText(filename)).toBeInTheDocument());
      const row = screen.getByText(filename).closest('tr');
      fireEvent.click(within(row as HTMLElement).getByLabelText('Restore'));
      // Dialog title appears.
      await screen.findByText('Restore Database');
    }

    it('opens the confirm dialog with the safety warning', async () => {
      await openRestoreDialog();
      const dialog = screen.getByRole('dialog');
      expect(
        within(dialog).getByText(/safety backup of the current database will be created automatically/i),
      ).toBeInTheDocument();
      // Confirm button is disabled until checkbox + RESTORE text.
      expect(within(dialog).getByRole('button', { name: /^restore$/i })).toBeDisabled();
    });

    it('keeps Restore disabled until the checkbox and RESTORE text are provided', async () => {
      await openRestoreDialog();
      const dialog = screen.getByRole('dialog');
      const confirmBtn = within(dialog).getByRole('button', { name: /^restore$/i });

      // Check the acknowledgement checkbox only — still disabled.
      fireEvent.click(within(dialog).getByRole('checkbox'));
      expect(confirmBtn).toBeDisabled();

      // Type the wrong text — still disabled.
      const textField = within(dialog).getByLabelText(/type restore to proceed/i);
      fireEvent.change(textField, { target: { value: 'restore' } });
      expect(confirmBtn).toBeDisabled();

      // Type RESTORE in caps — now enabled.
      fireEvent.change(textField, { target: { value: 'RESTORE' } });
      expect(confirmBtn).toBeEnabled();
    });

    it('confirms the restore and calls backupApi.restore with the filename', async () => {
      await openRestoreDialog('backup_restore.db');
      const dialog = screen.getByRole('dialog');
      fireEvent.click(within(dialog).getByRole('checkbox'));
      fireEvent.change(within(dialog).getByLabelText(/type restore to proceed/i), {
        target: { value: 'RESTORE' },
      });
      fireEvent.click(within(dialog).getByRole('button', { name: /^restore$/i }));

      await waitFor(() => {
        expect(restore).toHaveBeenCalledWith('backup_restore.db');
      });
      await waitFor(() => {
        expect(screen.getByText(/database restored from/i)).toBeInTheDocument();
      });
    });

    it('shows an error alert when restore fails', async () => {
      restore.mockRejectedValue(new Error('restore failed'));
      await openRestoreDialog('backup_restore.db');
      const dialog = screen.getByRole('dialog');
      fireEvent.click(within(dialog).getByRole('checkbox'));
      fireEvent.change(within(dialog).getByLabelText(/type restore to proceed/i), {
        target: { value: 'RESTORE' },
      });
      fireEvent.click(within(dialog).getByRole('button', { name: /^restore$/i }));

      await waitFor(() => {
        expect(screen.getByText(/failed to restore from backup_restore\.db/i)).toBeInTheDocument();
      });
    });

    it('cancels the restore dialog without calling the API', async () => {
      await openRestoreDialog();
      const dialog = screen.getByRole('dialog');
      fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));
      await waitFor(() => {
        expect(screen.queryByText('Restore Database')).not.toBeInTheDocument();
      });
      expect(restore).not.toHaveBeenCalled();
    });
  });

  describe('delete backup', () => {
    async function openDeleteDialog(filename = 'backup_del.db') {
      list.mockResolvedValue([makeBackup({ filename })]);
      renderTab();
      await waitFor(() => expect(screen.getByText(filename)).toBeInTheDocument());
      const row = screen.getByText(filename).closest('tr');
      fireEvent.click(within(row as HTMLElement).getByLabelText('Delete backup'));
      await screen.findByText('Delete Backup');
    }

    it('confirms the delete and calls backupApi.delete then refreshes', async () => {
      list.mockResolvedValue([makeBackup({ filename: 'backup_del.db' })]);
      renderTab();
      await waitFor(() => expect(screen.getByText('backup_del.db')).toBeInTheDocument());
      const row = screen.getByText('backup_del.db').closest('tr');
      fireEvent.click(within(row as HTMLElement).getByLabelText('Delete backup'));
      await screen.findByText('Delete Backup');

      const dialog = screen.getByRole('dialog');
      fireEvent.click(within(dialog).getByRole('button', { name: /^delete$/i }));

      await waitFor(() => {
        expect(deleteBackup).toHaveBeenCalledWith('backup_del.db');
      });
      await waitFor(() => {
        expect(screen.getByText(/deleted backup_del\.db/i)).toBeInTheDocument();
      });
      // initial load + refresh after delete (invalidateQueries).
      await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    });

    it('shows an error alert when delete fails', async () => {
      deleteBackup.mockRejectedValue(new Error('delete failed'));
      await openDeleteDialog('backup_del.db');
      const dialog = screen.getByRole('dialog');
      fireEvent.click(within(dialog).getByRole('button', { name: /^delete$/i }));

      await waitFor(() => {
        expect(screen.getByText(/failed to delete backup_del\.db/i)).toBeInTheDocument();
      });
    });

    it('cancels the delete dialog without calling the API', async () => {
      await openDeleteDialog();
      const dialog = screen.getByRole('dialog');
      fireEvent.click(within(dialog).getByRole('button', { name: /cancel/i }));
      await waitFor(() => {
        expect(screen.queryByText('Delete Backup')).not.toBeInTheDocument();
      });
      expect(deleteBackup).not.toHaveBeenCalled();
    });
  });
});
