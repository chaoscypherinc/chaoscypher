// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * useDatabaseSelector — owns the AppBar database selector's server state
 * (list + current database) and its actions (switch, create-and-switch).
 *
 * Extracted from Layout to keep the shell component thin, mirroring
 * {@link useUploadDialogState}.
 *
 * Both actions reload the page on success so the app re-bootstraps against the
 * new database. That makes error surfacing essential: a swallowed rejection
 * would leave the create dialog silently resetting with no reload and no
 * feedback (the original bug). On failure we show a toast — with a distinct
 * message per stage so create-vs-switch failures are distinguishable — and skip
 * the reload so the user keeps their place.
 */

import { useState, useEffect, useCallback } from 'react';
import { databaseApi } from '../services/api/databases';
import { useNotification } from '../contexts/useNotification';
import { getApiErrorMessage } from '../utils/errors';
import { logger } from '../utils/logger';
import type { DatabaseInfo } from '../types/database';

interface UseDatabaseSelectorReturn {
  /** All databases known to the backend. */
  databases: DatabaseInfo[];
  /** Name of the currently active database. */
  currentDatabase: string;
  /** Switch to an existing database, then reload. No-op if already current. */
  switchDatabase: (name: string) => Promise<void>;
  /** Create a database and switch to it, then reload. */
  createDatabase: (name: string) => Promise<void>;
}

export function useDatabaseSelector(): UseDatabaseSelectorReturn {
  const { notify } = useNotification();
  const [databases, setDatabases] = useState<DatabaseInfo[]>([]);
  const [currentDatabase, setCurrentDatabase] = useState<string>('default');

  useEffect(() => {
    Promise.all([
      databaseApi.getCurrent().catch(() => null),
      databaseApi.list().catch(() => []),
    ]).then(([current, list]) => {
      if (current) setCurrentDatabase(current.current);
      setDatabases(list);
    });
  }, []);

  const switchDatabase = useCallback(
    async (dbName: string) => {
      if (dbName === currentDatabase) return;
      try {
        await databaseApi.switch(dbName);
        window.location.reload();
      } catch (error) {
        logger.error('Failed to switch database:', error);
        notify(`Couldn't switch to "${dbName}": ${getApiErrorMessage(error)}`, 'error');
      }
    },
    [currentDatabase, notify],
  );

  const createDatabase = useCallback(
    async (name: string) => {
      // Two stages with separate catches so the toast names which call failed.
      // create vs. switch is the key diagnostic for this flow — and the switch
      // path needs different guidance (the database already exists on disk).
      try {
        await databaseApi.create({ name });
      } catch (error) {
        logger.error('Failed to create database:', error);
        notify(`Couldn't create database "${name}": ${getApiErrorMessage(error)}`, 'error');
        return;
      }

      try {
        await databaseApi.switch(name);
      } catch (error) {
        logger.error('Failed to switch to newly created database:', error);
        notify(
          `Database "${name}" was created, but switching to it failed: ` +
            `${getApiErrorMessage(error)}. Pick it from the database menu to switch manually.`,
          'error',
        );
        return;
      }

      window.location.reload();
    },
    [notify],
  );

  return { databases, currentDatabase, switchDatabase, createDatabase };
}
