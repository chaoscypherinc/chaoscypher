// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * Notification context value type and React context object.
 */
import { createContext } from 'react';
import type { AlertColor } from '@mui/material';

export interface NotificationAction {
  label: string;
  onClick: () => void;
}

export interface NotificationContextValue {
  notify: (message: string, severity?: AlertColor, action?: NotificationAction) => void;
}

export const NotificationContext = createContext<NotificationContextValue | null>(null);
