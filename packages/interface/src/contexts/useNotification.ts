// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useNotification — hook to access the notification system.
 *
 * Must be used within a <NotificationProvider />.
 */
import { useContext } from 'react';
import { NotificationContext, type NotificationContextValue } from './notificationContextValue';

export function useNotification(): NotificationContextValue {
  const context = useContext(NotificationContext);
  if (!context) {
    throw new Error('useNotification must be used within a NotificationProvider');
  }
  return context;
}
