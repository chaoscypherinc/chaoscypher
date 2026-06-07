// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

import { useState, useCallback, useMemo, type ReactNode } from 'react';
import { Snackbar, Alert, Button, type AlertColor } from '@mui/material';
import { NotificationContext, type NotificationAction } from './notificationContextValue';

interface Notification {
  message: string;
  severity: AlertColor;
  action?: NotificationAction;
}

/**
 * Provides a notification system using MUI Snackbar + Alert.
 * Wrap the app with this provider and use the `useNotification` hook
 * to show toast notifications from any component.
 *
 * Supports an optional `action` button (label + onClick) for toasts that
 * require a follow-up navigation, e.g. "Open source" after a skipped duplicate.
 */
export function NotificationProvider({ children }: { children: ReactNode }) {
  const [notification, setNotification] = useState<Notification | null>(null);
  const [open, setOpen] = useState(false);

  const notify = useCallback((message: string, severity: AlertColor = 'info', action?: NotificationAction) => {
    setNotification({ message, severity, action });
    setOpen(true);
  }, []);

  const handleClose = useCallback((_?: React.SyntheticEvent | Event, reason?: string) => {
    if (reason === 'clickaway') return;
    setOpen(false);
  }, []);

  const handleActionClick = useCallback(() => {
    notification?.action?.onClick();
    setOpen(false);
  }, [notification]);

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <NotificationContext.Provider value={value}>
      {children}
      <Snackbar
        open={open}
        autoHideDuration={notification?.action ? 8000 : 4000}
        onClose={handleClose}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          onClose={handleClose}
          severity={notification?.severity ?? 'info'}
          variant="filled"
          sx={{ width: '100%' }}
          action={
            notification?.action ? (
              <Button
                color="inherit"
                size="small"
                onClick={handleActionClick}
                sx={{ fontWeight: 600, whiteSpace: 'nowrap' }}
              >
                {notification.action.label}
              </Button>
            ) : undefined
          }
        >
          {notification?.message}
        </Alert>
      </Snackbar>
    </NotificationContext.Provider>
  );
}

