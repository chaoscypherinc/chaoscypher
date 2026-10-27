// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useCallback, useState } from 'react';
import { Alert, Button, IconButton, Snackbar } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';

import ConfirmDialog from './ConfirmDialog';
import { useNotification } from '../contexts/useNotification';
import { usePendingUpgrades, useRollbackUpgrade } from '../services/api/useMaintenance';
import { readStoredString, writeStoredString } from '../utils/storage';

const DISMISS_KEY = 'chaoscypher-upgrade-notice-dismissed';

function readDismissed(): string | null {
  return readStoredString(DISMISS_KEY);
}

function writeDismissed(backup: string): void {
  // private-mode / quota — non-fatal; the notice just re-shows next load
  writeStoredString(DISMISS_KEY, backup);
}

/**
 * Sticky toast shown after a SILENT, data-changing schema auto-upgrade ran at
 * startup (ready=true, but the upgrade-state record retains last_applied +
 * data_changing + last_backup). Tells the operator it happened and offers a
 * one-click rollback to the pre-upgrade backup. Stays until dismissed (the
 * Alert close button), keyed by the backup path so a later upgrade re-shows
 * it. The MCP analog is the upgrade_status tool; this is the web surface.
 *
 * A first install is not an upgrade: the backend builds a new database's
 * schema without a backup or a record. Without a backup there is also nothing
 * to roll back to, so the notice never shows without one.
 */
export function PostUpgradeNotice() {
  const { data } = usePendingUpgrades();
  const rollback = useRollbackUpgrade();
  const { notify } = useNotification();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [closed, setClosed] = useState(false);

  const lastBackup = data?.last_backup ?? null;
  const show =
    !!data &&
    !!lastBackup &&
    data.ready &&
    data.data_changing &&
    data.last_applied.length > 0 &&
    !closed &&
    readDismissed() !== lastBackup;

  const handleClose = useCallback(() => {
    if (lastBackup) writeDismissed(lastBackup);
    setClosed(true);
  }, [lastBackup]);

  const handleRollback = useCallback(() => {
    setConfirmOpen(false);
    rollback.mutate(undefined, {
      onSuccess: () => {
        notify('Database rolled back to the pre-upgrade backup.', 'success');
        window.location.reload();
      },
      onError: () => {
        notify('Roll back failed. The backup is unchanged; check server logs.', 'error');
      },
    });
  }, [rollback, notify]);

  if (!show) {
    return null;
  }

  const count = data.last_applied.length;

  return (
    <>
      <Snackbar
        open
        autoHideDuration={null}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          severity="info"
          variant="filled"
          sx={{ width: '100%' }}
          action={
            <>
              <Button
                color="inherit"
                size="small"
                disabled={rollback.isPending}
                onClick={() => setConfirmOpen(true)}
                sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}
              >
                Roll back
              </Button>
              <IconButton
                aria-label="Close"
                color="inherit"
                size="small"
                onClick={handleClose}
              >
                <CloseIcon fontSize="small" />
              </IconButton>
            </>
          }
        >
          {`Database auto-upgraded — ${count} migration${count === 1 ? '' : 's'} applied, including data-changing changes. A pre-upgrade backup was saved.`}
        </Alert>
      </Snackbar>
      <ConfirmDialog
        open={confirmOpen}
        title="Roll back the database upgrade?"
        message="This restores the database from the pre-upgrade backup and discards the migrations that were applied. The app will reload."
        confirmLabel="Roll back"
        confirmColor="warning"
        onConfirm={handleRollback}
        onCancel={() => setConfirmOpen(false)}
      />
    </>
  );
}
