// Copyright (C) 2024-2026 Chaos Cypher, Inc.
// SPDX-License-Identifier: AGPL-3.0-only

/**
 * The settings sections saved by their own control on the Settings page, not by
 * the page-wide Save: the Cache accordion (`PATCH /cache`) and the GraphRAG
 * accordion (`PATCH /settings` with the `graphrag` section). The page holds the
 * copy of the settings it loaded; sending a self-saved section back with the
 * page's Save would undo a switch flipped since the page opened.
 */

import type { Settings } from '../types';

export const SELF_SAVED_SECTIONS = ['cache', 'graphrag'] as const satisfies readonly (keyof Settings)[];

/** The page's settings as the page-wide Save sends them: every section but the self-saved ones. */
export function pageSaveUpdates(settings: Settings): Partial<Settings> {
  const updates: Partial<Settings> = { ...settings };
  for (const section of SELF_SAVED_SECTIONS) {
    delete updates[section];
  }
  return updates;
}
